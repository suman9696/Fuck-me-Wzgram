"""Restricted-content mirror bot powered by WZGram (async Pyrogram-compatible API)."""

import asyncio
import json
import os
import time
from os import environ

import pyrogram
from pyrogram import Client, filters, compose
from pyrogram.errors import (
    FloodWait,
    InviteHashExpired,
    UserAlreadyParticipant,
    UsernameNotOccupied,
)
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BOT_TOKEN = environ["TOKEN"]
API_HASH = environ["HASH"]
API_ID = int(environ["ID"])
STRING_SESSION = environ.get("STRING", "").strip()
# WZGram download tuning.
# These are moderate starting values for a non-Premium user session.
# WZGram can still be limited by Telegram/DC routing and Heroku networking.
environ.setdefault("WZGRAM_DL_POOL_USER", "6")
environ.setdefault("WZGRAM_DL_WORKERS_USER", "4")
environ.setdefault("WZGRAM_DL_RATE_USER", "16")
environ.setdefault("WZGRAM_DL_BURST_USER", "12")

MAX_CONCURRENT_TRANSMISSIONS = int(
    environ.get("MAX_CONCURRENT_TRANSMISSIONS", "8")
)

bot = Client(
    "mybot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    max_concurrent_transmissions=MAX_CONCURRENT_TRANSMISSIONS,
    sleep_threshold=60,
)

acc = (
    Client(
        "myacc",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=STRING_SESSION,
        max_concurrent_transmissions=MAX_CONCURRENT_TRANSMISSIONS,
        sleep_threshold=60,
    )
    if STRING_SESSION
    else None
)

OWNER_ID = int(environ.get("OWNER_ID", "0"))
TARGET_CHAT_ID = environ.get("TARGET_CHAT_ID", "").strip()
AUTO_UPLOAD = True
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
        json.dump(
            {"target_chat_id": TARGET_CHAT_ID, "auto_upload": AUTO_UPLOAD},
            f,
        )


_load_target_config()


def _chat_id(value):
    value = str(value).strip()
    return int(value) if value.lstrip("-").isdigit() else value


async def safe_call(function, *args, **kwargs):
    """Await a WZGram API call, retrying bounded FloodWait errors."""
    retries = 0
    while True:
        try:
            return await function(*args, **kwargs)
        except FloodWait as error:
            retries += 1
            wait_seconds = int(
                getattr(error, "value", getattr(error, "x", 1))
            )
            print(f"FloodWait: sleeping {wait_seconds + 1}s ({retries}/5)")
            if retries >= 5:
                raise
            await asyncio.sleep(wait_seconds + 1)


def _destinations(private_chat_id):
    # Always send to the user's private bot chat; optionally copy to one target.
    result = [private_chat_id]
    if AUTO_UPLOAD and TARGET_CHAT_ID:
        target = _chat_id(TARGET_CHAT_ID)
        if target != private_chat_id:
            result.append(target)
    return result


async def _send_copy_to_destinations(
    source_chat_id, source_message_id, private_chat_id, reply_to_id=None
):
    for destination in _destinations(private_chat_id):
        kwargs = {}
        if destination == private_chat_id and reply_to_id:
            kwargs["reply_to_message_id"] = reply_to_id
        await safe_call(
            bot.copy_message,
            destination,
            source_chat_id,
            source_message_id,
            **kwargs,
        )


# ---------------------------------------------------------------------------
# Progress display
# ---------------------------------------------------------------------------
_PROGRESS_WRITE_INTERVAL = 1.0
_progress_state = {}


async def progress(current, total, message, transfer_type):
    """Update the Telegram progress message with percentage, speed and ETA."""
    if not total or total <= 0:
        return

    now = time.monotonic()
    key = (message.chat.id, message.id, transfer_type)
    state = _progress_state.get(key)
    if state is None:
        state = {
            "last_time": now,
            "last_bytes": current,
            "speed": 0.0,
            "last_write": 0.0,
        }
        _progress_state[key] = state

    elapsed = now - state["last_time"]
    if elapsed > 0.25:
        instant_speed = max(0, current - state["last_bytes"]) / elapsed
        state["speed"] = (
            instant_speed
            if not state["speed"]
            else 0.7 * state["speed"] + 0.3 * instant_speed
        )
        state["last_time"] = now
        state["last_bytes"] = current

    complete = current >= total
    if not complete and now - state["last_write"] < _PROGRESS_WRITE_INTERVAL:
        return
    state["last_write"] = now

    percent = min(100.0, current * 100.0 / total)
    filled = int(percent // 10)
    bar = "█" * filled + "░" * (10 - filled)
    speed = state["speed"]
    speed_text = f"{speed / (1024 * 1024):.2f} MB/s" if speed > 0 else "Calculating…"
    eta_seconds = int(max(0, total - current) / speed) if speed > 0 else 0
    if speed > 0:
        minutes, seconds = divmod(eta_seconds, 60)
        eta_text = f"{minutes:02d}:{seconds:02d}"
    else:
        eta_text = "Calculating…"

    label = "📥 **DOWNLOADING**" if transfer_type == "down" else "📤 **UPLOADING**"
    text = (
        f"{label}\n"
        f"`{bar}` **{percent:.1f}%**\n"
        f"**Size:** {current / (1024 * 1024):.1f} / {total / (1024 * 1024):.1f} MB\n"
        f"**Speed:** {speed_text}\n"
        f"**ETA:** {eta_text}"
    )
    try:
        await bot.edit_message_text(message.chat.id, message.id, text)
    except Exception:
        # The next progress callback will retry the edit if Telegram is busy.
        pass

    if complete:
        _progress_state.pop(key, None)


# ---------------------------------------------------------------------------
# Bot commands
# ---------------------------------------------------------------------------
@bot.on_message(filters.command(["start"]))
async def send_start(client, message):
    mention = message.from_user.mention if message.from_user else "there"
    await safe_call(
        bot.send_message,
        message.chat.id,
        f"**♻️ 𝙷𝚒** **{mention}**, **𝐈 𝐚𝐦 𝐒𝐚𝐯𝐞 𝐑𝐞𝐬𝐭𝐫𝐢𝐜𝐭𝐞𝐝 𝐁𝐨𝐭 , 𝐈 𝐜𝐚𝐧 𝐬𝐞𝐧𝐝 𝐲𝐨𝐮 𝐫𝐞𝐬𝐭𝐫𝐢𝐜𝐭𝐞𝐝 𝐜𝐨𝐧𝐭𝐞𝐧𝐭 𝐛𝐲 𝐢𝐭'𝐬 𝐩𝐨𝐬𝐭 𝐥𝐢𝐧𝐤**\n\n{USAGE}",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("☢️ 𝐁𝐨𝐭 𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫", url="tg://openmessage?user_id=2112247533")]]
        ),
        reply_to_message_id=message.id,
    )


@bot.on_message(filters.command(["settarget"]) & filters.private)
async def set_target_command(client, message):
    global TARGET_CHAT_ID
    if len(message.command) < 2:
        return await safe_call(
            bot.send_message,
            message.chat.id,
            "Usage: `/settarget -1001234567890` or `/settarget @channelusername`",
        )

    value = message.command[1].strip()
    try:
        chat_id = _chat_id(value)
        chat = await safe_call(bot.get_chat, chat_id)
        TARGET_CHAT_ID = str(chat.id)
        _save_target_config()
        await safe_call(
            bot.send_message,
            message.chat.id,
            f"✅ Target set to **{chat.title or chat.first_name or TARGET_CHAT_ID}** (`{TARGET_CHAT_ID}`).",
        )
    except Exception as error:
        await safe_call(
            bot.send_message,
            message.chat.id,
            f"❌ Could not access that chat: `{str(error)[:400]}`\nAdd the bot to the group/channel first and give it posting permission.",
        )


@bot.on_message(filters.command(["removetarget"]) & filters.private)
async def remove_target_command(client, message):
    global TARGET_CHAT_ID, AUTO_UPLOAD
    if not TARGET_CHAT_ID:
        return await safe_call(
            bot.send_message, message.chat.id, "ℹ️ No target is currently configured."
        )

    old_target = TARGET_CHAT_ID
    TARGET_CHAT_ID = ""
    AUTO_UPLOAD = False
    _save_target_config()
    await safe_call(
        bot.send_message,
        message.chat.id,
        f"✅ Target `{old_target}` removed. Auto upload is now **OFF**.",
    )


@bot.on_message(filters.command(["mytargets"]) & filters.private)
async def my_targets_command(client, message):
    status = "ON" if AUTO_UPLOAD else "OFF"
    await safe_call(
        bot.send_message,
        message.chat.id,
        f"**Target:** `{TARGET_CHAT_ID or 'Not set'}`\n**Auto upload:** `{status}`",
    )


@bot.on_message(filters.command(["autoupload"]) & filters.private)
async def auto_upload_command(client, message):
    global AUTO_UPLOAD
    if len(message.command) < 2 or message.command[1].lower() not in ("on", "off"):
        return await safe_call(
            bot.send_message,
            message.chat.id,
            "Usage: `/autoupload on` or `/autoupload off`",
        )
    if message.command[1].lower() == "on" and not TARGET_CHAT_ID:
        return await safe_call(
            bot.send_message,
            message.chat.id,
            "❌ Set a target first using `/settarget`.",
        )

    AUTO_UPLOAD = message.command[1].lower() == "on"
    _save_target_config()
    await safe_call(
        bot.send_message,
        message.chat.id,
        f"✅ Auto upload **{'ON' if AUTO_UPLOAD else 'OFF'}**.",
    )


@bot.on_message(filters.text)
async def save(client, message):
    """Process each incoming link sequentially for this message."""
    try:
        await _process_message(client, message)
    except Exception as error:
        print(f"Task failed: {error!r}")
        try:
            await safe_call(
                bot.send_message,
                message.chat.id,
                f"❌ **Task failed:** `{str(error)[:700]}`",
                reply_to_message_id=message.id,
            )
        except Exception as send_error:
            print(f"Could not report task error: {send_error!r}")


# ---------------------------------------------------------------------------
# Link processing and restricted/private media handling
# ---------------------------------------------------------------------------
async def _process_message(client, message):
    text = message.text or ""
    print(text)

    if "https://t.me/+" in text or "https://t.me/joinchat/" in text:
        if acc is None:
            await safe_call(
                bot.send_message,
                message.chat.id,
                "**String Session is not Set**",
                reply_to_message_id=message.id,
            )
            return
        try:
            await safe_call(acc.join_chat, text.strip())
            reply = "**Chat Joined**"
        except UserAlreadyParticipant:
            reply = "**Chat already Joined**"
        except InviteHashExpired:
            reply = "**Invalid Link**"
        except Exception as error:
            reply = f"**Error:** __{error}__"
        await safe_call(
            bot.send_message, message.chat.id, reply, reply_to_message_id=message.id
        )
        return

    if "https://t.me/" not in text:
        return

    datas = text.split("/")
    try:
        tail = datas[-1].split("?")[0]
        ids = tail.split("-")
        from_id = int(ids[0].strip())
        to_id = int(ids[1].strip()) if len(ids) > 1 else from_id
        if to_id < from_id or to_id - from_id > 1000:
            raise ValueError("Invalid post range (maximum 1001 posts at a time).")
    except (ValueError, IndexError):
        await safe_call(
            bot.send_message,
            message.chat.id,
            "❌ Invalid Telegram post link or message range.",
            reply_to_message_id=message.id,
        )
        return

    for msg_id in range(from_id, to_id + 1):
        if "https://t.me/c/" in text:
            chat_id = int("-100" + datas[4])
            if acc is None:
                await safe_call(
                    bot.send_message,
                    message.chat.id,
                    "**String Session is not Set**",
                    reply_to_message_id=message.id,
                )
                return
            await handle_private(message, chat_id, msg_id)

        elif "https://t.me/b/" in text:
            username = datas[4]
            if acc is None:
                await safe_call(
                    bot.send_message,
                    message.chat.id,
                    "**String Session is not Set**",
                    reply_to_message_id=message.id,
                )
                return
            await handle_private(message, username, msg_id)

        else:
            username = datas[3]
            try:
                msg = await safe_call(bot.get_messages, username, msg_id)
            except UsernameNotOccupied:
                await safe_call(
                    bot.send_message,
                    message.chat.id,
                    "**The username is not occupied by anyone**",
                    reply_to_message_id=message.id,
                )
                return

            try:
                await _send_copy_to_destinations(
                    msg.chat.id, msg.id, message.chat.id, message.id
                )
            except Exception:
                if acc is None:
                    await safe_call(
                        bot.send_message,
                        message.chat.id,
                        "**String Session is not Set**",
                        reply_to_message_id=message.id,
                    )
                    return
                await handle_private(message, username, msg_id)


async def handle_private(message, chat_id, msg_id):
    if acc is None:
        return

    msg = await safe_call(acc.get_messages, chat_id, msg_id)
    msg_type = get_message_type(msg)

    if msg_type == "Text":
        sent = await safe_call(
            bot.send_message,
            message.chat.id,
            msg.text,
            entities=msg.entities,
            reply_to_message_id=message.id,
        )
        if AUTO_UPLOAD and TARGET_CHAT_ID:
            target = _chat_id(TARGET_CHAT_ID)
            if target != message.chat.id:
                await safe_call(bot.copy_message, target, sent.chat.id, sent.id)
        return

    status_message = await safe_call(
        bot.send_message,
        message.chat.id,
        "📥 **Preparing download…**",
        reply_to_message_id=message.id,
    )
    file_path = None
    thumb_path = None
    try:
        file_path = await safe_call(
            acc.download_media,
            msg,
            progress=progress,
            progress_args=(status_message, "down"),
        )
        if not file_path:
            raise RuntimeError("WZGram could not download this media.")

        media = getattr(msg, msg_type.lower(), None)
        if msg_type in ("Document", "Video"):
            try:
                thumbs = getattr(media, "thumbs", None) if media else None
                if thumbs:
                    thumb_path = await safe_call(acc.download_media, thumbs[0].file_id)
            except Exception:
                thumb_path = None

        common = {"reply_to_message_id": message.id}
        caption = msg.caption
        caption_entities = msg.caption_entities
        upload_progress = {
            "progress": progress,
            "progress_args": (status_message, "up"),
        }

        if msg_type == "Document":
            sent = await safe_call(
                bot.send_document,
                message.chat.id,
                file_path,
                thumb=thumb_path,
                caption=caption,
                caption_entities=caption_entities,
                **upload_progress,
                **common,
            )
        elif msg_type == "Video":
            sent = await safe_call(
                bot.send_video,
                message.chat.id,
                file_path,
                duration=msg.video.duration,
                width=msg.video.width,
                height=msg.video.height,
                thumb=thumb_path,
                caption=caption,
                caption_entities=caption_entities,
                **upload_progress,
                **common,
            )
        elif msg_type == "Animation":
            sent = await safe_call(
                bot.send_animation, message.chat.id, file_path,
                caption=caption, caption_entities=caption_entities, **common
            )
        elif msg_type == "Sticker":
            sent = await safe_call(bot.send_sticker, message.chat.id, file_path, **common)
        elif msg_type == "Voice":
            sent = await safe_call(
                bot.send_voice, message.chat.id, file_path,
                caption=caption, caption_entities=caption_entities,
                **upload_progress, **common
            )
        elif msg_type == "Audio":
            sent = await safe_call(
                bot.send_audio, message.chat.id, file_path,
                caption=caption, caption_entities=caption_entities,
                **upload_progress, **common
            )
        elif msg_type == "Photo":
            sent = await safe_call(
                bot.send_photo, message.chat.id, file_path,
                caption=caption, caption_entities=caption_entities, **common
            )
        else:
            raise RuntimeError(f"Unsupported media type: {msg_type}")

        if sent and AUTO_UPLOAD and TARGET_CHAT_ID:
            target = _chat_id(TARGET_CHAT_ID)
            if target != message.chat.id:
                await safe_call(bot.copy_message, target, sent.chat.id, sent.id)
    finally:
        for path in (thumb_path, file_path):
            if path and isinstance(path, str) and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        try:
            await safe_call(bot.delete_messages, message.chat.id, [status_message.id])
        except Exception:
            pass


def get_message_type(msg):
    for attribute, label in (
        ("document", "Document"),
        ("video", "Video"),
        ("animation", "Animation"),
        ("sticker", "Sticker"),
        ("voice", "Voice"),
        ("audio", "Audio"),
        ("photo", "Photo"),
    ):
        if getattr(msg, attribute, None):
            return label
    if msg.text:
        return "Text"
    return None


USAGE = """**🈂️ 𝐅𝐨𝐫 𝐏𝐮𝐛𝐥𝐢𝐜 𝐂𝐡𝐚𝐭𝐬 🈂️**
𝐉𝐮𝐬𝐭 𝐒𝐞𝐧𝐝 𝐏𝐨𝐬𝐭/𝐬 𝐋𝐢𝐧𝐤

**🈂️ 𝐅𝐨𝐫 𝐏𝐫𝐢𝐯𝐚𝐭𝐞 𝐂𝐡𝐚𝐭𝐬 🈂️**
𝐅𝐢𝐫𝐬𝐭 𝐬𝐞𝐧𝐝 𝐚𝐧 𝐢𝐧𝐯𝐢𝐭𝐞 𝐥𝐢𝐧𝐤 𝐨𝐟 𝐭𝐡𝐞 𝐜𝐡𝐚𝐭 (𝐮𝐧𝐧𝐞𝐜𝐞𝐬𝐬𝐚𝐫𝐲 𝐢𝐟 𝐭𝐡𝐞 𝐚𝐜𝐜𝐨𝐮𝐧𝐭 𝐨𝐟 𝐬𝐭𝐫𝐢𝐧𝐠 𝐬𝐞𝐬𝐬𝐢𝐨𝐧 𝐢𝐬 𝐚𝐥𝐫𝐞𝐚𝐝𝐲 𝐚 𝐦𝐞𝐦𝐛𝐞𝐫) 𝐭𝐡𝐞𝐧 𝐬𝐞𝐧𝐝 𝐭𝐡𝐞 𝐩𝐨𝐬𝐭 𝐥𝐢𝐧𝐤.

**🈂️ 𝐌𝐮𝐥𝐭𝐢 𝐏𝐨𝐬𝐭𝐬 🈂️**
https://t.me/channel/101-110
"""


if __name__ == "__main__":
    # compose() starts both clients on the same asyncio event loop and keeps
    # them running until the process is stopped.
    clients = [bot, acc] if acc is not None else [bot]
    bot.run(compose(clients))
