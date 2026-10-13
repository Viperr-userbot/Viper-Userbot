import asyncio
import json
import os
import sys
from dotenv import set_key
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

STATE_FILE = ".otp_state.json"
ENV_FILE = ".env"

API_ID = 2040
API_HASH = "b18441a1ff607e10a989891a5462e627"
PHONE = "+18052705102"
PASSWORD = "8310"

async def send_code():
    client = TelegramClient(
        StringSession(),
        api_id=API_ID,
        api_hash=API_HASH,
        device_model="Desktop",
        system_version="Windows 10",
        app_version="4.16.8",
        lang_code="en",
        system_lang_code="en"
    )
    await client.connect()

    print(f"Sending login code to Telegram app for {PHONE}...", flush=True)
    res = await client.send_code_request(PHONE)

    state = {
        "phone": PHONE,
        "phone_code_hash": res.phone_code_hash,
        "session": client.session.save(),
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)

    print(f"SUCCESS: OTP Code sent to your Telegram app (in 'Telegram' chat)!", flush=True)
    await client.disconnect()

async def verify_code(code: str):
    if not os.path.exists(STATE_FILE):
        print("ERROR: No pending OTP request. Please run send first.", flush=True)
        return

    with open(STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)

    client = TelegramClient(
        StringSession(state["session"]),
        api_id=API_ID,
        api_hash=API_HASH,
        device_model="Desktop",
        system_version="Windows 10",
        app_version="4.16.8",
        lang_code="en",
        system_lang_code="en"
    )
    await client.connect()

    try:
        user = await client.sign_in(phone=state["phone"], code=code, phone_code_hash=state["phone_code_hash"])
    except SessionPasswordNeededError:
        print(f"2FA triggered. Applying password...", flush=True)
        user = await client.sign_in(password=PASSWORD)

    print(f"🎉 LOGIN SUCCESS: Logged in as {user.first_name} [ID: {user.id}]", flush=True)
    new_session = client.session.save()

    set_key(ENV_FILE, "SANGMATA_API_ID", str(API_ID))
    set_key(ENV_FILE, "SANGMATA_API_HASH", API_HASH)
    set_key(ENV_FILE, "SANGMATA_SESSION", new_session)
    set_key(ENV_FILE, "OWNER_ID", str(user.id))
    print(f"NEW_SESSION: {new_session}", flush=True)
    print("ENV_UPDATED_SUCCESSFULLY", flush=True)

    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
    await client.disconnect()

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "verify":
        code = sys.argv[2] if len(sys.argv) > 2 else input("Enter OTP code: ")
        asyncio.run(verify_code(code.strip()))
    else:
        asyncio.run(send_code())
