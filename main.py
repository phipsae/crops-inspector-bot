from __future__ import annotations

import asyncio
import logging
import re
import time

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from html import escape as html_escape

import ai_backend
import config
import database
import formatter
import github_fetcher

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def start_command(update: Update, context):
    await update.message.reply_text(
        "<b>CROPS Inspector</b>\n\n"
        "I score Ethereum projects against the "
        "<b>CROPS</b> framework (Censorship Resistance, Open Source, Privacy, Security).\n\n"
        "Send me a project name like:\n"
        "- Aave\n"
        "- MetaMask\n"
        "- Uniswap\n"
        "- Lido\n\n"
        "Type /help for more info.",
        parse_mode="HTML",
    )


async def help_command(update: Update, context):
    await update.message.reply_text(
        "<b>How to use</b>\n\n"
        "Send any Ethereum project name and I'll score it against the "
        "CROPS framework.\n\n"
        "<b>Examples:</b>\n"
        "- Aave\n"
        "- Uniswap\n"
        "- MetaMask\n"
        "- Lido\n"
        "- Tornado Cash\n"
        "- USDC\n"
        "- Gnosis Safe\n\n"
        "<b>CROPS</b> = Censorship Resistance, Open Source, Privacy, Security.\n"
        "Each property is scored Pass/Weak/Fail with a numerical 0-10 score.",
        parse_mode="HTML",
    )


_NON_PROJECT = [
    "hello", "hi ", "hi!", "hey", "thanks", "thank you", "ok", "bye",
    "what is", "what's", "how do", "how does",
    "help", "explain", "tell me about crops", "what does",
]

GUIDANCE_MSG = (
    "Send me an Ethereum project name and I'll score it against CROPS.\n\n"
    "Examples: <b>Aave</b>, <b>Uniswap</b>, <b>USDC</b>, <b>MetaMask</b>"
)


def _looks_like_project_name(text: str) -> bool:
    lower = text.lower().strip()
    if len(lower) < 2 or len(lower) > 100:
        return False
    if any(lower.startswith(p) for p in _NON_PROJECT):
        return False
    if lower in ("hi", "ok", "no", "yes", "yo", "ty", "thx"):
        return False
    return True


_LEADING_POLITENESS = [
    r"^please\s+",
    r"^pls\s+",
    r"^could you\s+",
    r"^can you\s+",
]

_PREFIX_PATTERNS = [
    r"^what about\s+",
    r"^how about\s+",
    r"^how is\s+",
    r"^how's\s+",
    r"^tell me about\s+",
    r"^is\s+",
    r"^score\s+",
    r"^check\s+",
    r"^look up\s+",
    r"^assess\s+",
    r"^rate\s+",
    r"^evaluate\s+",
    r"^analyze\s+",
]

_SUFFIX_PATTERNS = [
    r"\s+for (me|us)\s*\??\s*$",
    r"\s+crops\s*\??\s*$",
    r"\s+(please|pls)\s*\??\s*$",
    r"\s+thanks?\s*\??\s*$",
]


def _extract_project_name(text: str) -> str:
    """Strip conversational wrapping to get just the project name."""
    cleaned = text.strip().rstrip("?.!")
    # Strip leading politeness first (e.g. "please", "can you")
    for pattern in _LEADING_POLITENESS:
        new = re.sub(pattern, "", cleaned, count=1, flags=re.IGNORECASE)
        if new != cleaned:
            cleaned = new
            break
    # Strip conversational prefixes (e.g. "score", "rate")
    for pattern in _PREFIX_PATTERNS:
        new = re.sub(pattern, "", cleaned, count=1, flags=re.IGNORECASE)
        if new != cleaned:
            cleaned = new
            break
    # Strip suffixes (can apply multiple)
    prev = None
    while prev != cleaned:
        prev = cleaned
        for pattern in _SUFFIX_PATTERNS:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip().rstrip("?.!").strip() or text.strip()


async def handle_message(update: Update, context):
    query = update.message.text.strip()
    if not query:
        return

    # Detect a GitHub URL anywhere in the message — fetch authoritative repo data
    gh_match = github_fetcher.find_github_url(query)
    project_name: str
    if gh_match:
        owner, repo = gh_match
        project_name = f"{owner}/{repo}"
    else:
        if not _looks_like_project_name(query):
            await update.message.reply_text(GUIDANCE_MSG, parse_mode="HTML")
            return
        project_name = _extract_project_name(query)

    chat_id = update.effective_chat.id
    user = update.effective_user

    # Acknowledge immediately
    status_msg = await update.message.reply_text(
        f"Scoring <b>{html_escape(project_name)}</b> against CROPS framework...\n"
        "This typically takes 30-60 seconds.",
        parse_mode="HTML",
    )

    # Save query to DB
    query_id = None
    try:
        query_id = await database.save_query(
            user.id, user.username, user.first_name, query,
        )
    except Exception as e:
        logger.error("Failed to save query: %s", e)

    # Send typing indicator in background
    typing_task = asyncio.create_task(_typing_loop(chat_id, context.bot))

    # Fetch GitHub context if a URL was detected
    github_context: str | None = None
    if gh_match:
        try:
            ctx = await github_fetcher.fetch_github_context(*gh_match)
            if ctx:
                github_context = ctx.to_prompt_block()
            else:
                logger.info("GitHub repo not found: %s/%s", *gh_match)
        except Exception as e:
            logger.error("Failed to fetch GitHub context: %s", e)

    try:
        start_time = time.time()
        result_text, input_tokens, output_tokens = await ai_backend.score_project(
            project_name, github_context=github_context,
        )
        duration = time.time() - start_time

        # Save result to DB
        if query_id is not None:
            try:
                await database.save_result(
                    query_id, result_text, config.ANTHROPIC_MODEL,
                    input_tokens, output_tokens, duration,
                )
            except Exception as e:
                logger.error("Failed to save result: %s", e)

        # Delete status message
        try:
            await status_msg.delete()
        except Exception:
            pass

        # Send formatted messages
        messages = formatter.format_for_telegram(result_text)
        for msg in messages:
            await update.message.reply_text(msg, parse_mode="HTML")
            await asyncio.sleep(0.3)

    except Exception as e:
        logger.exception("Research failed")
        try:
            await status_msg.edit_text(f"Error: {str(e)[:300]}")
        except Exception:
            await update.message.reply_text(f"Error: {str(e)[:300]}")
    finally:
        typing_task.cancel()


async def _typing_loop(chat_id: int, bot):
    try:
        while True:
            await bot.send_chat_action(chat_id, ChatAction.TYPING)
            await asyncio.sleep(5)
    except asyncio.CancelledError:
        pass


async def error_handler(update, context):
    logger.error("Unhandled exception: %s", context.error, exc_info=context.error)
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "Something went wrong. Please try again."
            )
        except Exception:
            pass


async def post_init(application):
    await database.init_db(config.DATABASE_URL)
    logger.info("Database initialized")


async def post_shutdown(application):
    await database.close_db()


def main():
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    app.post_init = post_init
    app.post_shutdown = post_shutdown

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    logger.info("Starting CROPS Inspector bot...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
