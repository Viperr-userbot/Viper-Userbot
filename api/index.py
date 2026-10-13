import os
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

app = FastAPI()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

async def setup_webhook(host_url: str):
    async with httpx.AsyncClient() as c:
        res = await c.post(f"{API_URL}/setWebhook", json={"url": f"{host_url}/webhook"})
        return res.json()

@app.get("/")
async def root():
    html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>🐍 Viper Userbot</title>
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #0f172a; color: #f8fafc; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; margin: 0; }
            .container { text-align: center; max-width: 600px; padding: 2rem; background-color: #1e293b; border-radius: 1rem; box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1); }
            h1 { color: #38bdf8; font-size: 2.5rem; margin-bottom: 0.5rem; }
            p { font-size: 1.125rem; line-height: 1.75; color: #cbd5e1; margin-bottom: 2rem; }
            .btn { display: inline-block; padding: 0.75rem 1.5rem; background-color: #0ea5e9; color: white; text-decoration: none; border-radius: 0.5rem; font-weight: bold; transition: background-color 0.2s; }
            .btn:hover { background-color: #0284c7; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🐍 Viper Userbot</h1>
            <p>Ultra-fast Telegram Assistant, AI Suite & Game Solver. Bot service is active and running 24/7.</p>
            <a href="https://t.me/Gazarkahalwahu" class="btn" target="_blank">Developer & Support</a>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

@app.post("/webhook")
async def webhook(request: Request):
    try:
        update = await request.json()
    except Exception:
        return {"status": "error"}

    if "message" in update:
        msg = update["message"]
        text = msg.get("text", "")
        chat_id = msg.get("chat", {}).get("id")

        if text.startswith("/start"):
            welcome_text = (
                "👋 <b>Welcome!</b>\n\n"
                "To receive game answers automatically here, you need to be promoted by the Admin.\n"
                "Please make sure you don't block this bot, or you won't receive the answers."
            )
            async with httpx.AsyncClient() as c:
                await c.post(f"{API_URL}/sendMessage", json={
                    "chat_id": chat_id,
                    "text": welcome_text,
                    "parse_mode": "HTML"
                })
    return {"status": "ok"}
