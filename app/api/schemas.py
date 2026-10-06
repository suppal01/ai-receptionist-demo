"""Request and response models for POST /api/turn (docs/plan.md section 4)."""

from typing import Any

from pydantic import BaseModel, Field


class TurnRequest(BaseModel):
    """One caller message."""

    call_id: str | None = Field(
        default=None,
        description="Empty on the first turn; the server assigns one and returns it.",
    )
    text: str = Field(
        min_length=1,
        max_length=2000,
        description="What the caller said or typed.",
    )


class FeedbackRequest(BaseModel):
    """A tester's flag on one receptionist reply."""

    call_id: str = Field(min_length=1, max_length=100)
    turn: int = Field(ge=1, description="1 = the receptionist's first reply in the call.")
    expected: str = Field(min_length=1, max_length=2000, description="What the tester expected instead.")


class TurnResponse(BaseModel):
    """One agent reply, plus a record of what happened during the turn."""

    call_id: str
    reply: str
    stage: str = Field(description="Conversation stage after this turn.")
    events: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Tool calls, route decisions and guardrail triggers.",
    )
    ended: bool = Field(description="True when the call is closed.")
