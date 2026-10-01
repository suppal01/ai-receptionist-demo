"""Per-call state the engine keeps between turns (stored in the `calls` table)."""

from dataclasses import dataclass, field


@dataclass
class CallState:
    id: str
    stage: str = "open"
    request_type: str | None = None
    fields: dict[str, str] = field(default_factory=dict)
    history: list[dict[str, str]] = field(default_factory=list)
    ended: bool = False
    # emergency > handoff > request_saved > info_only; set by the engine as things happen.
    outcome: str | None = None
