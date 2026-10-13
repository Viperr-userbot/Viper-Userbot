import asyncio
import os
import sys
import qrcode
from dotenv import load_dotenv, set_key
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

ENV_FILE = ".env"
load_dotenv(ENV_FILE)
API_ID = int(os.getenv("SANGMATA_API_ID") or 6)
API_HASH = os.getenv("SANGMATA_API_HASH", "eb06d4abfb49dc3eeb1aeb98ae0f581e")
PASSWORD = sys.argv[1] if len(sys.argv) > 1 else os.getenv("TWO_FACTOR_PASSWORD", "")

async def main():
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()

    print("Telegram client connected. Generating QR code with auto-2FA...", flush=True)
    qr_login = await client.qr_login()

    img = qrcode.make(qr_login.url)
    img.save("qr.png")
    print("QR_READY at qr.png! Please scan now.", flush=True)

    try:
        user = await qr_login.wait(timeout=60)
    except SessionPasswordNeededError:
        print(f"2FA triggered. Signing in with provided password...", flush=True)
        user = await client.sign_in(password=PASSWORD)

    print(f"\n🎉 SUCCESS: Logged in as {user.first_name} (@{user.username or 'none'}) [ID: {user.id}]", flush=True)
    new_session = client.session.save()
    set_key(ENV_FILE, "SANGMATA_SESSION", new_session)
    set_key(ENV_FILE, "OWNER_ID", str(user.id))
    print(f"NEW_SESSION_SAVED: {new_session}", flush=True)
    print("ALL_DONE", flush=True)
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
