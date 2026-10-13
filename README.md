# 🐍 Viper Userbot — High-Performance Telegram AI Assistant & Puzzle Solvers

[![Deploy on Voroa](https://img.shields.io/badge/Deploy%20on-Voroa-6366f1?style=for-the-badge&logo=rocket&logoColor=white)](https://app.getvoroa.com/)
[![Fork Repo](https://img.shields.io/badge/🍴%20Fork-Repository-orange?style=for-the-badge&logo=github)](../../fork)
[![Star Repo](https://img.shields.io/badge/⭐%20Star-Repository-yellow?style=for-the-badge&logo=github)](../../)
[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.12-3776ab?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Telethon](https://img.shields.io/badge/Telegram-Telethon%20v1.36-2ca5e0?style=for-the-badge&logo=telegram&logoColor=white)](https://github.com/LonamiWebs/Telethon)

A powerful, high-speed, feature-packed Telegram userbot equipped with automated **Game Solvers** (Wordly, Hidden Word Hunt, 10Things, Octopus), multi-model **AI Assistant** (Groq, Gemini, Mistral, OpenRouter), **320kbps Music Downloader**, **Sticker Maker** (Kang & Quotly), and **Smart Productivity Tools**.

> 🚀 **Release Status & Safety Notice:**  
> **Viper Userbot is a new release!** To keep your Telegram account 100% safe from bans, this initial version comes packed with **carefully tested, safe, and stable core features** (including human typing simulation delays and smart debouncing).  
> ✨ **More Features Coming Soon:** More puzzle solvers, fun tools, media downloaders, and automation modules are actively in development and will be added very soon! Keep an eye out for updates via `.update`.

---

## ⚡ 1-Click Deployment on Voroa

Deploying your userbot on **[Voroa Cloud](https://app.getvoroa.com/)** takes less than 2 minutes and runs 24/7 without needing your PC to stay on:

[![Deploy on Voroa](https://img.shields.io/badge/🚀%20Deploy%20Now%20on-Voroa-6366f1?style=for-the-badge&logo=rocket&logoColor=white)](https://app.getvoroa.com/)

### 📋 Step-by-Step Voroa Deployment Guide:

#### Step 1: Fork This Repository
Before deploying, click the **Fork** button at the top right of this page (or use the button above) to copy this repository to your personal GitHub account.
> 💡 **Why is forking required?**
> 1. Voroa directly connects to your personal GitHub repository for secure, automated builds.
> 2. Whenever a new feature or fix is released, you can simply send `.update` in Telegram, and your bot will auto-pull updates directly from the official upstream repo.

#### Step 2: Sign In to Voroa & Connect Your Forked Repository
1. Go to **[https://app.getvoroa.com/](https://app.getvoroa.com/)** (or [Render](https://render.com/)).
2. **Sign In / Log In**: If possible, log in using your **GitHub account** (logging in with **Google** also works completely fine!). Logging in via GitHub allows Voroa to instantly discover and sync your repositories.
3. **Import / Add Forked Repository**: Click **"New"** / **"New Web Service"** (or `+ New`), grant repository access if prompted, and select/add your forked **`Viper-Userbot`** project from your GitHub account.
4. **Choose Service Type**: Under **"Choose a service"**, select **`Web Service`** (Always choose Web Service for 100% free 24/7 uptime and automated healthcheck monitoring).

#### Step 3: Configure Build & Runtime Settings
Fill in the build configuration fields as shown below:

| Configuration Field | What to Enter / Select | Importance |
|---|---|---|
| **Service Type** | **`Web Service`** | Essential for free 24/7 uptime & healthcheck monitoring. |
| **Runtime / Environment** | **`Python 3`** *(or `Docker`)* | If `Docker` is chosen, `Dockerfile` builds everything automatically. |
| **Branch** | `main` | Default production branch. |
| **Build Command** | `pip install -r requirements.txt` | ⚠️ **Never use `npm install`** (this is a Python bot, not Node.js). |
| **Start Command** | `python main.py` | ⚠️ **Never use `npm start`**. |
| **Pre-deploy Command** | *(Leave Completely Blank)* | Clear any default `npm run migrate`. |
| **Health Check Path** | `/healthz` | Built-in probe that marks deploy **Live** immediately. |

#### Step 4: Configure Environment Variables
In the **Environment Variables / Config** tab, add your credentials:

##### 📱 1. Mandatory Telegram Credentials (Required to Run):
| Variable | Required | Where to Get |
|---|---|---|
| `SANGMATA_API_ID` | **Yes** | Your API ID from [my.telegram.org](https://my.telegram.org) |
| `SANGMATA_API_HASH` | **Yes** | Your API Hash from [my.telegram.org](https://my.telegram.org) |
| `SANGMATA_SESSION` | **Yes** | Telethon String Session from [@StringFatherRobot](https://t.me/StringFatherRobot) (Select **Telethon**). |
| `BOT_TOKEN` | **Yes** | Telegram Bot Token from [@BotFather](https://t.me/BotFather) |
| `OWNER_ID` | **Yes** | Your numerical Telegram User ID from [@userinfobot](https://t.me/userinfobot) |

##### 🤖 2. Supported AI Providers & Backup Key Failover (Optional):
Viper Userbot features **intelligent multi-tier failover and automated multi-key rotation**. If your primary API key hits hourly rate limits (HTTP 429), the bot automatically cascades to your backup key or the next provider with zero downtime!

> 💡 **IMPORTANT NOTE FOR DEPLOYMENT:**  
> **You only need to enter the API KEY!**  
> You do **NOT** need to enter Base URLs or Model names on the hosting platform. All official API endpoints, models, triggers, and game settings are pre-configured with the best defaults inside the bot. Simply supply whichever API keys you have!

| Provider | Variable(s) | Free Key Link | Engine Features & Backup Strategy |
|---|---|---|---|
| **Groq Cloud** | `GROQ_API_KEY`<br>`GROQ_API_KEY_BACKUP`<br>`GROQ_API_KEYS` | [console.groq.com](https://console.groq.com) | Ultra-fast 0.5s turbo engine (Qwen / Llama 3). Automatically fails over to backup key if primary is rate-limited. |
| **Google Gemini** | `GEMINI_API_KEY`<br>`GEMINI_API_KEYS` | [aistudio.google.com](https://aistudio.google.com) | Multimodal intelligence (`gemini-3.8-flash`). Supports comma-separated keys for automatic round-robin rotation. |
| **Mistral AI** | `MISTRAL_API_KEY`<br>`MISTRAL_API_KEY_BACKUP` | [console.mistral.ai](https://console.mistral.ai) | Powers AI Vision and game grid analysis (`pixtral-12b-2409`). Automated backup key failover. |
| **OpenRouter** | `OPENROUTER_API_KEY` | [openrouter.ai](https://openrouter.ai) | Access to free unmetered models (Nemotron, LFM, Gemma). |
| **NVIDIA NIM** | `NVIDIA_API_KEY` | [build.nvidia.com](https://build.nvidia.com) | High-speed vision engine (`meta/llama-3.2-11b-vision-instruct`). |
| **SambaNova Cloud** | `SAMBANOVA_API_KEY` | [cloud.sambanova.ai](https://cloud.sambanova.ai) | Fast reasoning and complex NLP with `MiniMax-M3`. |
| **Cloudflare AI** | `CLOUDFLARE_API_KEY`<br>`CLOUDFLARE_ACCOUNT_ID` | [dash.cloudflare.com](https://dash.cloudflare.com) | Edge-accelerated serverless AI inference. |
| **Hugging Face** | `HUGGINGFACE_API_KEY` | [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) | Access to open-source models and inference endpoints. |
| **OCR.space** | `OCR_SPACE_API_KEY` | [ocr.space/ocrapi](https://ocr.space/ocrapi) | Cloud-based image text recognition backup. |
| **OpenAgentic** | `OPENAGENTIC_API_KEY`<br>`OPENAGENTIC_API_KEYS` | [openagentic.id](https://openagentic.id) | 37+ OpenAI-compatible models proxy with sequential model failover. |
| **Apinex Proxy** | `APINEX_API_KEYS` | [api.apinex.bond](https://api.apinex.bond) | High-throughput custom proxy key pool. |
| **Pollinations.ai** | `POLLINATIONS_URL` | [pollinations.ai](https://pollinations.ai) | **100% Free Unmetered Fallback:** Runs automatically with zero API key required! |
| **PostgreSQL DB** | `DATABASE_URL` | [neon.tech](https://neon.tech) | Cloud database storage (automatically uses local JSON storage if left blank). |

##### 🎮 3. Game Solvers, Triggers & Server Defaults (Pre-Configured):
All these settings have built-in defaults; you only need to change them if you want custom behavior:

| Variable | Default Value | Description |
|---|---|---|
| `GAME_BOT` | `hiddenwordhuntbot` | Target Telegram game bot to monitor and solve. |
| `TRIGGER` | `^(\.k\|\.c)$` | Regex command trigger to activate game solvers (e.g., `.k` or `.c`). |
| `VOTES` | `3` | Number of puzzle solution attempts / votes (range: 1-7). |
| `PORT` | `8000` | Web server port for healthchecks & 24/7 uptime monitoring. |
| `UPSTREAM_REPO` | `https://github.com/Viperr-userbot/Viper-Userbot.git` | Official repository for `.update` auto-pulls. |
| `UPSTREAM_BRANCH` | `main` | Production branch for automatic updates. |

#### Step 5: Click Deploy!
Click the **"Deploy"** button. The platform will automatically install dependencies, initialize the encrypted runtime, and start your bot within 60 seconds!

> [!IMPORTANT]
> ### 🚨 **IMPORTANT: Prevent Sleep Mode (Keep Bot Running 24/7 Online)**
> Free cloud platforms (jaise Voroa, Render, etc.) par 15 minute bina request ke bot **Sleep Mode** me chala jata hai. Bot ko **24/7 bina ruke active** rakhne ke liye sabse popular aur 100% free service hai **[UptimeRobot](https://uptimerobot.com/)**.  and also use https://hetrixtools.com/
>
> **📋 Step-by-Step Keep-Alive Setup:**
> 1. Deploy hone ke baad aapko aapki site ka live URL milega (jaise: `https://pepebot.getvoroa.com` ya `https://your-bot.onrender.com`).
> 2. **[UptimeRobot](https://uptimerobot.com/)** par jayein aur free account banakar **`+ Add New Monitor`** par click karein.
> 3. Yeh options set karein:
>    - **Monitor Type:** `HTTP(s)`
>    - **Friendly Name:** `Viper Userbot`
>    - **URL (or IP):** Apni site ka link aage `/healthz` laga kar daalein:  
>      👉 **`https://pepebot.getvoroa.com/healthz`** *(⚠️ **Must:** Aage `/healthz` zaroor lagayein taaki probe hamesha `200 OK` return kare!)*
>    - **Monitoring Interval:** `5 minutes`
> 4. **`Create Monitor`** par click kar dein. Ab UptimeRobot har 5 minute me bot ko ping karta rahega aur aapka bot **kabhi sleep mode me nahi jayega!**

> 💬 **Need Help Deploying?**
> If you encounter any deployment errors or have configuration questions, contact the developer directly on Telegram: **[@Gazarkahalwahu](https://t.me/Gazarkahalwahu)**

---

## 🔑 How to Generate Telegram String Session

You can generate your Telethon string session in two convenient ways:

### 🌟 Method 1 (Instant via Telegram — Recommended):
1. Open Telegram and search for **[@StringFatherRobot](https://t.me/StringFatherRobot)** (or open [t.me/StringFatherRobot](https://t.me/StringFatherRobot)).
2. Send `/start`.
3. Select **Telethon** *(Recommended — choose Telethon for best performance, though Pyrogram is also auto-supported)*.
4. Enter your phone number with your international country code (e.g., `+919876543210`).
5. Enter the login OTP sent to your official Telegram app.
6. Copy the string session delivered directly to your Telegram **Saved Messages**!

> 💡 **PRO TIP — IF YOU ENCOUNTER AN OTP OR LOGIN ERROR:**  
> Generating directly from your primary account usually works smoothly. However, if Telegram rejects your OTP or flags an active session clash, **try this easy workaround:**  
> 1. Open **[@StringFatherRobot](https://t.me/StringFatherRobot)** from a **secondary / alternate Telegram account**.  
> 2. When the bot asks for credentials, enter your **target account's** API ID, API Hash, and phone number.  
> 3. The official login OTP will arrive on your target account's Telegram app.  
> 4. Simply submit that OTP into `@StringFatherRobot` from your secondary account.  
> 5. Your string session will generate instantly without any session conflict!

> ⚠️ **We are not responsible for acc ban but its safe dekha jaye to**

### 💻 Method 2 (Via Local Terminal):
Run the interactive generator on your computer:
```bash
python generate_session.py
```
Follow the prompt to enter your phone number and OTP code.

---

## 🎮 Available Commands

### 🕹️ Game Solvers & Automation
- `.k` / `.c` — Automatically solve Wordly, Hidden Word Hunt, 10Things, and Octopus puzzles.
- `.g [link]` / `.grab [link]` *(or reply `.g`)* — Save or download media, photos, videos, audio, voice notes, or message text directly to your personal bot DM / Saved Messages.
- `.gcreply on / off / status` — Direct group chat answer replies with automated score verification.
- `.humanize on / off / status` — Realistic human typing simulation delay (1s – 8s) to avoid bot detection.

### 🤖 AI Assistant & Vision
- `.gpt <query>` — Ask AI with automated multi-tier failover (Groq ⚡, Gemini, Mistral Vision, OpenRouter).
- `.models` / `.active` — View active AI models, health status, and response latency.
- `.act` — Perform live ping and latency benchmark across AI endpoints.
- `.1gpt on / off / status` — Send the final answer in a fresh message after the initial "Thinking..." prompt.

### 🎵 Media & Utility Tools
- `.song <name>` / `.music <name>` — Download 320kbps high-quality MP3 tracks with metadata.
- `.del [n]` / `.deleted` — View recent message history and logs in the chat.
- `.kang [emoji]` — Kang any sticker, photo, or GIF into your custom Telegram sticker pack.
- `.packs` — View your custom sticker pack links and sticker counts.
- `.q` / `.qf` / `.qr` — Quotly sticker generator with customizable themes and background colors.
- `.tts <text>` — Generate natural voice notes from text using Microsoft Edge Neural TTS.
- `.tr [lang]` — Instant multi-language translator supporting text and voice notes.

### 🧠 Memory & Auto-Reply
- `.yaad <text>` — Save custom memories, notes, and facts.
- `.yaadein` / `.memories` — List all saved facts and memories.
- `.bhool <id/all>` — Delete specific or all saved memories.
- `.on` / `.off` — Toggle AI auto-reply mode in the current chat.

### 🛠️ General & Administration
- `.afk [reason]` / `.unafk` — AFK status tracker with automated mention alerts.
- `.info [user]` / `.whois` — Comprehensive user information profile card.
- `.ping` — Measure round-trip bot response latency in milliseconds.
- `,restart` — Perform full bot restart (Owner only).
- `.update` — Pull latest updates from GitHub and auto-restart (`.update check` to view changelog).
- `.stop` — Cancel all running background queues and active solver tasks.
- `.help` / `.cmd` — Interactive category-based button navigation menu.
- `.gcauth [chat_id]` / `.gcunauth` — Authorize or deauthorize group chats.
- `.sudo [user_id]` / `.unsudo` — Grant or revoke sudo administrative access.

---

## 💻 Alternative Deployment Methods

### Method 1: Local PC / VPS
```bash
# Clone repository
git clone https://github.com/Viperr-userbot/Viper-Userbot.git
cd Viper-Userbot

# Install dependencies
pip install -r requirements.txt

# Configure environment variables
cp .env.example .env
# Fill in your credentials in .env

# Start userbot
python main.py
```

### Method 2: Docker
```bash
# Build Docker image
docker build -t viper-userbot .

# Run container with your .env
docker run -d --name viper-userbot --env-file .env viper-userbot
```

---

## 🔒 Security & Privacy Notice

- Never share your `SANGMATA_SESSION`, `BOT_TOKEN`, or API keys with anyone.
- Keep your `.env` file private and never commit credentials to public Git repositories.
- Use this userbot responsibly in accordance with Telegram's Terms of Service.

> ⚠️ **We are not responsible for acc ban but its safe dekha jaye to**

---

## 💬 Support & Developer Assistance

If you encounter any issues during setup, generating string sessions, or deploying on **Voroa**, feel free to reach out directly:

- 👨‍💻 **Developer / Support:** [@Gazarkahalwahu](https://t.me/Gazarkahalwahu)
- 🚀 **Telegram Direct Chat:** [t.me/Gazarkahalwahu](https://t.me/Gazarkahalwahu)
- ⚡ **Assistance:** 24/7 Setup, Deployment & Bug Resolution Support!
