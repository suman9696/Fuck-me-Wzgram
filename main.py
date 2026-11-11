import pyrogram
from pyrogram import Client, filters
from pyrogram.errors import UserAlreadyParticipant, InviteHashExpired, UsernameNotOccupied, FloodWait
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import time
import os
import threading
import json
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
                safe_call(bot.copy_message, message.chat.id, msg.chat.id, msg.id,
                          reply_to_message_id=message.id)
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

        if "Text" == msg_type:
            safe_call(bot.send_message, message.chat.id, msg.text, entities=msg.entities, reply_to_message_id=message.id)
            return

        smsg = safe_call(bot.send_message, message.chat.id, '**Loading 🔄**', reply_to_message_id=message.id)
        dosta = threading.Thread(target=lambda:downstatus(f'{message.chat.id}_{message.id}_downstatus.txt',smsg),daemon=True)
        dosta.start()
        file = safe_call(acc.download_media, msg, progress=progress, progress_args=[message,"down"])
        os.remove(f'{message.chat.id}_{message.id}_downstatus.txt')

        upsta = threading.Thread(target=lambda:upstatus(f'{message.chat.id}_{message.id}_upstatus.txt',smsg),daemon=True)
        upsta.start()
        
        if "Document" == msg_type:
            try:
                thumb = safe_call(acc.download_media, msg.document.thumbs[0].file_id)
            except: thumb = None
            
            safe_call(bot.send_document, message.chat.id, file, thumb=thumb, caption=msg.caption, caption_entities=msg.caption_entities, reply_to_message_id=message.id, progress=progress, progress_args=[message,"up"])
            if thumb != None: os.remove(thumb)

        elif "Video" == msg_type:
            try: 
                thumb = safe_call(acc.download_media, msg.video.thumbs[0].file_id)
            except: thumb = None

            safe_call(bot.send_video, message.chat.id, file, duration=msg.video.duration, width=msg.video.width, height=msg.video.height, thumb=thumb, caption=msg.caption, caption_entities=msg.caption_entities, reply_to_message_id=message.id, progress=progress, progress_args=[message,"up"])
            if thumb != None: os.remove(thumb)

        elif "Animation" == msg_type:
            safe_call(bot.send_animation, message.chat.id, file, reply_to_message_id=message.id)
               
        elif "Sticker" == msg_type:
            safe_call(bot.send_sticker, message.chat.id, file, reply_to_message_id=message.id)

        elif "Voice" == msg_type:
            thumb = None
            safe_call(bot.send_voice, message.chat.id, file, caption=msg.caption, thumb=thumb, caption_entities=msg.caption_entities, reply_to_message_id=message.id, progress=progress, progress_args=[message,"up"])

        elif "Audio" == msg_type:
            try:
                thumb = safe_call(acc.download_media, msg.audio.thumbs[0].file_id)
            except: thumb = None
                
            safe_call(bot.send_audio, message.chat.id, file, caption=msg.caption, caption_entities=msg.caption_entities, reply_to_message_id=message.id, progress=progress, progress_args=[message,"up"])   
            if thumb != None: os.remove(thumb)

        elif "Photo" == msg_type:
            safe_call(bot.send_photo, message.chat.id, file, caption=msg.caption, caption_entities=msg.caption_entities, reply_to_message_id=message.id)

        os.remove(file)
        if os.path.exists(f'{message.chat.id}_{message.id}_upstatus.txt'): os.remove(f'{message.chat.id}_{message.id}_upstatus.txt')
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


# infinty polling
bot.run()
