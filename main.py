import os
import requests
import asyncio
from datetime import datetime

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
)
from telegram.constants import ParseMode
from dotenv import load_dotenv

# --- keep-alive web server (aiohttp) ---
from aiohttp import web

async def _health(request):
    return web.Response(text="ok")

async def _run_keepalive_server():
    app = web.Application()
    app.router.add_get("/", _health)
    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Keep-alive server started on port {port}")
    return runner

# === CONFIGURATION ===
load_dotenv(dotenv_path='private.env')
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = '@Afterlife_update'
BANNER_FILE_PATH = "banner/banner.jpg"
BASE_URL = "https://raw.githubusercontent.com/AfterlifeOS/device_afterlife_ota/refs/heads/16"
DONATE_URL = "https://t.me/donate_zero/6"
AFL_SUPPORT = "https://t.me/AfterLifeOS"
SOURCE_CHANGELOGS_URL = "https://github.com/AfterlifeOS/Release_changelogs/blob/main/AfterLife-Changelogs.mk"
allowed_ids_str = os.environ.get("ALLOWED_CHAT_IDS", "")
ALLOWED_CHAT_IDS = [int(x.strip()) for x in allowed_ids_str.split(",") if x.strip().isdigit()]

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
    mt_support = data.get("support_group") or AFL_SUPPORT
    cl = f"{BASE_URL}/{data['device_codename']}/changelog.txt"
    size = bytes_to_gb(data.get("size"))
    device_codename_tag = f"#{data['device_codename']}"
    release_codename_tag = f"#{data['release_codename']}" if data.get("release_codename") else ""

    post = (
        f"📢 <b>Released {rom_name} v{version} for {device_name}</b>\n"
        f"🔹 <b>Maintainer:</b> <a href='{maintainer_link}'>{maintainer_name}</a>\n"
        f"🔹 <b>Build date:</b> {build_date}\n\n"
        f"💬 <b><a href='{mt_support}'>Device support</a></b>\n"
        f"📋 <b><a href='{cl}'>Device changelog</a></b>\n"
    )

    if notes_list:
        notes_section = "\n".join(notes_list)
        post += f"\n📝 <b>Notes:</b>\n{notes_section}\n"

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
    buttons = [
        [
            InlineKeyboardButton("⬇️ Download", url=f"https://afterlifeos.com/device/{codename}/"),
            InlineKeyboardButton("🧾 Source Changelogs", url=SOURCE_CHANGELOGS_URL),
        ],
        [
            InlineKeyboardButton("💬 Support Group", url=AFL_SUPPORT),
            InlineKeyboardButton("❤️ Donate", url=DONATE_URL),
        ],
    ]
    return InlineKeyboardMarkup(buttons)

def confirm_keyboard(device_codename, poster_username, user_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Post to Channel", callback_data=f"confirm_send:{device_codename}:{poster_username}:{user_id}")],
        [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_post:{user_id}")]
    ])

# === COMMANDS ===
async def view_banner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in ALLOWED_CHAT_IDS:
        await update.message.reply_text("Sorry, this command is only allowed in specific groups.")
        return

    if os.path.exists(BANNER_FILE_PATH):
        try:
            with open(BANNER_FILE_PATH, "rb") as banner_photo:
                await update.message.reply_photo(
                    photo=banner_photo,
                    caption="This is the currently used banner."
                )
        except Exception as e:
            await update.message.reply_text(f"Failed to send banner: {e}")
    else:
        await update.message.reply_text("Banner not found. Please upload file banner/banner.jpg.")

async def post_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in ALLOWED_CHAT_IDS:
        await update.message.reply_text("Sorry, this command is only allowed in specific groups.")
        return

    if not os.path.exists(BANNER_FILE_PATH):
        await update.message.reply_text(
            f"⚠️ Banner not found at <code>{BANNER_FILE_PATH}</code>.\n"
            "Please upload the static banner file.",
            parse_mode=ParseMode.HTML
        )
        return

    if not context.args:
        await update.message.reply_text(
            "Usage:\n/post <codename>\nExample: /post surya\n\n"
            "Or:\n/post <codename> notes1; notes2\nExample: /post surya Clean flash is mandatory; Use latest firmware"
        )
        return

    device_codename = context.args[0].lower()
    notes_raw = " ".join(context.args[1:]) if len(context.args) > 1 else None
    notes_list = [f"- {line.strip()}" for line in notes_raw.split(";") if line.strip()] if notes_raw else None

    data = fetch_rom_data(device_codename)
    if not data:
        await update.message.reply_text(
            f"Failed to fetch data for <code>{device_codename}</code>. Make sure the JSON file exists.",
            parse_mode=ParseMode.HTML
        )
        return

    data["notes_list"] = notes_list
    poster_username = data.get("maintainer_name", update.effective_user.username or update.effective_user.first_name)
    post_preview = format_post(data, poster_username, notes_list)
    keyboard = confirm_keyboard(device_codename, poster_username, update.effective_user.id)

    try:
        with open(BANNER_FILE_PATH, "rb") as banner_photo:
            await update.message.reply_photo(
                photo=banner_photo,
                caption=post_preview,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )
    except Exception as e:
        await update.message.reply_text(f"Failed to send preview: {e}")

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    if query.data.startswith("cancel_post:"):
        _, expected_user_id = query.data.split(":")
        if str(user_id) != expected_user_id:
            await query.answer("You are not allowed to cancel this post.", show_alert=True)
            return
        await query.edit_message_reply_markup(None)
        await query.message.reply_text("❌ Post canceled.")
        return

    if query.data.startswith("confirm_send:"):
        try:
            _, device_codename, poster_username, expected_user_id = query.data.split(":", 3)
        except ValueError:
            await query.edit_message_text("Error: Invalid callback data format.")
            return

        if str(user_id) != expected_user_id:
            await query.answer("You are not allowed to send this post.", show_alert=True)
            return

        data = fetch_rom_data(device_codename)
        if not data:
            await query.edit_message_text("Failed to re-fetch JSON data.")
            return

        original_caption = query.message.caption_html
        notes_list_final = []
        if "📝 <b>Notes:</b>" in original_caption:
            try:
                notes_section = original_caption.split("📝 <b>Notes:</b>\n")[1].split("\n\n")[0]
                notes_list_final = notes_section.split("\n")
            except IndexError:
                pass

        msg = format_post(data, poster_username, notes_list_final)
        kb = build_keyboard(data)
        bot = Bot(token=BOT_TOKEN)

        try:
            with open(BANNER_FILE_PATH, "rb") as banner_photo:
                await bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=banner_photo,
                    caption=msg,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb
                )
            await query.edit_message_reply_markup(None)
            await query.message.reply_text("✅ Post sent to channel successfully.")
        except Exception as e:
            await query.message.reply_text(f"Failed to send to channel: {e}")

async def main():
    if not BOT_TOKEN:
        print("BOT_TOKEN not found. Set it in Secrets or private.env")
        return

    os.makedirs(os.path.dirname(BANNER_FILE_PATH), exist_ok=True)

    # Start keep-alive server
    await _run_keepalive_server()

    # Build and start bot
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("post", post_command))
    app.add_handler(CommandHandler("banner", view_banner_command))
    app.add_handler(CallbackQueryHandler(callback_handler))

    print("Bot is running...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    
    # Keep running forever
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

if __name__ == "__main__":
    asyncio.run(main())