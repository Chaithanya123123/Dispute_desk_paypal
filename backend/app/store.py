from typing import Dict, List
from .models import DisputeCase, AuditEntry, MerchantPolicy

# In-memory store (swap for DB later). Good enough for hackathon demo + Render.
CASES: Dict[str, DisputeCase] = {}
AUDIT: List[AuditEntry] = []
POLICY = MerchantPolicy()

def log(case_id: str, action: str, reason: str, actor: str = "agent"):
    entry = AuditEntry(case_id=case_id, action=action, reason=reason, actor=actor)
    AUDIT.append(entry)
    return entry

def upsert_case(case: DisputeCase):
    from .models import now_iso
    case.updated_at = now_iso()
    CASES[case.case_id] = case
    return case

def list_cases():
    """Newest first, but PASSED/CRITICAL deadlines always float to the top."""
    from .models import URGENCY_RANK
    newest_first = sorted(CASES.values(), key=lambda c: c.created_at, reverse=True)
    return sorted(newest_first, key=lambda c: URGENCY_RANK.get(c.urgency, 9))

def get_case(case_id: str):
    return CASES.get(case_id)

def find_by_paypal_id(paypal_dispute_id: str):
    return next((c for c in CASES.values() if c.paypal_dispute_id == paypal_dispute_id), None)
