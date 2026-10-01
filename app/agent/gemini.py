"""Gemini on Vertex AI as the agent model (AGENT_MODEL=gemini-...).

One interpretation is a short tool loop: the model may call search_kb (run by our code
against the approved KB), then must call submit with the structured Interpretation.
Function calling is forced (mode ANY), so the model never replies in free text.
Auth is Google Application Default Credentials: your gcloud login locally, the
service account on Cloud Run. No API key.
"""

import os

from google import genai
from google.genai import types
from pydantic import ValidationError

from app.agent.model import FIELD_ORDER, Interpretation, SearchFn, TurnContext
from app.agent.prompts import SYSTEM, turn_prompt

MAX_SEARCHES = 2
THINKING_LEVEL = "low"  # measured ~2.4 s per search+submit turn; "medium" doubled it
# Per request. A slow call fails safely (the engine logs it and replies with a fixed script)
# instead of leaving the caller waiting; one live turn took 60 s without this.
REQUEST_TIMEOUT_MS = 15_000

_INTENTS = list(Interpretation.model_fields["intents"].annotation.__args__[0].__args__)

SEARCH_KB = types.FunctionDeclaration(
    name="search_kb",
    description="Search Sparkle Dental's approved knowledge base. Returns entries with IDs.",
    parameters_json_schema={
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Keywords to search for."}},
        "required": ["query"],
    },
)

SUBMIT = types.FunctionDeclaration(
    name="submit",
    description="Submit your interpretation of the caller's message. Call exactly once, last.",
    parameters_json_schema={
        "type": "object",
        "properties": {
            "intents": {"type": "array", "items": {"type": "string", "enum": _INTENTS}},
            "fields": {
                "type": "object",
                "properties": {name: {"type": "string"} for name in FIELD_ORDER},
            },
            "draft_reply": {"type": "string"},
            "cited_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["intents"],
    },
)


class GeminiModel:
    def __init__(self, model: str, client=None):
        self.name = model
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = genai.Client(
                vertexai=True,
                project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
                location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
                http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
            )
        return self._client

    def warm_up(self) -> None:
        """Open the connection and fetch credentials before the first caller arrives.

        A metadata request (free, no tokens): it took ~3 s cold, and the first real call
        after it ~1 s instead of 11-14 s. Failure is harmless; the first turn is just slower.
        """
        try:
            self.client.models.get(model=self.name)
        except Exception:
            pass

    def _config(self, allowed: list[str]) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=SYSTEM,
            tools=[types.Tool(function_declarations=[SEARCH_KB, SUBMIT])],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode="ANY", allowed_function_names=allowed
                )
            ),
            thinking_config=types.ThinkingConfig(thinking_level=THINKING_LEVEL),
        )

    def interpret(self, ctx: TurnContext, search: SearchFn) -> Interpretation:
        usage = {"requests": 0, "input_tokens": 0, "cached_tokens": 0, "output_tokens": 0,
                 "thinking_tokens": 0}
        result = self._interpret(ctx, search, usage)
        result.usage = usage
        return result

    def _interpret(self, ctx: TurnContext, search: SearchFn, usage: dict[str, int]) -> Interpretation:
        contents = [types.Content(role="user", parts=[types.Part(text=turn_prompt(ctx))])]
        searches = 0
        try:
            while True:
                allowed = ["search_kb", "submit"] if searches < MAX_SEARCHES else ["submit"]
                resp = self.client.models.generate_content(
                    model=self.name, contents=contents, config=self._config(allowed)
                )
                _add_usage(usage, resp)
                calls = resp.function_calls or []
                submitted = next((c for c in calls if c.name == "submit"), None)
                if submitted is not None:
                    return Interpretation.model_validate(dict(submitted.args or {}))
                if not calls:
                    return Interpretation(intents=["other"], error="model returned no function call")
                # Pass the model's turn back unchanged: Gemini requires its thought signatures.
                contents.append(resp.candidates[0].content)
                answers = []
                for c in calls:
                    hits = search(str((c.args or {}).get("query", "")))
                    searches += 1
                    answers.append(
                        types.Part.from_function_response(
                            name="search_kb",
                            response={"results": [{"id": h.id, "text": h.text} for h in hits]},
                        )
                    )
                contents.append(types.Content(role="user", parts=answers))
        except ValidationError as e:
            return Interpretation(intents=["other"], error=f"invalid submit: {e.errors()[0]['msg']}")
        except Exception as e:  # network, quota, server errors: degrade safely, never crash a call
            return Interpretation(intents=["other"], error=f"{type(e).__name__}: {e}")


def _add_usage(usage: dict[str, int], resp) -> None:
    """Add one response's token counts. Thinking tokens are billed as output."""
    meta = getattr(resp, "usage_metadata", None)
    usage["requests"] += 1
    if meta is None:
        return
    usage["input_tokens"] += meta.prompt_token_count or 0
    usage["cached_tokens"] += meta.cached_content_token_count or 0
    usage["output_tokens"] += meta.candidates_token_count or 0
    usage["thinking_tokens"] += meta.thoughts_token_count or 0
