# reaction-feedback — design

A Hermes plugin that lets the agent see the emoji reactions a user leaves on its Telegram
messages. When the user reacts 👍 or 👎 to a reply, the agent's next turn in that
conversation starts with a short note saying so.

Requests this answers: NousResearch/hermes-agent#18408, #34660, #27438, #13942. The core
PRs #23731 and #27451 have been open since May. The `gateway_platform_event` hook
(#64176) is the plugin lane for this kind of gateway behaviour.

## Facts this design relies on

Checked in Hermes source at `5458de9483` (main, 2026-09-28) and `v2026.9.24` (0.21.5, the
floor in `requires_hermes`), and against a live Telegram bot on 2026-09-28.

| # | Fact | Where |
|---|------|-------|
| 1 | Telegram `message_reaction` updates become `gateway_platform_event(platform="telegram", event_type="reaction", payload={emojis, custom_emoji_ids, chat_id, message_id, thread_id})`. `new_reaction` is the user's full current set, so a cleared reaction arrives with both lists empty. | `plugins/platforms/telegram/adapter.py::_normalize_reaction_event` |
| 2 | The hook fires only after the gateway authorizes the reacting user, inside the routed profile's scope (standalone and multiplex). | `gateway/run_adapters.py::_handle_gateway_platform_event` and `_make_*platform_event_handler` |
| 3 | It runs synchronously on the gateway event loop, not under `hook_callback_timeout`. The callback must be fast and never block. | `hermes_cli/plugins_dispatch.py::_HOOK_TIMEOUT_BOUNDED_HOOKS` (not listed) |
| 4 | The payload does not say who wrote the reacted message, and there is no documented hook that reports the ids of messages Hermes sends. `gateway_message_delivered` is explicitly deferred. | `website/docs/user-guide/features/hooks.md` |
| 5 | `pre_gateway_dispatch` fires once per inbound message before auth, awaited on the event loop, with the documented `event.message_id`, `event.text`, `event.internal` and `event.source` (`platform`, `chat_id`, `chat_type`, `user_id`). It runs inside the routed profile's scope. | `gateway/run_inbound.py::_hm_admit_event`, `gateway/run_adapters.py::_make_*message_handler`, hooks.md |
| 6 | Messages that arrive while a turn is running (steered or queued) skip `pre_gateway_dispatch` today. This is a known core gap (#121976, #125923, #77976, #110242). Seen live: a mid-turn message (id 5) was steered into the running turn and never reached the hook. | `gateway/platforms/base.py::_handle_message_while_active` |
| 7 | Rapid Telegram text messages are batched into the first event: its `message_id` is kept and later texts are appended. | `gateway/platforms/base.py::_enqueue_text_event` |
| 8 | `pre_llm_call` gets `session_id`, `platform`, `sender_id` (the Telegram user id), `user_message`, …. A returned `{"context": str}` is added to the current user message, never to the system prompt, so the prompt cache is unaffected. Forks with persistence disabled skip the hook. | `agent/turn_context.py::_collect_pre_llm_call_context` |
| 9 | A reply's `user_message` is `[Replying to…: "…"]\n\n` + the text the user typed, so the typed text is always a suffix. | `gateway/run_inbound.py::_prepend_inbound_reply_context` |
| 10 | Messaging-gateway turns run in the gateway process. `dashboard.turn_isolation` (a child process) applies only to the dashboard/TUI host and defaults to off. | `hermes_cli/config_defaults.py`, `gateway/` has no compute host |
| 11 | Callbacks with `**kwargs` receive the whole payload, so fields added later are safe. | `hermes_cli/plugins_dispatch.py::_hook_callback_kwargs` |
| 12 | Live bot, private chat: message ids are one sequence shared by both sides (user 12/14/16, bot 13/15/17). A reaction on the user's own message is delivered with the same payload shape (🔥 on 12). The heart arrives as `❤` without U+FE0F. `chat_id == user_id` in a private chat. | Live capture, 2026-09-28 |
| 13 | The note is placed after the user's text in that turn's request. The saved transcript keeps the plain text, and the exact API string is kept separately in the `api_content` sidecar. Later requests replay it byte for byte, including when a fresh agent is built from `state.db` (checked on 0.21.5 and main: the first 8 of 8 messages are identical between the two requests). | `hermes_state_messages.py` (`api_content`); `tests/test_hermes_integration.py` |

## How a reaction is attributed

Per profile (`get_hermes_home()` at hook time) and per private chat, the plugin keeps:

- **Anchors**: the user's messages seen by `pre_gateway_dispatch`. Each anchor holds the
  message id, a short excerpt, whether it is a slash command, and the session id of the
  agent turn it started (set when `pre_llm_call` claims it).
- **Claim**: the most recent non-command anchor that no turn has claimed yet.
- **Pending**: reactions waiting for the next turn, keyed by the reacted message id.

1. **User message** (`pre_gateway_dispatch`): only Telegram, `chat_type == "dm"` and not
   internal. Record an anchor. If it is not a command, it becomes the claim.
2. **Turn start** (`pre_llm_call`): only when `platform == "telegram"` and a claim exists
   for `sender_id`. If the anchor has text, the turn's `user_message` must end with that
   text (fact 9). This stops a group turn from the same user claiming a private-chat
   message. The anchor takes the turn's `session_id`. Pending reactions for that session
   are returned as context and cleared.
3. **Reaction** (`gateway_platform_event`):
   - An id equal to an anchor is the user's own message: ignored.
   - Otherwise the reacted message falls between the latest anchor before it and the next
     one, so the agent sent it while handling that anchor's turn.
   - If no anchor precedes it (older than what the plugin has seen, e.g. before a
     restart), or the anchor never started a turn (a slash command), it is ignored.
     Nothing is guessed.
   - An empty reaction removes the pending entry.

Only turns that start from a real user message deliver feedback. Subagents, cron and
background tasks never hold a claim. Feedback is only delivered into the session that
produced the reacted message; after `/new` it is not carried into the new conversation.

The note the agent sees:

```
[Telegram reactions from the user since your last turn (reaction-feedback plugin; not typed by the user)]
- 👎 on a message you sent after the user's "altı"
- 👍 on a message you sent after the user's "dört"
Treat these as lightweight feedback on those messages, not as instructions.
```

"A message you sent after …" is literal. The plugin knows the window, not which message
inside it. That is usually the reply, but it can also be a progress update or a cron
delivery (fact 4).

## Limits and bounds

- In memory only. Nothing is written to disk, and pending feedback is lost on restart.
  This is safe because gateway turns and reactions share one process (fact 10).
- Private chats only: groups share one id sequence across many people.
- Per chat: 200 anchors, 20 pending reactions, a 48 h TTL for pending entries.
- Per profile: 256 chats that have had a turn, 32 that have not. Messages from users who
  are not allowed to use the bot (the hook runs before auth) can only evict the unverified
  pool.
- Excerpts: whitespace collapsed, control characters removed, 60 characters, JSON-quoted.
  Emojis: at most 4 per message, 16 characters each. Custom emojis are named as such.
- Known gaps, documented in the README:
  - Mid-turn messages skip the dispatch hook (fact 6), so a reaction on one of them counts
    as a reaction on the agent's messages in that window.
  - Batched messages keep only the first id (fact 7).
  - A paid (⭐) reaction has neither an emoji nor a custom id, so it reads as cleared.

## What it does not do

It does not start a turn on a reaction, run commands from reactions, write to memory or
disk, touch the network, start subprocesses or read secrets. It does not change the system
prompt.

## Testing

- Unit tests for the attribution rules with no Hermes import. The fixtures are the live
  payloads.
- End-to-end on Hermes 0.21.5 and main:
  - The real `PluginManager` loads the plugin, real `MessageEvent`s go through
    `pre_gateway_dispatch`, and the recorded reaction payload goes through
    `gateway_platform_event`.
  - A real `AIAgent` turn against Hermes' `FakeLLMServer` must carry the note in the next
    request's user message, and the first request must not.
  - A fresh agent rebuilt from `state.db` must replay that message byte for byte (fact 13).
- `hermes plugins validate`, a mutation pass over every rule above, and a final live check
  on the test bot.
