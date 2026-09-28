"""reaction-feedback: tell the agent about the emoji reactions a user leaves on its Telegram messages.

``register`` only registers three hooks. Everything is kept in memory, per profile and per
private chat; see ``state.py`` and docs/DESIGN.md for how a reaction is attributed.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from .state import ReactionLedger

logger = logging.getLogger(__name__)

LEDGER = ReactionLedger()


def _profile_home() -> Optional[str]:
    """The profile the hook is running for (Hermes scopes each hook to its profile)."""
    try:
        from hermes_constants import get_hermes_home

        return os.path.realpath(str(get_hermes_home()))
    except Exception:
        return None


def _platform_name(value: Any) -> str:
    value = getattr(value, "value", value)  # gateway Platform enum or a plain string
    return value.strip().lower() if isinstance(value, str) else ""


def on_user_message(event: Any = None, **_kwargs: Any) -> None:
    """``pre_gateway_dispatch``: remember a private-chat Telegram message. Never alters dispatch."""
    try:
        if event is None or getattr(event, "internal", False):
            return None
        source = getattr(event, "source", None)
        if _platform_name(getattr(source, "platform", None)) != "telegram":
            return None
        if getattr(source, "chat_type", None) != "dm":
            return None
        home = _profile_home()
        if home is not None:
            LEDGER.record_user_message(home, getattr(source, "chat_id", None),
                                       getattr(event, "message_id", None), getattr(event, "text", None),
                                       getattr(source, "thread_id", None))
    except Exception:
        logger.debug("reaction-feedback: could not record a message", exc_info=True)
    return None


def on_turn_start(platform: Any = None, sender_id: Any = None, session_id: Any = None,
                  user_message: Any = None, **_kwargs: Any) -> Optional[dict]:
    """``pre_llm_call``: hand the turn the reactions left on this session's earlier messages."""
    try:
        if _platform_name(platform) != "telegram":
            return None
        home = _profile_home()
        if home is None:
            return None
        note = LEDGER.start_turn(home, sender_id, session_id, user_message)
        return {"context": note} if note else None
    except Exception:
        logger.debug("reaction-feedback: could not prepare the turn note", exc_info=True)
        return None


def on_platform_event(platform: Any = None, event_type: Any = None, payload: Any = None,
                      **_kwargs: Any) -> None:
    """``gateway_platform_event``: record a Telegram reaction (runs on the gateway event loop)."""
    try:
        if _platform_name(platform) != "telegram" or event_type != "reaction" or not isinstance(payload, dict):
            return None
        home = _profile_home()
        if home is not None:
            LEDGER.record_reaction(home, payload.get("chat_id"), payload.get("message_id"),
                                   payload.get("emojis"), payload.get("custom_emoji_ids"))
    except Exception:
        logger.debug("reaction-feedback: could not record a reaction", exc_info=True)
    return None


def register(ctx) -> None:
    ctx.register_hook("pre_gateway_dispatch", on_user_message)
    ctx.register_hook("pre_llm_call", on_turn_start)
    ctx.register_hook("gateway_platform_event", on_platform_event)
