import functools
import html
import io
import logging
import os
import time
from typing import Optional

import pandas as pd
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
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
MAX_RESULTS = int(os.environ.get("MAX_RESULTS", "10"))       # rows shown per page
MAX_FILE_SIZE_MB = int(os.environ.get("MAX_FILE_SIZE_MB", "20"))

# Comma-separated Telegram user IDs allowed to use the bot, e.g. "123456789,987654321"
# Leave unset/empty to allow everyone (not recommended for private data).
_allowed_ids_raw = os.environ.get("ALLOWED_USER_IDS", "")
ALLOWED_USER_IDS = {
    int(uid.strip()) for uid in _allowed_ids_raw.split(",") if uid.strip()
}

CHAT_DF_KEY = "df"
CHAT_FILENAME_KEY = "filename"
CHAT_LOADED_AT_KEY = "loaded_at"
CHAT_LAST_RESULTS_KEY = "last_results"
CHAT_LAST_QUERY_KEY = "last_query"
CHAT_LAST_OFFSET_KEY = "last_offset"

DIVIDER = "▬" * 18


def _esc(value) -> str:
    """HTML-escape any cell value / user input before putting it in a message."""
    return html.escape(str(value))


def restricted(handler):
    """Decorator that blocks the wrapped handler for anyone not in ALLOWED_USER_IDS."""

    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if ALLOWED_USER_IDS and (user is None or user.id not in ALLOWED_USER_IDS):
            logger.warning("Blocked access from user_id=%s", user.id if user else "unknown")
            if update.effective_message:
                await update.effective_message.reply_text(
                    "🔒 <b>Access denied.</b> You're not authorized to use this bot.",
                    parse_mode=ParseMode.HTML,
                )
            return
        return await handler(update, context)

    return wrapper


@restricted
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "🗂️ <b>CSV Search Bot</b>\n"
        f"{DIVIDER}\n"
        "Upload a <code>.csv</code> file and query it like a database — "
        "right here in chat.\n\n"
        "<b>Getting started</b>\n"
        "📎 Send a .csv file to load it\n"
        "🔎 Type any word, name, or ID to search\n"
        "◀️ ▶️ Use the buttons to page through results\n\n"
        "<b>Commands</b>\n"
        "/status — dataset currently loaded\n"
        "/columns — list available fields\n"
        "/clear — unload the current file\n"
        "/help — show this message"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


@restricted
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    df = context.chat_data.get(CHAT_DF_KEY)
    if df is None:
        await update.message.reply_text(
            "📭 <b>No dataset loaded.</b>\nSend me a .csv file to get started.",
            parse_mode=ParseMode.HTML,
        )
        return

    filename = context.chat_data.get(CHAT_FILENAME_KEY, "unknown.csv")
    loaded_at = context.chat_data.get(CHAT_LOADED_AT_KEY)
    age = ""
    if loaded_at:
        mins = int((time.time() - loaded_at) / 60)
        age = f"{mins} min ago" if mins < 60 else f"{mins // 60}h {mins % 60}m ago"

    text = (
        "📊 <b>Dataset Status</b>\n"
        f"{DIVIDER}\n"
        f"<b>File:</b>    <code>{_esc(filename)}</code>\n"
        f"<b>Rows:</b>    {len(df):,}\n"
        f"<b>Columns:</b> {len(df.columns)}\n"
    )
    if age:
        text += f"<b>Loaded:</b>  {age}\n"

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


@restricted
async def columns(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    df = context.chat_data.get(CHAT_DF_KEY)
    if df is None:
        await update.message.reply_text(
            "📭 <b>No dataset loaded.</b>\nSend me a .csv file first.",
            parse_mode=ParseMode.HTML,
        )
        return

    rows = "\n".join(f"  <code>{i+1:>2}.</code> {_esc(c)}" for i, c in enumerate(df.columns))
    text = f"🧩 <b>Available Fields</b>\n{DIVIDER}\n{rows}"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


@restricted
async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    had_file = context.chat_data.pop(CHAT_DF_KEY, None) is not None
    context.chat_data.pop(CHAT_FILENAME_KEY, None)
    context.chat_data.pop(CHAT_LOADED_AT_KEY, None)
    context.chat_data.pop(CHAT_LAST_RESULTS_KEY, None)
    context.chat_data.pop(CHAT_LAST_QUERY_KEY, None)
    context.chat_data.pop(CHAT_LAST_OFFSET_KEY, None)

    msg = "🗑️ <b>Cleared.</b> Send a new .csv whenever you're ready." if had_file else \
          "Nothing was loaded — send me a .csv file to get started."
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


@restricted
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    doc = update.message.document
    filename = doc.file_name or ""

    if not filename.lower().endswith(".csv"):
        await update.message.reply_text(
            "⚠️ That doesn't look like a .csv file. Please send a file ending in <code>.csv</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    size_mb = doc.file_size / (1024 * 1024) if doc.file_size else 0
    if size_mb > MAX_FILE_SIZE_MB:
        await update.message.reply_text(
            f"⚠️ That file is {size_mb:.1f} MB, over the {MAX_FILE_SIZE_MB} MB limit.",
            parse_mode=ParseMode.HTML,
        )
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    try:
        tg_file = await doc.get_file()
        file_bytes = await tg_file.download_as_bytearray()
        df = pd.read_csv(io.BytesIO(file_bytes))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to parse CSV")
        await update.message.reply_text(
            f"❌ <b>Couldn't parse that CSV.</b>\n<code>{_esc(exc)}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if df.empty:
        await update.message.reply_text(
            "⚠️ That CSV loaded but appears to have no rows.", parse_mode=ParseMode.HTML
        )
        return

    context.chat_data[CHAT_DF_KEY] = df
    context.chat_data[CHAT_FILENAME_KEY] = filename
    context.chat_data[CHAT_LOADED_AT_KEY] = time.time()

    preview_cols = ", ".join(_esc(c) for c in df.columns[:6])
    if len(df.columns) > 6:
        preview_cols += ", …"

    text = (
        "✅ <b>Dataset Loaded</b>\n"
        f"{DIVIDER}\n"
        f"<b>File:</b>    <code>{_esc(filename)}</code>\n"
        f"<b>Rows:</b>    {len(df):,}\n"
        f"<b>Columns:</b> {len(df.columns)}\n"
        f"<b>Fields:</b>  {preview_cols}\n\n"
        "🔎 Type anything to search it."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


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


def _format_row_block(row: pd.Series, index: int) -> str:
    lines = [f"<b>▸ Result #{index}</b>"]
    for col in row.index:
        lines.append(f"   <b>{_esc(col)}:</b> <code>{_esc(row[col])}</code>")
    return "\n".join(lines)


def _format_page(df: pd.DataFrame, query: str, page: int, page_size: int) -> str:
    total = len(df)
    start = page * page_size
    shown = df.iloc[start: start + page_size]
    total_pages = max(1, -(-total // page_size))  # ceiling division

    blocks = [
        _format_row_block(row, start + i + 1) for i, (_, row) in enumerate(shown.iterrows())
    ]
    header = (
        f"🔎 <b>{total:,}</b> match(es) for “<b>{_esc(query)}</b>”"
        f"  ·  page {page + 1}/{total_pages}\n{DIVIDER}\n\n"
    )
    return header + f"\n{DIVIDER}\n\n".join(blocks)


def _pagination_keyboard(page: int, total_pages: int) -> Optional[InlineKeyboardMarkup]:
    if total_pages <= 1:
        return None
    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton("◀️ Previous", callback_data=f"page:{page - 1}"))
    buttons.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        buttons.append(InlineKeyboardButton("Next ▶️", callback_data=f"page:{page + 1}"))
    return InlineKeyboardMarkup([buttons])


async def _send_results_page(message_or_query, context: ContextTypes.DEFAULT_TYPE, page: int, edit: bool) -> None:
    results = context.chat_data.get(CHAT_LAST_RESULTS_KEY)
    query = context.chat_data.get(CHAT_LAST_QUERY_KEY, "")
    total = len(results)
    total_pages = max(1, -(-total // MAX_RESULTS))
    page = max(0, min(page, total_pages - 1))

    text = _format_page(results, query, page, MAX_RESULTS)
    if len(text) > 4000:
        text = text[:4000] + "\n\n…(truncated)"
    markup = _pagination_keyboard(page, total_pages)

    context.chat_data[CHAT_LAST_OFFSET_KEY] = page

    if edit:
        await message_or_query.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    else:
        await message_or_query.reply_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)


@restricted
async def handle_page_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query_cb = update.callback_query

    if query_cb.data == "noop":
        await query_cb.answer()
        return

    await query_cb.answer()

    results = context.chat_data.get(CHAT_LAST_RESULTS_KEY)
    if results is None:
        await query_cb.edit_message_text("⌛ This search has expired. Please search again.")
        return

    try:
        page = int(query_cb.data.split(":", 1)[1])
    except (IndexError, ValueError):
        return

    await _send_results_page(query_cb, context, page, edit=True)


@restricted
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
            "📭 <b>No dataset loaded.</b>\nSend me a .csv file first, then search away.",
            parse_mode=ParseMode.HTML,
        )
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    substring_mask = df.apply(
        lambda col: col.astype(str).str.contains(query, case=False, na=False, regex=False)
    ).any(axis=1)
    results = df[substring_mask]

    if results.empty:
        await update.message.reply_text(
            f"🔍 No rows matched “<b>{_esc(query)}</b>”.", parse_mode=ParseMode.HTML
        )
        return

    exact_mask = results.apply(
        lambda col: col.astype(str).str.lower() == query.lower()
    ).any(axis=1)
    ordered_results = pd.concat([results[exact_mask], results[~exact_mask]])

    context.chat_data[CHAT_LAST_RESULTS_KEY] = ordered_results
    context.chat_data[CHAT_LAST_QUERY_KEY] = query
    context.chat_data[CHAT_LAST_OFFSET_KEY] = 0

    await _send_results_page(update.message, context, page=0, edit=False)


async def _post_init(app: Application) -> None:
    """Registers the '/' command menu shown in Telegram's UI."""
    await app.bot.set_my_commands(
        [
            BotCommand("start", "Show welcome message & instructions"),
            BotCommand("help", "Show welcome message & instructions"),
            BotCommand("status", "Show the currently loaded dataset"),
            BotCommand("columns", "List available fields"),
            BotCommand("clear", "Unload the current dataset"),
        ]
    )


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit(
            "Set the TELEGRAM_BOT_TOKEN environment variable before running the bot."
        )

    app = Application.builder().token(BOT_TOKEN).post_init(_post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("columns", columns))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CallbackQueryHandler(handle_page_button, pattern=r"^(page:\d+|noop)$"))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search))

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
