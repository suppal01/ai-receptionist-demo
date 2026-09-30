"""Captured appointment and callback requests (the `requests` table in docs/plan.md section 4).

In memory until the database increment. A record can only be created with a valid,
complete set of fields; the engine calls save only after a confirmed read-back.
"""

import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator

RequestType = Literal["new_patient", "callback", "urgent"]

REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "new_patient": (
        "name",
        "callback_number",
        "preferred_times",
        "insurance_carrier",
        "reason_for_visit",
    ),
    "callback": ("name", "callback_number"),
    "urgent": ("name", "callback_number"),
}


class RequestRecord(BaseModel):
    id: str = Field(default_factory=lambda: f"req-{uuid.uuid4().hex[:8]}")
    call_id: str
    type: RequestType
    name: str | None = None
    callback_number: str | None = Field(default=None, pattern=r"^\d{10}$")
    preferred_times: str | None = None
    insurance_carrier: str | None = None
    reason_for_visit: str | None = None
    status: Literal["new", "booked", "not_booked"] = "new"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def _required_fields_present(self):
        missing = [f for f in REQUIRED_FIELDS[self.type] if not getattr(self, f)]
        if missing:
            raise ValueError(f"missing required fields: {', '.join(missing)}")
        return self
