"""The hook callbacks' own filtering (no Hermes needed)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pytest

from conftest import CHAT, rf

HOME = "/profiles/default"


@dataclass
class Source:
    platform: Any = "telegram"
    chat_id: Optional[str] = CHAT
    chat_type: str = "dm"
    user_id: Optional[str] = CHAT
    thread_id: Optional[str] = None


@dataclass
class Event:
    text: Optional[str]
    message_id: Optional[str]
    source: Any = None
    internal: bool = False


@dataclass
class PlatformEnum:  # stands in for gateway.config.Platform
    value: str


@pytest.fixture
def ledger(monkeypatch):
    fresh = rf.state.ReactionLedger()
    monkeypatch.setattr(rf, "LEDGER", fresh)
    monkeypatch.setattr(rf, "_profile_home", lambda: HOME)
    return fresh


def claims(ledger):
    chat = ledger._homes.get(HOME, {}).get(CHAT)
    return None if chat is None or chat.claim is None else chat.claim.message_id


def react(message_id, *emojis):
    return {"emojis": list(emojis), "custom_emoji_ids": [], "chat_id": CHAT,
            "message_id": str(message_id), "thread_id": None}


def test_private_telegram_messages_are_recorded(ledger):
    for platform in ("telegram", "Telegram", PlatformEnum("telegram")):
        assert rf.on_user_message(event=Event("hi", "10", Source(platform=platform)), gateway=None,
                                  session_store=None) is None
    assert claims(ledger) == 10


def test_the_topic_of_a_message_is_recorded(ledger):
    rf.on_user_message(event=Event("hi", "10", Source(thread_id="7")))
    assert ledger._homes[HOME][CHAT].anchors[0].thread == "7"


@pytest.mark.parametrize("event", [
    Event("hi", "10", Source(platform="discord")),
    Event("hi", "10", Source(platform=PlatformEnum("slack"))),
    Event("hi", "10", Source(chat_type="group", chat_id="-100123")),
    Event("hi", "10", Source(chat_type="channel")),
    Event("hi", "10", Source(), internal=True),
    Event("hi", "10", None),
    None,
])
def test_other_messages_are_not_recorded(ledger, event):
    assert rf.on_user_message(event=event) is None
    assert ledger._homes == {}


def test_turn_note_only_for_telegram(ledger):
    rf.on_user_message(event=Event("question", "10", Source()))
    assert rf.on_turn_start(platform="telegram", sender_id=CHAT, session_id="s1", user_message="question") is None
    rf.on_platform_event(platform="telegram", event_type="reaction", payload=react(11, "👍"))
    rf.on_user_message(event=Event("next", "12", Source()))
    assert rf.on_turn_start(platform="cli", sender_id=CHAT, session_id="s1", user_message="next") is None
    result = rf.on_turn_start(platform="telegram", sender_id=CHAT, session_id="s1", user_message="next",
                              task_id="t", turn_id="u", conversation_history=[], is_first_turn=False,
                              model="m", parent_session_id="", future_field=1)
    assert set(result) == {"context"} and "👍" in result["context"]


@pytest.mark.parametrize("kwargs", [
    {"platform": "discord", "event_type": "reaction", "payload": react(11, "👍")},
    {"platform": "telegram", "event_type": "message_edited", "payload": react(11, "👍")},
    {"platform": "telegram", "event_type": "reaction", "payload": None},
    {"platform": "telegram", "event_type": "reaction", "payload": "11"},
])
def test_other_platform_events_are_ignored(ledger, kwargs):
    rf.on_user_message(event=Event("question", "10", Source()))
    rf.on_turn_start(platform="telegram", sender_id=CHAT, session_id="s1", user_message="question")
    assert rf.on_platform_event(**kwargs) is None
    assert ledger._homes[HOME][CHAT].pending == {}


def test_callbacks_never_raise(ledger, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("boom")

    for name in ("record_user_message", "start_turn", "record_reaction"):
        monkeypatch.setattr(ledger, name, boom)
    assert rf.on_user_message(event=Event("hi", "10", Source())) is None
    assert rf.on_turn_start(platform="telegram", sender_id=CHAT, session_id="s1", user_message="hi") is None
    assert rf.on_platform_event(platform="telegram", event_type="reaction", payload=react(11, "👍")) is None


def test_without_a_profile_home_nothing_happens(monkeypatch):
    fresh = rf.state.ReactionLedger()
    monkeypatch.setattr(rf, "LEDGER", fresh)
    monkeypatch.setattr(rf, "_profile_home", lambda: None)
    rf.on_user_message(event=Event("hi", "10", Source()))
    rf.on_platform_event(platform="telegram", event_type="reaction", payload=react(11, "👍"))
    assert rf.on_turn_start(platform="telegram", sender_id=CHAT, session_id="s1", user_message="hi") is None
    assert fresh._homes == {}


def test_register_only_registers_the_three_hooks():
    calls = []

    class Ctx:
        def register_hook(self, name, callback):
            calls.append((name, callback))

        def __getattr__(self, name):  # any other registration would fail the test
            raise AssertionError(f"register() touched ctx.{name}")

    rf.register(Ctx())
    assert calls == [("pre_gateway_dispatch", rf.on_user_message), ("pre_llm_call", rf.on_turn_start),
                     ("gateway_platform_event", rf.on_platform_event)]
