import io
import logging
import os

import pandas as pd
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Config via environment variables
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
MAX_RESULTS = int(os.environ.get("MAX_RESULTS", "20"))       # rows shown per search
MAX_FILE_SIZE_MB = int(os.environ.get("MAX_FILE_SIZE_MB", "20"))

CHAT_DF_KEY = "df"          # key used in chat_data to store the active DataFrame
CHAT_FILENAME_KEY = "filename"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Hi! Send me a CSV file and I'll load it.\n"
        "After that, just type any word or phrase and I'll search every column "
        "for rows that match.\n\n"
        "Commands:\n"
        "/status — show which file is loaded\n"
        "/columns — list the columns in the loaded file\n"
        "/clear — forget the currently loaded file\n"
        "/help — show this message"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    df = context.chat_data.get(CHAT_DF_KEY)
    if df is None:
        await update.message.reply_text("No CSV loaded yet. Send me one to get started.")
        return
    filename = context.chat_data.get(CHAT_FILENAME_KEY, "unknown.csv")
    await update.message.reply_text(
        f"📄 Loaded: {filename}\n"
        f"Rows: {len(df)}\n"
        f"Columns: {len(df.columns)}"
    )


async def columns(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    df = context.chat_data.get(CHAT_DF_KEY)
    if df is None:
        await update.message.reply_text("No CSV loaded yet. Send me one first.")
        return
    cols = "\n".join(f"• {c}" for c in df.columns)
    await update.message.reply_text(f"Columns:\n{cols}")


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.chat_data.pop(CHAT_DF_KEY, None)
    context.chat_data.pop(CHAT_FILENAME_KEY, None)
    await update.message.reply_text("Cleared. Send a new CSV whenever you're ready.")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    doc = update.message.document
    filename = doc.file_name or ""

    if not filename.lower().endswith(".csv"):
        await update.message.reply_text(
            "That doesn't look like a .csv file. Please send a file ending in .csv."
        )
        return

    size_mb = doc.file_size / (1024 * 1024) if doc.file_size else 0
    if size_mb > MAX_FILE_SIZE_MB:
        await update.message.reply_text(
            f"That file is {size_mb:.1f} MB, which is over my {MAX_FILE_SIZE_MB} MB limit."
        )
        return

    try:
        tg_file = await doc.get_file()
        file_bytes = await tg_file.download_as_bytearray()
        df = pd.read_csv(io.BytesIO(file_bytes))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to parse CSV")
        await update.message.reply_text(f"Sorry, I couldn't parse that CSV: {exc}")
        return

    if df.empty:
        await update.message.reply_text("That CSV loaded but appears to have no rows.")
        return

    context.chat_data[CHAT_DF_KEY] = df
    context.chat_data[CHAT_FILENAME_KEY] = filename

    await update.message.reply_text(
        f"✅ Loaded {filename} — {len(df)} rows, {len(df.columns)} columns.\n"
        "Type anything to search it."
    )



# Phrases people naturally use before a search term; stripped before matching.
_SEARCH_PREFIXES = (
    "search for", "search", "find", "look up", "lookup", "look for",
    "get", "show me", "show",
)


def _strip_search_prefix(text: str) -> str:
    lowered = text.lower()
    for prefix in sorted(_SEARCH_PREFIXES, key=len, reverse=True):
        if lowered.startswith(prefix + " "):
            return text[len(prefix):].strip()
        if lowered == prefix:
            return ""
    return text


def _format_row_block(row: pd.Series) -> str:
    return "\n".join(f"*{col}:* {row[col]}" for col in row.index)


def _format_results(df: pd.DataFrame, max_rows: int) -> str:
    total = len(df)
    shown = df.head(max_rows)

    blocks = [_format_row_block(row) for _, row in shown.iterrows()]
    text = "\n\n---\n\n".join(blocks)
    if total > max_rows:
        text += f"\n\n…and {total - max_rows} more row(s) not shown."
    return text


async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw_text = update.message.text.strip()
    if not raw_text:
        return

    query = _strip_search_prefix(raw_text)
    if not query:
        await update.message.reply_text("What would you like me to search for?")
        return

    df = context.chat_data.get(CHAT_DF_KEY)
    if df is None:
        await update.message.reply_text(
            "No CSV loaded yet. Send me a .csv file first, then search away."
        )
        return

    # Exact match first (case-insensitive, whole cell value) — this is what
    # you want for IDs/codes like "A169372" so you get precisely that record.
    exact_mask = df.apply(
        lambda col: col.astype(str).str.lower() == query.lower()
    ).any(axis=1)
    results = df[exact_mask]
    match_type = "exact"

    # Fall back to substring search if no exact match anywhere.
    if results.empty:
        substring_mask = df.apply(
            lambda col: col.astype(str).str.contains(
                query, case=False, na=False, regex=False
            )
        ).any(axis=1)
        results = df[substring_mask]
        match_type = "partial"

    if results.empty:
        await update.message.reply_text(f"No rows matched “{query}”.")
        return

    label = "exact match" if match_type == "exact" else "partial match(es)"
    reply = f"🔎 {len(results)} {label} for “{query}”:\n\n" + _format_results(
        results, MAX_RESULTS
    )

    # Telegram messages cap at 4096 chars; trim just in case.
    if len(reply) > 4000:
        reply = reply[:4000] + "\n\n…(truncated)"

    try:
        await update.message.reply_text(reply, parse_mode=ParseMode.MARKDOWN)
    except Exception:  # noqa: BLE001 - e.g. a cell value breaks Markdown syntax
        plain_reply = reply.replace("*", "")
        await update.message.reply_text(plain_reply)


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit(
            "Set the TELEGRAM_BOT_TOKEN environment variable before running the bot."
        )

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("columns", columns))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search))

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
