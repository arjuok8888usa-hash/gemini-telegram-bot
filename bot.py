
"""
Telegram Gemini AI Bot
A secure Telegram bot that connects to Google Gemini AI, supports conversational memory,
restricts access to authorized Telegram User IDs, and handles long responses.
"""

import os
import sys
import logging
from typing import Dict, List, Optional
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
ALLOWED_USER_IDS_RAW = os.getenv("ALLOWED_USER_IDS", "").strip()

# Parse allowed user IDs if specified (comma-separated list of integer IDs)
ALLOWED_USER_IDS: set[int] = set()
if ALLOWED_USER_IDS_RAW:
    for uid in ALLOWED_USER_IDS_RAW.split(","):
        uid = uid.strip()
        if uid.isdigit():
            ALLOWED_USER_IDS.add(int(uid))

# Choice of Gemini model (gemini-2.5-flash or gemini-1.5-flash)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

# Initialize Gemini Client
# We support google-genai (new SDK) and google-generativeai (legacy SDK)
USE_NEW_SDK = False
try:
    from google import genai
    from google.genai import types
    gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
    USE_NEW_SDK = True
    logger.info("Using google-genai SDK.")
except ImportError:
    try:
        import google.generativeai as legacy_genai
        if GEMINI_API_KEY:
            legacy_genai.configure(api_key=GEMINI_API_KEY)
        USE_NEW_SDK = False
        logger.info("Using legacy google-generativeai SDK.")
    except ImportError:
        logger.error("Neither 'google-genai' nor 'google-generativeai' is installed.")
        gemini_client = None

# In-memory storage for active chats per user ID
# user_id -> chat session or list of message history
user_chats: Dict[int, any] = {}


def is_user_authorized(user_id: int) -> bool:
    """Check if the user is authorized to use the bot."""
    if not ALLOWED_USER_IDS:
        # If no user IDs are configured, allow all users by default
        return True
    return user_id in ALLOWED_USER_IDS


def split_message(text: str, max_length: int = 4000) -> List[str]:
    """Split long responses into chunks that fit within Telegram's 4096 character limit."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    while len(text) > max_length:
        # Try to find a clean break point (newline or space)
        split_idx = text.rfind("\n", 0, max_length)
        if split_idx == -1:
            split_idx = text.rfind(" ", 0, max_length)
        if split_idx == -1:
            split_idx = max_length

        chunks.append(text[:split_idx].strip())
        text = text[split_idx:].strip()

    if text:
        chunks.append(text)
    return chunks


async def send_response(update: Update, text: str) -> None:
    """Send text response safely, handling Markdown formatting and length limits."""
    chunks = split_message(text)
    for chunk in chunks:
        try:
            # First attempt: send with Markdown
            await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            # Fallback: send as plain text if Markdown formatting contains syntax errors
            await update.message.reply_text(chunk)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for /start command."""
    user = update.effective_user
    user_id = user.id if user else 0

    auth_status = "Authorized" if is_user_authorized(user_id) else "Restricted"

    msg = (
        f"Hello, {user.first_name if user else 'there'}!\n\n"
        f"I am your personal **Gemini AI Assistant** on Telegram.\n\n"
        f"**Your Telegram User ID:** `{user_id}`\n"
        f"**Access Status:** {auth_status}\n\n"
        "**Available Commands:**\n"
        "- Just send any question or message, and Gemini will answer.\n"
        "- `/id` - Check your Telegram User ID\n"
        "- `/reset` - Clear chat history and start a fresh session\n"
        "- `/help` - Show usage instructions"
    )

    if not is_user_authorized(user_id):
        msg += (
            f"\n\n*Note:* This bot is restricted. Add your ID (`{user_id}`) "
            "to `ALLOWED_USER_IDS` in your `.env` configuration file to unlock full access."
        )

    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for /id command to display the user's Telegram ID."""
    user = update.effective_user
    user_id = user.id if user else 0
    await update.message.reply_text(
        f"Your Telegram User ID is: `{user_id}`\n\n"
        f"You can place this in your `.env` file:\n"
        f"`ALLOWED_USER_IDS={user_id}`",
        parse_mode=ParseMode.MARKDOWN,
    )


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler to reset conversation history for the current user."""
    user = update.effective_user
    user_id = user.id if user else 0

    if user_id in user_chats:
        del user_chats[user_id]
        await update.message.reply_text("Your conversation history has been reset. Let's start fresh!")
    else:
        await update.message.reply_text("No active session history found. Feel free to ask anything!")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for /help command."""
    help_text = (
        "**Gemini AI Telegram Bot Help**\n\n"
        "1. **Ask a question:** Type any message directly into this chat.\n"
        "2. **Continuous Conversation:** Gemini remembers recent context within the conversation.\n"
        "3. **Reset Context:** Use `/reset` to start a new topic from scratch.\n"
        "4. **Your ID:** Use `/id` to check your Telegram User ID for security access control."
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process incoming text messages and query Google Gemini AI."""
    if not update.message or not update.message.text:
        return

    user = update.effective_user
    user_id = user.id if user else 0
    user_prompt = update.message.text.strip()

    # Security check: Check if user is authorized
    if not is_user_authorized(user_id):
        await update.message.reply_text(
            f"Access Denied: Your User ID (`{user_id}`) is not authorized to use this bot.\n"
            "Please configure `ALLOWED_USER_IDS` in the bot environment.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Check if Gemini API key is configured
    if not GEMINI_API_KEY:
        await update.message.reply_text(
            "Gemini API Key is not set. Please add `GEMINI_API_KEY` to your `.env` file."
        )
        return

    # Indicate typing state in Telegram chat
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    try:
        if USE_NEW_SDK:
            # Using google-genai SDK
            # Maintain multi-turn chat sessions per user_id
            if user_id not in user_chats:
                user_chats[user_id] = gemini_client.chats.create(model=GEMINI_MODEL)
            
            chat_session = user_chats[user_id]
            response = chat_session.send_message(user_prompt)
            reply_text = response.text or "I received an empty response from Gemini."

        else:
            # Using google-generativeai SDK
            if user_id not in user_chats:
                model = legacy_genai.GenerativeModel(GEMINI_MODEL)
                user_chats[user_id] = model.start_chat(history=[])
            
            chat_session = user_chats[user_id]
            response = chat_session.send_message(user_prompt)
            reply_text = response.text or "I received an empty response from Gemini."

        await send_response(update, reply_text)

    except Exception as e:
        logger.error(f"Error calling Gemini API for user {user_id}: {e}", exc_info=True)
        # If the chat session got corrupted or invalidated, reset it
        if user_id in user_chats:
            del user_chats[user_id]
        
        await update.message.reply_text(
            f"An error occurred while contacting Gemini AI:\n`{str(e)}`\n\n"
            "Your chat session has been reset. Please try again.",
            parse_mode=ParseMode.MARKDOWN,
        )


def main() -> None:
    """Start the Telegram bot."""
    if not TELEGRAM_BOT_TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN is missing. Please set it in your .env file or environment.")
        sys.exit(1)

    print("Starting Telegram Gemini AI Bot...")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Register handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("id", id_command))
    application.add_handler(CommandHandler("myid", id_command))
    application.add_handler(CommandHandler("reset", reset_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Run bot polling
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
