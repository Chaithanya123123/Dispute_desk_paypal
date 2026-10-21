from .models import DisputeCase
from .store import POLICY, log
from . import paypal_client

def _auto_limit(case: DisputeCase) -> float:
    """Auto-resolve dollar limit for this dispute type (NAD is riskier)."""
    if case.reason == "NOT_AS_DESCRIBED":
        return POLICY.auto_resolve_not_as_described_under
    return POLICY.auto_resolve_under

def _safe(fn, *args) -> tuple[bool, str]:
    """PayPal API errors must never crash the pipeline; they hold the case instead."""
    try:
        fn(*args)
        return True, ""
    except Exception as e:
        return False, str(e)

def _hold(case: DisputeCase, reason: str, action: str = "HELD_FOR_APPROVAL") -> DisputeCase:
    case.policy_decision = "NEEDS_APPROVAL"
    case.policy_reason = reason
    case.status = "WAITING_APPROVAL"
    log(case.case_id, action, reason)
    return case

def apply_policy(case: DisputeCase) -> DisputeCase:
    """Agentic Commerce rule: the LLM proposes, this code decides.

    Order of checks (first match wins):
      1. Validation gate — malformed agent output never acts, goes to a human.
      2. SLA gate — past the response deadline the agent never acts alone.
      3. Risk veto — any deterministic risk flag (injection, non-USD, repeat
         buyer, address mismatch, already refunded, partial/duplicate dispute)
         vetoes ALL auto-action regardless of confidence.
      4. Confidence gate — below POLICY.min_confidence the agent must stop and ask.
      5. Amount gate — unparseable amounts are never trusted for auto-action.
      6. Money rules — deterministic, keyed on the model's recommendation + merchant limits.
    Every step is logged with its reason.
    """
    amt = case.amount

    # 1. Validation gate
    if case.agent_error:
        return _hold(case,
            f"Agent output failed validation ({case.agent_error}) — human review required",
            "AGENT_OUTPUT_INVALID")

    # 2. SLA gate — the deadline is real; never act on a closed clock
    if case.urgency == "PASSED":
        return _hold(case,
            "Response deadline passed (SLA_BREACHED) — escalate to a human immediately",
            "SLA_BREACHED")

    # 3. Risk veto — edge cases the agent is never allowed to handle alone
    if case.risk_flags:
        return _hold(case,
            f"Risk flags: {', '.join(case.risk_flags)} — human review required",
            "RISK_VETO")

    # 4. Confidence gate
    if case.confidence < POLICY.min_confidence:
        return _hold(case,
            f"Confidence {case.confidence:.0%} < min {POLICY.min_confidence:.0%} — agent stops and asks a human",
            "LOW_CONFIDENCE_HOLD")

    # 5. Amount gate
    if not case.amount_known:
        return _hold(case,
            "Dispute amount could not be parsed from the PayPal payload — human review required",
            "UNKNOWN_AMOUNT_HOLD")

    # 6. Money rules (deterministic, testable, explainable — no LLM involved)
    if (case.reason == "UNAUTHORIZED"
            and case.recommended_action in ("refund", "partial_refund")
            and amt <= POLICY.auto_refund_unauthorized_under):
        ok, err = _safe(paypal_client.make_offer, case.paypal_dispute_id or case.case_id, "REFUND", amt)
        if not ok:
            return _hold(case, f"Auto-refund failed (PayPal API error: {err}) — human review required",
                         "PAYPAL_API_ERROR")
        case.policy_decision = "AUTO_REFUND"
        case.policy_reason = (f"Unauthorized ${amt:.2f} <= auto-refund "
                              f"${POLICY.auto_refund_unauthorized_under:.2f}, agent recommended refund")
        case.status = "AUTO_RESOLVED"
        log(case.case_id, "AUTO_REFUND", case.policy_reason)
    elif case.recommended_action == "contest" and _auto_limit(case) is not None and amt <= _auto_limit(case):
        limit = _auto_limit(case)
        ok, err = _safe(paypal_client.provide_evidence,
                        case.paypal_dispute_id or case.case_id,
                        {"response": case.drafted_response, "summary": case.evidence_summary})
        if not ok:
            return _hold(case, f"Auto-submit failed (PayPal API error: {err}) — human review required",
                         "PAYPAL_API_ERROR")
        case.policy_decision = "AUTO_SUBMIT"
        limit_name = ("auto-resolve (not-as-described)" if case.reason == "NOT_AS_DESCRIBED"
                      else "auto-resolve")
        case.policy_reason = (f"Agent recommended contest, ${amt:.2f} <= {limit_name} "
                              f"${limit:.2f}, confidence {case.confidence:.0%}")
        case.status = "AUTO_RESOLVED"
        log(case.case_id, "AUTO_SUBMIT_EVIDENCE", case.policy_reason)
    else:
        # Explain precisely why a human is needed
        if case.recommended_action == "escalate":
            reason = f"Agent recommended escalate — human review required"
        elif case.recommended_action in ("refund", "partial_refund"):
            reason = (f"Refund recommendation (${amt:.2f}, reason {case.reason}) not within "
                      f"auto-refund policy — human review required")
        elif amt > _auto_limit(case):
            limit_name = ("auto-resolve (not-as-described)" if case.reason == "NOT_AS_DESCRIBED"
                          else "auto-resolve")
            reason = f"${amt:.2f} > {limit_name} ${_auto_limit(case):.2f} — human review required"
        else:
            reason = (f"Recommendation '{case.recommended_action}' is not auto-handled — "
                      f"human review required")
        return _hold(case, reason)
    return case
