import os
import requests
import asyncio
from datetime import datetime
import re
import redis
from functools import partial

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot, ForceReply
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes,
    MessageHandler, filters
)
from telegram.constants import ParseMode
from dotenv import load_dotenv

# === CONFIGURATION ===
load_dotenv(dotenv_path='private.env')
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
REDIS_URL = os.environ.get("REDIS_URL")
BASE_URL = "https://raw.githubusercontent.com/AfterlifeOS/device_afterlife_ota/refs/heads/16"
DONATE_URL = "https://t.me/donate_zero/6"
AFL_SUPPORT = "https://t.me/AfterLifeOS"
SOURCE_CHANGELOGS_URL = "https://github.com/AfterlifeOS/Release_changelogs/blob/main/AfterLife-Changelogs.mk"

# Allowed Chat
allowed_ids_str = os.environ.get("ALLOWED_CHAT_IDS", "")
temp_ids_list = allowed_ids_str.split(",")
ALLOWED_CHAT_IDS = []

for item in temp_ids_list:
    item_stripped = item.strip()
    if item_stripped:
        try:
            ALLOWED_CHAT_IDS.append(int(item_stripped))
        except ValueError:
            print(f"[WARNING] Ignoring invalid ID in ALLOWED_CHAT_IDS: {item_stripped}")

print(f"Successfully loaded {len(ALLOWED_CHAT_IDS)} Chat IDs: {ALLOWED_CHAT_IDS}")

# User as Admin
admin_ids_str = os.environ.get("ADMIN_USER_IDS", "")
temp_admin_list = admin_ids_str.split(",")
ADMIN_USER_IDS = []

for item in temp_admin_list:
    item_stripped = item.strip()
    if item_stripped:
        try:
            ADMIN_USER_IDS.append(int(item_stripped))
        except ValueError:
            print(f"[WARNING] Ignoring invalid ID in ADMIN_USER_IDS: {item_stripped}")

if not ADMIN_USER_IDS:
    print("[WARNING] ADMIN_USER_IDS is not set. Banner commands will not be usable by anyone.")
else:
    print(f"Successfully loaded {len(ADMIN_USER_IDS)} Admin IDs: {ADMIN_USER_IDS}")

if not REDIS_URL:
    print("[ERROR] REDIS_URL is not set in environment. Bot cannot start.")
else:
    print(f"REDIS_URL loaded. Will attempt to connect.")

# === NEW REDIS FUNCTION ===
async def run_redis_command(redis_client, command_name, *args, **kwargs):
    """
    Runs a synchronous redis command in a thread pool to avoid
    blocking the asyncio event loop.
    """
    try:
        command_method = getattr(redis_client, command_name)
        sync_call = partial(command_method, *args, **kwargs)

        return await asyncio.to_thread(sync_call)
    except Exception as e:
        print(f"[ERROR] Redis command '{command_name}' failed: {e}")
        return None

# === HELPERS ===
def format_date(timestamp):
    return datetime.fromtimestamp(timestamp).strftime("%d %B %Y")

def fetch_rom_data(device_codename):
    url = f"{BASE_URL}/{device_codename}/updates.json"
    try:
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            json_data = res.json()
            if "response" in json_data and json_data["response"]:
                j = json_data["response"][0]
                return {
                    "device_codename": device_codename,
                    "device_name": j.get("device"),
                    "rom_name": "AfterlifeOS",
                    "version": j.get("version"),
                    "release_codename": j.get("codename"),
                    "download_url": j.get("download"),
                    "build_date": j.get("timestamp"),
                    "size": j.get("size"),
                    "build_type": j.get("buildtype"),
                    "maintainer_name": j.get("maintainer"),
                    "maintainer_link": j.get("telegram"),
                    "support_group": j.get("forum"),
                }
    except Exception as e:
        print(f"[ERROR] Failed to fetch JSON {device_codename}: {e}")
    return None

def bytes_to_gb(size_bytes):
    if not isinstance(size_bytes, (int, float)) or size_bytes == 0:
        return "N/A"
    return f"{size_bytes / (1024 ** 3):.2f} GB"

def format_post(data, posted_by_username, notes_list=None):
    rom_name = data.get("rom_name", "AfterlifeOS")
    version = data.get("version", "Unknown")
    device_name = data.get("device_name", data['device_codename'])
    maintainer_name = data.get("maintainer_name", posted_by_username)
    maintainer_link = data.get("maintainer_link", f"https://t.me/{posted_by_username}")
    build_date = format_date(int(data['build_date'])) if data.get("build_date") else "Unknown"
    size = bytes_to_gb(data.get("size"))
    release_type = data.get("build_type", "unofficial").capitalize()
    device_codename = data['device_codename']
    device_codename_tag = f"#{data['device_codename']}"
    release_codename = data['release_codename'].capitalize() if data.get("release_codename") else ""
    release_codename_tag = f"#{data['release_codename']}" if data.get("release_codename") else ""

    post = (
        f"<b>{rom_name} v{version} {release_codename} | {release_type} | Android 16</b>\n"
        f"Supported Device: {device_name} - {device_codename}\n"
        f"Build date: {build_date}\n"
        f"Maintainer: <a href='{maintainer_link}'>{maintainer_name}</a>\n"
    )

    if notes_list:
        notes_section = "\n".join([f"- {note.lstrip('- ')}" for note in notes_list if note.strip()])
        if notes_section:
            post += f"\n<b>Notes:</b>\n{notes_section}\n"

    post += (
        f"\nThere's nothing special about my rom, you can skip if you don't like, or you can taste it.\n"
        f"Subscribe For More <a href='https://t.me/Afterlife_update'>AfterlifeOS</a>\n\n"
        f"Hope you all have a happy life\n"
        f"Thank you.\n"
    )

    post += f"\n#{rom_name} {device_codename_tag} {release_codename_tag} #NeverDie"
    return post

def build_keyboard(data):
    codename = data['device_codename']
    mt_support = data.get("support_group") or AFL_SUPPORT
    buttons = [
        [
            InlineKeyboardButton("Download", url=f"https://afterlifeos.com/device/{codename}/"),
            InlineKeyboardButton("Source Changelogs", url=SOURCE_CHANGELOGS_URL),
        ],
        [
            InlineKeyboardButton("Support Group", url=AFL_SUPPORT),
            InlineKeyboardButton("Donate", url=DONATE_URL),
        ],
        [
            InlineKeyboardButton("Device Support", url=mt_support)
        ],
    ]
    return InlineKeyboardMarkup(buttons)

def confirm_keyboard(device_codename, poster_username, user_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Post to Channel", callback_data=f"confirm_send:{device_codename}:{poster_username}:{user_id}")],
        [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_post:{user_id}")]
    ])

def ask_notes_keyboard(device_codename, poster_username, user_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Yes, add notes", callback_data=f"notes_yes:{device_codename}:{poster_username}:{user_id}")],
        [InlineKeyboardButton("No, continue", callback_data=f"notes_no:{device_codename}:{poster_username}:{user_id}")]
    ])

# === COMMANDS ===

# /setbanner command
async def set_banner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if chat_id not in ALLOWED_CHAT_IDS:
        await update.message.reply_text("Sorry, this command is only allowed in specific groups.")
        return

    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("Sorry, you are not authorized to use this command.")
        return

    if not update.message.reply_to_message or not update.message.reply_to_message.photo:
        await update.message.reply_text(
            "Usage: Reply to a photo with `/setbanner` to capture its ID."
        )
        return

    file_id = update.message.reply_to_message.photo[-1].file_id
    
    try:
        redis_client: redis.Redis = context.bot_data["redis"]
        # Use the helper to run the sync command
        await run_redis_command(redis_client, "set", "banner_file_id", file_id)
        
        await update.message.reply_text(
            f"✅ Banner ID set successfully.\n",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        print(f"[ERROR] Failed to set banner in Redis: {e}")
        await update.message.reply_text(f"Failed to set banner in Redis: {e}")

# /removebanner command
async def remove_banner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if chat_id not in ALLOWED_CHAT_IDS:
        await update.message.reply_text("Sorry, this command is only allowed in specific groups.")
        return

    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("Sorry, you are not authorized to use this command.")
        return
        
    try:
        redis_client: redis.Redis = context.bot_data["redis"]
        # Use the helper to run the sync command
        await run_redis_command(redis_client, "delete", "banner_file_id")
        
        await update.message.reply_text(
            f"✅ Banner removed successfully."
        )
    except Exception as e:
        print(f"[ERROR] Failed to remove banner from Redis: {e}")
        await update.message.reply_text(f"Failed to remove banner from Redis: {e}")

# view_banner_command
async def view_banner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in ALLOWED_CHAT_IDS:
        await update.message.reply_text("Sorry, this command is only allowed in specific groups.")
        return

    redis_client: redis.Redis = context.bot_data["redis"]
    # Use the helper to run the sync command
    banner_file_id = await run_redis_command(redis_client, "get", "banner_file_id")

    if banner_file_id:
        try:
            await update.message.reply_photo(
                photo=banner_file_id,
                caption="This is the currently used banner."
            )
        except Exception as e:
            await update.message.reply_text(f"Failed to send banner using file_id: {e}")
    else:
        await update.message.reply_text(
            "Banner not set. Please set one using `/setbanner`."
        )

# post_command
async def post_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in ALLOWED_CHAT_IDS:
        await update.message.reply_text("Sorry, this command is only allowed in specific groups.")
        return

    redis_client: redis.Redis = context.bot_data["redis"]
    # Use the helper to run the sync command
    banner_file_id = await run_redis_command(redis_client, "get", "banner_file_id")

    if not banner_file_id:
        await update.message.reply_text(
            f"⚠️ Banner not found.\n"
            "Please set a banner using `/setbanner`.",
            parse_mode=ParseMode.HTML
        )
        return

    if not context.args:
        await update.message.reply_text(
            "Usage:\n/post <codename>\nExample: /post surya"
        )
        return

    device_codename = context.args[0].lower()
    
    data = fetch_rom_data(device_codename)
    if not data:
        await update.message.reply_text(
            f"Failed to fetch data for <code>{device_codename}</code>. Make sure the JSON file exists.",
            parse_mode=ParseMode.HTML
        )
        return

    poster_username = data.get("maintainer_name", update.effective_user.username or update.effective_user.first_name)
    post_preview = format_post(data, poster_username, notes_list=None) 
    keyboard = ask_notes_keyboard(device_codename, poster_username, update.effective_user.id)

    try:
        await update.message.reply_photo(
            photo=banner_file_id, 
            caption=post_preview,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
    except Exception as e:
        await update.message.reply_text(f"Failed to send preview: {e}")

# ... (handle_notes_reply remains exactly the same) ...
async def handle_notes_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if 'awaiting_notes_for' not in context.user_data:
        return 
            
    state = context.user_data['awaiting_notes_for']
    
    if (update.message.reply_to_message and 
        update.message.reply_to_message.message_id == state['prompt_message_id'] and
        user_id == state['user_id']):
        
        notes_raw = update.message.text
        notes_with_html_links = re.sub(r'\[(.*?)\]\((.*?)\)', r'<a href="\2">\1</a>', notes_raw)
        notes_list = [f"- {line.strip()}" for line in notes_with_html_links.split("\n") if line.strip()]

        device_codename = state['device_codename']
        poster_username = state['poster_username']
        original_preview_message_id = state['original_preview_message_id']

        data = fetch_rom_data(device_codename)
        if not data:
            await update.message.reply_text("Error: Failed to re-fetch device data. Please try the /post command again.")
            del context.user_data['awaiting_notes_for']
            return

        post_with_notes = format_post(data, poster_username, notes_list)
        keyboard = confirm_keyboard(device_codename, poster_username, user_id)

        try:
            await context.bot.edit_message_caption(
                chat_id=update.effective_chat.id,
                message_id=original_preview_message_id,
                caption=post_with_notes,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )
            
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=state['prompt_message_id'])
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.message_id)
        
        except Exception as e:
            await update.message.reply_text(f"An error occurred while updating the post: {e}")
        
        finally:
            del context.user_data['awaiting_notes_for']


# callback_handler
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    redis_client: redis.Redis = context.bot_data["redis"]

    # Handle "Yes, add notes"
    if query.data.startswith("notes_yes:"):
        try:
            _, device_codename, poster_username, expected_user_id = query.data.split(":", 3)
        except ValueError:
            await query.edit_message_text("Error: Invalid callback data format.")
            return
            
        if str(user_id) != expected_user_id:
            await query.answer("You are not allowed to perform this action.", show_alert=True)
            return

        await query.answer()
        await query.edit_message_reply_markup(None)
        
        prompt_msg = await query.message.reply_text(
            "Please reply to this message with your notes\\.\n"
            "Separate each note with a new line\\.\n\n"
            "To add a link, use format: `[text](url)`",
            reply_markup=ForceReply(selective=True),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        context.user_data['awaiting_notes_for'] = {
            'original_preview_message_id': query.message.message_id,
            'prompt_message_id': prompt_msg.message_id,
            'device_codename': device_codename,
            'poster_username': poster_username,
            'user_id': user_id
        }
        return

    # Handle "No, continue"
    if query.data.startswith("notes_no:"):
        try:
            _, device_codename, poster_username, expected_user_id = query.data.split(":", 3)
        except ValueError:
            await query.edit_message_text("Error: Invalid callback data format.")
            return

        if str(user_id) != expected_user_id:
            await query.answer("You are not allowed to perform this action.", show_alert=True)
            return

        await query.answer()
        keyboard = confirm_keyboard(device_codename, poster_username, user_id)
        await query.edit_message_reply_markup(keyboard)
        return

    # Handle "Cancel"
    if query.data.startswith("cancel_post:"):
        _, expected_user_id = query.data.split(":")
        if str(user_id) != expected_user_id:
            await query.answer("You are not allowed to cancel this post.", show_alert=True)
            return
        await query.edit_message_reply_markup(None)
        await query.message.reply_text("❌ Post canceled.")
        if 'awaiting_notes_for' in context.user_data:
            del context.user_data['awaiting_notes_for']
        return

    # Handle "Confirm Send"
    if query.data.startswith("confirm_send:"):
        try:
            _, device_codename, poster_username, expected_user_id = query.data.split(":", 3)
        except ValueError:
            await query.edit_message_text("Error: Invalid callback data format.")
            return

        if str(user_id) != expected_user_id:
            await query.answer("You are not allowed to send this post.", show_alert=True)
            return
            
        # Use the helper to run the sync command
        banner_file_id = await run_redis_command(redis_client, "get", "banner_file_id")
        
        if not banner_file_id:
            await query.message.reply_text(
                "Failed to send: `BANNER_FILE_ID` is not set. Please /setbanner.",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        data = fetch_rom_data(device_codename)
        if not data:
            await query.edit_message_text("Failed to re-fetch JSON data.")
            return

        original_caption = query.message.caption_html
        notes_list_final = []
        if "<b>Notes:</b>" in original_caption:
            try:
                notes_section = original_caption.split("<b>Notes:</b>\n")[1].split("\n\n")[0]
                notes_list_final = [line.lstrip('- ') for line in notes_section.split("\n") if line.strip()]
            except IndexError:
                pass

        msg = format_post(data, poster_username, notes_list_final)
        kb = build_keyboard(data)
        bot = Bot(token=BOT_TOKEN)

        try:
            await bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=banner_file_id, 
                caption=msg,
                parse_mode=ParseMode.HTML,
                reply_markup=kb
            )
            await query.edit_message_reply_markup(None)
            await query.message.reply_text(f"✅ Post sent to {CHANNEL_ID} successfully.")
        except Exception as e:
            await query.message.reply_text(f"Failed to send to channel: {e}")

# main() function
async def main():
    if not BOT_TOKEN:
        print("[ERROR] BOT_TOKEN not found. Set it in Secrets or private.env")
        return
        
    if not REDIS_URL:
        print("[ERROR] REDIS_URL not found. Set it in Secrets or private.env")
        return

    # Initialize standard (sync) Redis connection
    redis_client = None
    try:
        print(f"Attempting to connect to Redis (sync)...")
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        redis_client.ping()
        print("Successfully connected to Redis (sync).")
    except Exception as e:
        print(f"[ERROR] Failed to connect to Redis: {e}")
        print("Please ensure Redis server is running and REDIS_URL is correct.")
        return

    # Build and start bot
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.bot_data["redis"] = redis_client
    
    # ... (Handlers are added just as before) ...
    app.add_handler(CommandHandler("post", post_command))
    app.add_handler(CommandHandler("banner", view_banner_command))
    app.add_handler(CommandHandler("setbanner", set_banner_command))
    app.add_handler(CommandHandler("removebanner", remove_banner_command))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, handle_notes_reply))

    print("Bot is running...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        print("Bot stopping by user request...")
    finally:
        print("Shutting down... Closing Redis connection.")
        if redis_client:
            redis_client.close()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        print("Bot has shutdown.")

if __name__ == "__main__":
    asyncio.run(main())
