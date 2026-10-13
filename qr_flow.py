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

async def main():
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()

    print("Telegram client connected. Generating fresh QR...", flush=True)
    qr_login = await client.qr_login()

    img = qrcode.make(qr_login.url)
    img.save("qr.png")
    print(f"FRESH_QR_GENERATED at qr.png! Expires in {int((qr_login.expires - qr_login.expires.now(tz=qr_login.expires.tzinfo)).total_seconds())}s", flush=True)

    try:
        user = await qr_login.wait(timeout=60)
        print(f"LOGIN_SUCCESS: {user.first_name} (ID: {user.id})", flush=True)
        new_session = client.session.save()
        set_key(ENV_FILE, "SANGMATA_SESSION", new_session)
        set_key(ENV_FILE, "OWNER_ID", str(user.id))
        print("ENV_UPDATED_SUCCESSFULLY", flush=True)
    except SessionPasswordNeededError:
        print("2FA_PASSWORD_NEEDED", flush=True)
    except asyncio.TimeoutError:
        print("EXPIRED", flush=True)
    except Exception as e:
        print(f"ERROR: {e}", flush=True)
    finally:
        await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
