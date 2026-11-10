# Calendar — user guide

A shared family calendar you drive from a Telegram bot (**@zelabsbot**). Type or forward anything
event-like; the bot proposes the change and you tap once to apply it. **Nothing is saved until you
confirm.**

## Adding an event

- Type it: send `Dentist Friday 3pm` (or `Lunch with Sam tomorrow 12:30`).
- Or forward something: a screenshot of a poster/ticket, a WhatsApp chat, or any photo of a date.
- The bot replies with a card. Tap **Add to calendar** to save it, or **Cancel** to discard.
- Got a detail wrong? **Reply to the card** to adjust it — `make it 8pm`, `move it to Saturday`.

## Changing or removing events

Just describe the change; the bot looks up the real event first, then proposes it:

- `Move my gym session to 8am Saturday`
- `Delete the standups`
- `Change dinner to 7:30`

Tap **Apply changes** to confirm. After anything is applied you get an **Undo** button that reverses it.

## Repeating events

- `Every Saturday in November, 10am`
- `Standup every weekday at 9`

This creates one repeating series. To skip or change a single occurrence, edit it in the calendar UI.

## Asking questions

- `What's on next Saturday?` → a short answer, e.g. "You have Gym at 10am and Dinner at 7pm."

## Seeing the calendar

- `/agenda` — the next 7 days
- `/list` — the next 30 days
- `/calendar` — opens the visual calendar (month, week and agenda views) inside Telegram
- or open the web calendar in a browser (see below).

## Commands

| command | what it does |
| --- | --- |
| `/help` | this guide, in chat |
| `/agenda` | events for the next 7 days |
| `/list` | events for the next 30 days |
| `/calendar` | open the visual calendar |
| `/stop` | stop the bot replying to you |

## Good to know

- **Nothing is saved until you confirm** — you always see the proposal first.
- **Conflicts are flagged**: if a new event overlaps an existing one, the card warns you before you confirm.
- **"Unsure" guesses** are marked `(unsure)` and listed under **Check:** — cancel and resend with more
  detail if the bot guessed wrong.
- **Times** are in the family timezone (e.g. `Asia/Singapore`); you can write times plainly ("3pm", "noon").
- **It's shared**: events land on one family calendar, and each event is tagged with who added it.
- **Only invited members** can use the bot.

## The web calendar

Open the calendar in a browser for a full month/week view, a detail pop-up on each event, and Edit/Delete
buttons. The same calendar the bot writes to:

`http://localhost:3400`
