# calendar-agent

Forward anything event-like to a Telegram bot — a typed note, a screenshot of a poster or ticket, a WhatsApp
chat — and it proposes calendar changes (create, update, delete, or a plain answer). You tap **Apply** and they
land in a shared WebCalendar. Nothing is written until you confirm; conflicts with existing events are flagged;
**Undo** reverses what was applied.

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
| `models.py` | data types (Inbound, ProposedEvent, Proposal, ExistingEvent) |
| `extract.py` | LLM extraction via the OpenAI-compatible model-router; intent + change-set planning, local-time parsing, one JSON repair retry |
| `recurrence.py` | builds the RFC 5545 RRULE ("FREQ=WEEKLY;BYDAY=SA;UNTIL=…") handed to the backend's `add_recurring_event` |
| `agent.py` | propose / confirm / cancel / undo; channel-agnostic |
| `calendars/webcalendar.py` | Shared family calendar through WebCalendar's built-in MCP server |
| `channels/telegram.py` | Telegram front end; allow-listed users only |
| `channels/http.py` | HTTP front end for the ZeLabs gateway; verified by `X-Zelabs-Identity` |
| `mcp.py` | MCP server (stdio) wrapping the same Agent (`calendar-agent mcp`) |
| `identity.py` | verifies the gateway's `X-Auth-Email` / `X-Zelabs-Identity` (HMAC) |
| `pool.py` | builds an `Agent` per caller from the shared calendar + store |
| `store.py` | SQLite: pending proposals + a change journal for undo |

New channel (e.g. WhatsApp Cloud API) = build an `Inbound`, call `agent.propose`, call `agent.confirm`.
New front end = reuse the same `Agent`; `mcp.py` shows the minimal shape.

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

`compose.webcalendar.yml` builds the hardened WebCalendar derivative from `Dockerfile.webcalendar` (pinned
v1.9.27 base) with a persistent SQLite volume. It binds only to `127.0.0.1:8002`, uses the non-admin `family`
account for the shared calendar/MCP token, and restarts with Docker. The admin and family UI passwords are
generated during local provisioning and stored in the git-ignored `.env`.

The image hardening applies the findings from the security audit: Debian security updates at build time, the
installer/tools/docs and `security_audit.php` denied over HTTP, the HTML/TinyMCE description editor disabled and
its assets removed, a read-only root filesystem, all Linux capabilities dropped except the ones Apache needs,
and `no-new-privileges`.

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

Upgrading an existing installation to the hardened image also needs the editor setting flipped in its database
(the new default only seeds fresh installs):

```bash
docker compose -f compose.webcalendar.yml exec -u www-data webcalendar \
  php bin/webcal.php config set ALLOW_HTML_DESCRIPTION N
```

Enable WebCalendar's MCP server and MCP write access, then generate an API token for the agent user under
Preferences. Set `WEBCALENDAR_MCP_URL` to its `mcp.php` URL and `WEBCALENDAR_MCP_TOKEN` to that token. The WebCalendar
user's timezone preference must match `TIMEZONE`: writes are sent in GMT, while WebCalendar returns event times in
the user's local timezone. Keep the MCP endpoint authenticated even though calendar contents are shared.

## HTTP channel and MCP server

`calendar-agent serve` runs an HTTP front end behind the [ZeLabs gateway](../local-harness/docs/zelabs.md).
The caller is identified by the email Cloudflare Access puts in `X-Auth-Email`, trusted only when the
per-route `X-Zelabs-Identity` HMAC checks out against the gateway's master key (`~/.ows/zelabs.identity.key`).
Everyone shares the one WebCalendar.

```bash
calendar-agent serve         # http://127.0.0.1:8099, route id "calendar"
calendar-agent serve --insecure   # local testing only: trust X-Auth-Email as-is
calendar-agent mcp           # MCP server (stdio) wrapping the same Agent
```

Endpoints: `GET /api/health`, `POST /api/{propose,revise,confirm,cancel,undo}`.
Register the route in `zelabs/{routes.json,apps.json,policy.json}` and add a Calendar tab.

`calendar-agent mcp` speaks MCP over stdin/stdout and exposes `list_events`, `propose_change`, `confirm`,
`undo` and `agenda`, so an MCP client (Claude, the ows hub) drives the same Agent under the same
propose → confirm safety.

## Setup
1. Install WebCalendar v1.9.27 or a later security release, configure its shared agent account, and enable MCP/read-write access as above.
2. `pip install -e '.[telegram]'`.
3. Telegram: create a bot with @BotFather; get your numeric user id (e.g. from @userinfobot).
4. `cp .env.example .env`, fill it in, then `set -a; . ./.env; set +a; calendar-agent`.
5. Pick `EXTRACT_MODEL` from model-router: it must accept images (`vision-free`, `vision-default`, `vision-claude-code`, ...).

Forwarding WhatsApp: in WhatsApp, long-press a message (or share a screenshot) → Share/Forward → Telegram → your bot.

## Deploying (interim testing)

**In WebCalendar**, enable the MCP server and write access (**Admin → System Settings → MCP Server**:
`MCP_SERVER_ENABLED=Y`, `MCP_WRITE_ACCESS=Y` — add/recurring/update/delete are refused without the latter),
generate a token for the shared `family` account under **Preferences**, and set that user's timezone to
match `TIMEZONE`. The agent uses `list_events`, `add_event`, `add_recurring_event`, `update_event`,
`delete_event`, `check_conflicts` and `get_availability`.

**`.env`** needs at least: `CALENDAR_BACKEND=webcalendar`, `WEBCALENDAR_MCP_URL`,
`WEBCALENDAR_MCP_TOKEN`, `ROUTER_URL` / `ROUTER_API_KEY` / `EXTRACT_MODEL`, `TIMEZONE`,
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USERS`, `STATE_DB`. Optional: `CALENDAR_WEB_URL` (Mini App menu
button) and `MCP_USER_ID`.

Run any of the three front ends against the same Agent:

```bash
calendar-agent          # Telegram bot (long polling)
calendar-agent serve    # HTTP channel behind the ZeLabs gateway (127.0.0.1:8099)
calendar-agent mcp      # MCP server on stdio (Claude / the ows hub)
```

## Tests
`pip install -e '.[dev]' && pytest` — uses fakes for the LLM and calendar, no network.

## Notes
- Times are resolved in `TIMEZONE`; the model returns local wall-clock time and the code attaches the zone.
- Events with no end get `default_duration_min` (60); all-day events span the day.
- Low-confidence guesses are marked "(unsure)" and listed under "Check:" so you can cancel and resend with more detail.
- Reply to a proposal message to edit it ("make it 8pm", "drop the second one"); the proposal is re-checked for conflicts.
- Recurring plans ("every Saturday in November") become one recurring event: `recurrence.py` turns the
  model's weekday rule into an RRULE, and WebCalendar's `add_recurring_event` owns the series (with its
  own UI and .ics exceptions). The model never lists the dates itself.
- The model first classifies `intent`: **create**, **update**, **delete** or **query**. For anything
  but create the agent lists the real events in the range and asks the model which to change, so
  "move my Saturday gym to 8am" or "delete the standups" produce a change set against real event ids
  (never invented). `confirm` applies it; `undo` reverses it.
- Conflict warnings come from WebCalendar's own `check_conflicts` (`Calendar.find_conflicts`); updates
  use its `update_event`. `Calendar.busy_blocks` (`get_availability`) is exposed as the `availability`
  tool on the agent's MCP server.
- The bot has `/agenda` (next 7 days), `/list` (next 30 days) and `/calendar` (opens the web UI as a
  Telegram Mini App). Creates are grounded with the events already near that time, so they don't duplicate.
- A `query` ("what's on next Saturday?") gets a one-line prose answer plus the matching events.

## Roadmap
- **One WebCalendar client.** Route calendar-web's writes through this agent's HTTP API so the
  date/time/GMT/member-tagging logic lives in one place instead of a second client.
- **Web assistant panel** that reuses the same proposal card, and an optional rendered month image for
  Telegram clients that cannot use the Mini App.
- Still unbuilt: multiple calendars, reminders.
