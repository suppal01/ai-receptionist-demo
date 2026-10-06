"""Test doubles. A scripted stand-in for the agent model: no network, same result every run."""

from app.agent.model import Interpretation, RequestFields


def step(*intents, search=(), draft="", cited=(), asked_insurance=None, **fields):
    """One scripted model turn: searches to run, then the interpretation to return."""
    return (
        list(search),
        Interpretation(
            intents=list(intents),
            fields=RequestFields(**fields),
            draft_reply=draft,
            cited_ids=list(cited),
            insurance_asked_about=asked_insurance,
        ),
    )


class FakeModel:
    name = "fake"

    def __init__(self, steps):
        self.steps = list(steps)
        self.contexts = []

    def interpret(self, ctx, search):
        self.contexts.append(ctx)
        if not self.steps:
            raise AssertionError("model called more times than the test scripted")
        queries, interpretation = self.steps.pop(0)
        for query in queries:
            search(query)
        return interpretation
