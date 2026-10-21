"""Deterministic risk engine. Runs AFTER the agent, BEFORE policy.

These are the edge cases where no amount of model confidence should let the
agent act alone. Every check is plain code: testable, explainable, no LLM.
Any flag found -> the policy engine vetoes all auto-action and asks a human.
"""
import re
from .models import DisputeCase
from .store import CASES

# Mock buyer history — in production this comes from the seller's store / PayPal account.
# (email -> number of past disputes filed)
RISKY_BUYERS = {
    "risky-buyer@example.com": 4,
    "serial-claimant@example.com": 6,
}

# Buyer text that looks like an instruction aimed at the AI rather than a message.
# Conservative on purpose: false positives only cost a human review.
INJECTION_PATTERNS = (
    r"ignore\s+(all\s+)?(previous|prior|above|earlier)",
    r"system\s+prompt",
    r"developer\s+mode",
    r"you\s+must\s+(refund|obey|comply|do)",
    r"disregard\s+(your|all|the)",
    r"override\s+(your|all|the|these)",
    r"as\s+an\s+ai",
    r"never\s+ask\s+(the\s+)?(merchant|human|seller)",
)

def _norm_addr(a: str) -> str:
    return re.sub(r"[\s,]+", " ", (a or "").strip().lower())

def scan_risk(case: DisputeCase) -> list[str]:
    """Returns risk flags for this case. Empty list = no red flags."""
    flags: list[str] = []

    # 1. Prompt injection: buyer text containing instructions aimed at the AI
    text = " ".join(m.get("text", "") for m in case.customer_messages).lower()
    if any(re.search(p, text) for p in INJECTION_PATTERNS):
        flags.append("PROMPT_INJECTION_SUSPECTED")

    # 2. Policy runs in USD; foreign currency needs a human to check FX/limits
    if case.currency and case.currency.upper() != "USD":
        flags.append("CURRENCY_NOT_USD")

    # 3. Already refunded via the store -> contesting/ refunding again = double refund
    if case.already_refunded:
        flags.append("ALREADY_REFUNDED")

    # 4. Carrier delivered somewhere other than the confirmed shipping address
    #    (Seller Protection requires ship-to-confirmed-address)
    ship, delivered = _norm_addr(case.order.shipping_address), _norm_addr(case.order.delivered_address)
    if ship and delivered and ship != delivered:
        flags.append("ADDRESS_MISMATCH")

    # 5. Repeat disputant — buyer with a history of filing claims
    if RISKY_BUYERS.get(case.order.buyer_email.lower(), 0) >= 2:
        flags.append("BUYER_REPEAT_DISPUTANT")

    # 6. Partial dispute — dispute covers only part of the original order
    if case.order.amount and case.order.amount - case.amount > 0.01:
        flags.append("PARTIAL_DISPUTE_ORDER")

    # 7. Duplicate open dispute on the same order
    same_order = sum(1 for c in CASES.values()
                     if c.order.order_id == case.order.order_id and c.case_id != case.case_id)
    if same_order >= 1:
        flags.append("DUPLICATE_DISPUTE_SAME_ORDER")

    return flags
