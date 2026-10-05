"""Tests for the Gemini adapter's tool loop, using a fake Gemini client (no network)."""

from types import SimpleNamespace

from app.agent.gemini import MAX_SEARCHES, GeminiModel
from app.agent.model import TurnContext
from app.kb import Hit


def call(name, **args):
    return SimpleNamespace(name=name, args=args)


def response(*calls, prompt=100, output=10, thinking=5):
    return SimpleNamespace(
        function_calls=list(calls),
        candidates=[SimpleNamespace(content=f"model-turn:{calls[0].name}")],
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt, candidates_token_count=output,
            thoughts_token_count=thinking, cached_content_token_count=0,
        ),
    )


class FakeGenaiClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.models = self

    def get(self, model):
        self.requests.append({"get": model})

    def generate_content(self, model, contents, config):
        self.requests.append({"model": model, "contents": list(contents), "config": config})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


CTX = TurnContext(text="When do you close Friday?", stage="collect", missing_fields=["preferred_times"])
HOURS = Hit("kb-hours-001", "hours", "Friday, 8:00 AM to 1:00 PM.", 2.0)


def submit(**args):
    return call("submit", **({"intents": ["question"]} | args))


def test_search_then_submit_returns_the_interpretation():
    client = FakeGenaiClient(
        [
            response(call("search_kb", query="Friday hours")),
            response(submit(draft_reply="We close at 1:00 PM on Fridays.", cited_ids=["kb-hours-001"])),
        ]
    )
    queries = []
    result = GeminiModel("gemini-test", client=client).interpret(
        CTX, lambda q: queries.append(q) or [HOURS]
    )
    assert queries == ["Friday hours"]
    assert result.intents == ["question"]
    assert result.cited_ids == ["kb-hours-001"]
    assert result.error is None
    # The model's own turn is passed back unchanged (Gemini needs its signatures).
    assert "model-turn:search_kb" in client.requests[1]["contents"]


def test_token_usage_is_summed_across_the_turns_requests():
    client = FakeGenaiClient(
        [
            response(call("search_kb", query="hours"), prompt=800, output=20, thinking=40),
            response(submit(), prompt=900, output=50, thinking=0),
        ]
    )
    result = GeminiModel("gemini-test", client=client).interpret(CTX, lambda q: [])
    assert result.usage == {
        "requests": 2, "input_tokens": 1700, "cached_tokens": 0,
        "output_tokens": 70, "thinking_tokens": 40,
    }


def test_fields_are_parsed():
    client = FakeGenaiClient(
        [response(submit(intents=["provide_info"], fields={"name": "Priya Shah"}))]
    )
    result = GeminiModel("gemini-test", client=client).interpret(CTX, lambda q: [])
    assert result.fields.name == "Priya Shah"


def test_after_max_searches_only_submit_is_allowed():
    searches = [response(call("search_kb", query=f"q{i}")) for i in range(MAX_SEARCHES)]
    client = FakeGenaiClient([*searches, response(submit())])
    GeminiModel("gemini-test", client=client).interpret(CTX, lambda q: [])
    last = client.requests[-1]["config"].tool_config.function_calling_config
    assert last.allowed_function_names == ["submit"]


def test_api_error_returns_a_safe_interpretation():
    client = FakeGenaiClient([RuntimeError("503 unavailable"), RuntimeError("503 unavailable")])
    result = GeminiModel("gemini-test", client=client).interpret(CTX, lambda q: [])
    assert result.intents == ["other"]
    assert "503" in result.error


def test_a_google_side_failure_is_retried_once():
    # 504/499 timeouts failed about 1% of model calls in recent runs.
    client = FakeGenaiClient([RuntimeError("504 DEADLINE_EXCEEDED"), response(submit())])
    result = GeminiModel("gemini-test", client=client).interpret(CTX, lambda q: [])
    assert result.error is None
    assert result.intents == ["question"]
    assert len(client.requests) == 2


def test_a_second_failure_gives_up_safely():
    client = FakeGenaiClient([RuntimeError("504 DEADLINE_EXCEEDED"), RuntimeError("503 UNAVAILABLE")])
    result = GeminiModel("gemini-test", client=client).interpret(CTX, lambda q: [])
    assert result.intents == ["other"] and "503" in result.error


def test_a_bad_answer_is_not_retried():
    client = FakeGenaiClient([response(call("submit", intents=["made_up_intent"]))])
    result = GeminiModel("gemini-test", client=client).interpret(CTX, lambda q: [])
    assert result.error and len(client.requests) == 1


def test_invalid_submit_returns_a_safe_interpretation():
    client = FakeGenaiClient([response(call("submit", intents=["made_up_intent"]))])
    result = GeminiModel("gemini-test", client=client).interpret(CTX, lambda q: [])
    assert result.intents == ["other"]
    assert result.error


def test_warm_up_makes_a_metadata_request_not_a_generation():
    client = FakeGenaiClient([])
    GeminiModel("gemini-test", client=client).warm_up()
    assert client.requests == [{"get": "gemini-test"}]


def test_warm_up_failure_is_swallowed():
    class Broken(FakeGenaiClient):
        def get(self, model):
            raise RuntimeError("offline")

    GeminiModel("gemini-test", client=Broken([])).warm_up()  # must not raise


def test_prompt_carries_stage_missing_fields_and_caller_text():
    client = FakeGenaiClient([response(submit())])
    GeminiModel("gemini-test", client=client).interpret(CTX, lambda q: [])
    prompt = str(client.requests[0]["contents"][0])
    assert "collect" in prompt
    assert "preferred_times" in prompt
    assert "When do you close Friday?" in prompt
