from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
import uuid

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def new_id(prefix: str = "DSP") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"

def normalize_reason(raw: str) -> str:
    """Map PayPal dispute reason codes (and loose variants) to our enum.
    PayPal uses e.g. ITEMS_NOT_RECEIVED, ITEMS_NOT_AS_DESCRIBED, UNAUTHORIZD."""
    r = (raw or "").strip().upper().replace(" ", "_").replace("-", "_")
    if not r:
        return "ITEM_NOT_RECEIVED"
    if "NOT_RECEIVED" in r or "NOTRECEIVED" in r:
        return "ITEM_NOT_RECEIVED"
    if "NOT_AS_DESCRIBED" in r or "NOTASDESCRIBED" in r:
        return "NOT_AS_DESCRIBED"
    if "UNAUTH" in r:
        return "UNAUTHORIZED"
    return "OTHER"

def compute_urgency(respond_by: str, now: datetime | None = None) -> str:
    """Deadline urgency for the response SLA. PASSED blocks all auto-action."""
    if not respond_by:
        return "NORMAL"
    try:
        deadline = datetime.fromisoformat(respond_by)
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
    except ValueError:
        return "NORMAL"
    now = now or datetime.now(timezone.utc)
    hours = (deadline - now).total_seconds() / 3600
    if hours < 0:
        return "PASSED"
    if hours < 24:
        return "CRITICAL"
    if hours < 72:
        return "WARNING"
    return "NORMAL"

URGENCY_RANK = {"PASSED": 0, "CRITICAL": 1, "WARNING": 2, "NORMAL": 3}

class OrderInfo(BaseModel):
    order_id: str
    amount: float
    currency: str = "USD"
    buyer_email: str = ""
    items: List[Dict[str, Any]] = []
    tracking_number: str = ""
    tracking_status: str = ""
    purchase_date: str = ""
    shipping_address: str = ""      # where the order was shipped (confirmed address)
    delivered_address: str = ""     # what the carrier says (mismatch = risk)

class AgentRecommendation(BaseModel):
    """Schema-bound LLM output. The model PROPOSES; the policy engine DECIDES."""
    dispute_type: str          # ITEM_NOT_RECEIVED | NOT_AS_DESCRIBED | UNAUTHORIZED | OTHER
    evidence_strength: str     # strong | medium | weak
    recommended_action: str    # contest | refund | partial_refund | escalate
    confidence: float          # 0..1, model self-reported
    evidence_summary: str
    draft_response: str
    missing_evidence: List[str] = []

class DisputeCase(BaseModel):
    case_id: str = Field(default_factory=lambda: new_id())
    paypal_dispute_id: str = ""
    reason: str = "ITEM_NOT_RECEIVED"  # ITEM_NOT_RECEIVED | NOT_AS_DESCRIBED | UNAUTHORIZED
    amount: float = 0.0
    currency: str = "USD"
    status: str = "NEW"  # NEW | EVIDENCE_READY | AUTO_RESOLVED | WAITING_APPROVAL | REJECTED | SUBMITTED
    order: OrderInfo
    customer_messages: List[Dict[str, str]] = []
    evidence_summary: str = ""
    drafted_response: str = ""
    missing_items: List[str] = []
    recommended_action: str = ""   # from validated AgentRecommendation
    confidence: float = 0.0        # model self-reported, clamped 0..1
    agent_provider: str = ""       # anthropic | openai | template (deterministic fallback)
    agent_raw_output: str = ""     # raw model text (capped) — shown in the UI for transparency
    amount_known: bool = True      # False when amount could not be parsed -> never auto-act
    agent_error: str = ""          # set when LLM output fails validation -> forced human review
    respond_by: str = ""           # PayPal response deadline (ISO) — the clock sellers race
    urgency: str = "NORMAL"        # NORMAL | WARNING | CRITICAL | PASSED (computed from respond_by)
    risk_flags: List[str] = []     # deterministic risk-engine findings -> veto any auto-action
    already_refunded: bool = False # merchant already refunded -> never auto-act (double-refund risk)
    policy_decision: str = ""
    policy_reason: str = ""
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

class AuditEntry(BaseModel):
    id: str = Field(default_factory=lambda: new_id("AUD"))
    case_id: str
    action: str
    reason: str
    actor: str = "agent"  # agent | merchant | system
    created_at: str = Field(default_factory=now_iso)

class MerchantPolicy(BaseModel):
    auto_resolve_under: float = Field(50.0, ge=0)
    auto_resolve_not_as_described_under: float = Field(20.0, ge=0)  # NAD is riskier: lower limit
    auto_submit_evidence_under: float = Field(200.0, ge=0)
    require_approval_above: float = Field(200.0, ge=0)
    auto_refund_unauthorized_under: float = Field(30.0, ge=0)
    min_confidence: float = Field(0.7, ge=0, le=1)  # below this the agent must stop and ask a human
