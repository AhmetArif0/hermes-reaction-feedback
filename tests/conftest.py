"""Shared fixtures. The plugin directory has a hyphen, so load it under an importable name."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "reaction-feedback"


def _load_plugin_package():
    name = "reaction_feedback"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rf = _load_plugin_package()

# Captured from a live private chat with a test bot (Hermes main, 2026-09-28): the user sent
# 12 "dört", 14 "beş", 16 "altı"; the bot answered with 13, 15, 17. Reactions as delivered
# to gateway_platform_event (note ❤ arrives without U+FE0F). The chat id (the user's
# Telegram id) is replaced with a placeholder; message ids and emojis are as captured.
CHAT = "123456789"
LIVE_MESSAGES = [(12, "dört"), (14, "beş"), (16, "altı")]
LIVE_REACTIONS = [
    {"emojis": ["👍"], "custom_emoji_ids": [], "chat_id": CHAT, "message_id": "13", "thread_id": None},
    {"emojis": ["👎"], "custom_emoji_ids": [], "chat_id": CHAT, "message_id": "17", "thread_id": None},
    {"emojis": ["❤"], "custom_emoji_ids": [], "chat_id": CHAT, "message_id": "15", "thread_id": None},
    {"emojis": ["🔥"], "custom_emoji_ids": [], "chat_id": CHAT, "message_id": "12", "thread_id": None},
]
