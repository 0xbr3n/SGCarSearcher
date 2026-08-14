# SG Car Scout — Telegram bot (standalone Python)

Same search-building and true-depreciation math as the car-scout PWA, as a Telegram bot your friends can add to a group. It **never scrapes SGCarmart** — every command builds a link and hands it to whoever taps it, exactly like the site's own terms expect.

This version needs no website, no Netlify account, no webhook, no public URL — it long-polls Telegram directly, so it runs anywhere Python runs: your own PC, a spare server, a Raspberry Pi, or a free cloud host.

## Run it in 2 minutes

```bash
cd telegram-bot
pip install -r requirements.txt
cp .env.example .env      # then edit .env and paste your real token in
python bot.py
```

You should see `SG Car Scout bot starting (long-polling)...` in the terminal — leave it running. Message your bot on Telegram (or add it to a group) and send `/help`.

Don't have a bot yet? Message **@BotFather** on Telegram → `/newbot` → follow the prompts → it gives you a token like `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`. That's what goes in `.env`.

## Important: turn off group privacy mode before using it in a group

By default, Telegram bots only see messages starting with `/` inside a group chat. This bot's `/hunt` wizard, `/calibrate`, and `/add` all rely on reading your **plain follow-up reply** (no leading `/`) — with privacy mode on, those replies never reach the bot in a group, only in a 1:1 DM.

Fix it once: **@BotFather → `/mybots` → select your bot → Bot Settings → Group Privacy → Turn off.**

## Keeping it running 24/7

The terminal window needs to stay open (or the process needs to stay alive) for the bot to work. A few ways to do that, roughly easiest first:

- **Just leave it running** on a PC that's usually on — fine for a small friend group testing this out.
- **A free cloud host**, since this needs no public URL, just a place that keeps a process alive: Railway, Render (as a "Background Worker", not a Web Service — it has no HTTP endpoint to serve), or Fly.io all have small free/trial tiers as of writing. Check their current free-tier terms before committing, since these change; the deploy shape is the same everywhere: point it at this `telegram-bot/` folder, set `TELEGRAM_BOT_TOKEN` as an environment variable in their dashboard, run `pip install -r requirements.txt` then `python bot.py` as the start command.
- **Your own Linux server/VPS/Raspberry Pi**: run it under a process manager so it survives crashes and reboots, e.g. as a systemd service:
  ```ini
  # /etc/systemd/system/car-scout-bot.service
  [Unit]
  Description=SG Car Scout Telegram bot
  After=network.target

  [Service]
  WorkingDirectory=/path/to/telegram-bot
  ExecStart=/usr/bin/python3 bot.py
  Restart=always
  Environment=TELEGRAM_BOT_TOKEN=your-token-here

  [Install]
  WantedBy=multi-user.target
  ```
  then `sudo systemctl enable --now car-scout-bot`.
- **Windows**: simplest is a scheduled task set to run at logon with "Run whether user is logged on or not", action `pythonw.exe C:\path\to\telegram-bot\bot.py` (`pythonw.exe` instead of `python.exe` avoids a console window staying open).

## Data & backups

Everything (models, saved hunts, calibration, shortlist) lives in `telegram-bot/data/<chat_id>.json` — one plain JSON file per Telegram chat, no database. Back it up by copying that folder. If you ever move the bot to a different machine, copy `data/` along with it.

## Commands

- `/hunt` — pick vehicle types and models by tapping inline buttons, choose PARF/renewed/both, send your numbers as one line (`60000 15000 1.5 4 100000 2` — price, dep, coeMin, coeMax, km, owners, `-` to skip any), get back the full SGCarmart links as plain text.
- `/hunts` — save a built hunt with a name, list saved hunts, re-run or delete them. Shared with everyone in the chat.
- `/models`, `/addmodel <name>`, `/delmodel <name>` — manage the shared model list.
- `/calibrate` — pick a field, paste the plain sgcarmart.com URL, then the one-filter URL, and the bot works out the real parameter — same trick the PWA uses, since nobody outside Singapore can otherwise verify SGCarmart's exact filter names.
- `/add` — paste a listing's details block, get true depreciation and red flags back, logged to the group's shared shortlist.
- `/shortlist` — everyone's saved cars, ranked by the same scoring the app uses.
- `/reset` — cancel whatever multi-step flow you're in the middle of.

## Relationship to the rest of this repo

`index.html`, `fx.css`/`fx.js`, `icon-maker.html`, and `netlify/functions/` (the earlier Netlify-hosted webhook version of this same bot) are unrelated to running this standalone version — you don't need to deploy any of it, touch Netlify, or host a website to use `telegram-bot/`. They're left in place in case you want the home-screen PWA or the webhook-based bot later; delete them if you'd rather keep this repo just to the bot.
