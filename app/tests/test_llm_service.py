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


def test_order_followup_uses_customer_request_without_generic_fallback_prompt(monkeypatch):
    completions = FakeCompletions()
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )
    monkeypatch.setattr(llm_service, "_get_client", lambda: fake_client)

    message, source = llm_service.generate_message(
        kind="ORDER_CONFIRMATION_FOLLOWUP",
        context={
            "customer_message": "list the ordered products",
            "items": [
                {"product_name": "Meadow milk", "quantity": 1},
                {"product_name": "Sourdough bread", "quantity": 1},
            ],
        },
        fallback="Thanks for your message! Let us know if there's anything else about your order.",
    )

    assert source == "llm"
    assert message == "Your order includes Meadow milk and sourdough bread."
    assert "list the ordered products" in completions.prompts[0]
    assert "Thanks for your message!" not in completions.prompts[0]
    assert "actual product names and quantities" in completions.prompts[0]


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
