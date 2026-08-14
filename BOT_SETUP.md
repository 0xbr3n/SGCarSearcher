# Setting up the Telegram bot

The bot lives in `netlify/functions/` alongside the existing PWA (`index.html` etc.) — same Netlify site, same deploy, no scraping involved anywhere. Every link it sends is built the same calibrated way as the app: it never fetches SGCarmart itself, it only builds a link and hands it to whoever taps it.

## Before you start: this needs a GitHub-connected deploy, not drag-and-drop

The static app (`index.html`, `fx.js`, etc.) works fine dragged straight onto app.netlify.com/drop, like before. The bot doesn't — it depends on one small package (`@netlify/blobs`, for shared per-group storage), and a plain zip drop skips the `npm install` step that dependency needs. Connect this project to a GitHub repo instead:

1. Push this whole folder to a new **public or private** GitHub repo.
2. On [app.netlify.com](https://app.netlify.com), **Add new site → Import an existing project → Deploy with GitHub**, pick the repo.
3. Build settings: leave the build command blank, publish directory `.` — `netlify.toml` already has the rest (it points Netlify at `netlify/functions` for the bot). Deploy.
4. Note your site URL, e.g. `https://sg-car-scout-yourname.netlify.app`. Every future `git push` auto-redeploys both the app and the bot.

## 1. Create the bot

1. In Telegram, message **@BotFather** → `/newbot` → follow the prompts (name, username ending in `bot`).
2. BotFather gives you a **token** like `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`. Keep it secret — anyone with it can send messages as your bot.

## 2. Set environment variables

Netlify site → **Site configuration → Environment variables** → add:

| Key | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | the token from BotFather |
| `TELEGRAM_WEBHOOK_SECRET` | any random string you make up (e.g. 32 random characters) — this stops randoms from POSTing fake Telegram updates at your function |

Then **trigger a redeploy** (Deploys → Trigger deploy → Deploy site) so the functions pick up the new variables.

## 3. Point Telegram at your function

Your webhook URL is always `https://<your-site>.netlify.app/.netlify/functions/telegram`. Register it by running this once (swap in your real token, secret, and site URL):

```bash
curl "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  -d "url=https://<your-site>.netlify.app/.netlify/functions/telegram" \
  -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>"
```

A `{"ok":true,...}` response means it's live. (If you ever want to stop the bot, `curl "https://api.telegram.org/bot<TOKEN>/deleteWebhook"`.)

## 4. Add it to your group

In Telegram: create/open the group with your friends → group name → **Add member** → find your bot by its `@username` → add. Send `/help` in the group to confirm it responds. Every command works the same in a group as in a 1:1 DM — the bot's shared state (models, hunts, calibration, shortlist) is keyed per chat, so a group shares one setup and a DM has its own.

## What the bot can do

- `/hunt` — pick vehicle types and models by tapping inline buttons, choose PARF/renewed/both, send your numbers as one line (`60000 15000 1.5 4 100000 2` — price, dep, coeMin, coeMax, km, owners, `-` to skip any), get back tap-to-open SGCarmart links.
- `/hunts` — save a built hunt with a name, list saved hunts, re-run or delete them. Shared with everyone in the chat.
- `/models`, `/addmodel`, `/delmodel` — manage the shared model list.
- `/calibrate` — same calibration engine as the app: pick a field, paste the plain sgcarmart.com URL, then the one-filter URL, and the bot works out the real parameter. Calibrating in the bot and calibrating in the app are separate (the app's calibration lives in your phone's browser storage; the bot's lives in the group's shared cloud storage) — you'll want to do it once in whichever one your group actually uses.
- `/add` — paste a listing's details block, get true depreciation and red flags back, logged to the group's shared shortlist.
- `/shortlist` — everyone's saved cars, ranked by the same scoring the app uses.
- `/reset` — cancel whatever multi-step flow you're in the middle of.

## Troubleshooting

- **Bot doesn't respond at all:** check `setWebhook` returned `{"ok":true}`; check both env vars are set AND you redeployed after setting them; check Netlify **Site → Functions → telegram → real-time logs** for errors while you send a test message.
- **"Something went wrong" replies:** almost always a Blobs/storage error — check the function logs. If you see an error about `@netlify/blobs` not resolving, it means the site was deployed without a proper `npm install` (drag-and-drop, or a build system that skipped it) — reconnect via GitHub as described above.
- **Calibration says a URL "doesn't look like sgcarmart.com":** make sure you're pasting the full `https://www.sgcarmart.com/...` URL, not a shortened or app-share link.
- **Group members don't see each other's hunts/shortlist:** state is per Telegram chat — if people are DMing the bot individually instead of using the shared group chat, they each get their own separate storage.
