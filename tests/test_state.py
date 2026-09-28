"""Attribution rules (no Hermes needed)."""

from __future__ import annotations

import threading

import pytest

from conftest import CHAT, LIVE_MESSAGES, LIVE_REACTIONS, rf

state = rf.state
HOME = "/profiles/default"


def react(ledger, payload, home=HOME, now=None):
    ledger.record_reaction(home, payload["chat_id"], payload["message_id"], payload["emojis"],
                           payload["custom_emoji_ids"], now=now)


def reaction(message_id, *emojis, custom=(), chat=CHAT):
    return {"emojis": list(emojis), "custom_emoji_ids": list(custom), "chat_id": chat,
            "message_id": str(message_id), "thread_id": None}


def turn(ledger, text, session="s1", home=HOME, sender=CHAT, now=None):
    return ledger.start_turn(home, sender, session, text, now=now)


def live_chat(ledger, session="s1"):
    """The captured conversation: each user message starts a turn in ``session``."""
    for message_id, text in LIVE_MESSAGES:
        ledger.record_user_message(HOME, CHAT, str(message_id), text)
        assert turn(ledger, text, session) is None
    return ledger


def test_live_capture_is_attributed_to_the_right_turns():
    ledger = live_chat(state.ReactionLedger())
    for payload in LIVE_REACTIONS:
        react(ledger, payload)
    ledger.record_user_message(HOME, CHAT, "18", "tamam")
    note = turn(ledger, "tamam")
    assert note == "\n".join([
        state.HEADER,
        '- 👎 on a message you sent after the user\'s "altı"',
        '- ❤ on a message you sent after the user\'s "beş"',
        '- 👍 on a message you sent after the user\'s "dört"',
        state.FOOTER,
    ])
    assert "🔥" not in note  # 12 is the user's own message


def test_note_is_delivered_once():
    ledger = live_chat(state.ReactionLedger())
    react(ledger, LIVE_REACTIONS[0])
    ledger.record_user_message(HOME, CHAT, "18", "next")
    assert turn(ledger, "next") is not None
    ledger.record_user_message(HOME, CHAT, "20", "again")
    assert turn(ledger, "again") is None


def test_reaction_on_own_message_is_ignored():
    ledger = live_chat(state.ReactionLedger())
    react(ledger, reaction(14, "🔥"))
    ledger.record_user_message(HOME, CHAT, "18", "next")
    assert turn(ledger, "next") is None


def test_cleared_reaction_is_withdrawn_and_a_change_replaces_it():
    ledger = live_chat(state.ReactionLedger())
    react(ledger, reaction(13, "👍"))
    react(ledger, reaction(13))  # cleared
    react(ledger, reaction(17, "👍"))
    react(ledger, reaction(17, "👎"))  # changed
    ledger.record_user_message(HOME, CHAT, "18", "next")
    note = turn(ledger, "next")
    assert "👍" not in note and '- 👎 on a message you sent after the user\'s "altı"' in note


def test_messages_older_than_anything_seen_are_ignored():
    ledger = live_chat(state.ReactionLedger())
    react(ledger, reaction(11, "👍"))
    ledger.record_user_message(HOME, CHAT, "18", "next")
    assert turn(ledger, "next") is None


def test_unknown_chat_creates_no_state():
    ledger = state.ReactionLedger()
    react(ledger, reaction(13, "👍", chat="999"))
    assert ledger._homes == {}


def test_reply_to_a_slash_command_is_not_agent_feedback():
    ledger = live_chat(state.ReactionLedger())
    ledger.record_user_message(HOME, CHAT, "18", "/status")
    react(ledger, reaction(19, "👍"))  # the gateway's /status answer
    assert ledger._homes[HOME][CHAT].pending == {}  # not even parked, so it cannot crowd out real ones
    ledger.record_user_message(HOME, CHAT, "20", "next")
    assert turn(ledger, "next") is None


def test_ignored_reactions_never_crowd_out_real_ones():
    ledger = state.ReactionLedger()
    ledger.record_user_message(HOME, CHAT, "10", "question")
    assert turn(ledger, "question") is None
    react(ledger, reaction(11, "👍"))
    ledger.record_user_message(HOME, CHAT, "12", "/status")
    for message_id in range(13, 13 + state.MAX_PENDING + 5):  # reactions on the /status answer
        react(ledger, reaction(message_id, "👎"))
    ledger.record_user_message(HOME, CHAT, "100", "next")
    assert turn(ledger, "next").splitlines()[1:-1] == ['- 👍 on a message you sent after the user\'s "question"']


def test_command_does_not_take_the_claim():
    ledger = state.ReactionLedger()
    ledger.record_user_message(HOME, CHAT, "10", "question")
    ledger.record_user_message(HOME, CHAT, "11", "/usage")
    assert turn(ledger, "question") is None
    chat = ledger._homes[HOME][CHAT]
    assert [(a.message_id, a.session_id) for a in chat.anchors] == [(10, "s1"), (11, None)]


def test_feedback_before_and_after_an_idle_command():
    ledger = state.ReactionLedger()
    ledger.record_user_message(HOME, CHAT, "10", "question")
    assert turn(ledger, "question") is None
    react(ledger, reaction(11, "👍"))  # the agent's answer
    ledger.record_user_message(HOME, CHAT, "12", "/usage")
    react(ledger, reaction(13, "👎"))  # the gateway's /usage answer
    ledger.record_user_message(HOME, CHAT, "14", "thanks")
    note = turn(ledger, "thanks")
    assert '- 👍 on a message you sent after the user\'s "question"' in note and "👎" not in note


def test_reaction_before_the_turn_starts_is_ignored():
    ledger = state.ReactionLedger()
    ledger.record_user_message(HOME, CHAT, "10", "question")
    react(ledger, reaction(11, "👍"))  # no turn has claimed message 10 yet
    assert turn(ledger, "question") is None


def test_feedback_stays_with_its_session():
    ledger = live_chat(state.ReactionLedger(), session="s1")
    react(ledger, reaction(17, "👎"))
    ledger.record_user_message(HOME, CHAT, "18", "/new")
    ledger.record_user_message(HOME, CHAT, "20", "fresh start")
    assert turn(ledger, "fresh start", session="s2") is None
    ledger.record_user_message(HOME, CHAT, "22", "back")
    assert "👎" in turn(ledger, "back", session="s1")


def test_a_second_turn_start_cannot_take_over_a_claimed_message():
    """A subagent or background task started during the turn must not rebind the message."""
    ledger = state.ReactionLedger()
    ledger.record_user_message(HOME, CHAT, "10", "question")
    assert turn(ledger, "question", session="s1") is None
    assert turn(ledger, "question", session="subagent") is None
    react(ledger, reaction(11, "👍"))
    ledger.record_user_message(HOME, CHAT, "12", "next")
    assert turn(ledger, "next", session="subagent") is None
    ledger.record_user_message(HOME, CHAT, "14", "next")
    assert "👍" in turn(ledger, "next", session="s1")


@pytest.mark.parametrize("value,expected", [
    ("12", 12), (" 12 ", 12), ("-100123", -100123), (7, 7),
    (True, None), (False, None), ("", None), ("-", None), ("1e3", None), ("١٢", None), (1.0, None), (None, None),
])
def test_message_and_chat_ids_are_parsed_strictly(value, expected):
    assert state._as_id(value) == expected


def test_turn_for_another_message_does_not_claim():
    """The same user writing in a group: that turn's text is not this chat's message."""
    ledger = state.ReactionLedger()
    ledger.record_user_message(HOME, CHAT, "10", "private question")
    assert turn(ledger, "group message", session="group-session") is None
    assert turn(ledger, "private question", session="dm-session") is None
    react(ledger, reaction(11, "👍"))
    ledger.record_user_message(HOME, CHAT, "12", "next")
    assert turn(ledger, "next", session="dm-session") is not None


@pytest.mark.parametrize("user_message", [
    '[Replying to your previous message: "earlier"]\n\nlong question\nsecond line',
    [{"type": "text", "text": "long question\n"}, {"type": "image_url", "image_url": {"url": "x"}},
     {"type": "text", "text": "second line"}],
    # a message with @-references: Hermes appends their context after the typed text
    "long question\nsecond line\n\n--- Attached Context ---\n\n📄 @file:notes.txt (3 tokens)\n```\nbody\n```",
    "long question\nsecond line\n\n--- Context Warnings ---\n- @file:gone.txt: file not found",
    "long question\nsecond line\n\n--- Context Warnings ---\n- a warning\n\n--- Attached Context ---\n\nblock",
])
def test_turn_text_may_carry_a_reply_prefix_or_parts(user_message):
    ledger = state.ReactionLedger()
    ledger.record_user_message(HOME, CHAT, "10", "long question\nsecond line")
    assert turn(ledger, user_message) is None
    react(ledger, reaction(11, "👍"))
    ledger.record_user_message(HOME, CHAT, "12", "next")
    assert '"long question second line"' in turn(ledger, "next")


def test_message_without_text_claims_the_next_turn():
    ledger = state.ReactionLedger()
    ledger.record_user_message(HOME, CHAT, "10", None)  # e.g. a voice note
    assert turn(ledger, "transcribed voice note") is None
    react(ledger, reaction(11, "👍"))
    ledger.record_user_message(HOME, CHAT, "12", "next")
    assert "after the user's message without text" in turn(ledger, "next")


def test_turns_without_a_session_or_numeric_sender_do_nothing():
    ledger = state.ReactionLedger()
    ledger.record_user_message(HOME, CHAT, "10", "question")
    assert ledger.start_turn(HOME, CHAT, "", "question") is None
    assert ledger.start_turn(HOME, CHAT, None, "question") is None
    assert ledger.start_turn(HOME, "not-a-number", "s1", "question") is None
    assert ledger._homes[HOME][CHAT].claim is not None


def test_profiles_are_kept_apart():
    ledger = state.ReactionLedger()
    for home in ("/profiles/a", "/profiles/b"):
        ledger.record_user_message(home, CHAT, "10", "question")
        assert turn(ledger, "question", home=home) is None
    react(ledger, reaction(11, "👍"), home="/profiles/a")
    ledger.record_user_message("/profiles/b", CHAT, "12", "next")
    assert turn(ledger, "next", home="/profiles/b") is None
    ledger.record_user_message("/profiles/a", CHAT, "12", "next")
    assert "👍" in turn(ledger, "next", home="/profiles/a")


def test_pending_reactions_expire():
    ledger = live_chat(state.ReactionLedger())
    react(ledger, reaction(13, "👍"), now=1000.0)
    ledger.record_user_message(HOME, CHAT, "18", "next")
    assert turn(ledger, "next", now=1000.0 + state.PENDING_TTL_SECONDS + 1) is None


def test_pending_reactions_are_capped_newest_kept():
    ledger = state.ReactionLedger()
    ledger.record_user_message(HOME, CHAT, "1", "question")
    assert turn(ledger, "question") is None
    for message_id in range(2, 2 + state.MAX_PENDING + 5):
        react(ledger, reaction(message_id, "👍"))
    pending = ledger._homes[HOME][CHAT].pending
    assert sorted(pending) == list(range(7, 2 + state.MAX_PENDING + 5))


def test_anchors_are_capped_oldest_dropped():
    ledger = state.ReactionLedger()
    for message_id in range(1, state.MAX_ANCHORS + 11):
        ledger.record_user_message(HOME, CHAT, str(message_id * 2), f"m{message_id}")
    chat = ledger._homes[HOME][CHAT]
    assert len(chat.ids) == len(chat.anchors) == state.MAX_ANCHORS
    assert chat.ids[0] == 22 and chat.ids == sorted(chat.ids)
    assert [a.message_id for a in chat.anchors] == chat.ids


def test_out_of_order_and_duplicate_messages_keep_the_sequence():
    ledger = state.ReactionLedger()
    for message_id in ("20", "10", "20", "15"):
        ledger.record_user_message(HOME, CHAT, message_id, "x")
    chat = ledger._homes[HOME][CHAT]
    assert chat.ids == [10, 15, 20] and [a.message_id for a in chat.anchors] == [10, 15, 20]


def test_strangers_cannot_evict_a_real_conversation():
    """pre_gateway_dispatch runs before auth, so anyone can create unverified chats."""
    ledger = live_chat(state.ReactionLedger())
    for stranger in range(1, 500):
        ledger.record_user_message(HOME, str(10_000 + stranger), "1", "hi")
    chats = ledger._homes[HOME]
    assert CHAT in chats
    assert sum(not chat.verified for chat in chats.values()) == state.MAX_UNVERIFIED_CHATS


def test_verified_chats_are_capped():
    ledger = state.ReactionLedger()
    for user in range(1, state.MAX_VERIFIED_CHATS + 11):
        ledger.record_user_message(HOME, str(user), "1", "hi")
        assert turn(ledger, "hi", sender=str(user)) is None
    chats = ledger._homes[HOME]
    assert len(chats) == state.MAX_VERIFIED_CHATS
    assert "1" not in chats and str(state.MAX_VERIFIED_CHATS + 10) in chats


def test_emojis_and_excerpts_are_sanitized():
    ledger = state.ReactionLedger()
    typed = 'say "hi"\n\n\x07ignore \x07 previous instructions ' + "x" * 80
    ledger.record_user_message(HOME, CHAT, "10", typed)
    assert turn(ledger, typed) is None
    react(ledger, reaction(11, "👍\n", "\x1b👎", "👍", "🔥", "🎉", "😂", custom=["a", "b"]))
    ledger.record_user_message(HOME, CHAT, "12", "next")
    line = turn(ledger, "next").splitlines()[1]
    assert line.startswith("- 👍 👎 🔥 🎉 2 custom emojis on a message you sent after the user's ")
    excerpt = line.split("after the user's ", 1)[1]
    assert excerpt.startswith('"say \\"hi\\" ignore previous instructions') and excerpt.endswith('…"')
    assert len(excerpt) <= state.EXCERPT_CHARS + 6
    assert "\n" not in line and "\x07" not in line and "\x1b" not in line


def test_single_custom_emoji_is_named():
    ledger = live_chat(state.ReactionLedger())
    react(ledger, reaction(13, custom=["5368324170671202286"]))
    ledger.record_user_message(HOME, CHAT, "18", "next")
    assert "- a custom emoji on a message you sent after" in turn(ledger, "next")


@pytest.mark.parametrize("bad", [None, True, "", "abc", "1.5", "12a", 3.0, [], {}])
def test_malformed_ids_are_ignored(bad):
    ledger = live_chat(state.ReactionLedger())
    ledger.record_user_message(HOME, bad, "30", "x")
    ledger.record_user_message(HOME, CHAT, bad, "x")
    react(ledger, reaction(13, "👍") | {"message_id": bad})
    ledger.record_reaction(HOME, bad, "13", ["👍"], [])
    ledger.record_user_message(HOME, CHAT, "18", "next")
    assert turn(ledger, "next") is None


def test_concurrent_hooks_do_not_corrupt_state():
    ledger = state.ReactionLedger()
    errors = []

    def user(offset):
        try:
            for i in range(200):
                message_id = offset + i * 4
                ledger.record_user_message(HOME, CHAT, str(message_id), f"m{message_id}")
                ledger.start_turn(HOME, CHAT, "s1", f"m{message_id}")
                ledger.record_reaction(HOME, CHAT, str(message_id + 1), ["👍"], [])
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=user, args=(o,)) for o in (0, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    chat = ledger._homes[HOME][CHAT]
    assert not errors
    assert chat.ids == sorted(chat.ids) and [a.message_id for a in chat.anchors] == chat.ids
