"""New Patient Agent.

Increment 1 placeholder: answer from the top knowledge-base hit, or decline. The model-driven
stages (open, understand, answer or collect, confirm, close) replace this in increment 2.
"""

from typing import Any

from app.agent.scripts import script
from app.kb import search


def respond(text: str) -> tuple[str, str, list[dict[str, Any]]]:
    """Return (reply, stage, events) for a caller message the guardrail let through."""
    hits = search(text)
    cited = [hits[0].id] if hits else []
    reply = hits[0].text if hits else script("decline_unknown")
    event = {
        "type": "tool_call",
        "payload": {
            "tool": "search_kb",
            "query": text,
            "results": [{"id": h.id, "score": h.score} for h in hits],
            "cited": cited,
        },
    }
    return reply, "answer", [event]
