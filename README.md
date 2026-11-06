# Sathi: free AI chat bot for Telegram

A private Telegram bot that answers in Hinglish or English, remembers each chat, searches the web when it needs fresh facts, checks the weather, listens to voice notes and looks at photos. It runs on free AI tiers, and when a free tier runs out it moves to the next one.

**Right now only the admin ID can use it.** Everyone else gets one polite line. You can open it up later.

## How the fallback works

1. Gemini (free key from Google AI Studio)
2. Groq (free key, also does voice-to-text)
3. OpenRouter (free models only, no credit card)
4. Cloudflare Workers AI (optional, only if you set both values)
5. Ollama on your own server (optional, free and unlimited, but slow on CPU)

If a provider says "rate limited" (429), it sits out for 10 minutes. Every other error just moves to the next one. If all of them fail, the bot says it is busy and to try again later. Photos only go to Gemini (the only vision option here). Search uses Tavily first, then Serper. Weather uses Open-Meteo, which needs no key.

Honest limits: free tiers are capped per day and can change without notice. A rough guess is a few hundred to a couple thousand replies a day across all providers. That is a guess, not a promise.

---

## Hinglish version (short)

- Bot sirf admin (aap) ke liye chalega. Dusre log ko "Private bot" wali line milegi.
- Keys (Gemini, Groq, OpenRouter, Tavily) Coolify ke Environment me daalo. Code me, chat me, ya GitHub me kabhi mat daalna.
- Ek free provider ki limit khatam ho to agla apne aap chalta hai.
- Ollama (apne server ka model) bina limit ke chalta hai, par CPU pe bahut slow hai.

---

## Step 1: Get the keys (you do this yourself, about 15 minutes)

| What | Where | Notes |
|---|---|---|
| Telegram bot token | Telegram app, search **@BotFather**, send `/newbot` | Copy the token it gives you. |
| Your Telegram user ID | Message **@userinfobot** on Telegram | It replies with a number. That is `ADMIN_IDS`. |
| Gemini key | https://aistudio.google.com/apikey | Sign in with Google, create key. No card. |
| Groq key | https://console.groq.com/keys | Sign up, create key. No card for the free tier as far as the research could check (verify on the page). |
| OpenRouter key | https://openrouter.ai/settings/keys | Sign up, create key. Do not add credit. |
| Tavily key (search) | https://app.tavily.com | Free plan. |
| Serper key (search, backup) | https://serper.dev | Optional. |

Never send these keys or the token to anyone in a chat.

## Step 2: Put the code on your host

Any host that runs a Python or Docker process works. The bot needs no open port (it long-polls Telegram).

- **Dockerfile:** build it as is. Use a volume at `/data` to keep chat memory across redeploys.
- **Plain Python buildpack:** `requirements.txt` and `Procfile` (`worker: python -m ai_bot`) are at the top level.
- If the host sets `PORT`, a tiny health endpoint answers `ok` on it. If `/data` is not writable, the bot keeps its database in `./data` instead (memory then resets on redeploy).

### On Coolify

Option A (recommended): push this folder to a **private** GitHub repo, then in Coolify:
1. Open your project, click **+ New** → **Application**.
2. Pick your GitHub source, choose the repo and branch.
3. Build pack: **Dockerfile**. Port: leave empty (the bot does not open a port; it uses long polling).
4. Go to the **Persistent Storage** tab and add a volume: destination path `/data`. This keeps the chat memory safe across redeploys.

Option B: use **Docker Compose** resource and paste `docker-compose.yml`. Coolify then reads the `bot` service from it.

## Step 3: Set the environment variables

In the app's **Environment Variables** screen, add:

- `TELEGRAM_BOT_TOKEN` = the BotFather token (required)
- `ADMIN_IDS` = your numeric Telegram ID (required; comma-separated if more than one)
- `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `TAVILY_API_KEY`, `SERPER_API_KEY` (fill the ones you have)
- `ALLOW_ALL` = `false` (keep this until you want to open the bot)
- Optional: `BOT_NAME`, `DAILY_USER_LIMIT`, `PROVIDER_ORDER`, `MEMORY_TURNS`, `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`, `OLLAMA_URL`, `OLLAMA_MODEL`

`.env.example` lists every variable with placeholder values. Do not put real values in that file.

## Step 4: Deploy and check

1. Click **Deploy**. Wait for the build to finish.
2. Open **Logs**. You should see `bot is up`. If you see `TELEGRAM_BOT_TOKEN is not set`, fix the variable and redeploy.
3. In Telegram, open your bot and send `/start`, then `/model`. The model list shows which providers are ready.
4. Send a normal question, a Hinglish question, "aaj ka mausam Delhi ka", and a voice note.

## Step 5: Let someone else use it later

Send `/allow 123456789` (their Telegram ID) from your admin account. `/deny` takes it back, and `/users` lists who has access. For open access, set `ALLOW_ALL=true` and redeploy.

## Optional: local model on your server (Ollama)

This is free and has no daily limit, but a CPU-only server is slow. Expect slow replies, and do not expect the same quality as Gemini.

- Easiest on Coolify: deploy an **Ollama** service (image `ollama/ollama`), give it a volume at `/root/.ollama`, then run `ollama pull qwen2.5:3b-instruct` once in its console.
- Set `OLLAMA_URL` to the internal URL Coolify gives that service (for example `http://ollama:11434`), and `OLLAMA_MODEL=qwen2.5:3b-instruct`.
- The compose file also has an `ollama` profile (`docker compose --profile local-llm up -d`). Whether Coolify runs profiles is not verified here, so the separate service is the safer route.

## Running it on your own computer (for testing)

```
cp .env.example .env        # then fill in real values in .env (never commit it)
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest                       # tests use fake providers, no keys needed
python -m ai_bot             # long polling, Ctrl+C to stop
```

## Commands

- `/start`, `/help`: intro and help
- `/reset`: clear this chat's memory
- `/model`: which provider answered last, and which ones are cooling down
- `/usage`: today's counts per provider
- `/image <description>`: make a picture (also works by just asking "draw ...")
- `/doc <docx|xlsx|pdf|txt|md|csv> <what>`: make a file (also works by asking "make me an excel file of ...")
- `/read <link>`: read a web page and summarise it (also works by pasting a link and asking)
- `/github <owner/repo[/path]>`: read a public GitHub repo, file or issue
- Send a txt, md, csv, docx, xlsx or pdf with a caption like "fix the grammar" and you get the edited file back
- `/video`: says plainly that free video generation is not available
- Admin only: `/allow <id>`, `/deny <id>`, `/users`, `/run <python>` (only if `ENABLE_ADMIN_SANDBOX=true`)

## Extra features: what backs them and their limits

| Feature | Backed by | Limits |
|---|---|---|
| Pictures | Pollinations (no key). Optional second source: Cloudflare Workers AI flux-1-schnell if `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` are set | Pollinations anonymous tier: 1 picture per 45 seconds per server address, so a busy bot waits or says "busy". The Cloudflare path is not live-tested |
| Links | Fetched by the bot itself | http/https only; private, local and metadata addresses and redirects to them are blocked; 1.5 MB and 15 s caps; scripts stripped; JavaScript-only pages come back empty |
| GitHub | GitHub REST API | Public repos only. 60 requests/hour per address without `GITHUB_TOKEN`; a token only raises that limit |
| Files | python-docx, openpyxl, fpdf2, pypdf | The edited file is rebuilt from text: colours, images and exact layout are lost. PDF text comes out of the PDF reader and can be messy. PDF output only shows Hindi and other non-Latin scripts if the server has the DejaVu font, otherwise those characters become "?" |
| Video | none | No free service with usable limits was found, so the bot says so |
| Code running | the bot's own Python | Admin only, off by default (see below) |

Per-user daily cap for pictures, files, links and GitHub: `HEAVY_DAILY_LIMIT` (default 15). Admins are exempt.

## Sandbox on E2B (recommended): a real separate Linux computer for the admin

1. Make a free account at e2b.dev (the Hobby plan needs no card: a one-time $100 usage credit, sessions up to 1 hour, per the pricing page on 6 Nov 2026) and create an API key.
2. Put it in the host's environment variables as `E2B_API_KEY` and redeploy.
3. Admin commands: `/run <python>`, `/sh <command>` (pip install, curl, ls), `/files` (list), `/files /home/user/chart.png` (get a file in Telegram). Send any file that is not txt/md/csv/docx/xlsx/pdf, or any file with the caption `sandbox`, and it is uploaded to `/home/user`.
4. Agent mode: for the admin, the model can write code, run it in the sandbox, read errors and fix them (up to 5 rounds), and send back a file it made.

Each chat gets its own sandbox VM, which shuts down after `SANDBOX_IDLE_SECONDS` (default 300) of silence so the free credit lasts. The bot token and provider keys are never sent into the sandbox. Only admins can use it. `SANDBOX_DAILY_LIMIT` (default 200 runs a day) and a 60 s run limit protect the credit. Code inside the sandbox can reach the internet and anything you upload, so do not upload secrets. Tested here with a fake E2B only; the real service needs your key.

## The local code runner (`/run` without E2B) is not a security boundary

With `ENABLE_ADMIN_SANDBOX=true` an admin can run Python. The code runs in a separate process with an empty environment (the bot token and provider keys are not passed), a throwaway folder, and limits on time (10 s), CPU, memory, file size and output. It is **not** isolated like a container or a virtual machine: it runs as the same user on the same machine, can use the network, and can read any file that user can read. Only switch it on if the only admins are people you trust with the server. The bot also marks its own process non-dumpable so the runner cannot read its memory or environment through /proc; this was not tested on every host.

## About "uncensored"

The bot's personality is direct and does not lecture, and it will talk about ordinary adult topics. But it answers through free models run by Google, Groq and OpenRouter, and those have their own rules that this project cannot switch off. The bot also will not write sexual content about real people, anything involving children, help to hurt people, or material presented as real that is meant to deceive.

## What is tested and what is not

- Tested by automated tests (91 tests, all pass): fallback order, 429 cooldown, admin-only gate, /allow and /deny, memory and summary, daily cap, message splitting, search and weather code paths, and the Telegram handlers driven through aiogram with a recorded (not real) Telegram connection.
- Tested live: the weather lookup (Open-Meteo, no key) and the OpenRouter free-model list. The bot token was checked with Telegram's getMe.
- NOT tested yet: real Gemini, Groq, OpenRouter, Tavily answers with real keys; voice notes; photos; a real chat through Telegram; Docker build (Docker was not on the test machine); ARM servers; Ollama on your server; the Coolify deploy.
