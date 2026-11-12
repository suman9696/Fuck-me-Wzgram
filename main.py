import pyrogram
from pyrogram import Client, filters
from pyrogram.errors import UserAlreadyParticipant, InviteHashExpired, UsernameNotOccupied, FloodWait
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import time
import os
import threading
import json
import hmac
from html import escape
from flask import Flask, request, Response
from werkzeug.serving import make_server
from os import environ


def safe_call(function, *args, **kwargs):
    """Retry the same Telegram API operation after Telegram's FloodWait."""
    retries = 0
    while True:
        try:
            return function(*args, **kwargs)
        except FloodWait as error:
            retries += 1
            wait_seconds = int(getattr(error, "value", getattr(error, "x", 1)))
            print(f"FloodWait: sleeping {wait_seconds + 1}s before retry ({retries}/5)")
            time.sleep(wait_seconds + 1)
            if retries >= 5:
                raise


bot_token = environ["TOKEN"]

api_hash = environ["HASH"]

api_id = int(environ["ID"])

# Pyrogram's transfer concurrency. Start with 4; increase cautiously if
# your Heroku dyno has enough CPU, memory and network capacity.
MAX_CONCURRENT_TRANSMISSIONS = int(environ.get("MAX_CONCURRENT_TRANSMISSIONS", "4"))

bot = Client(
    "mybot",
    api_id=api_id,
    api_hash=api_hash,
    bot_token=bot_token,
    max_concurrent_transmissions=MAX_CONCURRENT_TRANSMISSIONS
)

ss = environ["STRING"]
if ss:
    acc = Client(
        "myacc",
        api_id=api_id,
        api_hash=api_hash,
        session_string=ss,
        max_concurrent_transmissions=MAX_CONCURRENT_TRANSMISSIONS
    )
    acc.start()
else: acc = None


# Shared destination settings. Anyone can change these using the bot commands.
OWNER_ID = int(environ.get("OWNER_ID", "0"))
TARGET_CHAT_ID = environ.get("TARGET_CHAT_ID", "").strip()
AUTO_UPLOAD = True  # Auto Upload is ON by default; no Heroku Config Var needed.
TARGET_CONFIG_FILE = "target_config.json"

def _load_target_config():
    global TARGET_CHAT_ID, AUTO_UPLOAD
    try:
        with open(TARGET_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        TARGET_CHAT_ID = str(data.get("target_chat_id") or TARGET_CHAT_ID).strip()
        AUTO_UPLOAD = bool(data.get("auto_upload", AUTO_UPLOAD))
    except (OSError, ValueError, TypeError):
        pass

def _save_target_config():
    with open(TARGET_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({"target_chat_id": TARGET_CHAT_ID, "auto_upload": AUTO_UPLOAD}, f)

_load_target_config()

def _is_owner(message):
    return bool(message.from_user and OWNER_ID and message.from_user.id == OWNER_ID)

def _destinations(private_chat_id):
    # Always deliver to the user's private bot chat; add the target only when enabled.
    result = [private_chat_id]
    if AUTO_UPLOAD and TARGET_CHAT_ID:
        target = int(TARGET_CHAT_ID) if TARGET_CHAT_ID.lstrip("-").isdigit() else TARGET_CHAT_ID
        if target != private_chat_id:
            result.append(target)
    return result

def _send_copy_to_destinations(source_chat_id, source_message_id, private_chat_id, reply_to_id=None):
    for destination in _destinations(private_chat_id):
        kwargs = {}
        if destination == private_chat_id and reply_to_id:
            kwargs["reply_to_message_id"] = reply_to_id
        safe_call(bot.copy_message, destination, source_chat_id, source_message_id, **kwargs)


# Progress status display
_STATUS_POLL_INTERVAL = 2.0
_PROGRESS_WRITE_INTERVAL = 1.0
_progress_state = {}
_progress_lock = threading.Lock()

def _status_worker(statusfile, message, label):
    last_text = None
    while True:
        if os.path.exists(statusfile):
            break
        time.sleep(0.2)

    while os.path.exists(statusfile):
        try:
            with open(statusfile, "r", encoding="utf-8") as status_file:
                details = status_file.read().strip()
            if details and details != last_text:
                safe_call(bot.edit_message_text,
                    message.chat.id, message.id,
                    f"{label}\n{details}"
                )
                last_text = details
        except Exception:
            # Telegram may reject an unchanged message or briefly be unavailable.
            pass
        time.sleep(_STATUS_POLL_INTERVAL)

def downstatus(statusfile, message):
    _status_worker(statusfile, message, "📥 **DOWNLOADING**")

def upstatus(statusfile, message):
    _status_worker(statusfile, message, "📤 **UPLOADING**")

def progress(current, total, message, transfer_type):
    """Render a compact progress bar with transfer speed and ETA."""
    if not total or total <= 0:
        return

    now = time.monotonic()
    key = (message.chat.id, message.id, transfer_type)
    status_path = f"{message.chat.id}_{message.id}_{transfer_type}status.txt"
    temp_path = status_path + ".tmp"

    with _progress_lock:
        state = _progress_state.get(key)
        if state is None:
            state = {"last_time": now, "last_bytes": current, "speed": 0.0, "last_write": 0.0}
            _progress_state[key] = state
        elapsed = now - state["last_time"]
        if elapsed > 0.25:
            instant_speed = max(0, current - state["last_bytes"]) / elapsed
            # Smooth short-term speed fluctuations.
            state["speed"] = instant_speed if not state["speed"] else (0.7 * state["speed"] + 0.3 * instant_speed)
            state["last_time"] = now
            state["last_bytes"] = current

        complete = current >= total
        if not complete and now - state["last_write"] < _PROGRESS_WRITE_INTERVAL:
            return
        state["last_write"] = now
        speed = state["speed"]

    percent = min(100.0, current * 100.0 / total)
    filled = int(percent // 10)
    bar = "█" * filled + "░" * (10 - filled)
    speed_text = f"{speed / (1024 * 1024):.2f} MB/s" if speed > 0 else "Calculating…"
    remaining = max(0, total - current)
    eta_seconds = int(remaining / speed) if speed > 0 else 0
    if speed > 0:
        minutes, seconds = divmod(eta_seconds, 60)
        eta_text = f"{minutes:02d}:{seconds:02d}"
    else:
        eta_text = "Calculating…"

    details = (
        f"`{bar}` **{percent:.1f}%**\n"
        f"**Size:** {current / (1024 * 1024):.1f} / {total / (1024 * 1024):.1f} MB\n"
        f"**Speed:** {speed_text}\n"
        f"**ETA:** {eta_text}"
    )
    try:
        with open(temp_path, "w", encoding="utf-8") as status_file:
            status_file.write(details)
        os.replace(temp_path, status_path)
    except OSError:
        pass

    if complete:
        with _progress_lock:
            _progress_state.pop(key, None)


# start command
@bot.on_message(filters.command(["start"]))
def send_start(client: pyrogram.client.Client, message: pyrogram.types.messages_and_media.message.Message):
    safe_call(bot.send_message, message.chat.id, f"**♻️ 𝙷𝚒** **{message.from_user.mention}**, **𝐈 𝐚𝐦 𝐒𝐚𝐯𝐞 𝐑𝐞𝐬𝐭𝐫𝐢𝐜𝐭𝐞𝐝 𝐁𝐨𝐭 , 𝐈 𝐜𝐚𝐧 𝐬𝐞𝐧𝐝 𝐲𝐨𝐮 𝐫𝐞𝐬𝐭𝐫𝐢𝐜𝐭𝐞𝐝 𝐜𝐨𝐧𝐭𝐞𝐧𝐭 𝐛𝐲 𝐢𝐭'𝐬 𝐩𝐨𝐬𝐭 𝐥𝐢𝐧𝐤**\n\n{USAGE}",
    reply_markup=InlineKeyboardMarkup([[ InlineKeyboardButton("☢️ 𝐁𝐨𝐭 𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫", url="tg://openmessage?user_id=2112247533")]]), reply_to_message_id=message.id)



@bot.on_message(filters.command(["settarget"]) & filters.private)
def set_target_command(client, message):
    global TARGET_CHAT_ID
    if len(message.command) < 2:
        return safe_call(bot.send_message, message.chat.id,
                         "Usage: `/settarget -1001234567890` or `/settarget @channelusername`")
    value = message.command[1].strip()
    try:
        TARGET_CHAT_ID = str(int(value)) if value.lstrip("-").isdigit() else value
        chat = safe_call(bot.get_chat, int(TARGET_CHAT_ID) if TARGET_CHAT_ID.lstrip("-").isdigit() else TARGET_CHAT_ID)
        _save_target_config()
        safe_call(bot.send_message, message.chat.id,
                  f"✅ Target set to **{chat.title or chat.first_name or TARGET_CHAT_ID}** (`{TARGET_CHAT_ID}`).")
    except Exception as e:
        TARGET_CHAT_ID = ""
        safe_call(bot.send_message, message.chat.id,
                  f"❌ Could not access that chat: `{str(e)[:400]}`\nAdd the bot to the group/channel first and give it posting permission.")

@bot.on_message(filters.command(["removetarget"]) & filters.private)
def remove_target_command(client, message):
    """Remove the shared destination target and disable automatic forwarding."""
    global TARGET_CHAT_ID, AUTO_UPLOAD
    if not TARGET_CHAT_ID:
        return safe_call(bot.send_message, message.chat.id, "ℹ️ No target is currently configured.")

    old_target = TARGET_CHAT_ID
    TARGET_CHAT_ID = ""
    AUTO_UPLOAD = False
    _save_target_config()
    safe_call(bot.send_message, message.chat.id,
              f"✅ Target `{old_target}` removed. Auto upload is now **OFF**.")


@bot.on_message(filters.command(["mytargets"]) & filters.private)
def my_targets_command(client, message):
    status = "ON" if AUTO_UPLOAD else "OFF"
    safe_call(bot.send_message, message.chat.id,
              f"**Target:** `{TARGET_CHAT_ID or 'Not set'}`\n**Auto upload:** `{status}`")

@bot.on_message(filters.command(["autoupload"]) & filters.private)
def auto_upload_command(client, message):
    global AUTO_UPLOAD
    if len(message.command) < 2 or message.command[1].lower() not in ("on", "off"):
        return safe_call(bot.send_message, message.chat.id, "Usage: `/autoupload on` or `/autoupload off`")
    if message.command[1].lower() == "on" and not TARGET_CHAT_ID:
        return safe_call(bot.send_message, message.chat.id, "❌ Set a target first using `/settarget`.")
    AUTO_UPLOAD = message.command[1].lower() == "on"
    _save_target_config()
    safe_call(bot.send_message, message.chat.id,
              f"✅ Auto upload **{'ON' if AUTO_UPLOAD else 'OFF'}**.")

@bot.on_message(filters.text)
def save(client: pyrogram.client.Client, message: pyrogram.types.messages_and_media.message.Message):
    """Process each incoming link sequentially; no parallel job executor."""
    try:
        _process_message(client, message)
    except FloodWait as error:
        # Normally handled by safe_call; this catches any FloodWait raised
        # outside a wrapped API call.
        wait_seconds = int(getattr(error, "value", getattr(error, "x", 1)))
        safe_call(time.sleep, wait_seconds + 1)
        try:
            _process_message(client, message)
        except Exception as retry_error:
            print(f"Task failed after FloodWait retry: {retry_error!r}")
            try:
                safe_call(bot.send_message, message.chat.id,
                          f"❌ **Task failed:** `{str(retry_error)[:700]}`",
                          reply_to_message_id=message.id)
            except Exception as send_error:
                print(f"Could not report task error: {send_error!r}")
    except Exception as error:
        print(f"Task failed: {error!r}")
        try:
            safe_call(bot.send_message, message.chat.id,
                      f"❌ **Task failed:** `{str(error)[:700]}`",
                      reply_to_message_id=message.id)
        except Exception as send_error:
            print(f"Could not report task error: {send_error!r}")


def _process_message(client: pyrogram.client.Client, message: pyrogram.types.messages_and_media.message.Message):
    print(message.text)

    # Joining private chats
    if "https://t.me/+" in message.text or "https://t.me/joinchat/" in message.text:
        if acc is None:
            safe_call(bot.send_message, message.chat.id, "**String Session is not Set**", reply_to_message_id=message.id)
            return
        try:
            safe_call(acc.join_chat, message.text)
            safe_call(bot.send_message, message.chat.id, "**Chat Joined**", reply_to_message_id=message.id)
        except UserAlreadyParticipant:
            safe_call(bot.send_message, message.chat.id, "**Chat already Joined**", reply_to_message_id=message.id)
        except InviteHashExpired:
            safe_call(bot.send_message, message.chat.id, "**Invalid Link**", reply_to_message_id=message.id)
        except Exception as error:
            safe_call(bot.send_message, message.chat.id, f"**Error:** __{error}__", reply_to_message_id=message.id)
        return

    if "https://t.me/" not in message.text:
        return

    datas = message.text.split("/")
    temp = datas[-1].replace("?single", "").split("-")
    fromID = int(temp[0].strip())
    toID = int(temp[1].strip()) if len(temp) > 1 else fromID

    for msgid in range(fromID, toID + 1):
        if "https://t.me/c/" in message.text:
            chatid = int("-100" + datas[4])
            if acc is None:
                safe_call(bot.send_message, message.chat.id, "**String Session is not Set**", reply_to_message_id=message.id)
                return
            handle_private(message, chatid, msgid)

        elif "https://t.me/b/" in message.text:
            username = datas[4]
            if acc is None:
                safe_call(bot.send_message, message.chat.id, "**String Session is not Set**", reply_to_message_id=message.id)
                return
            handle_private(message, username, msgid)

        else:
            username = datas[3]
            try:
                msg = safe_call(bot.get_messages, username, msgid)
            except UsernameNotOccupied:
                safe_call(bot.send_message, message.chat.id,
                          "**The username is not occupied by anyone**",
                          reply_to_message_id=message.id)
                return

            try:
                _send_copy_to_destinations(msg.chat.id, msg.id, message.chat.id, message.id)
            except Exception:
                if acc is None:
                    safe_call(bot.send_message, message.chat.id,
                              "**String Session is not Set**",
                              reply_to_message_id=message.id)
                    return
                handle_private(message, username, msgid)


# handle private
def handle_private(message: pyrogram.types.messages_and_media.message.Message, chatid: int, msgid: int):
        msg: pyrogram.types.messages_and_media.message.Message = safe_call(acc.get_messages, chatid, msgid)
        msg_type = get_message_type(msg)

        # Send/upload once to the user's private chat. Then copy Telegram's
        # already-uploaded message to the target chat (no second upload/progress).
        if "Text" == msg_type:
            sent = safe_call(bot.send_message, message.chat.id, msg.text,
                             entities=msg.entities, reply_to_message_id=message.id)
            if AUTO_UPLOAD and TARGET_CHAT_ID:
                target = int(TARGET_CHAT_ID) if TARGET_CHAT_ID.lstrip("-").isdigit() else TARGET_CHAT_ID
                if target != message.chat.id:
                    safe_call(bot.copy_message, target, sent.chat.id, sent.id)
            return

        smsg = safe_call(bot.send_message, message.chat.id, '**Loading 🔄**', reply_to_message_id=message.id)
        dosta = threading.Thread(target=lambda:downstatus(f'{message.chat.id}_{message.id}_downstatus.txt',smsg),daemon=True)
        dosta.start()
        file = safe_call(acc.download_media, msg, progress=progress, progress_args=[message,"down"])
        down_file = f'{message.chat.id}_{message.id}_downstatus.txt'
        if os.path.exists(down_file):
            os.remove(down_file)

        upsta = threading.Thread(target=lambda:upstatus(f'{message.chat.id}_{message.id}_upstatus.txt',smsg),daemon=True)
        upsta.start()

        common = {"reply_to_message_id": message.id}
        thumb = None
        if "Document" == msg_type:
            try:
                thumb = safe_call(acc.download_media, msg.document.thumbs[0].file_id)
            except Exception:
                thumb = None
            sent = safe_call(bot.send_document, message.chat.id, file, thumb=thumb,
                             caption=msg.caption, caption_entities=msg.caption_entities,
                             progress=progress, progress_args=[message, "up"], **common)

        elif "Video" == msg_type:
            try:
                thumb = safe_call(acc.download_media, msg.video.thumbs[0].file_id)
            except Exception:
                thumb = None
            sent = safe_call(bot.send_video, message.chat.id, file, duration=msg.video.duration,
                             width=msg.video.width, height=msg.video.height, thumb=thumb,
                             caption=msg.caption, caption_entities=msg.caption_entities,
                             progress=progress, progress_args=[message, "up"], **common)

        elif "Animation" == msg_type:
            sent = safe_call(bot.send_animation, message.chat.id, file, caption=msg.caption, **common)

        elif "Sticker" == msg_type:
            sent = safe_call(bot.send_sticker, message.chat.id, file, **common)

        elif "Voice" == msg_type:
            sent = safe_call(bot.send_voice, message.chat.id, file, caption=msg.caption,
                             caption_entities=msg.caption_entities,
                             progress=progress, progress_args=[message, "up"], **common)

        elif "Audio" == msg_type:
            sent = safe_call(bot.send_audio, message.chat.id, file, caption=msg.caption,
                             caption_entities=msg.caption_entities,
                             progress=progress, progress_args=[message, "up"], **common)

        elif "Photo" == msg_type:
            sent = safe_call(bot.send_photo, message.chat.id, file, caption=msg.caption,
                             caption_entities=msg.caption_entities, **common)
        else:
            sent = None

        if thumb and os.path.exists(thumb):
            os.remove(thumb)

        # Copy the completed upload to the configured destination. This is a
        # server-side Telegram copy, so the download/upload progress runs once.
        if sent and AUTO_UPLOAD and TARGET_CHAT_ID:
            target = int(TARGET_CHAT_ID) if TARGET_CHAT_ID.lstrip("-").isdigit() else TARGET_CHAT_ID
            if target != message.chat.id:
                safe_call(bot.copy_message, target, sent.chat.id, sent.id)

        if file and os.path.exists(file):
            os.remove(file)
        up_file = f'{message.chat.id}_{message.id}_upstatus.txt'
        if os.path.exists(up_file):
            os.remove(up_file)
        safe_call(bot.delete_messages, message.chat.id, [smsg.id])


# get the type of message
def get_message_type(msg: pyrogram.types.messages_and_media.message.Message):
    try:
        msg.document.file_id
        return "Document"
    except: pass

    try:
        msg.video.file_id
        return "Video"
    except: pass

    try:
        msg.animation.file_id
        return "Animation"
    except: pass

    try:
        msg.sticker.file_id
        return "Sticker"
    except: pass

    try:
        msg.voice.file_id
        return "Voice"
    except: pass

    try:
        msg.audio.file_id
        return "Audio"
    except: pass

    try:
        msg.photo.file_id
        return "Photo"
    except: pass

    try:
        msg.text
        return "Text"
    except: pass


# Heroku config var (optional):
# OWNER_ID=your_personal_numeric_telegram_id
# TARGET_CHAT_ID=-1001234567890 (optional)
# AUTO_UPLOAD=on (default; optional)
# MAX_CONCURRENT_TRANSMISSIONS=4
# This controls Pyrogram's parallel file-transfer parts, not parallel jobs.
# Start at 4; increasing it may increase memory use or trigger Telegram limits.

USAGE = """**🈂️ 𝐅𝐨𝐫 𝐏𝐮𝐛𝐥𝐢𝐜 𝐂𝐡𝐚𝐭𝐬 🈂️**
𝐉𝐮𝐬𝐭 𝐒𝐞𝐧𝐝 𝐏𝐨𝐬𝐭/𝐬 𝐋𝐢𝐧𝐤

**🈂️ 𝐅𝐨𝐫 𝐏𝐫𝐢𝐯𝐚𝐭𝐞 𝐂𝐡𝐚𝐭𝐬 🈂️**
𝐟𝐢𝐫𝐬𝐭 𝐬𝐞𝐧𝐝 𝐢𝐧𝐯𝐢𝐭𝐞 𝐥𝐢𝐧𝐤 𝐨𝐟 𝐭𝐡𝐞 𝐜𝐡𝐚𝐭 (𝐮𝐧𝐧𝐞𝐜𝐞𝐬𝐬𝐚𝐫𝐲 𝐢𝐟 𝐭𝐡𝐞 𝐚𝐜𝐜𝐨𝐮𝐧𝐭 𝐨𝐟 𝐬𝐭𝐫𝐢𝐧𝐠 𝐬𝐞𝐬𝐬𝐢𝐨𝐧 𝐚𝐥𝐫𝐞𝐚𝐝𝐲 𝐦𝐞𝐦𝐛𝐞𝐫 𝐨𝐟 𝐭𝐡𝐞 𝐜𝐡𝐚𝐭) 𝐭𝐡𝐞𝐧 𝐬𝐞𝐧𝐝 𝐩𝐨𝐬𝐭/𝐬 𝐥𝐢𝐧𝐤

**🈂️ 𝐌𝐮𝐥𝐭𝐢 𝐏𝐨𝐬𝐭𝐬 🈂️**
𝚑𝚝𝚝𝚙𝚜://𝚝.𝚖𝚎/𝚌/𝚡𝚡𝚡𝚡/101 - 110

"""


# ---------------------------------------------------------------------------
# Browser dashboard. Set DASHBOARD_PASSWORD in Heroku Config Vars.
# The web dyno runs this Flask server and the Telegram bot in one process.
# ---------------------------------------------------------------------------
app = Flask(__name__)
_dashboard_jobs = []
_dashboard_jobs_lock = threading.Lock()
_dashboard_job_counter = 0
_dashboard_password = environ.get("DASHBOARD_PASSWORD", "").strip()


def _dashboard_required():
    if not _dashboard_password:
        return Response("Dashboard disabled: set DASHBOARD_PASSWORD in Heroku Config Vars.", status=503)
    supplied = request.authorization.password if request.authorization else ""
    if not hmac.compare_digest(supplied or "", _dashboard_password):
        return Response("Authentication required", 401,
                        {"WWW-Authenticate": 'Basic realm="Telegram Upload Dashboard"'})
    return None


def _dashboard_page(message=""):
    with _dashboard_jobs_lock:
        jobs = list(reversed(_dashboard_jobs[-20:]))
    rows = "".join(
        f"<tr><td>#{j['id']}</td><td>{escape(j['link'])}</td><td>{escape(j['status'])}</td><td>{escape(j.get('detail', ''))}</td></tr>"
        for j in jobs
    ) or '<tr><td colspan="4">No browser uploads yet.</td></tr>'
    target = escape(TARGET_CHAT_ID or "Not configured")
    auto = "ON" if AUTO_UPLOAD else "OFF"
    notice = f'<p class="notice">{escape(message)}</p>' if message else ""
    page = f'''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Telegram Upload Dashboard</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#0b1020;color:#e8edf8;font:15px Arial,sans-serif;padding:24px}}
.wrap{{max-width:920px;margin:auto}}h1{{margin:0 0 8px}}.muted{{color:#9ba8c4}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin:22px 0}}
.card,form,.tablebox{{background:#151d33;border:1px solid #283552;border-radius:14px;padding:18px}}.value{{font-size:22px;font-weight:bold;margin-top:8px}}
input{{width:100%;padding:13px;border-radius:9px;border:1px solid #405071;background:#0d1528;color:white;margin:8px 0 12px}}
button{{background:#5b7cfa;color:white;border:0;padding:12px 18px;border-radius:9px;font-weight:bold;cursor:pointer}}
.notice{{background:#1d3150;padding:12px;border-radius:9px}}table{{width:100%;border-collapse:collapse;min-width:600px}}th,td{{text-align:left;padding:12px;border-bottom:1px solid #293550;overflow-wrap:anywhere}}.tablebox{{overflow-x:auto}}
</style></head><body><div class="wrap"><h1>🚀 Telegram Upload Dashboard</h1><div class="muted">Manage Telegram post uploads from your browser.</div>{notice}
<div class="grid"><div class="card"><div class="muted">Bot</div><div class="value">🟢 Running</div></div><div class="card"><div class="muted">Auto Upload</div><div class="value">{auto}</div></div><div class="card"><div class="muted">Target Chat</div><div class="value" style="font-size:16px">{target}</div></div></div>
<form method="post" action="/upload"><h2>📤 Start an upload</h2><label>Telegram post link</label><input name="link" type="url" required placeholder="https://t.me/channel/123"><button type="submit">Start Upload</button></form>
<div class="tablebox" style="margin-top:20px"><h2>📋 Recent browser uploads</h2><table><thead><tr><th>Job</th><th>Link</th><th>Status</th><th>Details</th></tr></thead><tbody>{rows}</tbody></table></div>
<p class="muted">Refresh this page to see the latest job status.</p></div></body></html>'''
    return Response(page, mimetype="text/html")


@app.route("/", methods=["GET"])
def dashboard_home():
    auth = _dashboard_required()
    return auth if auth else _dashboard_page()


@app.route("/upload", methods=["POST"])
def dashboard_upload():
    global _dashboard_job_counter
    auth = _dashboard_required()
    if auth:
        return auth
    link = (request.form.get("link") or "").strip()
    if not link.startswith("https://t.me/"):
        return _dashboard_page("Please enter a valid https://t.me/ post link."), 400
    if not OWNER_ID:
        return _dashboard_page("Set OWNER_ID in Heroku Config Vars to enable browser uploads."), 503
    with _dashboard_jobs_lock:
        _dashboard_job_counter += 1
        job = {"id": _dashboard_job_counter, "link": link, "status": "Queued", "detail": "Waiting to start"}
        _dashboard_jobs.append(job)
        del _dashboard_jobs[:-50]

    def run_job():
        try:
            sent_message = safe_call(bot.send_message, OWNER_ID, link)
            with _dashboard_jobs_lock:
                job["status"] = "Processing"
                job["detail"] = "Processing through the Telegram bot"
            _process_message(bot, sent_message)
            with _dashboard_jobs_lock:
                job["status"] = "Completed"
                job["detail"] = "Processing finished; check your Telegram chat"
        except Exception as error:
            with _dashboard_jobs_lock:
                job["status"] = "Failed"
                job["detail"] = str(error)[:250]
            print(f"Dashboard upload #{job['id']} failed: {error!r}")

    threading.Thread(target=run_job, daemon=True).start()
    return _dashboard_page(f"Upload job #{job['id']} has been queued.")


def _start_dashboard():
    port = int(environ.get("PORT", "8000"))
    make_server("0.0.0.0", port, app, threaded=True).serve_forever()


if __name__ == "__main__":
    threading.Thread(target=_start_dashboard, daemon=True).start()
    bot.run()
