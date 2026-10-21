# Demo video script (<3 min, 4 scenarios + real dispute)

Setup before recording: paste the Render `DESK_TOKEN` into the dashboard's **Desk token** field
(localStorage), open the dashboard full-screen, queue empty (delete test cases first).

0:00-0:15 One line problem + empty queue: "Small sellers lose PayPal disputes because the deadline runs out before the evidence is gathered."

0:15-0:45 Scenario A — auto-resolve: Simulate $29 dispute → AUTO_RESOLVED. Audit shows DISPUTE_RECEIVED → EVIDENCE_DRAFTED (action=contest, confidence 92%) → AUTO_SUBMIT_EVIDENCE. Line: "The agent prepared and submitted the case in seconds — inside the merchant's rules."

0:45-1:05 Real dispute: Click **Sync from PayPal** → the sandbox dispute `PP-R-ZXQ-10193897` ($50, ITEM_NOT_RECEIVED) appears as a case. Open it: same pipeline, real buyer messages from PayPal's API. Line: "That's a real dispute from PayPal's sandbox — pulled in through the Disputes API."

1:05-1:40 Scenario B — human loop: Simulate $349 → WAITING_APPROVAL. Show evidence summary + checklist. Edit the draft, save, Approve & Submit → SUBMITTED. Audit shows MERCHANT_EDITED_DRAFT → MERCHANT_APPROVED. Line: "Above the limit, nothing moves without the seller — even the draft can be edited first."

1:40-2:15 Scenario C — risk veto: Edge scenarios dropdown → Prompt-injection buyer → RISK_VETO, flags [PROMPT_INJECTION_SUSPECTED], the injection text visible in the buyer messages. Bonus 10s: deadline-passed scenario → SLA_BREACHED on a $29 case that would otherwise auto-resolve. Line: "Every edge case is a deterministic code check — the model never gets a vote on these."

2:15-2:45 Scenario D — audit + impact: Full AG Grid audit log, urgency-sorted queue (PASSED/CRITICAL on top), live policy editor. Impact line: "DisputeDesk turns a scattered, deadline-driven scramble into a logged, policy-bound workflow: the AI prepares, the seller decides, and every step is explainable."

2:45-3:00 End card: repo link, hosted URL, run instructions. AI tools line: "Claude/OpenAI (schema-validated) prepares the response, deterministic risk + policy engines hold the money."

Rules: under 3 min, public YouTube, no copyrighted music, show the dashboard on the device it was built for.
