# calendar-agent

Forward anything event-like to a Telegram bot — a typed note, a screenshot of a poster or ticket, a WhatsApp
chat — and it proposes calendar events. You tap **Add to calendar** and they land in a shared WebCalendar
(or, optionally, Google Calendar). Nothing is written until you confirm; conflicts with existing events are
flagged; **Undo** removes what was added.

```
Telegram ─► Inbound ─► RouterExtractor (model-router, vision) ─► ProposedEvents
                                                         │
                       conflict check (calendar backend) ◄┘
                                                         ▼
                              Proposal ─(you tap Confirm)─► create events + family-member tag
```

## Layout
| file | role |
|---|---|
| `models.py` | data types (Inbound, ProposedEvent, Proposal) |
| `extract.py` | LLM extraction via the OpenAI-compatible model-router; local-time parsing, one JSON repair retry |
| `agent.py` | propose / confirm / cancel / undo; channel-agnostic |
| `calendars/webcalendar.py` | Shared family calendar through WebCalendar's built-in MCP server |
| `calendars/google.py` | Optional Google Calendar adapter (`Calendar` protocol in `calendars/__init__.py`) |
| `calendars/registry.py` | email → calendar id, for the optional per-user Google backend |
| `calendars/peruser.py` | one agent account, one secondary calendar per user, shared to their Gmail |
| `channels/telegram.py` | Telegram front end; allow-listed users only |
| `channels/http.py` | HTTP front end for the ZeLabs gateway; per-user, verified by `X-Zelabs-Identity` |
| `identity.py` | verifies the gateway's `X-Auth-Email` / `X-Zelabs-Identity` (HMAC) |
| `pool.py` | builds an `Agent` bound to the caller's own calendar |
| `store.py` | SQLite: pending proposals + created event ids for undo |

New channel (e.g. WhatsApp Cloud API) = build an `Inbound`, call `agent.propose`, call `agent.confirm`.
New calendar backend = implement the 3-method `Calendar` protocol.

## Shared family calendar (WebCalendar)

Set `CALENDAR_BACKEND=webcalendar`. Create one WebCalendar account for the agent and share/layer its calendar
with the family users; every confirmed event uses that same account and calendar. The caller's Telegram id or
verified HTTP email is retained as a searchable description marker, for example:

```
Family member: Alice [family-member:alice@example.com]
```

`FAMILY_MEMBERS` is an optional JSON object mapping those caller ids to display names. Original event notes are
preserved. These are member tags, **not native WebCalendar participants**: the stock MCP `add_event` tool has no
participant/category argument. This is deliberate for the first shared, no-privacy version.

### Dedicated localhost Docker service

`compose.webcalendar.yml` runs pinned WebCalendar v1.9.27 with a persistent SQLite volume. It binds only to
`127.0.0.1:8002`, uses the non-admin `family` account for the shared calendar/MCP token, and restarts with Docker.
The admin and family UI passwords are generated during local provisioning and stored in the git-ignored `.env`.

```bash
# Start or update from the intended image.
docker compose -f compose.webcalendar.yml up -d

# Health, logs, stop, and restart.
docker compose -f compose.webcalendar.yml ps
docker compose -f compose.webcalendar.yml logs -f
docker compose -f compose.webcalendar.yml down
docker compose -f compose.webcalendar.yml up -d
```

`down` preserves the `calendar-agent-webcalendar_webcalendar-data` volume. Do not use `down -v` unless you intend
to erase the calendar. The WebCalendar UI is at <http://localhost:8002/>; use `WEBCALENDAR_AGENT_LOGIN` and
`WEBCALENDAR_AGENT_PASSWORD` from `.env` for the family calendar, or the corresponding admin credentials to manage it.

Enable WebCalendar's MCP server and MCP write access, then generate an API token for the agent user under
Preferences. Set `WEBCALENDAR_MCP_URL` to its `mcp.php` URL and `WEBCALENDAR_MCP_TOKEN` to that token. The WebCalendar
user's timezone preference must match `TIMEZONE`: writes are sent in GMT, while WebCalendar returns event times in
the user's local timezone. Keep the MCP endpoint authenticated even though calendar contents are shared.

## Per-user calendars with Google (alternative HTTP backend)

Set `CALENDAR_BACKEND=google` to use Google instead of WebCalendar.

`calendar-agent serve` runs an HTTP front end behind the [ZeLabs gateway](../local-harness/docs/zelabs.md)
that gives every user their own calendar without a per-user consent screen: the
single Google account above owns one secondary calendar per user and shares it
(ACL role `SHARE_ROLE`, default `writer`) with that user's Gmail, so events show
up inside *their* Google Calendar. `calendars/registry.py` remembers the mapping.

The caller is identified by the email Cloudflare Access puts in `X-Auth-Email`,
trusted only when the per-route `X-Zelabs-Identity` HMAC checks out against the
gateway's master key (`~/.ows/zelabs.identity.key`). Each request is served by an
`Agent` bound to that user's calendar, sharing the one `Store` and extractor.

```bash
calendar-agent auth          # once: the shared agent Google account
calendar-agent serve         # http://127.0.0.1:8099, route id "calendar"
calendar-agent serve --insecure   # local testing only: trust X-Auth-Email as-is
```

Endpoints: `GET /api/health`, `POST /api/{propose,revise,confirm,cancel,undo}`.
Then register the route in `zelabs/{routes.json,apps.json,policy.json}` and add a
Calendar tab. Undo is scoped to the requesting user.

## Setup
1. Install WebCalendar v1.9.27 or a later security release, configure its shared agent account, and enable MCP/read-write access as above.
2. `pip install -e '.[telegram]'` (add the `google` extra only when using the Google backend).
3. Telegram: create a bot with @BotFather; get your numeric user id (e.g. from @userinfobot).
4. `cp .env.example .env`, fill it in, then `set -a; . ./.env; set +a; calendar-agent`.
5. For Google instead: create an OAuth *Desktop* client in Google Cloud Console, enable the Calendar API, save it as `client_secret.json`, then run `calendar-agent auth` once.
6. Pick `EXTRACT_MODEL` from model-router: it must accept images (`vision-free`, `vision-default`, `vision-claude-code`, ...).

Forwarding WhatsApp: in WhatsApp, long-press a message (or share a screenshot) → Share/Forward → Telegram → your bot.

## Tests
`pip install -e '.[dev]' && pytest` — uses fakes for the LLM and calendar, no network.

## Notes
- Times are resolved in `TIMEZONE`; the model returns local wall-clock time and the code attaches the zone.
- Events with no end get `default_duration_min` (60); all-day events span the day.
- Low-confidence guesses are marked "(unsure)" and listed under "Check:" so you can cancel and resend with more detail.
- Reply to a proposal message to edit it ("make it 8pm", "drop the second one"); the proposal is re-checked for conflicts.
- Not built yet: recurring events, multiple calendars, reminders.
