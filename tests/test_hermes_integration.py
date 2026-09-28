"""End-to-end checks against a real Hermes checkout (skipped when Hermes is not importable).

The plugin is copied into an isolated HERMES_HOME, enabled in config.yaml and loaded by the
real PluginManager. Real ``MessageEvent``s go through ``pre_gateway_dispatch``, the reaction
payload captured from a live bot goes through ``gateway_platform_event``, and real
``AIAgent`` turns run against Hermes' own ``FakeLLMServer`` so the test sees exactly what
reaches the model.
"""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

hermes_plugins = pytest.importorskip("hermes_cli.plugins")

from conftest import CHAT, LIVE_MESSAGES, LIVE_REACTIONS, PLUGIN_DIR, rf  # noqa: E402

PLUGIN_KEY = "reaction-feedback"
HOOKS = ["gateway_platform_event", "pre_gateway_dispatch", "pre_llm_call"]


def _fake_llm_module():
    """Hermes' tests/fakes/fake_llm_provider.py, loaded by path (our tests/ shadows theirs)."""
    path = Path(hermes_plugins.__file__).resolve().parents[1] / "tests" / "fakes" / "fake_llm_provider.py"
    if not path.is_file():
        pytest.skip("this Hermes checkout has no tests/fakes/fake_llm_provider.py")
    spec = importlib.util.spec_from_file_location("hermes_fake_llm_provider", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _install(home: Path) -> None:
    shutil.copytree(PLUGIN_DIR, home / "plugins" / PLUGIN_KEY, ignore=shutil.ignore_patterns("__pycache__"))


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))  # a fresh home per test also keys a fresh ledger
    _install(home)
    hermes_plugins._reset_plugin_managers_for_tests()
    yield home
    hermes_plugins._reset_plugin_managers_for_tests()


def _enable(home: Path, extra: str = "") -> None:
    (home / "config.yaml").write_text(
        extra + f"plugins:\n  enabled:\n    - {PLUGIN_KEY}\n", encoding="utf-8", newline="\n")


def test_loads_through_the_real_plugin_manager(hermes_home, tmp_path, monkeypatch):
    bundled = tmp_path / "bundled-plugins"
    bundled.mkdir()
    monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(bundled))
    _enable(hermes_home)
    manager = hermes_plugins.PluginManager()
    manager.discover_and_load()
    loaded = manager._plugins[PLUGIN_KEY]
    assert loaded.error is None, loaded.error
    registered = sorted(name for name, callbacks in manager._hooks.items()
                        if any(PLUGIN_KEY.replace("-", "_") in getattr(cb, "__module__", "") for cb in callbacks))
    assert registered == HOOKS


def _dispatch(message_id: int, text: str, *, chat_type: str = "dm", chat_id: str = CHAT):
    from gateway.config import Platform
    from gateway.platforms.event import MessageEvent
    from gateway.session import SessionSource
    from hermes_cli.lifecycle import ainvoke_hook

    source = SessionSource(platform=Platform.TELEGRAM, chat_id=chat_id, chat_type=chat_type, user_id=CHAT)
    event = MessageEvent(text=text, source=source, message_id=str(message_id))
    return asyncio.run(ainvoke_hook("pre_gateway_dispatch", event=event, gateway=None, session_store=None))


def _user_text(body: dict) -> str:
    """The last user message in one request body (``FakeLLMServer.main_requests()`` item), as text."""
    for message in reversed(body["messages"]):
        if message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, list):
                return "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
            return content or ""
    return ""


def test_a_reaction_reaches_the_next_real_turn(hermes_home):
    fake = _fake_llm_module()
    from hermes_cli.lifecycle import invoke_hook
    from hermes_state import SessionDB
    from run_agent import AIAgent

    with fake.FakeLLMServer(default_text="ok") as server:
        fake.write_hermes_home(hermes_home, server.base_url,
                               extra_config=f"plugins:\n  enabled:\n    - {PLUGIN_KEY}\n")
        hermes_plugins.discover_plugins(force=True)
        db = SessionDB(db_path=hermes_home / "state.db")

        def _agent():
            return AIAgent(provider="custom", base_url=server.base_url, api_key="sk-fake-e2e",
                           model=fake.MODEL_ID, session_db=db, session_id="dm-session", quiet_mode=True,
                           platform="telegram", user_id=CHAT, enabled_toolsets=["file"],
                           skip_context_files=True, skip_memory=True)

        agent = _agent()
        try:
            history: list = []
            for message_id, text in LIVE_MESSAGES:  # the captured chat: 12 dört, 14 beş, 16 altı
                assert _dispatch(message_id, text) == []  # never changes how the gateway dispatches
                history = agent.run_conversation(text, conversation_history=history)["messages"]
            earlier_turns = [_user_text(r) for r in server.main_requests()]

            for payload in LIVE_REACTIONS:  # 👍 13, 👎 17, ❤ 15, 🔥 12 (the user's own message)
                assert invoke_hook("gateway_platform_event", platform="telegram",
                                   event_type="reaction", payload=dict(payload)) == []

            assert _dispatch(18, "tamam") == []
            agent.run_conversation("tamam", conversation_history=history)
            note_turn = _user_text(server.main_requests()[-1])

            # Prompt cache: the gateway shape rebuilds the agent from state.db; the next request
            # must replay the noted user message byte for byte (Hermes' api_content sidecar).
            agent.close()
            agent = _agent()
            assert _dispatch(20, "sonraki") == []
            agent.run_conversation("sonraki", conversation_history=db.get_messages_as_conversation("dm-session"))
            noted, following = server.main_requests()[-2:]
        finally:
            agent.close()
            db.close()

    assert following["messages"][:len(noted["messages"])] == noted["messages"]

    header = rf.state.HEADER
    assert len(earlier_turns) == 3 and all(header not in text for text in earlier_turns)
    assert "tamam" in note_turn and header in note_turn
    assert note_turn[note_turn.index(header):].splitlines()[:5] == [
        header,
        '- 👎 on a message you sent after the user\'s "altı"',
        '- ❤ on a message you sent after the user\'s "beş"',
        '- 👍 on a message you sent after the user\'s "dört"',
        rf.state.FOOTER,
    ]


def test_group_messages_and_other_platforms_are_left_alone(hermes_home):
    _enable(hermes_home)
    hermes_plugins.discover_plugins(force=True)
    from hermes_cli.lifecycle import invoke_hook

    assert _dispatch(10, "in a group", chat_type="group", chat_id="-100123") == []
    assert invoke_hook("pre_llm_call", session_id="g", platform="telegram", sender_id=CHAT,
                       user_message="in a group") == []
    assert invoke_hook("gateway_platform_event", platform="telegram", event_type="reaction",
                       payload={"emojis": ["👍"], "custom_emoji_ids": [], "chat_id": "-100123",
                                "message_id": "11", "thread_id": None}) == []
    assert invoke_hook("gateway_platform_event", platform="discord", event_type="message_edited",
                       payload={"chat_id": "1", "message_id": "2", "thread_id": None, "text": "x"}) == []
    assert invoke_hook("pre_llm_call", session_id="c", platform="cli", sender_id="",
                       user_message="hi") == []
