"""Caller simulator: an LLM plays a test caller, one short message per turn.

It must be a different model from the agent (default gemini-2.5-flash, SIM_MODEL). It knows
only the case's persona, facts and behavior, and decides when the call is over.
"""

import json
import os
import threading
from dataclasses import dataclass

SIM_PROMPT_VERSION = "caller-v1"

SYSTEM = """\
You are role-playing a person phoning Sparkle Dental, a dental practice. You are talking to \
its receptionist. Stay in character. Use only the facts you are given; if asked something \
your facts don't cover, say you're not sure. Follow your behavior instructions exactly, at \
the moment they describe. Write one short, natural message per turn, the way people talk on \
the phone: usually one sentence, never a list. Don't volunteer details you weren't asked \
for unless your behavior says to. End the call (end_call true) once your goal is done and \
the receptionist has nothing more to ask, or if the receptionist says goodbye; when you end \
it, your message is a brief sign-off such as "No, that's all, thanks."."""


@dataclass
class CallerTurn:
    text: str
    end_call: bool


def caller_prompt(case: dict, transcript: list[dict]) -> str:
    c = case["caller"]
    facts = "\n".join(f"- {k}: {v}" for k, v in c["facts"].items())
    lines = [f"You are: {c['persona']}", f"\nYour facts:\n{facts}", f"\nYour behavior: {c['behavior']}",
             "\nThe call so far:"]
    lines += [f"{'You' if t['role'] == 'caller' else 'Receptionist'}: {t['text']}" for t in transcript]
    lines.append("\nWrite your next message.")
    return "\n".join(lines)


SCHEMA = {
    "type": "object",
    "properties": {"message": {"type": "string"}, "end_call": {"type": "boolean"}},
    "required": ["message", "end_call"],
}


class GeminiCaller:
    def __init__(self, model: str | None = None, client=None):
        self.name = model or os.environ.get("SIM_MODEL") or "gemini-2.5-flash"
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
                    http_options=types.HttpOptions(
                        timeout=60_000,
                        retry_options=types.HttpRetryOptions(attempts=3, http_status_codes=[429, 500, 502, 503, 504]),
                    ),
                )
        return self._client

    def next_message(self, case: dict, transcript: list[dict]) -> CallerTurn:
        from google.genai import types

        resp = self.client.models.generate_content(
            model=self.name,
            contents=caller_prompt(case, transcript),
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM,
                temperature=0.3,
                response_mime_type="application/json",
                response_json_schema=SCHEMA,
            ),
        )
        data = json.loads(resp.text)
        return CallerTurn(text=data["message"].strip(), end_call=bool(data["end_call"]))
