import os
from datetime import datetime, timedelta, timezone
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError
from .models import (DisputeCase, OrderInfo, MerchantPolicy, new_id, now_iso,
                     normalize_reason, compute_urgency)
from .store import CASES, AUDIT, POLICY, log, upsert_case, list_cases, get_case, find_by_paypal_id
from .evidence_agent import run_evidence_agent
from .policy_engine import apply_policy
from .risk_engine import scan_risk
from .scenarios import get_scenario, respond_by_from_hours, SCENARIOS
from . import paypal_client

app = FastAPI(title="DisputeDesk API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class SimulateIn(BaseModel):
    scenario: str = ""              # key into scenarios.SCENARIOS (edge cases)
    reason: str = "ITEM_NOT_RECEIVED"
    amount: float = 29.99
    currency: str = "USD"
    order_total: float = 0.0        # > amount -> partial dispute flag
    tracking_status: str = "Delivered Oct 10, signed"
    buyer_email: str = "buyer@example.com"
    shipping_address: str = ""
    delivered_address: str = ""
    already_refunded: bool = False
    respond_hours: float = 168.0    # PayPal gives sellers ~7-21 days
    messages: list = []

class DraftIn(BaseModel):
    draft_response: str = Field(..., max_length=3000)

class CounterofferIn(BaseModel):
    amount: float

def require_desk_token(request: Request):
    """Shared-token auth for state-changing endpoints. Set DESK_TOKEN in production
    and send it as X-Desk-Token. Unset (local dev / tests) = open, same as before."""
    token = os.getenv("DESK_TOKEN", "")
    if not token:
        return
    if request.headers.get("x-desk-token", "") != token:
        raise HTTPException(401, "missing or invalid X-Desk-Token header")

PROTECTED = [Depends(require_desk_token)]

def _to_amount(raw) -> float | None:
    """PayPal may send amount as {value, currency_code} or a plain number. Never crash."""
    if isinstance(raw, dict):
        raw = raw.get("value")
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None

async def _pipeline(case: DisputeCase) -> DisputeCase:
    """Shared case pipeline: agent -> risk scan -> policy. Used by webhook + simulator."""
    case = await run_evidence_agent(case)
    upsert_case(case)
    log(case.case_id, "EVIDENCE_DRAFTED",
        f"action={case.recommended_action or 'n/a'}, confidence {case.confidence:.0%}, "
        f"missing={case.missing_items}" + (f", VALIDATION FAILED: {case.agent_error}" if case.agent_error else ""))
    case.urgency = compute_urgency(case.respond_by)
    case.risk_flags = scan_risk(case)
    if case.risk_flags:
        log(case.case_id, "RISK_FLAGS", f"Risk engine: {', '.join(case.risk_flags)}", actor="system")
    if case.urgency == "PASSED":
        log(case.case_id, "SLA_BREACHED", f"Respond-by {case.respond_by} has passed", actor="system")
    case = apply_policy(case)
    upsert_case(case)
    return case

@app.get("/health")
def health():
    return {"ok": True, "mock_paypal": paypal_client.MOCK, "cases": len(CASES)}

@app.get("/api/policy")
def get_policy():
    return POLICY.model_dump()

@app.post("/api/policy", dependencies=PROTECTED)
def set_policy(p: dict):
    fields = type(POLICY).model_fields
    update = {k: v for k, v in p.items() if k in fields}
    if not update:
        raise HTTPException(422, f"no known policy fields; valid: {list(fields)}")
    try:
        merged = MerchantPolicy.model_validate({**POLICY.model_dump(), **update})
    except ValidationError as e:
        raise HTTPException(422, e.errors())
    for k in fields:
        setattr(POLICY, k, getattr(merged, k))
    log("GLOBAL", "POLICY_UPDATED", str(POLICY.model_dump()), actor="merchant")
    return POLICY.model_dump()

# --- PayPal webhook intake ---
@app.post("/webhooks/paypal")
async def paypal_webhook(request: Request, event: dict):
    """Receives CUSTOMER.DISPUTE.CREATED / UPDATED etc.
    Verifies the webhook signature when PAYPAL_WEBHOOK_ID is set (MOCK mode accepts).
    Idempotent: re-delivered events update the existing case instead of duplicating it."""
    if not paypal_client.verify_webhook_signature(dict(request.headers), event):
        log("GLOBAL", "WEBHOOK_REJECTED", "signature verification failed", actor="system")
        raise HTTPException(401, "webhook signature verification failed")
    etype = event.get("event_type", "")
    res = event.get("resource", {}) or {}
    pp_id = res.get("id") or res.get("dispute_id") or new_id("PP")
    # Best-effort live enrichment — never breaks demo
    if not paypal_client.MOCK:
        try:
            live = paypal_client.get_dispute(pp_id)
            res = {**res, **live} if isinstance(live, dict) else res
            log("GLOBAL", "DISPUTE_ENRICHED", f"Live fetch for {pp_id} ok", actor="system")
        except Exception as e:
            log("GLOBAL", "DISPUTE_ENRICH_WARN", f"Live fetch failed for {pp_id}: {e}", actor="system")
    reason = normalize_reason(res.get("reason", ""))
    amount = _to_amount(res.get("disputed_amount") or res.get("amount"))
    raw_amt = res.get("disputed_amount") or res.get("amount")
    currency = "USD"
    if isinstance(raw_amt, dict) and raw_amt.get("currency_code"):
        currency = raw_amt["currency_code"]
    # PayPal response deadline: use payload value when present, else ~7 days from receipt
    respond_by = res.get("response_deadline") or res.get("respond_by") or (
        datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

    existing = find_by_paypal_id(pp_id)
    if existing and existing.status in ("AUTO_RESOLVED", "SUBMITTED", "REJECTED"):
        # Retried/late webhook for an already-closed case — do not re-act or duplicate
        log(existing.case_id, "WEBHOOK_DUP_IGNORED",
            f"{etype} for {pp_id} ignored: case already {existing.status}", actor="system")
        return {"case_id": existing.case_id, "status": existing.status,
                "decision": existing.policy_decision, "duplicate": True}

    order = OrderInfo(
        order_id=res.get("invoice_number") or res.get("order_id") or new_id("ORD"),
        amount=amount or 0.0,
        buyer_email=res.get("buyer_email", "buyer@example.com"),
        tracking_number=res.get("tracking_number", ""),
        tracking_status=res.get("tracking_status", ""),
        purchase_date=res.get("create_time", now_iso()),
    )
    if existing:
        case = existing
        case.reason = reason
        case.amount = amount or case.amount
        case.amount_known = amount is not None
        case.currency = currency
        case.respond_by = respond_by
        case.order = order
        # UPDATED events may carry a new buyer message — append and re-scan (injection can arrive late)
        new_msg = res.get("buyer_message")
        if new_msg and not any(m.get("text") == new_msg for m in case.customer_messages):
            case.customer_messages.append({"from": "buyer", "text": new_msg})
        log(case.case_id, "DISPUTE_UPDATED", f"Webhook {etype} dispute {pp_id}", actor="system")
    else:
        case = DisputeCase(paypal_dispute_id=pp_id, reason=reason, amount=amount or 0.0,
            amount_known=amount is not None, currency=currency, respond_by=respond_by,
            already_refunded=bool(res.get("already_refunded")), order=order,
            customer_messages=[{"from": "buyer", "text": res.get("buyer_message", "Item never arrived")}])
        upsert_case(case)
        log(case.case_id, "DISPUTE_RECEIVED",
            f"Webhook {etype} dispute {pp_id} ${amount if amount is not None else 'unknown'}", actor="system")
    case = await _pipeline(case)
    return {"case_id": case.case_id, "status": case.status, "decision": case.policy_decision}

# --- Simulator (Day-1 fallback: sandbox disputes are hard to trigger) ---
@app.get("/api/scenarios")
def scenarios_list():
    """Edge-case scenarios the dashboard can fire in one click."""
    return [{"id": k, "story": v.get("story", "")} for k, v in SCENARIOS.items()]

@app.post("/api/simulate/dispute")
async def simulate(s: SimulateIn):
    # Scenario overrides let one endpoint reproduce every edge case
    if s.scenario:
        s = SimulateIn(**{**s.model_dump(), **get_scenario(s.scenario)})
    pp_id = new_id("PP-DSP")
    reason = normalize_reason(s.reason)
    delivered = "delivered" in s.tracking_status.lower()
    order_total = s.order_total if s.order_total > 0 else s.amount
    order = OrderInfo(order_id=new_id("ORD"), amount=order_total, buyer_email=s.buyer_email,
        items=[{"name": "Headphones", "qty": 1}],
        tracking_number="1Z999AA10123456784" if delivered else "",
        tracking_status=s.tracking_status, purchase_date=now_iso(),
        shipping_address=s.shipping_address, delivered_address=s.delivered_address)
    msgs = s.messages or [
        {"from": "buyer", "text": "My headphones never arrived, I want a refund"},
        {"from": "merchant", "text": "Tracking shows delivered Oct 10, signed. Can you check with household?"}]
    case = DisputeCase(paypal_dispute_id=pp_id, reason=reason, amount=s.amount,
        currency=s.currency, order=order, customer_messages=msgs,
        already_refunded=s.already_refunded,
        respond_by=respond_by_from_hours(s.respond_hours))
    upsert_case(case)
    log(case.case_id, "DISPUTE_RECEIVED",
        f"Simulated {reason} {s.currency} ${s.amount:.2f}"
        + (f" [{s.scenario}]" if s.scenario else "") + " (webhook equivalent)", actor="system")
    case = await _pipeline(case)
    return case.model_dump()


@app.post("/api/disputes/sync")
async def sync_disputes():
    """Poll live disputes from PayPal and ingest any new ones into the pipeline."""
    if paypal_client.MOCK:
        return {"synced": 0, "message": "PayPal is in mock mode"}
    try:
        items = paypal_client.list_disputes()
    except Exception as e:
        raise HTTPException(502, f"Failed to list disputes from PayPal: {e}")
    ingested = []
    for item in items:
        pp_id = item.get("dispute_id") or item.get("id")
        if not pp_id:
            continue
        if find_by_paypal_id(pp_id):
            continue
        try:
            live = paypal_client.get_dispute(pp_id)
        except Exception:
            live = item
        res = {**item, **live} if isinstance(live, dict) else item
        reason = normalize_reason(res.get("reason", ""))
        amount = _to_amount(res.get("dispute_amount") or res.get("disputed_amount") or res.get("amount"))
        raw_amt = res.get("dispute_amount") or res.get("disputed_amount") or res.get("amount")
        currency = "USD"
        if isinstance(raw_amt, dict) and raw_amt.get("currency_code"):
            currency = raw_amt["currency_code"]
        respond_by = res.get("seller_response_due_date") or res.get("response_deadline") or res.get("respond_by") or (
            datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

        messages = res.get("messages") or []
        buyer_msgs = [m.get("content") for m in messages if isinstance(m, dict) and m.get("posted_by") == "BUYER" and m.get("content")]
        first_msg = buyer_msgs[0] if buyer_msgs else res.get("buyer_message", "Item never arrived")

        order = OrderInfo(
            order_id=res.get("invoice_number") or res.get("order_id") or new_id("ORD"),
            amount=amount or 0.0,
            buyer_email=res.get("buyer_email", "buyer@example.com"),
            tracking_number=res.get("tracking_number", ""),
            tracking_status=res.get("tracking_status", ""),
            purchase_date=res.get("create_time", now_iso()),
        )
        case = DisputeCase(
            paypal_dispute_id=pp_id,
            reason=reason,
            amount=amount or 0.0,
            amount_known=amount is not None,
            currency=currency,
            respond_by=respond_by,
            already_refunded=bool(res.get("already_refunded")),
            order=order,
            customer_messages=[{"from": "buyer", "text": first_msg}]
        )
        upsert_case(case)
        log(case.case_id, "DISPUTE_RECEIVED",
            f"Synced dispute {pp_id} ", actor="system")
        case = await _pipeline(case)
        ingested.append(case.case_id)
    return {"synced": len(ingested), "cases": ingested}

@app.get("/api/cases")
def cases():
    return [c.model_dump() for c in list_cases()]

@app.get("/api/cases/{case_id}")
def one_case(case_id: str):
    c = get_case(case_id)
    if not c:
        raise HTTPException(404, "case not found")
    return c.model_dump()

@app.post("/api/cases/{case_id}/approve", dependencies=PROTECTED)
def approve(case_id: str):
    c = get_case(case_id)
    if not c:
        raise HTTPException(404, "case not found")
    if c.status != "WAITING_APPROVAL":
        raise HTTPException(409, f"case is {c.status}; only WAITING_APPROVAL cases can be approved")
    try:
        paypal_client.provide_evidence(c.paypal_dispute_id or c.case_id,
            {"response": c.drafted_response, "summary": c.evidence_summary})
    except Exception as e:
        log(case_id, "PAYPAL_API_ERROR", f"approve submit failed: {e}", actor="merchant")
        raise HTTPException(502, f"PayPal API error: {e}")
    c.status = "SUBMITTED"
    upsert_case(c)
    log(case_id, "MERCHANT_APPROVED", "Merchant approved drafted evidence, submitted to PayPal", actor="merchant")
    return c.model_dump()

@app.post("/api/cases/{case_id}/reject", dependencies=PROTECTED)
def reject(case_id: str):
    c = get_case(case_id)
    if not c:
        raise HTTPException(404, "case not found")
    if c.status != "WAITING_APPROVAL":
        raise HTTPException(409, f"case is {c.status}; only WAITING_APPROVAL cases can be rejected")
    c.status = "REJECTED"
    upsert_case(c)
    log(case_id, "MERCHANT_REJECTED", "Merchant rejected draft — no submission", actor="merchant")
    return c.model_dump()

@app.put("/api/cases/{case_id}/draft", dependencies=PROTECTED)
def edit_draft(case_id: str, body: DraftIn):
    """Merchant edits the AI draft before approving — human-in-the-loop, fully logged."""
    c = get_case(case_id)
    if not c:
        raise HTTPException(404, "case not found")
    if c.status != "WAITING_APPROVAL":
        raise HTTPException(409, f"case is {c.status}; only WAITING_APPROVAL cases can be edited")
    text = body.draft_response.strip()
    if not text:
        raise HTTPException(422, "draft_response cannot be empty")
    c.drafted_response = text
    upsert_case(c)
    log(case_id, "MERCHANT_EDITED_DRAFT",
        f"Merchant edited draft ({len(text)} chars) before approval", actor="merchant")
    return c.model_dump()

@app.post("/api/cases/{case_id}/counteroffer", dependencies=PROTECTED)
def counteroffer(case_id: str, body: CounterofferIn):
    """Merchant offers a partial/full refund via PayPal make-offer, instead of contesting."""
    c = get_case(case_id)
    if not c:
        raise HTTPException(404, "case not found")
    if c.status != "WAITING_APPROVAL":
        raise HTTPException(409, f"case is {c.status}; only WAITING_APPROVAL cases can get an offer")
    if body.amount <= 0 or body.amount > c.amount:
        raise HTTPException(422, f"offer amount must be between $0.01 and the disputed ${c.amount:.2f}")
    try:
        paypal_client.make_offer(c.paypal_dispute_id or c.case_id, "REFUND", body.amount)
    except Exception as e:
        log(case_id, "PAYPAL_API_ERROR", f"counteroffer failed: {e}", actor="merchant")
        raise HTTPException(502, f"PayPal API error: {e}")
    c.status = "SUBMITTED"
    upsert_case(c)
    log(case_id, "MERCHANT_COUNTEROFFER",
        f"Merchant offered ${body.amount:.2f} refund to resolve dispute", actor="merchant")
    return c.model_dump()

@app.get("/api/audit")
def audit():
    return [a.model_dump() for a in reversed(AUDIT)]

