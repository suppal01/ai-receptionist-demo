"""The agent model's job and its interface.

The model interprets one caller message: what the caller wants (intents), any request
details they gave (fields), and a draft reply for questions, grounded in search_kb results.
It does not choose the stage, save requests, or hand off: the engine does that in code.

Implementations:
  KBOnlyModel  no LLM; answers with the top KB hit (used when AGENT_MODEL is unset)
  (Vertex AI Claude model: added next, selected by AGENT_MODEL)
"""

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from app.kb import Hit

Intent = Literal[
    "question",      # asks about the practice (hours, services, insurance, ...)
    "request",       # wants an appointment or a callback
    "provide_info",  # gives request details (name, number, times, ...)
    "correction",    # changes a detail given earlier
    "confirm",       # says the read-back is right
    "deny",          # says the read-back is wrong, without the fix
    "price",         # asks what something costs
    "clinical",      # asks for medical or dental advice
    "human",         # asks for a person
    "goodbye",       # ends the call
    "other",
]

FIELD_ORDER = (
    "name",
    "callback_number",
    "preferred_times",
    "insurance_carrier",
    "reason_for_visit",
)


class RequestFields(BaseModel):
    """Request details found in this message only. Omitted fields were not mentioned."""

    name: str | None = None
    callback_number: str | None = None
    preferred_times: str | None = None
    insurance_carrier: str | None = Field(
        default=None, description="Carrier and plan, or 'none' if the caller has no insurance."
    )
    reason_for_visit: str | None = None


class Interpretation(BaseModel):
    intents: list[Intent]
    fields: RequestFields = RequestFields()
    draft_reply: str = Field(
        default="",
        description="Answer to the caller's question using only search_kb results. Empty if no question.",
    )
    cited_ids: list[str] = Field(default_factory=list, description="KB entry IDs the draft uses.")
    # Set by an adapter when the model call failed; the engine logs it and carries on safely.
    error: str | None = Field(default=None, exclude=True)
    # Token counts for the turn, summed over the adapter's requests (for cost tracking).
    usage: dict[str, int] | None = Field(default=None, exclude=True)


@dataclass
class TurnContext:
    """What the engine tells the model about the call."""

    text: str
    stage: str
    missing_fields: list[str]
    history: list[dict[str, str]] = field(default_factory=list)


SearchFn = Callable[[str], list[Hit]]


class AgentModel(Protocol):
    name: str

    def interpret(self, ctx: TurnContext, search: SearchFn) -> Interpretation: ...


class KBOnlyModel:
    """No LLM: treats every message as a question and answers with the top KB hit as written."""

    name = "kb-only"

    def interpret(self, ctx: TurnContext, search: SearchFn) -> Interpretation:
        hits = search(ctx.text)
        if not hits:
            return Interpretation(intents=["question"])
        return Interpretation(intents=["question"], draft_reply=hits[0].text, cited_ids=[hits[0].id])


def model_from_env() -> AgentModel:
    """Pick the agent model from AGENT_MODEL. Unset means the KB-only stand-in."""
    name = os.environ.get("AGENT_MODEL", "").strip()
    if not name:
        return KBOnlyModel()
    if name.startswith("gemini"):
        from app.agent.gemini import GeminiModel

        return GeminiModel(name)
    raise ValueError(f"AGENT_MODEL={name!r} is not supported yet")
