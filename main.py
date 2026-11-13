# WZGram is async-only: every Telegram API call must be awaited.
import asyncio
import inspect
import json
import os
import time
from os import environ

import pyrogram
from wzgram import Client, filters, idle
from pyrogram.errors import (
    UserAlreadyParticipant,
    InviteHashExpired,
    UsernameNotOccupied,
    FloodWait,
)
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def _flood_wait_seconds(error):
    return int(getattr(error, "value", getattr(error, "x", 1)))


async def safe_call(function, *args, **kwargs):
    """Await a Telegram API call and retry FloodWait responses safely."""
    for attempt in range(1, 6):
        try:
            result = function(*args, **kwargs)
            if inspect.isawaitable(result):
                return await result
            return result
        except FloodWait as error:
            if attempt >= 5:
                raise
            delay = _flood_wait_seconds(error) + 1
            print(f"FloodWait: waiting {delay}s ({attempt}/5)")
            await asyncio.sleep(delay)


def _is_connection_error(error):
    name = type(error).__name__.lower()
    detail = str(error).lower()
    return (
        isinstance(error, (ConnectionError, TimeoutError))
        or name in {"connectionclosed", "connectionclosederror", "networkerror"}
        or any(part in detail for part in (
            "connection closed", "connection reset", "connection aborted",
            "server disconnected", "timed out", "network is unreachable"
        ))
    )


async def safe_read_call(function, *args, **kwargs):
    """Retry read/download calls after temporary connection failures."""
    for attempt in range(1, 4):
        try:
            return await safe_call(function, *args, **kwargs)
        except Exception as error:
            if not _is_connection_error(error) or attempt == 3:
                raise
            delay = min(2 ** attempt, 8)
            print(
                f"Temporary connection error in "
                f"{getattr(function, '__name__', 'Telegram call')}: "
                f"{error!r}. Retrying in {delay}s ({attempt}/3)."
            )
            await asyncio.sleep(delay)


def _is_no_downloadable_media_error(error):
    detail = str(error).lower()
    return (
        "doesn't contain any downloadable media" in detail
        or "does not contain any downloadable media" in detail
    )


async def _cleanup_status_message(chat_id, status_message_id, *status_paths):
    for path in status_paths:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError as error:
            print(f"Could not remove status file {path}: {error!r}")
    try:
        await safe_call(bot.delete_messages, chat_id, [status_message_id])
    except Exception as error:
        print(f"Could not remove transfer status message: {error!r}")


bot_token = environ["TOKEN"]
api_hash = environ["HASH"]
api_id = int(environ["ID"])
MAX_CONCURRENT_TRANSMISSIONS = int(
    environ.get("MAX_CONCURRENT_TRANSMISSIONS", "2")
)

bot = Client(
    "mybot",
    api_id=api_id,
    api_hash=api_hash,
    bot_token=bot_token,
    max_concurrent_transmissions=MAX_CONCURRENT_TRANSMISSIONS,
)

ss = environ.get("STRING", "").strip()
acc = None
if ss:
    acc = Client(
        "myacc",
        api_id=api_id,
        api_hash=api_hash,
        session_string=ss,
        max_concurrent_transmissions=MAX_CONCURRENT_TRANSMISSIONS,
    )


# Target settings are kept in the existing JSON config file.
OWNER_ID = int(environ.get("OWNER_ID", "0"))
TARGET_CHAT_ID = environ.get("TARGET_CHAT_ID", "").strip()
AUTO_UPLOAD = True
TARGET_CONFIG_FILE = "target_config.json"


def _load_target_config():
    global TARGET_CHAT_ID, AUTO_UPLOAD
    try:
        with open(TARGET_CONFIG_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        TARGET_CHAT_ID = str(data.get("target_chat_id") or TARGET_CHAT_ID).strip()
        AUTO_UPLOAD = bool(data.get("auto_upload", AUTO_UPLOAD))
    except (OSError, ValueError, TypeError):
        pass


def _save_target_config():
    with open(TARGET_CONFIG_FILE, "w", encoding="utf-8") as file:
        json.dump(
            {"target_chat_id": TARGET_CHAT_ID, "auto_upload": AUTO_UPLOAD},
            file,
        )


_load_target_config()


def _is_owner(message):
    return bool(message.from_user and OWNER_ID and message.from_user.id == OWNER_ID)


def _target_id():
    if not TARGET_CHAT_ID:
        return None
    return int(TARGET_CHAT_ID) if TARGET_CHAT_ID.lstrip("-").isdigit() else TARGET_CHAT_ID


def _destinations(private_chat_id):
    result = [private_chat_id]
    target = _target_id() if AUTO_UPLOAD else None
    if target is not None and target != private_chat_id:
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


# Async progress callback. WZGram supports async progress callbacks.
_PROGRESS_WRITE_INTERVAL = 1.0
_progress_last_edit = {}


async def progress(current, total, status_message, transfer_type):
    if not total or total <= 0:
        return

    now = time.monotonic()
    key = (status_message.chat.id, status_message.id, transfer_type)
    previous = _progress_last_edit.get(key)

    if previous and current < total and now - previous[0] < _PROGRESS_WRITE_INTERVAL:
        return

    if previous:
        old_time, old_bytes, old_speed = previous
        elapsed = now - old_time
        instant_speed = max(0, current - old_bytes) / elapsed if elapsed > 0 else 0
        speed = instant_speed if not old_speed else 0.7 * old_speed + 0.3 * instant_speed
    else:
        speed = 0.0

    _progress_last_edit[key] = (now, current, speed)

    percent = min(100.0, current * 100.0 / total)
    filled = int(percent // 10)
    bar = "█" * filled + "░" * (10 - filled)
    speed_text = f"{speed / (1024 * 1024):.2f} MB/s" if speed > 0 else "Calculating…"
    remaining = max(0, total - current)
    eta_seconds = int(remaining / speed) if speed > 0 else 0
    eta_text = (
        f"{eta_seconds // 60:02d}:{eta_seconds % 60:02d}"
        if speed > 0 else "Calculating…"
    )

    label = "📥 **DOWNLOADING**" if transfer_type == "down" else "📤 **UPLOADING**"
    details = (
        f"{label}\n`{bar}` **{percent:.1f}%**\n"
        f"**Size:** {current / (1024 * 1024):.1f} / "
        f"{total / (1024 * 1024):.1f} MB\n"
        f"**Speed:** {speed_text}\n**ETA:** {eta_text}"
    )
    try:
        await safe_call(
            bot.edit_message_text,
            status_message.chat.id,
            status_message.id,
            details,
        )
    except Exception as error:
        # "Message is not modified" and transient edit failures are harmless.
        if "not modified" not in str(error).lower():
            print(f"Progress update failed: {error!r}")

    if current >= total:
        _progress_last_edit.pop(key, None)


USAGE = """**🈂️ 𝐅𝐨𝐫 𝐏𝐮𝐛𝐥𝐢𝐜 𝐂𝐡𝐚𝐭𝐬 🈂️**
𝐉𝐮𝐬𝐭 𝐒𝐞𝐧𝐝 𝐏𝐨𝐬𝐭/𝐬 𝐋𝐢𝐧𝐤

**🈂️ 𝐅𝐨𝐫 𝐏𝐫𝐢𝐯𝐚𝐭𝐞 𝐂𝐡𝐚𝐭𝐬 🈂️**
𝐟𝐢𝐫𝐬𝐭 𝐬𝐞𝐧𝐝 𝐢𝐧𝐯𝐢𝐭𝐞 𝐥𝐢𝐧𝐤 𝐨𝐟 𝐭𝐡𝐞 𝐜𝐡𝐚𝐭 (𝐮𝐧𝐧𝐞𝐜𝐞𝐬𝐬𝐚𝐫𝐲 𝐢𝐟 𝐭𝐡𝐞 𝐚𝐜𝐜𝐨𝐮𝐧𝐭 𝐨𝐟 𝐬𝐭𝐫𝐢𝐧𝐠 𝐬𝐞𝐬𝐬𝐢𝐨𝐧 𝐚𝐥𝐫𝐞𝐚𝐝𝐲 𝐦𝐞𝐦𝐛𝐞𝐫 𝐨𝐟 𝐭𝐡𝐞 𝐜𝐡𝐚𝐭) 𝐭𝐡𝐞𝐧 𝐬𝐞𝐧𝐝 𝐩𝐨𝐬𝐭/𝐬 𝐥𝐢𝐧𝐤

**🈂️ 𝐌𝐮𝐥𝐭𝐢 𝐏𝐨𝐬𝐭𝐬 🈂️**
𝚑𝚝𝚝𝚙𝚜://𝚝.𝚖𝚎/𝚌/𝚡𝚡𝚡𝚡/101 - 110
"""


@bot.on_message(filters.command(["start"]))
async def send_start(client, message):
    mention = message.from_user.mention if message.from_user else "there"
    await safe_call(
        bot.send_message,
        message.chat.id,
        f"**♻️ 𝙷𝚒** **{mention}**, "
        f"**𝐈 𝐚𝐦 𝐒𝐚𝐯𝐞 𝐑𝐞𝐬𝐭𝐫𝐢𝐜𝐭𝐞𝐝 𝐁𝐨𝐭, 𝐈 𝐜𝐚𝐧 𝐬𝐞𝐧𝐝 𝐲𝐨𝐮 "
        f"𝐫𝐞𝐬𝐭𝐫𝐢𝐜𝐭𝐞𝐝 𝐜𝐨𝐧𝐭𝐞𝐧𝐭 𝐛𝐲 𝐢𝐭'𝐬 𝐩𝐨𝐬𝐭 𝐥𝐢𝐧𝐤**\n\n{USAGE}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "☢️ 𝐁𝐨𝐭 𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫",
                url="tg://openmessage?user_id=7771180127",
            )
        ]]),
        reply_to_message_id=message.id,
    )


@bot.on_message(filters.command(["settarget"]) & filters.private)
async def set_target_command(client, message):
    global TARGET_CHAT_ID
    if len(message.command) < 2:
        await safe_call(
            bot.send_message,
            message.chat.id,
            "Usage: `/settarget -1001234567890` or `/settarget @channelusername`",
        )
        return

    value = message.command[1].strip()
    old_value = TARGET_CHAT_ID
    try:
        TARGET_CHAT_ID = str(int(value)) if value.lstrip("-").isdigit() else value
        chat = await safe_call(
            bot.get_chat,
            int(TARGET_CHAT_ID) if TARGET_CHAT_ID.lstrip("-").isdigit() else TARGET_CHAT_ID,
        )
        _save_target_config()
        title = chat.title or chat.first_name or TARGET_CHAT_ID
        await safe_call(
            bot.send_message,
            message.chat.id,
            f"✅ Target set to **{title}** (`{TARGET_CHAT_ID}`).",
        )
    except Exception as error:
        TARGET_CHAT_ID = old_value
        await safe_call(
            bot.send_message,
            message.chat.id,
            f"❌ Could not access that chat: `{str(error)[:400]}`\n"
            "Add the bot to the group/channel first and give it posting permission.",
        )


@bot.on_message(filters.command(["removetarget"]) & filters.private)
async def remove_target_command(client, message):
    global TARGET_CHAT_ID, AUTO_UPLOAD
    if not TARGET_CHAT_ID:
        await safe_call(bot.send_message, message.chat.id, "ℹ️ No target is currently configured.")
        return

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
        await safe_call(
            bot.send_message,
            message.chat.id,
            "Usage: `/autoupload on` or `/autoupload off`",
        )
        return

    if message.command[1].lower() == "on" and not TARGET_CHAT_ID:
        await safe_call(
            bot.send_message,
            message.chat.id,
            "❌ Set a target first using `/settarget`.",
        )
        return

    AUTO_UPLOAD = message.command[1].lower() == "on"
    _save_target_config()
    await safe_call(
        bot.send_message,
        message.chat.id,
        f"✅ Auto upload **{'ON' if AUTO_UPLOAD else 'OFF'}**.",
    )


_job_lock = asyncio.Lock()


@bot.on_message(filters.text)
async def save(client, message):
    """Process incoming links one job at a time."""
    async with _job_lock:
        try:
            await _process_message(client, message)
        except Exception as error:
            print(f"Task failed: {error!r}")
            # Do not send the unwanted no-downloadable-media notification.
            if not _is_no_downloadable_media_error(error):
                try:
                    await safe_call(
                        bot.send_message,
                        message.chat.id,
                        f"❌ **Task failed:** `{str(error)[:700]}`",
                        reply_to_message_id=message.id,
                    )
                except Exception as send_error:
                    print(f"Could not report task error: {send_error!r}")


async def _process_message(client, message):
    text = message.text or ""
    print(text)

    if "https://t.me/+" in text or "https://t.me/joinchat/" in text:
        if acc is None:
            await safe_call(
                bot.send_message, message.chat.id,
                "**String Session is not Set**",
                reply_to_message_id=message.id,
            )
            return
        try:
            await safe_call(acc.join_chat, text)
            await safe_call(
                bot.send_message, message.chat.id,
                "**Chat Joined**", reply_to_message_id=message.id,
            )
        except UserAlreadyParticipant:
            await safe_call(
                bot.send_message, message.chat.id,
                "**Chat already Joined**", reply_to_message_id=message.id,
            )
        except InviteHashExpired:
            await safe_call(
                bot.send_message, message.chat.id,
                "**Invalid Link**", reply_to_message_id=message.id,
            )
        except Exception as error:
            await safe_call(
                bot.send_message, message.chat.id,
                f"**Error:** __{error}__", reply_to_message_id=message.id,
            )
        return

    if "https://t.me/" not in text:
        return

    try:
        datas = text.split("/")
        tail = datas[-1].replace("?single", "").split("-")
        from_id = int(tail[0].strip())
        to_id = int(tail[1].strip()) if len(tail) > 1 else from_id
    except (ValueError, IndexError):
        print("Could not parse Telegram post link.")
        return

    for msgid in range(from_id, to_id + 1):
        try:
            if "https://t.me/c/" in text:
                chatid = int("-100" + datas[4])
                if acc is None:
                    await safe_call(
                        bot.send_message, message.chat.id,
                        "**String Session is not Set**",
                        reply_to_message_id=message.id,
                    )
                    return
                await handle_private(message, chatid, msgid)

            elif "https://t.me/b/" in text:
                username = datas[4]
                if acc is None:
                    await safe_call(
                        bot.send_message, message.chat.id,
                        "**String Session is not Set**",
                        reply_to_message_id=message.id,
                    )
                    return
                await handle_private(message, username, msgid)

            else:
                username = datas[3]
                try:
                    msg = await safe_read_call(bot.get_messages, username, msgid)
                except UsernameNotOccupied:
                    await safe_call(
                        bot.send_message, message.chat.id,
                        "**The username is not occupied by anyone**",
                        reply_to_message_id=message.id,
                    )
                    return

                if not msg or getattr(msg, "empty", False):
                    print(f"Post {msgid}: empty or unavailable; skipped.")
                    continue

                try:
                    await _send_copy_to_destinations(
                        msg.chat.id, msg.id, message.chat.id, message.id
                    )
                except Exception as copy_error:
                    print(f"Post {msgid}: bot copy failed: {copy_error!r}")
                    if acc is None:
                        print(f"Post {msgid}: no user session; skipped.")
                        continue
                    try:
                        await handle_private(message, username, msgid)
                    except Exception as fallback_error:
                        print(f"Post {msgid}: user-session fallback failed: {fallback_error!r}")

        except Exception as post_error:
            print(f"Post {msgid} failed; continuing: {post_error!r}")


async def handle_private(message, chatid, msgid):
    if acc is None:
        print(f"Post {msgid}: user session is not configured; skipped.")
        return

    msg = await safe_read_call(acc.get_messages, chatid, msgid)
    if not msg or getattr(msg, "empty", False):
        print(f"Post {msgid}: empty/deleted message; skipped.")
        return

    msg_type = get_message_type(msg)
    if msg_type is None:
        print(f"Post {msgid}: no supported media or text; skipped.")
        return

    if msg_type == "Text":
        sent = await safe_call(
            bot.send_message,
            message.chat.id,
            msg.text,
            entities=msg.entities,
            reply_to_message_id=message.id,
        )
        target = _target_id() if AUTO_UPLOAD else None
        if sent and target is not None and target != message.chat.id:
            await safe_call(bot.copy_message, target, sent.chat.id, sent.id)
        return

    status_message = await safe_call(
        bot.send_message,
        message.chat.id,
        "**Loading 🔄**",
        reply_to_message_id=message.id,
    )
    down_file = f"{message.chat.id}_{message.id}_downstatus.txt"
    up_file = f"{message.chat.id}_{message.id}_upstatus.txt"
    downloaded_file = None
    thumb = None

    try:
        try:
            downloaded_file = await safe_read_call(
                acc.download_media,
                msg,
                progress=progress,
                progress_args=[status_message, "down"],
            )
        except Exception as download_error:
            if _is_no_downloadable_media_error(download_error):
                print(f"Post {msgid}: no downloadable media; skipped silently.")
                return
            raise

        if not downloaded_file:
            print(f"Post {msgid}: Telegram returned no downloaded file; skipped.")
            return

        common = {"reply_to_message_id": message.id}
        caption_args = {
            "caption": msg.caption,
            "caption_entities": msg.caption_entities,
        }

        if msg_type == "Document":
            try:
                if msg.document.thumbs:
                    thumb = await safe_read_call(
                        acc.download_media, msg.document.thumbs[0].file_id
                    )
            except Exception:
                thumb = None
            sent = await safe_call(
                bot.send_document, message.chat.id, downloaded_file,
                thumb=thumb, **caption_args,
                progress=progress,
                progress_args=[status_message, "up"], **common,
            )

        elif msg_type == "Video":
            try:
                if msg.video.thumbs:
                    thumb = await safe_read_call(
                        acc.download_media, msg.video.thumbs[0].file_id
                    )
            except Exception:
                thumb = None
            sent = await safe_call(
                bot.send_video, message.chat.id, downloaded_file,
                duration=msg.video.duration,
                width=msg.video.width,
                height=msg.video.height,
                thumb=thumb, **caption_args,
                progress=progress,
                progress_args=[status_message, "up"], **common,
            )

        elif msg_type == "Animation":
            sent = await safe_call(
                bot.send_animation, message.chat.id, downloaded_file,
                **caption_args, **common,
            )
        elif msg_type == "Sticker":
            sent = await safe_call(
                bot.send_sticker, message.chat.id, downloaded_file, **common
            )
        elif msg_type == "Voice":
            sent = await safe_call(
                bot.send_voice, message.chat.id, downloaded_file,
                **caption_args, progress=progress,
                progress_args=[status_message, "up"], **common,
            )
        elif msg_type == "Audio":
            sent = await safe_call(
                bot.send_audio, message.chat.id, downloaded_file,
                **caption_args, progress=progress,
                progress_args=[status_message, "up"], **common,
            )
        elif msg_type == "Photo":
            sent = await safe_call(
                bot.send_photo, message.chat.id, downloaded_file,
                **caption_args, **common,
            )
        else:
            sent = None

        if sent and AUTO_UPLOAD:
            target = _target_id()
            if target is not None and target != message.chat.id:
                await safe_call(bot.copy_message, target, sent.chat.id, sent.id)

    finally:
        for path in (thumb, downloaded_file, down_file, up_file):
            try:
                if path and isinstance(path, str) and os.path.exists(path):
                    os.remove(path)
            except OSError as error:
                print(f"Could not remove temporary file {path}: {error!r}")
        _progress_last_edit.pop(
            (status_message.chat.id, status_message.id, "down"), None
        )
        _progress_last_edit.pop(
            (status_message.chat.id, status_message.id, "up"), None
        )
        try:
            await safe_call(
                bot.delete_messages,
                message.chat.id,
                [status_message.id],
            )
        except Exception as error:
            print(f"Could not delete transfer status: {error!r}")


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
        media = getattr(msg, attribute, None)
        if media and getattr(media, "file_id", None):
            return label
    if getattr(msg, "text", None):
        return "Text"
    return None


# Heroku Config Vars:
# TOKEN, HASH, ID, STRING
# OWNER_ID=your numeric Telegram user ID
# TARGET_CHAT_ID=-1001234567890 (optional)
# MAX_CONCURRENT_TRANSMISSIONS=2


async def main():
    """Start both WZGram clients on the same event loop."""
    started_acc = False
    try:
        if acc is not None:
            await acc.start()
            started_acc = True
        await bot.start()
        print("WZGram bot is running.")
        await idle()
    finally:
        try:
            await bot.stop()
        except Exception:
            pass
        if started_acc:
            try:
                await acc.stop()
            except Exception:
                pass


if __name__ == "__main__":
    bot.run(main())
