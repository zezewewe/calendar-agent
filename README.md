# calendar-agent

Forward anything event-like to a Telegram bot — a typed note, a screenshot of a poster or ticket, a WhatsApp
chat — and it proposes calendar events. You tap **Add to calendar** and they land in Google Calendar.
Nothing is written until you confirm; conflicts with existing events are flagged; **Undo** removes what was added.

```
Telegram ─► Inbound ─► RouterExtractor (model-router, vision) ─► ProposedEvents
                                                         │
                       conflict check (Google Calendar) ◄┘
                                                         ▼
                              Proposal ─(you tap Confirm)─► create events
```

## Layout
| file | role |
|---|---|
| `models.py` | data types (Inbound, ProposedEvent, Proposal) |
| `extract.py` | LLM extraction via the OpenAI-compatible model-router; local-time parsing, one JSON repair retry |
| `agent.py` | propose / confirm / cancel / undo; channel-agnostic |
| `calendars/google.py` | Google Calendar adapter (`Calendar` protocol in `calendars/__init__.py`) |
| `channels/telegram.py` | Telegram front end; allow-listed users only |
| `store.py` | SQLite: pending proposals + created event ids for undo |

New channel (e.g. WhatsApp Cloud API) = build an `Inbound`, call `agent.propose`, call `agent.confirm`.
New calendar backend = implement the 3-method `Calendar` protocol.

## Setup
1. `pip install -e '.[google,telegram]'`
2. Google: create an OAuth *Desktop* client in Google Cloud Console, enable the Calendar API, save it as `client_secret.json`, then run `calendar-agent auth` once.
3. Telegram: create a bot with @BotFather; get your numeric user id (e.g. from @userinfobot).
4. `cp .env.example .env`, fill it in, then `set -a; . ./.env; set +a; calendar-agent`.
5. Pick `EXTRACT_MODEL` from model-router: it must accept images (`vision-free`, `vision-default`, `vision-claude-code`, ...).

Forwarding WhatsApp: in WhatsApp, long-press a message (or share a screenshot) → Share/Forward → Telegram → your bot.

## Tests
`pip install -e '.[dev]' && pytest` — uses fakes for the LLM and calendar, no network.

## Notes
- Times are resolved in `TIMEZONE`; the model returns local wall-clock time and the code attaches the zone.
- Events with no end get `default_duration_min` (60); all-day events span the day.
- Low-confidence guesses are marked "(unsure)" and listed under "Check:" so you can cancel and resend with more detail.
- Not built yet: editing a proposal in chat, recurring events, multiple calendars, reminders.
