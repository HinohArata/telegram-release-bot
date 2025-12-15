import os
import requests
import asyncio
import html
import json
from datetime import datetime, timedelta
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
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
TELEGRAM_TOPIC_ID = os.environ.get("TELEGRAM_TOPIC_ID")
if TELEGRAM_TOPIC_ID:
    try:
        TELEGRAM_TOPIC_ID = int(TELEGRAM_TOPIC_ID)
    except ValueError:
        print(f"[WARNING] Invalid TELEGRAM_TOPIC_ID: {TELEGRAM_TOPIC_ID}")
        TELEGRAM_TOPIC_ID = None

REDIS_URL = os.environ.get("REDIS_URL")
STICKER_ID = os.environ.get("STICKER_ID")
BASE_URL = "https://raw.githubusercontent.com/AfterlifeOS/device_afterlife_ota/refs/heads/16"
DONATE_URL = "https://t.me/donate_zero/6"
AFL_SUPPORT = "https://t.me/AfterLifeOS"
SOURCE_CHANGELOGS_URL = "https://afterlifeos.com/changelog/"

# Jenkins/Build Bot Config
GH_PAT = os.environ.get("GH_PAT")
REPO_OWNER = "AfterlifeOS"
REPO_NAME = "AfterlifeOS-Builder"
WORKFLOW_FILE = "afterlife_build.yml"
USERS_JSON_PATH = "users.json"

# === Testing Env ===
TEST_GROUP_ID = int(os.environ.get("TEST_GROUP_ID", "0"))
TEST_CHANNEL_ID = os.environ.get("TEST_CHANNEL_ID")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))

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

if TEST_GROUP_ID != 0 and TEST_GROUP_ID not in ALLOWED_CHAT_IDS:
    ALLOWED_CHAT_IDS.append(TEST_GROUP_ID)
    print(f"Added TEST_GROUP_ID {TEST_GROUP_ID} to allowed list.")

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
                
                # FIX: Ensure telegram link has https://
                maintainer_link = j.get("telegram", "")
                if maintainer_link and not maintainer_link.startswith("http"):
                    maintainer_link = f"https://{maintainer_link}"
                
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
                    "maintainer_link": maintainer_link, 
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

# FIXED: Removed poster_username from callback data to save bytes
def confirm_keyboard(device_codename, user_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Post to Channel", callback_data=f"confirm_send:{device_codename}:{user_id}")],
        [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_post:{user_id}")]
    ])

# FIXED: Removed poster_username from callback data to save bytes
def ask_notes_keyboard(device_codename, user_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Yes, add notes", callback_data=f"notes_yes:{device_codename}:{user_id}")],
        [InlineKeyboardButton("No, continue", callback_data=f"notes_no:{device_codename}:{user_id}")],
        [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_post:{user_id}")]
    ])

# === BUILD BOT HELPERS ===
def get_gh_headers():
    return {
        "Authorization": f"token {GH_PAT}",
        "Accept": "application/vnd.github.v3+json"
    }

def fetch_users_db():
    url = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/main/{USERS_JSON_PATH}"
    try:
        res = requests.get(url, headers=get_gh_headers())
        if res.status_code == 200:
            return res.json()
    except Exception as e:
        print(f"Error fetching users: {e}")
    return {}

def get_requester(user_id, users_db):
    return users_db.get(str(user_id))

# === BUILD MENU HELPERS ===
def get_build_config_text(config):
    return (
        f"🛠 <b>Build Configuration</b>\n"
        f"<b>Device:</b> <code>{config['DEVICE']}</code>\n"
        f"<b>Manifest:</b> <a href='{config['LOCAL_MANIFEST_URL']}'>Link</a>\n"
        f"<b>Type:</b> <code>{config['BUILD_TYPE']}</code>\n"
        f"<b>Variant:</b> <code>{config['BUILD_VARIANT']}</code>\n"
        f"<b>FSGen:</b> {'❌ Disabled' if config['DISABLE_FSGEN'] == 'true' else '✅ Enabled'}\n"
        f"<b>Dirty:</b> {'✅ Yes' if config['DIRTY_BUILD'] == 'true' else '❌ No'}\n"
        f"<b>Clean:</b> {'✅ Yes' if config['CLEAN_BUILD'] == 'true' else '❌ No'}"
    )

def get_build_keyboard(config, is_admin, user_id):
    # Icons
    t_icon = "🔨"
    v_icon = "📦"
    fs_icon = "💀" if config['DISABLE_FSGEN'] == 'true' else "🧬"
    d_icon = "⚡️" if config['DIRTY_BUILD'] == 'true' else "🧹"
    c_icon = "✨" if config['CLEAN_BUILD'] == 'true' else "🗑"

    # Helper to append user_id
    def cb(action):
        return f"bmenu_{action}:{user_id}"

    # Buttons
    row1 = [
        InlineKeyboardButton(f"{t_icon} Type: {config['BUILD_TYPE']}", callback_data=cb("type")),
        InlineKeyboardButton(f"{v_icon} Var: {config['BUILD_VARIANT']}", callback_data=cb("var"))
    ]
    row2 = [
        InlineKeyboardButton(f"{fs_icon} FSGen: {'OFF' if config['DISABLE_FSGEN'] == 'true' else 'ON'}", callback_data=cb("fsg")),
        InlineKeyboardButton(f"{d_icon} Dirty: {'ON' if config['DIRTY_BUILD'] == 'true' else 'OFF'}", callback_data=cb("dirty"))
    ]
    
    row3 = []
    if is_admin:
         row3.append(InlineKeyboardButton(f"{c_icon} Clean: {'ON' if config['CLEAN_BUILD'] == 'true' else 'OFF'}", callback_data=cb("clean")))
    else:
         row3.append(InlineKeyboardButton("🔒 Clean (Admin Only)", callback_data=cb("locked")))

    row4 = [
        InlineKeyboardButton("🚀 START BUILD", callback_data=cb("start")),
        InlineKeyboardButton("❌ Cancel", callback_data=cb("cancel"))
    ]

    return InlineKeyboardMarkup([row1, row2, row3, row4])

# === COMMANDS ===

# /build command
async def build_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not GH_PAT:
        await update.message.reply_text("⛔ Admin has not configured GH_PAT.")
        return

    users_db = await asyncio.to_thread(fetch_users_db)
    requester = get_requester(update.effective_user.id, users_db)
    
    if not requester:
        await update.message.reply_text(
            "⛔ <b>Access Denied</b>\n"
            "You are not registered in the build system.\n"
            "Contact an Admin to verify your account.",
            parse_mode=ParseMode.HTML
        )
        return

    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text(
            "⚠️ *Invalid Format*\nUsage: `/build <device> <manifest_url>`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    device = args[0]
    manifest_url = args[1]

    # Clean up manifest URL if user included 'manifest=' prefix
    if manifest_url.startswith("manifest="):
        manifest_url = manifest_url.split("=", 1)[1]

    # AUTO-CONVERT GitHub Blob -> Raw
    if "github.com" in manifest_url and "/blob/" in manifest_url:
        manifest_url = manifest_url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
        # Optionally warn/notify user? No, silent fix is smoother UX.

    # Default Config
    config = {
        "DEVICE": device,
        "LOCAL_MANIFEST_URL": manifest_url,
        "BUILD_TYPE": "userdebug",
        "BUILD_VARIANT": "Test",
        "CLEAN_BUILD": "false",
        "DIRTY_BUILD": "false",
        "DISABLE_FSGEN": "false",
        "REQUESTER": requester
    }

    # Save to context (keyed by user_id to avoid conflicts)
    context.user_data['build_config'] = config
    
    # Check Admin for Clean Build UI
    is_admin = update.effective_user.id in ADMIN_USER_IDS

    await update.message.reply_text(
        get_build_config_text(config),
        reply_markup=get_build_keyboard(config, is_admin, update.effective_user.id),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )

# /status command
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not GH_PAT: return
    
    users_db = await asyncio.to_thread(fetch_users_db)
    if not get_requester(update.effective_user.id, users_db):
        await update.message.reply_text(
            "⛔ <b>Access Denied</b>\n"
            "You are not registered in the build system.",
            parse_mode=ParseMode.HTML
        )
        return
    
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/actions/runs?status=in_progress"
    
    def fetch_status():
        return requests.get(url, headers=get_gh_headers())

    try:
        res = await asyncio.to_thread(fetch_status)
        data = res.json()
        count = data.get('total_count', 0)
        
        if count == 0:
            await update.message.reply_text("✅ No active builds at the moment.")
        else:
            redis_client: redis.Redis = context.bot_data["redis"]
            msg = f"🔄 *Active Builds ({count}):*\n"
            
            for run in data.get('workflow_runs', []):
                actor = run['actor']['login']
                
                # Try to get linked Message ID
                tg_link = ""
                try:
                    # We stored it as build_msg:<gh_user> -> "chat_id:msg_id"
                    redis_val = await run_redis_command(redis_client, "get", f"build_msg:{actor}")
                    
                    if redis_val:
                        if ":" in redis_val:
                            target_chat_id, target_msg_id = redis_val.split(":")
                        else:
                            # Fallback for old keys
                            target_chat_id = str(CHANNEL_ID)
                            target_msg_id = redis_val
                        
                        # Construct Link (Private Group/Channel style)
                        # Remove -100 prefix
                        clean_id = str(target_chat_id).replace("-100", "")
                        
                        if TELEGRAM_TOPIC_ID:
                            link_url = f"https://t.me/c/{clean_id}/{TELEGRAM_TOPIC_ID}/{target_msg_id}"
                        else:
                            link_url = f"https://t.me/c/{clean_id}/{target_msg_id}"
                            
                        tg_link = f"\n  [See monitor progress]({link_url})"
                except Exception:
                    pass

                msg += f"- `{run['name']}`\n  Trigger: `{actor}`\n  ID: `{run['id']}`\n  [View Log]({run['html_url']}){tg_link}\n\n"
            
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

# /quota command
async def quota_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not GH_PAT: return

    users_db = await asyncio.to_thread(fetch_users_db)
    requester = get_requester(update.effective_user.id, users_db)
    
    if not requester:
        await update.message.reply_text(
            "⛔ <b>Access Denied</b>\n"
            "You are not registered in the build system.",
            parse_mode=ParseMode.HTML
        )
        return

    url = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/main/.github/workflow_counter.json"
    
    try:
        res = await asyncio.to_thread(requests.get, url, headers=get_gh_headers())
        if res.status_code != 200:
            await update.message.reply_text("⚠️ Failed to fetch quota data.")
            return
            
        data = res.json()
        user_data = data.get(requester, {})
        
        # Get Today's Date in UTC
        now = datetime.utcnow()
        today_utc = now.strftime("%Y-%m-%d")
        usage_today = user_data.get(today_utc, 0)
        
        # --- CHECK GITHUB PERMISSION ---
        perm_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/collaborators/{requester}/permission"
        
        def get_perm():
            return requests.get(perm_url, headers=get_gh_headers())
            
        perm_res = await asyncio.to_thread(get_perm)
        is_admin_gh = False
        
        if perm_res.status_code == 200:
            p_data = perm_res.json()
            # GitHub returns 'admin' or 'write' or 'read'
            if p_data.get("permission") == "admin":
                is_admin_gh = True
        
        # Determine Limit info
        if is_admin_gh:
            usage_info = f"{usage_today} builds (Unlimited ♾️)"
        else:
            daily_limit = 5
            remaining = max(0, daily_limit - usage_today)
            usage_info = f"{usage_today} builds ({remaining} left)"

        # Calculate time until next 00:00 UTC
        tomorrow = now + timedelta(days=1)
        next_reset = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
        diff = next_reset - now
        hours = diff.seconds // 3600
        minutes = (diff.seconds % 3600) // 60
        
        msg = (
            f"📊 <b>Quota Statistics</b>\n"
            f"<b>User:</b> <code>{requester}</code>\n"
            f"<b>Date (UTC):</b> {today_utc}\n"
            f"<b>Usage Today:</b> {usage_info}\n"
            f"<b>Reset In:</b> {hours}h {minutes}m"
        )
        
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

# /cancel command
async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not GH_PAT: return

    users_db = await asyncio.to_thread(fetch_users_db)
    if not get_requester(update.effective_user.id, users_db):
        await update.message.reply_text(
            "⛔ <b>Access Denied</b>\n"
            "You are not registered in the build system.",
            parse_mode=ParseMode.HTML
        )
        return

    # Check active builds
    status_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/actions/runs?status=in_progress"
    
    try:
        res = await asyncio.to_thread(requests.get, status_url, headers=get_gh_headers())
        data = res.json()
        
        if data.get('total_count', 0) == 0:
            await update.message.reply_text("No active builds to cancel.")
            return

        args = context.args
        target_id = None
        
        if len(args) > 0:
            target_id = args[0]
        elif data['total_count'] == 1:
            target_id = data['workflow_runs'][0]['id']
        else:
            await update.message.reply_text("⚠️ Multiple builds active. Use `/cancel <run_id>`.\nCheck IDs with `/status`.")
            return

        cancel_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/actions/runs/{target_id}/cancel"
        
        res_cancel = await asyncio.to_thread(requests.post, cancel_url, headers=get_gh_headers())
        
        if res_cancel.status_code == 202:
            await update.message.reply_text(f"🛑 Cancel signal sent to Run ID: `{target_id}`", parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(f"❌ Failed to cancel. Code: {res_cancel.status_code}")

    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

# /help command
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🤖 <b>AfterlifeOS Bot Commands</b>\n\n"
        "<b>📢 Channel Management</b>\n"
        "• <code>/post &lt;codename&gt;</code> - Create and post a new ROM update (Public).\n"
        "• <code>/banner</code> - View the currently set banner image.\n"
        "• <code>/setbanner</code> - Set post banner (Reply to photo) <b>(Admin Only)</b>.\n"
        "• <code>/removebanner</code> - Remove current banner <b>(Admin Only)</b>.\n"
        "• <code>/adduser &lt;id&gt; &lt;gh_user&gt;</code> - Add user to build database <b>(Admin Only)</b>.\n"
        "• <code>/removeuser &lt;id&gt;</code> - Remove user from database & revoke access <b>(Admin Only)</b>.\n"
        "• <code>/revoke &lt;gh_user&gt;</code> - Directly revoke GitHub access <b>(Admin Only)</b>.\n\n"
        
        "<b>🏗️ Build System (Registered Users)</b>\n"
        "• <code>/build &lt;device&gt; &lt;manifest_url&gt;</code> - Open menu to start a Build configuration.\n"
        "• <code>/status</code> - Check active build queue.\n"
        "• <code>/quota</code> - Check daily build quota (UTC).\n"
        "• <code>/cancel [run_id]</code> - Cancel your running build.\n\n"
        
        "<i>Note: Build commands require your Telegram ID to be linked in the user database.</i>"
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)

# /adduser command
async def add_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_USER_IDS:
        await update.message.reply_text("⛔ Admin Only.")
        return

    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text("Usage: `/adduser <telegram_id> <github_username>`", parse_mode=ParseMode.MARKDOWN)
        return

    new_tg_id = args[0]
    new_gh_user = args[1]
    
    await update.message.reply_text(f"⏳ Adding user `{new_gh_user}` ({new_tg_id})...", parse_mode=ParseMode.MARKDOWN)

    # 1. Fetch current users.json (Need SHA for update)
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{USERS_JSON_PATH}"
    headers = get_gh_headers()
    
    def update_repo():
        # Get current file
        res = requests.get(url, headers=headers)
        if res.status_code != 200:
            return f"Failed to fetch users.json: {res.status_code}"
            
        data = res.json()
        sha = data['sha']
        import base64
        content = base64.b64decode(data['content']).decode('utf-8')
        
        # Update JSON
        users = json.loads(content)
        if str(new_tg_id) in users:
            return f"User ID `{new_tg_id}` already exists as `{users[str(new_tg_id)]}`."
            
        users[str(new_tg_id)] = new_gh_user
        
        # Prepare Commit
        new_content = json.dumps(users, indent=2)
        new_content_encoded = base64.b64encode(new_content.encode('utf-8')).decode('utf-8')
        
        payload = {
            "message": f"users: Add @{new_gh_user} to verified users",
            "content": new_content_encoded,
            "sha": sha,
            "branch": "main" 
        }
        
        # Push Update
        put_res = requests.put(url, headers=headers, json=payload)
        if put_res.status_code in [200, 201]:
            # === AUTO-INVITE COLLABORATOR (READ ONLY) ===
            invite_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/collaborators/{new_gh_user}"
            invite_payload = {"permission": "pull"} # 'pull' = Read permission
            
            invite_res = requests.put(invite_url, headers=headers, json=invite_payload)
            
            invite_msg = ""
            if invite_res.status_code == 201:
                invite_msg = "\n📩 <b>Invitation Sent!</b> User needs to accept email invite."
            elif invite_res.status_code == 204:
                invite_msg = "\n✅ <b>Already a Collaborator.</b> Permission set to Read."
            else:
                invite_msg = f"\n⚠️ <b>Invite Failed:</b> {invite_res.status_code} {invite_res.text}"

            return True, invite_msg
        else:
            return f"Failed to commit: {put_res.status_code} {put_res.text}", ""

    try:
        result, msg = await asyncio.to_thread(update_repo)
        if result is True:
             await update.message.reply_text(
                 f"✅ User `{new_gh_user}` added to database!{msg}", 
                 parse_mode=ParseMode.HTML
             )
        else:
             await update.message.reply_text(f"❌ Error: {result}", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"Exception: {e}")

# /removeuser command
async def remove_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_USER_IDS:
        await update.message.reply_text("⛔ Admin Only.")
        return

    args = context.args
    if not args or len(args) < 1:
        await update.message.reply_text("Usage: `/removeuser <telegram_id>`", parse_mode=ParseMode.MARKDOWN)
        return

    target_tg_id = args[0]
    
    await update.message.reply_text(f"⏳ Removing user ID `{target_tg_id}`...", parse_mode=ParseMode.MARKDOWN)

    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{USERS_JSON_PATH}"
    headers = get_gh_headers()
    
    def process_removal():
        # 1. Fetch current users.json
        res = requests.get(url, headers=headers)
        if res.status_code != 200:
            return f"Failed to fetch users.json: {res.status_code}", ""
            
        data = res.json()
        sha = data['sha']
        import base64
        content = base64.b64decode(data['content']).decode('utf-8')
        
        users = json.loads(content)
        
        # 2. Check existence
        if str(target_tg_id) not in users:
            return f"User ID `{target_tg_id}` not found in database.", ""
            
        target_gh_user = users[str(target_tg_id)]
        
        # 3. Remove from JSON
        del users[str(target_tg_id)]
        
        # 4. Commit Update
        new_content = json.dumps(users, indent=2)
        new_content_encoded = base64.b64encode(new_content.encode('utf-8')).decode('utf-8')
        
        payload = {
            "message": f"users: Remove @{target_gh_user} (ID: {target_tg_id})",
            "content": new_content_encoded,
            "sha": sha,
            "branch": "main" 
        }
        
        put_res = requests.put(url, headers=headers, json=payload)
        if put_res.status_code not in [200, 201]:
             return f"Failed to commit DB update: {put_res.status_code}", target_gh_user

        # 5. Remove Collaborator from Repo
        collab_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/collaborators/{target_gh_user}"
        del_res = requests.delete(collab_url, headers=headers)
        
        collab_msg = ""
        if del_res.status_code == 204:
            collab_msg = f"\n🗑 <b>GitHub Access Revoked</b> for <code>{target_gh_user}</code>."
        else:
            collab_msg = f"\n⚠️ <b>Failed to Revoke Access:</b> {del_res.status_code}."

        return True, collab_msg

    try:
        result, msg = await asyncio.to_thread(process_removal)
        if result is True:
             await update.message.reply_text(
                 f"✅ User ID `{target_tg_id}` removed from database.{msg}", 
                 parse_mode=ParseMode.HTML
             )
        else:
             await update.message.reply_text(f"❌ Error: {result}", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"Exception: {e}")

# /revoke command
async def revoke_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_USER_IDS:
        await update.message.reply_text("⛔ Admin Only.")
        return

    args = context.args
    if not args or len(args) < 1:
        await update.message.reply_text("Usage: `/revoke <github_username>`", parse_mode=ParseMode.MARKDOWN)
        return

    target_gh_user = args[0]
    
    await update.message.reply_text(f"⏳ Revoking access for `{target_gh_user}`...", parse_mode=ParseMode.MARKDOWN)

    headers = get_gh_headers()
    collab_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/collaborators/{target_gh_user}"
    
    def process_revoke():
        del_res = requests.delete(collab_url, headers=headers)
        if del_res.status_code == 204:
            return True, f"🗑 <b>Access Revoked</b> for <code>{target_gh_user}</code>."
        elif del_res.status_code == 404:
            return False, f"⚠️ User <code>{target_gh_user}</code> is not a collaborator."
        else:
            return False, f"⚠️ <b>Failed:</b> {del_res.status_code} {del_res.text}"

    try:
        success, msg = await asyncio.to_thread(process_revoke)
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"Exception: {e}")

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
    user_id = update.effective_user.id 
    
    if chat_id not in ALLOWED_CHAT_IDS:
        await update.message.reply_text("Sorry, this command is only allowed in specific groups.")
        return

    if chat_id == TEST_GROUP_ID:
        if user_id != OWNER_ID:
            await update.message.reply_text("⛔ In this test group, only the Owner is allowed to post.")
            return
    # else:
        # In other allowed groups, ANYONE can post.
        # Previously: if user_id not in ADMIN_USER_IDS: return error
        # Now: No check needed, proceed.
        pass

    redis_client: redis.Redis = context.bot_data["redis"]
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

    device_codename = context.args[0]
    
    data = fetch_rom_data(device_codename)
    if not data:
        await update.message.reply_text(
            f"Failed to fetch data for <code>{device_codename}</code>. Make sure the JSON file exists.",
            parse_mode=ParseMode.HTML
        )
        return

    poster_username = data.get("maintainer_name", update.effective_user.username or update.effective_user.first_name)
    post_preview = format_post(data, poster_username, notes_list=None) 
    
    # Updated: removed poster_username from arguments
    keyboard = ask_notes_keyboard(device_codename, update.effective_user.id)

    try:
        await update.message.reply_photo(
            photo=banner_file_id, 
            caption=post_preview,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
    except Exception as e:
        await update.message.reply_text(f"Failed to send preview: {e}")

# ... (handle_notes_reply) ...
async def handle_notes_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if 'awaiting_notes_for' not in context.user_data:
        return 
            
    state = context.user_data['awaiting_notes_for']
    
    if (update.message.reply_to_message and 
        update.message.reply_to_message.message_id == state['prompt_message_id'] and
        user_id == state['user_id']):

        notes_raw = update.message.text
        notes_safe = html.escape(notes_raw)
        notes_with_html_links = re.sub(
            r'\[(.*?)]\s*\(\s*(.*?)\s*\)', 
            r'<a href="\2">\1</a>', 
            notes_safe
        )

        notes_list = [f"- {line.strip()}" for line in notes_with_html_links.split("\n") if line.strip()]

        device_codename = state['device_codename']
        original_preview_message_id = state['original_preview_message_id']

        data = fetch_rom_data(device_codename)
        if not data:
            await update.message.reply_text("Error: Failed to re-fetch device data. Please try the /post command again.")
            del context.user_data['awaiting_notes_for']
            return

        # Fetch maintainer from data again, fallback to user name
        poster_username = data.get("maintainer_name", update.effective_user.first_name)

        post_with_notes = format_post(data, poster_username, notes_list)
        
        # Updated: removed poster_username arg
        keyboard = confirm_keyboard(device_codename, user_id)

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
            print(f"[ERROR] Edit caption failed: {e}")
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
            # Updated split: only 3 parts now
            _, device_codename, expected_user_id = query.data.split(":", 2)
        except ValueError:
            await query.edit_message_text("Error: Invalid callback data format.")
            return
            
        if str(user_id) != expected_user_id:
            await query.answer("You are not allowed to perform this action.", show_alert=True)
            return

        await query.answer()
        await query.edit_message_reply_markup(None)
        
        # Navigation Buttons for Notes Stage
        nav_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back", callback_data=f"notes_back:{device_codename}:{user_id}")],
            [InlineKeyboardButton("❌ Cancel Post", callback_data=f"cancel_post:{user_id}")]
        ])
        
        prompt_msg = await query.message.reply_text(
            "Please reply to this message with your notes\\.\n"
            "Separate each note with a new line\\.\n\n"
            "To add a link, use format: `[text]<Space>(url)`\\.\n\n"
            "Example:\n"
            "Initial Build\n"
            "Use this \\[Recovery\\] \\(https://t\\.me/HinohArata\\)",
            reply_markup=nav_keyboard, # Add buttons here (Use standard keyboard, ForceReply might conflict visually but usually ok)
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        # Note: We cannot attach InlineKeyboard to a ForceReply object directly in the same message easily 
        # without verify logic. Usually ForceReply is just an interface.
        # BETTER UX: Send the instruction with InlineKeyboard. User just replies normally (no ForceReply object needed strictly if we track state).
        # However, to keep it consistent with your existing handle_notes_reply logic (which relies on reply_to_message_id), 
        # we need to keep the message accessible.
        
        context.user_data['awaiting_notes_for'] = {
            'original_preview_message_id': query.message.message_id,
            'prompt_message_id': prompt_msg.message_id,
            'device_codename': device_codename,
            'user_id': user_id
        }
        return

    # Handle "No, continue"
    if query.data.startswith("notes_no:"):
        try:
            # Updated split: only 3 parts now
            _, device_codename, expected_user_id = query.data.split(":", 2)
        except ValueError:
            await query.edit_message_text("Error: Invalid callback data format.")
            return

        if str(user_id) != expected_user_id:
            await query.answer("You are not allowed to perform this action.", show_alert=True)
            return

        await query.answer()
        # Updated keyboard call
        keyboard = confirm_keyboard(device_codename, user_id)
        await query.edit_message_reply_markup(keyboard)
        return

    # Handle "Back" from Notes Input
    if query.data.startswith("notes_back:"):
        try:
            _, device_codename, expected_user_id = query.data.split(":", 2)
        except ValueError:
            await query.answer("Invalid data", show_alert=True)
            return

        if str(user_id) != expected_user_id:
            await query.answer("Not allowed.", show_alert=True)
            return

        await query.answer()
        
        # 1. Delete the prompt message (the one with the Back button)
        try:
            await query.message.delete()
        except Exception:
            pass

        # 2. Restore the original preview message's keyboard (Yes/No Notes)
        # We need to find the original message ID from context if possible, 
        # OR we just rely on the fact that the prompt is separate.
        # Actually, the original message is the one that triggered 'notes_yes'.
        # But we edited its markup to None. We need to restore it.
        
        if 'awaiting_notes_for' in context.user_data:
            orig_msg_id = context.user_data['awaiting_notes_for']['original_preview_message_id']
            try:
                # Restore "Yes/No" keyboard on the photo preview
                await context.bot.edit_message_reply_markup(
                    chat_id=query.message.chat.id,
                    message_id=orig_msg_id,
                    reply_markup=ask_notes_keyboard(device_codename, user_id)
                )
            except Exception as e:
                print(f"Failed to restore keyboard: {e}")
            
            # Clear state
            del context.user_data['awaiting_notes_for']
        
        return

    # Handle "Cancel"
    if query.data.startswith("cancel_post:"):
        try:
            _, expected_user_id = query.data.split(":")
        except ValueError:
             expected_user_id = query.data.split(":")[-1]

        if str(user_id) != expected_user_id:
            await query.answer("You are not allowed to cancel this post.", show_alert=True)
            return
        await query.edit_message_reply_markup(None)
        await query.message.reply_text("❌ Post canceled.")
        
        # If cancelling from the "Notes Prompt" message, we should try to restore/clean the original preview too if needed,
        # or just leave it without markup (already done).
        # But crucially, we must check if we are in 'awaiting_notes' state to delete the prompt itself if this click came from there.
        
        if 'awaiting_notes_for' in context.user_data:
            state = context.user_data['awaiting_notes_for']
            # If the cancel button clicked was ON the prompt message, deleting query.message handles it.
            # If it was on the original preview (though we remove markup there), rare case.
            
            # If we are cancelling, we might want to delete the original preview photo too to be clean?
            # Let's delete the original preview to be thorough.
            try:
                await context.bot.delete_message(chat_id=query.message.chat.id, message_id=state['original_preview_message_id'])
            except Exception:
                pass
            
            # Also delete prompt if the click didn't come from it (e.g. some other flow)
            # But here query.message IS the prompt (if back/cancel buttons are on it).
            # If query.message is the Preview (standard cancel), we just deleted markup. 
            # Let's just try to delete the message that triggered this callback.
            try:
                await query.message.delete()
            except Exception:
                pass

            del context.user_data['awaiting_notes_for']
        else:
            # Standard cancel from Preview
            try:
                await query.message.delete()
            except Exception:
                pass

        return

    # Handle "Confirm Send"
    if query.data.startswith("confirm_send:"):
        try:
            # Updated split: only 3 parts now
            _, device_codename, expected_user_id = query.data.split(":", 2)
        except ValueError:
            await query.edit_message_text("Error: Invalid callback data format.")
            return

        if str(user_id) != expected_user_id:
            await query.answer("You are not allowed to send this post.", show_alert=True)
            return

        current_chat_id = query.message.chat.id
        target_chat_id = CHANNEL_ID
        if current_chat_id == TEST_GROUP_ID:
            if not TEST_CHANNEL_ID:
                await query.message.reply_text("⚠️ Error: TEST_CHANNEL_ID is not set in env!")
                return
            target_chat_id = TEST_CHANNEL_ID

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

        # Fetch Maintainer Name directly from JSON to avoid Button size limit
        poster_username = data.get("maintainer_name", query.from_user.first_name)

        original_caption = query.message.caption_html
        notes_list_final = []
        if "<b>Notes:</b>" in original_caption:
            try:
                # Parse notes from existing caption
                notes_part = original_caption.split("<b>Notes:</b>\n")[1]
                # Try to split by double newline or take it all if it's at the end
                if "\n\n" in notes_part:
                    notes_section = notes_part.split("\n\n")[0]
                else:
                    notes_section = notes_part 
                
                notes_list_final = [line.lstrip('- ') for line in notes_section.split("\n") if line.strip()]
            except IndexError:
                pass

        msg = format_post(data, poster_username, notes_list_final)
        kb = build_keyboard(data)
        bot = Bot(token=BOT_TOKEN)

        await query.edit_message_reply_markup(None)
        
        # 2. Send Sticker (if STICKER_ID is configured)
        if STICKER_ID:
            try:
                await bot.send_sticker(chat_id=target_chat_id, sticker=STICKER_ID)
                
                await query.message.reply_text(
                    f"⏳ <b>Sticker sent to {target_chat_id}.</b>\nWaiting 30 seconds...", 
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                print(f"[ERROR] Failed to send sticker: {e}")
                await query.message.reply_text(
                    f"⚠️ Failed to send sticker: {e}. Proceeding with the post...", 
                    parse_mode=ParseMode.HTML
                )
        else:
            await query.message.reply_text(
                "⚠️ `STICKER_ID` is not set. Waiting 30 seconds...", 
                parse_mode=ParseMode.MARKDOWN
            )

        # 3. Delay for 30 seconds
        await asyncio.sleep(30)

        # 4. Send the main post
        try:
            await bot.send_photo(
                chat_id=target_chat_id,
                photo=banner_file_id, 
                caption=msg,
                parse_mode=ParseMode.HTML,
                reply_markup=kb
            )
            await query.message.reply_text(f"✅ Post sent to {target_chat_id} successfully.")
        except Exception as e:
            print(f"[ERROR] Sending photo failed: {e}")
            await query.message.reply_text(f"Failed to send to channel: {e}")
        return

    # === BUILD MENU HANDLER ===
    if query.data.startswith("bmenu_"):
        try:
            # Format: bmenu_action:user_id
            raw_action, expected_user_id = query.data.split(":", 1)
            action = raw_action.split("_")[1]
        except ValueError:
            await query.answer("Invalid data", show_alert=True)
            return

        # STRICT OWNERSHIP CHECK
        if str(user_id) != expected_user_id:
            await query.answer("⛔ This is not your menu!", show_alert=True)
            return

        config = context.user_data.get('build_config')
        if not config:
            await query.answer("Session expired. Please run /build again.", show_alert=True)
            return

        is_admin = user_id in ADMIN_USER_IDS

        # 1. HANDLE TOGGLES/CYCLES
        if action == "type":
            # Cycle: userdebug -> user -> eng
            modes = ["userdebug", "user", "eng"]
            curr = config['BUILD_TYPE']
            try:
                idx = modes.index(curr)
                config['BUILD_TYPE'] = modes[(idx + 1) % len(modes)]
            except ValueError:
                config['BUILD_TYPE'] = "userdebug"
            await query.answer(f"Type set to {config['BUILD_TYPE']}")

        elif action == "var":
            # Cycle: Test -> Release
            config['BUILD_VARIANT'] = "Release" if config['BUILD_VARIANT'] == "Test" else "Test"
            await query.answer(f"Variant set to {config['BUILD_VARIANT']}")

        elif action == "fsg":
            # Toggle 'DISABLE_FSGEN' (true <-> false)
            # Note: UI says "FSGen: ON" which means DISABLE=false
            config['DISABLE_FSGEN'] = "false" if config['DISABLE_FSGEN'] == "true" else "true"
            status = "Disabled" if config['DISABLE_FSGEN'] == "true" else "Enabled"
            await query.answer(f"FSGen {status}")

        elif action == "dirty":
            config['DIRTY_BUILD'] = "true" if config['DIRTY_BUILD'] == "false" else "false"
            await query.answer(f"Dirty Build: {config['DIRTY_BUILD']}")

        elif action == "clean":
            if not is_admin:
                await query.answer("⛔ Admin Only!", show_alert=True)
            else:
                config['CLEAN_BUILD'] = "true" if config['CLEAN_BUILD'] == "false" else "false"
                await query.answer(f"Clean Build: {config['CLEAN_BUILD']}")

        elif action == "locked":
             await query.answer("⛔ This option is locked for Admins.", show_alert=True)
             return

        elif action == "cancel":
            del context.user_data['build_config']
            await query.edit_message_text("❌ Build Configuration Cancelled.")
            return

        elif action == "start":
            # EXECUTE BUILD
            await query.answer("🚀 Starting...")
            
            # 1. Send Initial "Queued" Message to Channel/Chat
            # Send to the TELEGRAM_CHAT_ID defined in env (syncs with GitHub Actions)
            target_chat_id = TELEGRAM_CHAT_ID

            try:
                # Send placeholder message
                bot = ContextTypes.DEFAULT_TYPE(app).bot if not context.bot else context.bot
                
                # Format Boolean to Human Readable
                fsgen_status = "❌ Disabled" if config['DISABLE_FSGEN'] == "true" else "✅ Enabled"
                dirty_status = "✅ Yes" if config['DIRTY_BUILD'] == "true" else "❌ No"
                clean_status = "✅ Yes" if config['CLEAN_BUILD'] == "true" else "❌ No"

                try:
                    # Try sending to specific Topic first
                    queued_msg = await context.bot.send_message(
                        chat_id=target_chat_id,
                        message_thread_id=TELEGRAM_TOPIC_ID,
                        text=f"⏳ *Build Request Queued*\n"
                             f"*Device:* `{config['DEVICE']}`\n"
                             f"*Type:* `{config['BUILD_TYPE']}`\n"
                             f"*Variant:* `{config['BUILD_VARIANT']}`\n"
                             f"*FSGen:* `{fsgen_status}`\n"
                             f"*Dirty:* `{dirty_status}`\n"
                             f"*Clean:* `{clean_status}`\n"
                             f"*User:* `{config['REQUESTER']}`\n"
                             f"Handing over to GitHub Actions...",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception as e:
                    print(f"[WARN] Failed to send to Topic {TELEGRAM_TOPIC_ID}: {e}. Fallback to General/Current Thread.")
                    # Fallback: Send to the thread where command was issued (or General)
                    queued_msg = await context.bot.send_message(
                        chat_id=target_chat_id,
                        message_thread_id=query.message.message_thread_id,
                        text=f"⏳ *Build Request Queued (Fallback)*\n"
                             f"*Device:* `{config['DEVICE']}`\n"
                             f"*Type:* `{config['BUILD_TYPE']}`\n"
                             f"*Variant:* `{config['BUILD_VARIANT']}`\n"
                             f"*FSGen:* `{fsgen_status}`\n"
                             f"*Dirty:* `{dirty_status}`\n"
                             f"*Clean:* `{clean_status}`\n"
                             f"*User:* `{config['REQUESTER']}`\n"
                             f"Handing over to GitHub Actions...",
                        parse_mode=ParseMode.MARKDOWN
                    )

                msg_id = queued_msg.message_id
                
                # Save to Redis for /status linking
                # Key: build_msg:<gh_user> (Simplification: assuming 1 active build per user usually)
                # Value: chat_id:message_id
                await run_redis_command(redis_client, "setex", f"build_msg:{config['REQUESTER']}", 86400, f"{target_chat_id}:{msg_id}")
                
            except Exception as e:
                print(f"[ERROR] Failed to send queue message: {e}")
                msg_id = None

            # 2. Trigger GitHub Dispatch with TG_MSG_ID
            url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/dispatches"
            
            # Inject TG_MSG_ID into payload
            if msg_id:
                config['TG_MSG_ID'] = msg_id
                # Also pass Chat ID if needed, but secrets handle that mostly
            
            # Pass Telegram User ID for tagging
            config['TG_USER_ID'] = user_id
            
            payload = {
                "event_type": "Telegram-Builder",
                "client_payload": config
            }
            
            def trigger():
                return requests.post(url, json=payload, headers=get_gh_headers())

            try:
                res = await asyncio.to_thread(trigger)
                if res.status_code == 204:
                    requester_link = f"https://github.com/{config['REQUESTER']}"
                    
                    # Generate Link
                    link_text = ""
                    if msg_id:
                        # Fix Chat ID for link (remove -100)
                        clean_id = str(target_chat_id).replace("-100", "")
                        
                        if TELEGRAM_TOPIC_ID:
                            link_url = f"https://t.me/c/{clean_id}/{TELEGRAM_TOPIC_ID}/{msg_id}"
                        else:
                            link_url = f"https://t.me/c/{clean_id}/{msg_id}"
                            
                        link_text = f"\n📡 <a href='{link_url}'>See monitor progress</a>"
                    
                    await query.edit_message_text(
                        f"✅ <b>Build Request Sent!</b>\n"
                        f"<b>Device:</b> <code>{config['DEVICE']}</code>\n"
                        f"<b>Type:</b> <code>{config['BUILD_TYPE']}</code>\n"
                        f"<b>Requester:</b> <a href='{requester_link}'>{config['REQUESTER']}</a>"
                        f"{link_text}",
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True
                    )
                else:
                    await query.edit_message_text(f"❌ Failed to trigger. Code: {res.status_code}\n{res.text}")
            except Exception as e:
                await query.edit_message_text(f"Error: {e}")
            
            # Cleanup
            del context.user_data['build_config']
            return

        # UPDATE UI (If not start/cancel)
        await query.edit_message_text(
            get_build_config_text(config),
            reply_markup=get_build_keyboard(config, is_admin, user_id),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
        return

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
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("post", post_command))
    app.add_handler(CommandHandler("banner", view_banner_command))
    app.add_handler(CommandHandler("setbanner", set_banner_command))
    app.add_handler(CommandHandler("removebanner", remove_banner_command))
    
    # Build Bot Handlers
    app.add_handler(CommandHandler("build", build_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("quota", quota_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("adduser", add_user_command))
    app.add_handler(CommandHandler("removeuser", remove_user_command))
    app.add_handler(CommandHandler("revoke", revoke_command))
    
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
