# CSV Search Telegram Bot

A Telegram bot that loads a CSV file you send it, then lets you search across
every column just by typing a word or phrase — with a polished, card-style
interface.

## How it works

1. Send the bot a `.csv` file.
2. It parses the file with pandas and keeps it in memory, per chat.
3. Send any text message — the bot searches all columns (case-insensitive,
   substring match), shows an exact match first if there is one, and replies
   with nicely formatted result cards.
4. If there are more matches than fit on one page, use the
   **◀️ Previous / Next ▶️** buttons under the message to page through them.
5. Send a new CSV any time to replace the loaded one.

Commands (also available via Telegram's `/` command menu):
- `/start` or `/help` — usage info
- `/status` — shows the loaded file, row/column counts, and how long ago it was loaded
- `/columns` — lists all available fields
- `/clear` — unloads the current file and clears any active search

## Setup

1. **Create a bot and get a token**
   - Message [@BotFather](https://t.me/BotFather) on Telegram
   - Send `/newbot` and follow the prompts
   - Copy the token it gives you

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set your bot token**
   ```bash
   export TELEGRAM_BOT_TOKEN="123456789:your-token-here"
   ```

4. **Run the bot**
   ```bash
   python bot.py
   ```

The bot uses long polling, so no public URL or webhook setup is needed —
just run it and message your bot on Telegram.

## Configuration (optional environment variables)

| Variable            | Default | Description                                  |
|---------------------|---------|-----------------------------------------------|
| `TELEGRAM_BOT_TOKEN`| —       | Required. Your bot token from BotFather.      |
| `MAX_RESULTS`       | 10      | Rows shown per page of search results.        |
| `MAX_FILE_SIZE_MB`  | 20      | Max CSV size accepted.                        |
| `ALLOWED_USER_IDS`  | (none)  | Comma-separated Telegram user IDs allowed to use the bot. Leave empty to allow anyone. |

### Restricting the bot to one person

1. Find your Telegram numeric user ID — message [@userinfobot](https://t.me/userinfobot) and it'll reply with your ID.
2. Set the environment variable:
   ```bash
   export ALLOWED_USER_IDS="123456789"
   ```
   For multiple people, separate with commas: `"123456789,987654321"`.
3. Restart the bot. Anyone not on the list gets an "Access denied" message for every command and message.

## Interface notes

- Messages use Telegram's HTML formatting (bold labels, monospace values,
  divider lines) for a cleaner, more "dashboard"-like look than plain text.
- All user-provided and CSV-cell text is HTML-escaped before being sent, so a
  cell containing `<`, `>`, or `&` won't break formatting or get interpreted
  as markup.
- The bot shows a "typing…" indicator while parsing an upload or running a
  search, so it doesn't feel unresponsive on larger files.
- The command menu (the `/` button in Telegram's chat UI) is registered
  automatically on startup — no need to configure it manually via BotFather.

## Notes / limitations

- Data is stored **in memory only**, per chat — restarting the bot clears
  all loaded CSVs. For persistence across restarts, you'd want to save the
  DataFrame to disk (e.g. as a pickle or parquet file) keyed by chat ID.
- Search is a simple substring match across all columns (not a fuzzy search
  or query language).
- One CSV is kept per chat, not per user — if the bot is in a group, group
  members share the same loaded file.
- Telegram bot messages are capped at 4096 characters, so very large result
  sets are paginated rather than shown all at once.
