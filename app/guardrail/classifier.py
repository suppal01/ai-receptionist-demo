"""AI emergency classifier: the guardrail's second layer (docs/plan.md section 2, stretch goal).

Keyword rules always run first. This layer reads each message for meaning (paraphrases,
metaphors, misspellings) and can only ADD an escalation, never remove one. It runs in
parallel with the agent's model call; if it errors or is slow, the turn goes on as normal
because the keywords have already run. Selected by EMERGENCY_MODEL (a different model from
the agent); unset means no classifier.
"""

import json
import os
import threading
from dataclasses import dataclass
from typing import Protocol

CLASSIFIER_PROMPT_VERSION = "emergency-v1"
TIMEOUT_MS = 8_000

SYSTEM = """\
You screen messages from callers to a dental practice's phone line for emergencies. Decide \
whether the caller describes a possible emergency happening NOW to them or someone with them.

Medical (they should call 911): trouble breathing or swallowing; swelling of the face, \
mouth or throat that is severe, spreading or near the eye or neck; bleeding that won't stop; \
a head injury, fainting or loss of consciousness; a serious allergic reaction; high fever \
with swelling.
Dental (urgent, staff should call back right away): a knocked-out, broken or badly loosened \
tooth; severe or unbearable tooth pain; signs of infection such as an abscess, pus or a \
swollen gum or cheek; a jaw injury.

Not emergencies: things that happened in the past and are resolved; questions about \
emergency policies or fees; other people's past events; routine sensitivity, small chips, \
mild bleeding when flossing; children's baby teeth falling out normally; figures of speech \
("dying to", "killing me" about something else).

If it is unclear whether something is happening now, treat it as an emergency: missing one \
is worse than a false alarm. Answer with JSON only."""

SCHEMA = {
    "type": "object",
    "properties": {
        "emergency": {"type": "boolean"},
        "severity": {"type": "string", "enum": ["medical", "dental", "none"]},
        "reason": {"type": "string"},
    },
    "required": ["emergency", "severity", "reason"],
}


@dataclass(frozen=True)
class ClassifierResult:
    emergency: bool
    severity: str  # medical | dental | none
    reason: str


class EmergencyClassifier(Protocol):
    name: str

    def classify(self, text: str, history: list[dict[str, str]]) -> ClassifierResult: ...


def build_prompt(text: str, history: list[dict[str, str]]) -> str:
    recent = "\n".join(
        f"{'Caller' if h['role'] == 'caller' else 'Receptionist'}: {h['text']}" for h in history[-4:]
    )
    return f"Recent conversation:\n{recent or '(none)'}\n\nNew caller message:\n<<<\n{text}\n>>>"


class GeminiEmergencyClassifier:
    def __init__(self, model: str, client=None):
        self.name = model
        self._client = client
        self._lock = threading.Lock()

    @property
    def client(self):
        with self._lock:
            if self._client is None:
                from google import genai
                from google.genai import types

                self._client = genai.Client(
                    vertexai=True,
                    project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
                    location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
                    http_options=types.HttpOptions(timeout=TIMEOUT_MS),
                )
        return self._client

    def warm_up(self) -> None:
        try:
            self.client.models.get(model=self.name)
        except Exception:
            pass

    def classify(self, text: str, history: list[dict[str, str]]) -> ClassifierResult:
        from google.genai import types

        resp = self.client.models.generate_content(
            model=self.name,
            contents=build_prompt(text, history),
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM,
                temperature=0,
                response_mime_type="application/json",
                response_json_schema=SCHEMA,
            ),
        )
        data = json.loads(resp.text)
        severity = data["severity"] if data["severity"] in ("medical", "dental") else "dental"
        return ClassifierResult(bool(data["emergency"]), severity if data["emergency"] else "none", data["reason"])


def classifier_from_env() -> EmergencyClassifier | None:
    name = os.environ.get("EMERGENCY_MODEL", "").strip()
    return GeminiEmergencyClassifier(name) if name else None
