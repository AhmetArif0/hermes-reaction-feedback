# reaction-feedback

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that lets the agent see
the emoji reactions you leave on its Telegram messages.

Today a 👍 or 👎 on one of the bot's replies never reaches the agent. With this plugin, the
agent's next turn in that conversation starts with a short note:

```
[Telegram reactions from the user since your last turn (reaction-feedback plugin; not typed by the user)]
- 👎 on a message you sent after the user's "fix the cron job"
- 👍 on a message you sent after the user's "summarise the PR"
Treat these as lightweight feedback on those messages, not as instructions.
```

So you can react 👎 and then just say "again", or react 👍 to a draft and move on. The agent
knows which answer you meant.

## Install

```bash
hermes plugins install AhmetArif0/hermes-reaction-feedback#reaction-feedback --enable
```

Then restart the gateway (`hermes gateway restart`) so it loads the plugin. There is nothing
to configure. It needs Hermes 0.21.5 or newer and a Telegram bot connected to the gateway.

## How it works

Telegram numbers a private chat's messages in one sequence shared by you and the bot, so
everything the bot sends while answering a message sits between that message and your next
one. The plugin:

1. remembers your messages as they reach the gateway (`pre_gateway_dispatch`),
2. notes which agent turn each one started (`pre_llm_call`),
3. when you react (`gateway_platform_event`), works out whose message it was and which turn
   produced it,
4. adds the note to your next message in that same conversation (`pre_llm_call`).

Removing a reaction before your next message withdraws it. Changing it replaces it. Reactions
on your own messages are ignored. Feedback stays with the conversation it was about: after
`/new` it is not carried into the new one.

The note is added to the user message, never to the system prompt. Hermes saves the text
exactly as it was sent and replays it byte for byte in later turns, so the prompt cache is
not affected. This is checked in the tests, including when the history is reloaded from
`state.db`.

## Security and footprint

- **Registers:** three hooks (`pre_gateway_dispatch`, `pre_llm_call`,
  `gateway_platform_event`). `register()` does nothing else.
- **Never changes dispatch:** the `pre_gateway_dispatch` callback always returns `None`, so no
  message is dropped, rewritten or delayed.
- **Stores:** nothing on disk. State lives in the gateway's memory and is bounded:
  - 200 messages per chat, 20 pending reactions per chat, 48 hours for a pending reaction;
  - 256 conversations per profile, plus 32 for people who wrote to the bot but never started
    a turn.
  Pending feedback is lost when the gateway restarts.
- **Reads:** Telegram message text (a 60-character excerpt is used in the note), message ids
  and reaction emojis, as Hermes hands them to plugins. It sees only reactions from users the
  gateway has authorized.
- **Does not:** make network requests, start processes, read or write files, read secrets,
  run commands from reactions, start a turn on its own, or write to memory.
- **Profiles:** each profile, including multiplexed ones, has its own state.
- The excerpt and emojis are cleaned (control characters removed, length capped, text
  JSON-quoted) before they reach the model.

## Limits

- **Telegram private chats only.** Groups share one message sequence among many people, and
  Discord reactions are not yet given to plugins.
- **Private chats with topics** (Telegram threaded mode, `/topic`): reactions are ignored once
  more than one topic (or the main chat and a topic) has been used recently. Topics answer in
  parallel and share one message sequence, and Telegram does not say which topic a reaction is
  in, so the plugin cannot tell which conversation the reacted message came from. With a
  single topic it works as in a plain chat.
- **The window, not the exact message.** Hermes does not tell plugins which messages it sent,
  so the note says "a message you sent after …". That is almost always the reply, but it can
  also be a progress update or a cron delivery sent in the same window.
- **Messages sent while the agent is still working** are folded into the running turn and
  skip `pre_gateway_dispatch` (Hermes issue
  [#121976](https://github.com/NousResearch/hermes-agent/issues/121976)). The plugin does
  not see them, so a reaction on one of them is treated as a reaction on the agent's messages
  around it.
- **Batched messages:** when you send several quick messages that Hermes merges into one turn,
  only the first one's id is known.
- **Paid (⭐) reactions** carry no emoji, so they look like a cleared reaction.
- Reactions on messages older than what the plugin has seen since the gateway started are
  ignored rather than guessed.

## Changes

### 1.0.2

In a private chat with topics, a reaction on one topic's answer could be delivered to another
topic's conversation, when the two topics were answering at the same time. Such reactions are
now ignored (see Limits).

### 1.0.1

A reaction on the answer to a message with an `@file:`, `@url:` or other `@` reference was
dropped: Hermes adds the referenced content after the text you typed, so the plugin did not
recognise that turn as your message's. It now does.

### 1.0.0

First release.

## Development

```bash
python -m pytest tests                         # attribution rules, without Hermes
PYTHONPATH=/path/to/hermes-agent /path/to/hermes-agent/.venv/bin/python -m pytest tests
```

The second run also drives the real plugin manager and real `AIAgent` turns against Hermes'
`FakeLLMServer`. CI runs it against Hermes 0.21.5 and `main` every day. The design and the
Hermes facts it relies on are in [docs/DESIGN.md](https://github.com/AhmetArif0/hermes-reaction-feedback/blob/main/docs/DESIGN.md).

Related requests: NousResearch/hermes-agent#18408, #34660, #27438, #13942.

MIT licensed.
