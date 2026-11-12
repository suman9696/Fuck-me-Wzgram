import pyrogram
from pyrogram import Client, filters
from pyrogram.errors import UserAlreadyParticipant, InviteHashExpired, UsernameNotOccupied
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import time
import os
import threading
import json
from os import environ

bot_token = environ.get("TOKEN", "6662320983:AAFt8iZukwYD7oH5cvRIwk0bWE4aZCcWayE") 
api_hash = environ.get("HASH", "402721dee326fb686f383e75ccdda8ef") 
api_id = int(environ.get("ID", "23615650"))
bot = Client("mybot", api_id=api_id, api_hash=api_hash, bot_token=bot_token)

ss = environ.get("STRING", "AQFoWKIALxvxEiWgs9c_85oXpdH37W4Ku2qcPEzTjn2cGz1E0JssB9lBYJOTXSKqu9zCVPyFaLPwil90uavmeEjQuh99AXoiFqSzDOOdt7adypsC2RlTyh_XSPp_x59X6Pm1IV_xiuhLa2gdlJLzblDwQSWXa8nysw2RTvhJ0MhMSxEzEpEywsRk_v2EIteT9SNS1H5sEa7hqvo9iRdUrrzitTIydgn8Pke11XfKxF8bx7KxpbrNrvMGwRh8W-IYoCihJv0g9n9723znqQqqr336jkYMh0Q_VeoUGAjlEqjZalRB9Jc8tSWINys7G1JjpnIr08_wPf8gVuV3RJExMjLDpBBxdQAAAAFhiTrHAA")
if ss is not None:
	acc = Client("myacc" ,api_id=api_id, api_hash=api_hash, session_string=ss)
	acc.start()
else: acc = None

# download status
def downstatus(statusfile,message):
	while True:
		if os.path.exists(statusfile):
			break

	time.sleep(3)      
	while os.path.exists(statusfile):
		with open(statusfile,"r") as downread:
			txt = downread.read()
		try:
			bot.edit_message_text(message.chat.id, message.id, f"**𝙳𝚘𝚠𝚗𝚕𝚘𝚊𝚍𝚎𝚍** ♀️ : **{txt}**")
			time.sleep(10)
		except:
			time.sleep(5)


# upload status
def upstatus(statusfile,message):
	while True:
		if os.path.exists(statusfile):
			break

	time.sleep(3)      
	while os.path.exists(statusfile):
		with open(statusfile,"r") as upread:
			txt = upread.read()
		try:
			bot.edit_message_text(message.chat.id, message.id, f"**𝚄𝚙𝚕𝚘𝚊𝚍𝚎𝚍** ♨️ : **{txt}**")
			time.sleep(10)
		except:
			time.sleep(5)


# progress writter
def progress(current, total, message, type):
	with open(f'{message.id}{type}status.txt',"w") as fileup:
		fileup.write(f"{current * 100 / total:.1f}%")


# start command
@bot.on_message(filters.command(["start"]))
def send_start(client: pyrogram.client.Client, message: pyrogram.types.messages_and_media.message.Message):
	bot.send_message(message.chat.id, f"**♻️ 𝙷𝚒** **{message.from_user.mention}**, **𝐈 𝐚𝐦 𝐒𝐚𝐯𝐞 𝐑𝐞𝐬𝐭𝐫𝐢𝐜𝐭𝐞𝐝 𝐁𝐨𝐭 , 𝐈 𝐜𝐚𝐧 𝐬𝐞𝐧𝐝 𝐲𝐨𝐮 𝐫𝐞𝐬𝐭𝐫𝐢𝐜𝐭𝐞𝐝 𝐜𝐨𝐧𝐭𝐞𝐧𝐭 𝐛𝐲 𝐢𝐭'𝐬 𝐩𝐨𝐬𝐭 𝐥𝐢𝐧𝐤**\n\n{USAGE}",
	reply_markup=InlineKeyboardMarkup([[ InlineKeyboardButton("☢️ 𝐁𝐨𝐭 𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫", url="tg://openmessage?user_id=2112247533")]]), reply_to_message_id=message.id)


@bot.on_message(filters.text)
def save(client: pyrogram.client.Client, message: pyrogram.types.messages_and_media.message.Message):
	print(message.text)

	# joining chats
	if "https://t.me/+" in message.text or "https://t.me/joinchat/" in message.text:

		if acc is None:
			bot.send_message(message.chat.id,f"**String Session is not Set**", reply_to_message_id=message.id)
			return

		try:
			try: acc.join_chat(message.text)
			except Exception as e: 
				bot.send_message(message.chat.id,f"**Error** : __{e}__", reply_to_message_id=message.id)
				return
			bot.send_message(message.chat.id,"**Chat Joined**", reply_to_message_id=message.id)
		except UserAlreadyParticipant:
			bot.send_message(message.chat.id,"**Chat alredy Joined**", reply_to_message_id=message.id)
		except InviteHashExpired:
			bot.send_message(message.chat.id,"**Invalid Link**", reply_to_message_id=message.id)

	# getting message
	elif "https://t.me/" in message.text:

		datas = message.text.split("/")
		temp = datas[-1].replace("?single","").split("-")
		fromID = int(temp[0].strip())
		try: toID = int(temp[1].strip())
		except: toID = fromID

		for msgid in range(fromID, toID+1):

			# private
			if "https://t.me/c/" in message.text:
				chatid = int("-100" + datas[4])
				
				if acc is None:
					bot.send_message(message.chat.id,f"**String Session is not Set**", reply_to_message_id=message.id)
					return
				
				handle_private(message,chatid,msgid)
				# try: handle_private(message,chatid,msgid)
				# except Exception as e: bot.send_message(message.chat.id,f"**Error** : __{e}__", reply_to_message_id=message.id)
			
			# bot
			elif "https://t.me/b/" in message.text:
				username = datas[4]
				
				if acc is None:
					bot.send_message(message.chat.id,f"**String Session is not Set**", reply_to_message_id=message.id)
					return
				try: handle_private(message,username,msgid)
				except Exception as e: bot.send_message(message.chat.id,f"**Error** : __{e}__", reply_to_message_id=message.id)

			# public
			else:
				username = datas[3]

				try: msg  = bot.get_messages(username,msgid)
				except UsernameNotOccupied: 
					bot.send_message(message.chat.id,f"**The username is not occupied by anyone**", reply_to_message_id=message.id)
					return

				try: bot.copy_message(message.chat.id, msg.chat.id, msg.id,reply_to_message_id=message.id)
				except:
					if acc is None:
						bot.send_message(message.chat.id,f"**String Session is not Set**", reply_to_message_id=message.id)
						return
					try: handle_private(message,username,msgid)
					except Exception as e: bot.send_message(message.chat.id,f"**Error** : __{e}__", reply_to_message_id=message.id)

			# wait time
			time.sleep(3)


# handle private
def handle_private(message: pyrogram.types.messages_and_media.message.Message, chatid: int, msgid: int):
		msg: pyrogram.types.messages_and_media.message.Message = acc.get_messages(chatid,msgid)
		msg_type = get_message_type(msg)

		if "Text" == msg_type:
			bot.send_message(message.chat.id, msg.text, entities=msg.entities, reply_to_message_id=message.id)
			return

		smsg = bot.send_message(message.chat.id, '**Loading 🔄**', reply_to_message_id=message.id)
		dosta = threading.Thread(target=lambda:downstatus(f'{message.id}downstatus.txt',smsg),daemon=True)
		dosta.start()
		file = acc.download_media(msg, progress=progress, progress_args=[message,"down"])
		os.remove(f'{message.id}downstatus.txt')

		upsta = threading.Thread(target=lambda:upstatus(f'{message.id}upstatus.txt',smsg),daemon=True)
		upsta.start()
		
		if "Document" == msg_type:
			try:
				thumb = acc.download_media(msg.document.thumbs[0].file_id)
			except: thumb = None
			
			bot.send_document(message.chat.id, file, thumb=thumb, caption=msg.caption, caption_entities=msg.caption_entities, reply_to_message_id=message.id, progress=progress, progress_args=[message,"up"])
			if thumb != None: os.remove(thumb)

		elif "Video" == msg_type:
			try: 
				thumb = acc.download_media(msg.video.thumbs[0].file_id)
			except: thumb = None

			bot.send_video(message.chat.id, file, duration=msg.video.duration, width=msg.video.width, height=msg.video.height, thumb=thumb, caption=msg.caption, caption_entities=msg.caption_entities, reply_to_message_id=message.id, progress=progress, progress_args=[message,"up"])
			if thumb != None: os.remove(thumb)

		elif "Animation" == msg_type:
			bot.send_animation(message.chat.id, file, reply_to_message_id=message.id)
			   
		elif "Sticker" == msg_type:
			bot.send_sticker(message.chat.id, file, reply_to_message_id=message.id)

		elif "Voice" == msg_type:
			bot.send_voice(message.chat.id, file, caption=msg.caption, thumb=thumb, caption_entities=msg.caption_entities, reply_to_message_id=message.id, progress=progress, progress_args=[message,"up"])

		elif "Audio" == msg_type:
			try:
				thumb = acc.download_media(msg.audio.thumbs[0].file_id)
			except: thumb = None
				
			bot.send_audio(message.chat.id, file, caption=msg.caption, caption_entities=msg.caption_entities, reply_to_message_id=message.id, progress=progress, progress_args=[message,"up"])   
			if thumb != None: os.remove(thumb)

		elif "Photo" == msg_type:
			bot.send_photo(message.chat.id, file, caption=msg.caption, caption_entities=msg.caption_entities, reply_to_message_id=message.id)

		os.remove(file)
		if os.path.exists(f'{message.id}upstatus.txt'): os.remove(f'{message.id}upstatus.txt')
		bot.delete_messages(message.chat.id,[smsg.id])


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


USAGE = """**🈂️ 𝐅𝐨𝐫 𝐏𝐮𝐛𝐥𝐢𝐜 𝐂𝐡𝐚𝐭𝐬 🈂️**
𝐉𝐮𝐬𝐭 𝐒𝐞𝐧𝐝 𝐏𝐨𝐬𝐭/𝐬 𝐋𝐢𝐧𝐤

**🈂️ 𝐅𝐨𝐫 𝐏𝐫𝐢𝐯𝐚𝐭𝐞 𝐂𝐡𝐚𝐭𝐬 🈂️**
𝐟𝐢𝐫𝐬𝐭 𝐬𝐞𝐧𝐝 𝐢𝐧𝐯𝐢𝐭𝐞 𝐥𝐢𝐧𝐤 𝐨𝐟 𝐭𝐡𝐞 𝐜𝐡𝐚𝐭 (𝐮𝐧𝐧𝐞𝐜𝐞𝐬𝐬𝐚𝐫𝐲 𝐢𝐟 𝐭𝐡𝐞 𝐚𝐜𝐜𝐨𝐮𝐧𝐭 𝐨𝐟 𝐬𝐭𝐫𝐢𝐧𝐠 𝐬𝐞𝐬𝐬𝐢𝐨𝐧 𝐚𝐥𝐫𝐞𝐚𝐝𝐲 𝐦𝐞𝐦𝐛𝐞𝐫 𝐨𝐟 𝐭𝐡𝐞 𝐜𝐡𝐚𝐭) 𝐭𝐡𝐞𝐧 𝐬𝐞𝐧𝐝 𝐩𝐨𝐬𝐭/𝐬 𝐥𝐢𝐧𝐤

**🈂️ 𝐌𝐮𝐥𝐭𝐢 𝐏𝐨𝐬𝐭𝐬 🈂️**
𝚑𝚝𝚝𝚙𝚜://𝚝.𝚖𝚎/𝚌/𝚡𝚡𝚡𝚡/101 - 110
"""


# infinty polling
bot.run()
