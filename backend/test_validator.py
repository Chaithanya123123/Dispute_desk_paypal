"""Checks for validator + template agent + policy gates. Run: python test_validator.py"""
import os
# Unit tests must never call live PayPal: force MOCK before app modules load
# (load_dotenv uses override=False, so a pre-set env var wins over .env)
os.environ["PAYPAL_MOCK"] = "true"
import asyncio
from fastapi import HTTPException
from app.validator import validate_agent_output
from app.evidence_agent import run_evidence_agent
from app.policy_engine import apply_policy
from app.models import DisputeCase, OrderInfo, normalize_reason
from app.store import POLICY

# --- validator ---
VALID = """```json
{
  "dispute_type": "item_not_received",
  "evidence_strength": "STRONG",
  "recommended_action": "Contest",
  "confidence": 1.7,
  "evidence_summary": "- Tracking shows delivered",
  "draft_response": "Hello PayPal team...",
  "missing_evidence": []
}
```"""
rec, err = validate_agent_output(VALID)
assert err == "", err
assert rec.dispute_type == "ITEM_NOT_RECEIVED"
assert rec.evidence_strength == "strong"
assert rec.recommended_action == "contest"
assert rec.confidence == 1.0  # clamped from 1.7
print("1. fenced JSON + normalization + clamp: OK")

_, err = validate_agent_output("sorry, I cannot help with that")
assert "not valid JSON" in err
print("2. non-JSON rejected:", err)

_, err = validate_agent_output('{"dispute_type":"OTHER","evidence_strength":"strong","recommended_action":"refund_now","confidence":0.9,"evidence_summary":"x","draft_response":"y"}')
assert "recommended_action" in err
print("3. bad enum rejected:", err)

_, err = validate_agent_output('{"dispute_type":"OTHER","confidence":0.9}')
assert "schema mismatch" in err
print("4. missing fields rejected:", err)

# --- reason normalization (PayPal codes) ---
assert normalize_reason("ITEMS_NOT_RECEIVED") == "ITEM_NOT_RECEIVED"
assert normalize_reason("ITEMS_NOT_AS_DESCRIBED") == "NOT_AS_DESCRIBED"
assert normalize_reason("UNAUTHORIZD") == "UNAUTHORIZED"   # sic — PayPal's actual code
assert normalize_reason("unauthorized") == "UNAUTHORIZED"
assert normalize_reason("WEIRD_CODE_XYZ") == "OTHER"
assert normalize_reason("") == "ITEM_NOT_RECEIVED"
print("5. normalize_reason: OK")

# --- policy gates with template fallback (no API keys needed) ---
from datetime import datetime, timedelta, timezone
from app.risk_engine import scan_risk
from app.scenarios import get_scenario
from app.models import compute_urgency

def make_case(amount, tracking, reason="ITEM_NOT_RECEIVED", amount_known=True, **kw):
    delivered = "delivered" in tracking.lower()
    order_total = kw.pop("order_total", amount)
    return DisputeCase(
        paypal_dispute_id="PP-TEST", reason=reason, amount=amount, amount_known=amount_known,
        currency=kw.pop("currency", "USD"), already_refunded=kw.pop("already_refunded", False),
        respond_by=kw.pop("respond_by", (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()),
        order=OrderInfo(order_id=kw.pop("order_id", "ORD-1"), amount=order_total,
                        tracking_number="1Z999" if delivered else "",
                        tracking_status=tracking, purchase_date="2026-10-10",
                        shipping_address=kw.pop("shipping_address", ""),
                        delivered_address=kw.pop("delivered_address", "")),
        customer_messages=kw.pop("messages", [{"from": "buyer", "text": "never arrived"}]))

def run(amount, tracking, **kw):
    c = make_case(amount, tracking, **kw)
    c = asyncio.run(run_evidence_agent(c))
    c.urgency = compute_urgency(c.respond_by)
    c.risk_flags = scan_risk(c)
    return apply_policy(c)

c = run(29.99, "Delivered Oct 10, signed")
assert c.status == "AUTO_RESOLVED" and c.policy_decision == "AUTO_SUBMIT", (c.status, c.policy_decision)
print(f"6. $29 delivered -> {c.policy_decision} ({c.policy_reason})")

c = run(349.0, "Delivered Oct 10, signed")
assert c.status == "WAITING_APPROVAL" and "349" in c.policy_reason, (c.status, c.policy_reason)
print(f"7. $349 delivered -> {c.policy_decision} ({c.policy_reason})")

c = run(19.99, "No tracking available")
assert c.status == "WAITING_APPROVAL" and c.confidence < POLICY.min_confidence
print(f"8. $19 no-tracking -> {c.policy_decision} (confidence {c.confidence:.0%} < {POLICY.min_confidence:.0%})")

c = run(25.0, "", reason="UNAUTHORIZED")
assert c.status == "AUTO_RESOLVED" and c.policy_decision == "AUTO_REFUND", (c.status, c.policy_decision)
print(f"9. $25 unauthorized -> {c.policy_decision} ({c.policy_reason})")

c = run(40.0, "", reason="UNAUTHORIZED")
assert c.status == "WAITING_APPROVAL" and c.policy_decision == "NEEDS_APPROVAL", (c.status, c.policy_decision)
print(f"10. $40 unauthorized -> {c.policy_decision} ({c.policy_reason})")

c = run(29.99, "Delivered", reason="SOMETHING_ELSE")
assert c.status == "WAITING_APPROVAL" and c.recommended_action == "escalate", (c.status, c.recommended_action)
print(f"11. unknown reason -> {c.policy_decision} ({c.policy_reason})")

# unknown amount must never auto-act even with strong evidence
c = run(29.99, "Delivered Oct 10, signed", amount_known=False)
assert c.status == "WAITING_APPROVAL" and "could not be parsed" in c.policy_reason
print(f"12. unknown amount -> {c.policy_decision} ({c.policy_reason})")

# --- edge cases: risk engine vetoes + SLA gate ---
inj = get_scenario("injection")
c = run(inj["amount"], inj["tracking_status"], reason=inj["reason"], messages=inj["messages"])
assert "PROMPT_INJECTION_SUSPECTED" in c.risk_flags and c.policy_decision != "AUTO_SUBMIT"
assert c.status == "WAITING_APPROVAL" and "PROMPT_INJECTION_SUSPECTED" in c.policy_reason
print(f"13. injection attempt -> {c.policy_decision} ({c.policy_reason})")

c = run(40.0, "Delivered Oct 10, signed", currency="EUR")
assert "CURRENCY_NOT_USD" in c.risk_flags and c.status == "WAITING_APPROVAL"
print(f"14. EUR claim -> {c.policy_decision} ({c.policy_reason})")

mismatch = get_scenario("address_mismatch")
c = run(mismatch["amount"], mismatch["tracking_status"],
        shipping_address=mismatch["shipping_address"], delivered_address=mismatch["delivered_address"])
assert "ADDRESS_MISMATCH" in c.risk_flags and c.status == "WAITING_APPROVAL"
print(f"15. address mismatch -> {c.policy_decision} ({c.policy_reason})")

c = run(15.0, "Delivered Oct 10, signed", already_refunded=True)
assert "ALREADY_REFUNDED" in c.risk_flags and c.status == "WAITING_APPROVAL"
print(f"16. already refunded -> {c.policy_decision} ({c.policy_reason})")

c = run(29.99, "Delivered Oct 10, signed", messages=[{"from": "buyer", "text": "never arrived"}],
        order_id="ORD-RISKY")
c.order.buyer_email = "risky-buyer@example.com"
c.risk_flags = scan_risk(c)
c = apply_policy(c)
assert "BUYER_REPEAT_DISPUTANT" in c.risk_flags and c.status == "WAITING_APPROVAL"
print(f"17. repeat disputant -> {c.policy_decision} ({c.policy_reason})")

c = run(30.0, "Delivered Oct 10, signed", order_total=180.0)
assert "PARTIAL_DISPUTE_ORDER" in c.risk_flags and c.status == "WAITING_APPROVAL"
print(f"18. partial dispute $30 of $180 -> {c.policy_decision} ({c.policy_reason})")

# SLA: passed deadline vetoes even a strong $29 case; 18h remaining still auto-acts (with CRITICAL badge)
passed_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
c = run(29.99, "Delivered Oct 10, signed", respond_by=passed_at)
assert c.urgency == "PASSED" and c.status == "WAITING_APPROVAL" and c.policy_decision != "AUTO_SUBMIT"
print(f"19. deadline passed -> {c.policy_decision} ({c.policy_reason})")
soon_at = (datetime.now(timezone.utc) + timedelta(hours=18)).isoformat()
c = run(29.99, "Delivered Oct 10, signed", respond_by=soon_at)
assert c.urgency == "CRITICAL" and c.status == "AUTO_RESOLVED"
print(f"20. deadline in 18h, strong case -> still {c.policy_decision} (urgency CRITICAL, acts fast)")

# per-reason limit: NAD auto-resolves <= $20 (not the $50 INR limit)
nad = make_case(15.0, "Delivered Oct 10, signed", reason="NOT_AS_DESCRIBED")
nad.status, nad.confidence, nad.recommended_action = "EVIDENCE_READY", 0.9, "contest"
nad.urgency, nad.risk_flags = "NORMAL", []
c = apply_policy(nad)
assert c.status == "AUTO_RESOLVED" and "not-as-described" in c.policy_reason
print(f"21. NAD $15 strong evidence -> {c.policy_decision} ({c.policy_reason})")
nad2 = make_case(30.0, "Delivered Oct 10, signed", reason="NOT_AS_DESCRIBED")
nad2.status, nad2.confidence, nad2.recommended_action = "EVIDENCE_READY", 0.9, "contest"
nad2.urgency, nad2.risk_flags = "NORMAL", []
c = apply_policy(nad2)
assert c.status == "WAITING_APPROVAL" and "not-as-described" in c.policy_reason
print(f"22. NAD $30 strong evidence -> {c.policy_decision} ({c.policy_reason})")

# --- endpoint state guards ---
from app.main import approve, counteroffer, edit_draft, CounterofferIn, DraftIn
from app.store import upsert_case
auto = run(29.99, "Delivered Oct 10, signed")
upsert_case(auto)
try:
    approve(auto.case_id)
    raise AssertionError("approve on AUTO_RESOLVED should 409")
except HTTPException as e:
    assert e.status_code == 409
print("23. approve on AUTO_RESOLVED blocked with 409: OK")

held = run(349.0, "Delivered Oct 10, signed")
upsert_case(held)
try:
    counteroffer(held.case_id, CounterofferIn(amount=999.0))
    raise AssertionError("oversized offer should 422")
except HTTPException as e:
    assert e.status_code == 422
print("24. oversized counteroffer rejected with 422: OK")
r = counteroffer(held.case_id, CounterofferIn(amount=100.0))
assert r["status"] == "SUBMITTED"
print("25. counteroffer $100 on $349 -> SUBMITTED: OK")
try:
    counteroffer(held.case_id, CounterofferIn(amount=50.0))
    raise AssertionError("offer after submit should 409")
except HTTPException as e:
    assert e.status_code == 409
print("26. counteroffer after submit blocked: OK")

held2 = run(349.0, "Delivered Oct 10, signed")
upsert_case(held2)
r = edit_draft(held2.case_id, DraftIn(draft_response="Merchant-edited text " * 5))
assert "Merchant-edited text" in r["drafted_response"] and r["status"] == "WAITING_APPROVAL"
print("27. merchant draft edit saved, still needs approval: OK")

# --- provider transparency ---
assert held2.agent_provider == "template" and held2.agent_raw_output == ""
print("28. template fallback labeled correctly (agent_provider='template', no raw output): OK")

# --- shared-token auth on action endpoints (DESK_TOKEN unset = open, set = 401 without header) ---
import os
from fastapi.testclient import TestClient
from app.main import app

os.environ["DESK_TOKEN"] = "test-secret-token"
try:
    client = TestClient(app)
    held3 = run(349.0, "Delivered Oct 10, signed")
    upsert_case(held3)
    r = client.post(f"/api/cases/{held3.case_id}/approve")
    assert r.status_code == 401, r.status_code
    r = client.post(f"/api/cases/{held3.case_id}/approve", headers={"X-Desk-Token": "wrong"})
    assert r.status_code == 401, r.status_code
    r = client.post(f"/api/cases/{held3.case_id}/approve", headers={"X-Desk-Token": "test-secret-token"})
    assert r.status_code == 200 and r.json()["status"] == "SUBMITTED", (r.status_code, r.text[:100])
    print("29. DESK_TOKEN: 401 without/wrong token, 200 with: OK")

    held4 = run(349.0, "Delivered Oct 10, signed")
    upsert_case(held4)
    r = client.put(f"/api/cases/{held4.case_id}/draft",
                   headers={"X-Desk-Token": "test-secret-token"},
                   json={"draft_response": "x" * 3001})
    assert r.status_code == 422, r.status_code
    print("30. draft over 3000 chars rejected with 422: OK")
finally:
    del os.environ["DESK_TOKEN"]

print("\nAll checks passed.")
