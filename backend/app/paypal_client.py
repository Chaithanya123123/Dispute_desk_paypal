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

def get_dispute(paypal_dispute_id: str) -> dict:
    """GET /v1/customer/disputes/{id} — falls back to mock in hackathon."""
    if MOCK:
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
    """POST /v1/customer/disputes/{id}/provide-evidence"""
    if MOCK:
        return {"mock": True, "dispute_id": paypal_dispute_id, "submitted": evidence}
    token = get_access_token()
    r = requests.post(
        f"{PAYPAL_BASE}/v1/customer/disputes/{paypal_dispute_id}/provide-evidence",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=evidence,
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

def make_offer(paypal_dispute_id: str, offer_type: str, amount: float | None = None) -> dict:
    """POST /v1/customer/disputes/{id}/make-offer — refund / partial offer."""
    if MOCK:
        return {"mock": True, "dispute_id": paypal_dispute_id, "offer_type": offer_type, "amount": amount}
    token = get_access_token()
    body: dict = {"offer_type": offer_type}
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
