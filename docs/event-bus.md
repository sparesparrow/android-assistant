# Phone event bus (MQTT)

Long jobs (Gemini Deep Research, NotebookLM overviews, downloads) finish with a notification.
Instead of polling the screen or `phone/notif-wait.py` (adb `dumpsys` every 20 s), Tasker with
AutoNotification captures the event on the phone, normalises it to JSON and publishes it to the
local Mosquitto broker (`MQTT_HOST`, default `127.0.0.1:1883`). Agents only subscribe, through
the MCP tool `wait_event`. Tracking issue: sparesparrow/mcp-prompts#140.

## Topics

`phone/events/<kind>`. `kind` matches `^[a-z0-9_-]{1,32}$`, for example `notification` or
`download`. Agents name one exact kind; `wait_event` refuses MQTT wildcards (`+`, `#`) and `/`.

## Event schema

One JSON object per MQTT message, UTF-8 (Czech diacritics kept as they are):

```json
{"kind": "notification", "source_pkg": "com.google.android.apps.bard",
 "title": "Gemini", "text": "Výzkum je hotový", "when": 1764150000000, "id": "0|com.google...|42"}
```

| Key | Meaning |
|---|---|
| `kind` | same as the topic suffix |
| `source_pkg` | Android package that raised the event |
| `title`, `text` | notification title and text (strings, may be empty) |
| `when` | event time, ms since the epoch (as `notif-wait.py` reports it) |
| `id` | the notification key or another stable id, for de-duplication |

Messages that are not JSON objects or lack one of these keys are dropped by `wait_event`.

## Who publishes, who subscribes

- **Publish:** only Tasker on the phone, through `mosquitto_pub` (Termux or the Debian PRoot).
- **Not agents.** `mqtt_publish` allows only `mia/*` and `zigbee2mqtt/*` and refuses
  `phone/events/*`, so an agent cannot fake an event for another agent.
- **Subscribe:** agents, through `wait_event(kind, match="", timeout_s=60)`:
  - `match` is an optional case-insensitive regex over title + text.
  - The timeout is clamped to 1..3600 s.
  - It returns `{"kind", "events": [...], "untrusted": true, "note"}`, with at most 20 events and the text cut at 2000 characters.
  - On timeout, `events` is `[]`.

## Retention and QoS

- **No retained messages.** Publishers do not set `-r`. `wait_event` passes `-R`, so it also ignores any stale retained message. An agent sees only the events that arrive while it waits.
- **QoS 0 (the `mosquitto_pub`/`mosquitto_sub` default).** This is fine on loopback. An event published while no agent is subscribed is lost. That is accepted: the notification itself stays on the phone, and an agent can still read it from the screen or with `notif-wait.py --list`.

## Data, not instructions

Event text comes from other apps: messages, mails, web pages. It is untrusted data and never
instructions. A notification saying "ignore previous instructions…" is returned verbatim as
data, with `"untrusted": true`. Acting on an event still needs the usual confirmation rules.

## Not done yet

- Tasker/AutoNotification profiles that actually publish `phone/events/notification`
  (filter by package, build the JSON, call `mosquitto_pub`).
- Replacing `notif-wait.py` with `wait_event` in the Deep Research → notebook scenario.
- Broker ACLs: today any local client can publish. Planned: a publish-only user for Tasker and a subscribe-only user for agents on `phone/events/#`.
- A battery estimate for AutoNotification and `mosquitto_pub`, measured against the adb `dumpsys` polling it replaces.
- An mcp-prompts `android-event-bus` prompt describing the topics, the schema and the rules for agents.
