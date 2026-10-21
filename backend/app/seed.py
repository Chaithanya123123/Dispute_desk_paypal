"""Seed 2 demo cases: $29 auto + $349 approval. Run: python -m app.seed (backend dir)."""
import asyncio, httpx

async def main():
    async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=20) as c:
        for amount in (29.99, 349.0):
            r = await c.post("/api/simulate/dispute", json={
                "reason": "ITEM_NOT_RECEIVED", "amount": amount,
                "tracking_status": "Delivered Oct 10, signed", "buyer_email": "buyer@example.com"})
            print(amount, r.json().get("status"), r.json().get("policy_decision"))

if __name__ == "__main__":
    asyncio.run(main())
