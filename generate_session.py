import asyncio
import json
import os
import sys
from dotenv import load_dotenv, set_key
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

CACHE_FILE = ".session_temp.json"
ENV_FILE = ".env"

load_dotenv(ENV_FILE)
API_ID = int(os.getenv("SANGMATA_API_ID") or 6)
API_HASH = os.getenv("SANGMATA_API_HASH", "eb06d4abfb49dc3eeb1aeb98ae0f581e")

async def request_code(phone: str):
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()

    print(f"Sending login code request for {phone}...")
    res = await client.send_code_request(phone)

    cache = {
        "phone": phone,
        "phone_code_hash": res.phone_code_hash,
        "session_str": client.session.save(),
    }
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f)

    await client.disconnect()
    print("SUCCESS: Code has been sent to your Telegram app / SMS!")

async def verify_code(code: str, password: str = None):
    if not os.path.exists(CACHE_FILE):
        print("ERROR: No pending login request found. Please run request first.")
        return False

    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        cache = json.load(f)

    phone = cache["phone"]
    phone_code_hash = cache["phone_code_hash"]
    saved_session = cache["session_str"]

    client = TelegramClient(StringSession(saved_session), API_ID, API_HASH)
    await client.connect()

    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
    except SessionPasswordNeededError:
        if not password:
            print("2FA_REQUIRED: Two-Step Verification password is required.")
            await client.disconnect()
            return False
        await client.sign_in(password=password)

    me = await client.get_me()
    new_session_str = client.session.save()

    print(f"SUCCESS: Logged in as {me.first_name} (@{me.username or 'none'}) [ID: {me.id}]")
    print(f"NEW_SESSION: {new_session_str}")

    set_key(ENV_FILE, "SANGMATA_SESSION", new_session_str)
    set_key(ENV_FILE, "OWNER_ID", str(me.id))
    print("UPDATED: .env updated with new SANGMATA_SESSION and OWNER_ID!")

    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)

    await client.disconnect()
    return True

async def interactive_mode():
    print("=" * 60)
    print("🔑 Telegram String Session Generator")
    print("💡 Easiest Way: Use Telegram Bot @StringFatherRobot (Select 'Telethon')")
    print("⚠️ We are not responsible for acc ban but its safe dekha jaye to**")
    print("=" * 60)
    phone = input("\nEnter your phone number with country code (e.g. +919876543210): ").strip()
    if not phone:
        print("❌ Phone number cannot be empty.")
        return
    await request_code(phone)
    code = input("\nEnter the login code you received: ").strip()
    pwd = input("Enter 2FA password (leave empty if none): ").strip()
    await verify_code(code, pwd or None)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        try:
            asyncio.run(interactive_mode())
        except (KeyboardInterrupt, EOFError):
            print("\nSession generation cancelled.")
        sys.exit(0)

    action = sys.argv[1].lower()
    if action == "request":
        if len(sys.argv) < 3:
            print("Error: Phone number required. e.g. python generate_session.py request +919876543210")
            print("💡 Tip: Or generate via Telegram: https://t.me/StringFatherRobot")
            sys.exit(1)
        asyncio.run(request_code(sys.argv[2].strip()))
    elif action == "verify":
        if len(sys.argv) < 3:
            print("Error: Code required. e.g. python generate_session.py verify 12345 [password]")
            sys.exit(1)
        pwd = sys.argv[3] if len(sys.argv) > 3 else None
        asyncio.run(verify_code(sys.argv[2].strip(), pwd))
    else:
        print(f"Unknown action: {action}")
        print("💡 Tip: Generate easily via Telegram: https://t.me/StringFatherRobot")
