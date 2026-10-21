"""Validator: schema-check the LLM's recommendation before anything acts on it.

Design rule: if the model output is malformed, out of bounds, or missing fields,
the case goes to a human. The model never gets the benefit of the doubt.
"""
import json
from typing import Optional, Tuple, Union
from pydantic import ValidationError
from .models import AgentRecommendation

ALLOWED_DISPUTE_TYPES = {"ITEM_NOT_RECEIVED", "NOT_AS_DESCRIBED", "UNAUTHORIZED", "OTHER"}
ALLOWED_STRENGTHS = {"strong", "medium", "weak"}
ALLOWED_ACTIONS = {"contest", "refund", "partial_refund", "escalate"}


def extract_json(text: str) -> str:
    """LLMs sometimes wrap JSON in ``` fences or add prose. Isolate the object."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        if t.startswith("json"):
            t = t[4:]
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return text
    return t[start:end + 1]


def validate_agent_output(raw: Union[str, dict]) -> Tuple[Optional[AgentRecommendation], str]:
    """Returns (recommendation, error). error != '' means: route to a human."""
    if isinstance(raw, str):
        try:
            raw = json.loads(extract_json(raw))
        except (json.JSONDecodeError, TypeError) as e:
            return None, f"output is not valid JSON: {e}"
    if not isinstance(raw, dict):
        return None, f"expected JSON object, got {type(raw).__name__}"

    try:
        rec = AgentRecommendation.model_validate(raw)
    except ValidationError as e:
        first = e.errors()[0]
        return None, f"schema mismatch at '{'.'.join(map(str, first['loc']))}': {first['msg']}"

    rec.dispute_type = rec.dispute_type.strip().upper()
    rec.evidence_strength = rec.evidence_strength.strip().lower()
    rec.recommended_action = rec.recommended_action.strip().lower()

    if rec.dispute_type not in ALLOWED_DISPUTE_TYPES:
        return None, f"unknown dispute_type '{rec.dispute_type}'"
    if rec.evidence_strength not in ALLOWED_STRENGTHS:
        return None, f"unknown evidence_strength '{rec.evidence_strength}'"
    if rec.recommended_action not in ALLOWED_ACTIONS:
        return None, f"unknown recommended_action '{rec.recommended_action}'"

    rec.confidence = min(max(float(rec.confidence), 0.0), 1.0)
    rec.evidence_summary = rec.evidence_summary[:2000]
    rec.draft_response = rec.draft_response[:3000]
    rec.missing_evidence = [str(m)[:200] for m in rec.missing_evidence[:10]]
    return rec, ""
