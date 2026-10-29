from types import SimpleNamespace

import pytest

from app.services import llm_service


@pytest.fixture(autouse=True)
def no_live_llm_calls():
    pass


class FakeCompletions:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def create(self, **kwargs):
        self.prompts.append(kwargs["messages"][1]["content"])
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Your order includes Meadow milk and sourdough bread.")
                )
            ]
        )


def test_event_notification_keeps_fallback_reference_prompt(monkeypatch):
    completions = FakeCompletions()
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )
    monkeypatch.setattr(llm_service, "_get_client", lambda: fake_client)
    fallback = "Your Meadow milk is out of stock. Here are two options."

    llm_service.generate_message(
        kind="SUBSTITUTION_OFFER",
        context={"order_id": "ORD-TEST-1"},
        fallback=fallback,
    )

    assert fallback in completions.prompts[0]
