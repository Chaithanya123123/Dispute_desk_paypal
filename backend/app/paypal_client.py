"""PayPal sandbox client. Works in MOCK mode without credentials so demo never breaks."""
import os
import requests

PAYPAL_BASE = os.getenv("PAYPAL_BASE", "https://api-m.sandbox.paypal.com")
CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")
MOCK = not (CLIENT_ID and CLIENT_SECRET) or os.getenv("PAYPAL_MOCK", "true").lower() == "true"

_token_cache = {"token": None}

def get_access_token() -> str:
    if MOCK:
        return "MOCK-TOKEN"
    if _token_cache["token"]:
        return _token_cache["token"]
    r = requests.post(
        f"{PAYPAL_BASE}/v1/oauth2/token",
        auth=(CLIENT_ID, CLIENT_SECRET),
        data={"grant_type": "client_credentials"},
        timeout=15,
    )
    r.raise_for_status()
    _token_cache["token"] = r.json()["access_token"]
    return _token_cache["token"]

def _is_live_dispute_id(dispute_id) -> bool:
    """Only real PayPal dispute IDs (PP-R-*, PP-D-*) may hit the live API.
    Simulated/demo IDs (PP-DSP-*, DSP-*, PP-TEST) are synthetic — calling PayPal
    with them returns 400 and would break the demo in live mode."""
    return isinstance(dispute_id, str) and dispute_id.startswith(("PP-R-", "PP-D-"))

def get_dispute(paypal_dispute_id: str) -> dict:
    """GET /v1/customer/disputes/{id} — mock for hackathon / simulated IDs."""
    if MOCK or not _is_live_dispute_id(paypal_dispute_id):
        return {"id": paypal_dispute_id, "mock": True, "status": "OPEN"}
    token = get_access_token()
    r = requests.get(
        f"{PAYPAL_BASE}/v1/customer/disputes/{paypal_dispute_id}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()

def provide_evidence(paypal_dispute_id: str, evidence: dict) -> dict:
    """POST /v1/customer/disputes/{id}/provide-evidence with PayPal's evidences[] schema.
    `evidence` is a simple {response, summary} dict from our app; PayPal expects
    [{evidence_type, notes, evidence_info}] — translation happens here.
    See https://developer.paypal.com/api/disputes#provide-evidence"""
    if MOCK or not _is_live_dispute_id(paypal_dispute_id):
        return {"mock": True, "dispute_id": paypal_dispute_id, "submitted": evidence}
    etype = "PROOF_OF_FULFILLMENT"
    try:
        live = get_dispute(paypal_dispute_id)
        requested = [e for e in (live.get("evidences") or [])
                     if isinstance(e, dict) and e.get("source") == "REQUESTED_FROM_SELLER"
                     and e.get("evidence_type")]
        if requested:
            etype = requested[0]["evidence_type"]
    except Exception:
        pass  # best-effort: fall back to PROOF_OF_FULFILLMENT
    notes = " ".join(x for x in (evidence.get("response"), evidence.get("summary")) if x)
    item = {"evidence_type": etype, "notes": (notes or "Evidence attached.")[:1000]}
    tn = evidence.get("tracking_number") or ""
    if tn and etype == "PROOF_OF_FULFILLMENT":
        item["evidence_info"] = {"tracking_info": [{"carrier_name": "USPS", "tracking_number": tn}]}
    token = get_access_token()
    r = requests.post(
        f"{PAYPAL_BASE}/v1/customer/disputes/{paypal_dispute_id}/provide-evidence",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"evidences": [item]},
        timeout=15,
    )
    r.raise_for_status()
    return r.json() if r.text else {"ok": True}

def verify_webhook_signature(headers: dict, event: dict) -> bool:
    """Verify PayPal webhook signature. Returns True in MOCK / hackathon mode.
    Live: POST /v1/notifications/verify-webhook-signature when PAYPAL_WEBHOOK_ID is set."""
    webhook_id = os.getenv("PAYPAL_WEBHOOK_ID", "")
    if MOCK or not webhook_id:
        return True
    required = ("paypal-transmission-id", "paypal-transmission-time",
                "paypal-cert-url", "paypal-auth-algo", "paypal-transmission-sig")
    if not all(headers.get(h) for h in required):
        return False
    try:
        token = get_access_token()
        body = {
            "transmission_id": headers.get("paypal-transmission-id", ""),
            "transmission_time": headers.get("paypal-transmission-time", ""),
            "cert_url": headers.get("paypal-cert-url", ""),
            "auth_algo": headers.get("paypal-auth-algo", ""),
            "transmission_sig": headers.get("paypal-transmission-sig", ""),
            "webhook_id": webhook_id,
            "webhook_event": event,
        }
        r = requests.post(
            f"{PAYPAL_BASE}/v1/notifications/verify-webhook-signature",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get("verification_status") == "SUCCESS"
    except Exception:
        return False

def make_offer(paypal_dispute_id: str, offer_type: str, amount: float | None = None) -> dict:
    """POST /v1/customer/disputes/{id}/make-offer — refund / partial offer.
    Offer type must be in the dispute's allowed_response_options.make_offer.offer_types."""
    if MOCK or not _is_live_dispute_id(paypal_dispute_id):
        return {"mock": True, "dispute_id": paypal_dispute_id, "offer_type": offer_type, "amount": amount}
    try:
        live = get_dispute(paypal_dispute_id)
        allowed = (((live.get("allowed_response_options") or {}).get("make_offer") or {})
                   .get("offer_types") or [])
        if allowed and offer_type not in allowed:
            offer_type = allowed[0]
    except Exception:
        pass  # best-effort; PayPal will validate the offer_type itself
    token = get_access_token()
    body: dict = {"offer_type": offer_type, "note": "Settlement offer from DisputeDesk"}
    if amount is not None:
        body["offer_amount"] = {"value": f"{amount:.2f}", "currency_code": "USD"}
    r = requests.post(
        f"{PAYPAL_BASE}/v1/customer/disputes/{paypal_dispute_id}/make-offer",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=15,
    )
    r.raise_for_status()
    return r.json() if r.text else {"ok": True}


def list_disputes() -> list:
    """GET /v1/customer/disputes"""
    if MOCK:
        return []
    token = get_access_token()
    r = requests.get(
        f"{PAYPAL_BASE}/v1/customer/disputes",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json().get("items", [])
