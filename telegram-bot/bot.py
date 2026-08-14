"""SG Car Scout — standalone Telegram bot.

Runs with long-polling (no webhook, no public URL, no hosting account
needed) — start it with `python bot.py` anywhere Python runs. It never
fetches SGCarmart itself; every command builds a link the same way the
car-scout PWA does, and hands it to a human to tap. See ../README.md's
"Two things worth knowing" for why that's a deliberate choice, not a
missing feature.

State is per Telegram chat (a group shares one setup; a DM has its own),
stored as plain JSON files in ./data/ — see store.py.
"""
from __future__ import annotations
import logging
import os
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Update
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters,
)

from carmath import derive, flags, money, num, score_all
from calib import CAL_FIELDS, build_queue, calib_apply, normalize_calib
from parse import parse_listing
from store import get_chat, save_chat

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("carscout-bot")

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
CALIB_NEEDS_VALUE = {"price", "dep", "km"}
MAX_QUEUE_ENTRIES = 60
MSG_BUDGET = 3500  # headroom under Telegram's 4096-char hard limit


def esc(s) -> str:
    return re.sub(r"[&<>]", lambda m: {"&": "&amp;", "<": "&lt;", ">": "&gt;"}[m.group(0)], str(s or ""))


def kb(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=data) for text, data in row] for row in rows])


async def send_queue_as_text(bot, chat_id, header_html: str, queue: list[dict]):
    """Telegram caps messages at 4096 chars, and full SGCarmart URLs (with
    every filter param) can run 150-350 chars each — pack entries greedily
    into as few messages as fit, splitting only when needed."""
    shown = queue[:MAX_QUEUE_ENTRIES]
    blocks = [f"<b>{esc(q['label'])}</b>\n{esc(q['url'])}" for q in shown]
    capped_note = (f"\n\n(showing first {MAX_QUEUE_ENTRIES} of {len(queue)} — narrow your models/types for fewer)"
                    if len(queue) > MAX_QUEUE_ENTRIES else "")

    chunks, cur, cur_len = [], [], len(header_html) + len(capped_note)
    for b in blocks:
        if cur_len + len(b) + 2 > MSG_BUDGET and cur:
            chunks.append(cur); cur, cur_len = [], 0
        cur.append(b); cur_len += len(b) + 2
    if cur:
        chunks.append(cur)

    for i, chunk in enumerate(chunks):
        head = f"{header_html}{capped_note}\n\n" if i == 0 else f"(part {i + 1}/{len(chunks)})\n\n"
        await bot.send_message(chat_id, head + "\n\n".join(chunk), parse_mode="HTML", link_preview_options=NO_PREVIEW)


# ============================== commands ==============================

HELP_TEXT = (
    "🕷️ <b>SG Car Scout bot</b> — builds SGCarmart search links for the group, no scraping, ever. "
    "This never fetches listings itself; it only builds a link and hands it to whoever taps it, "
    "same as sgcarmart.com wants.\n\n"
    "<b>/hunt</b> — pick vehicle types + models + PARF/renewed, send your numbers, get links\n"
    "<b>/hunts</b> — list, run, or delete saved hunts (shared with everyone in this chat)\n"
    "<b>/models</b> — show the saved model list · <b>/addmodel</b> <i>name</i> · <b>/delmodel</b> <i>name</i>\n"
    "<b>/calibrate</b> — teach the bot SGCarmart's real filter params (see below)\n"
    "<b>/add</b> — paste a listing's details block, get true depreciation + red flags\n"
    "<b>/shortlist</b> — everyone's saved cars, ranked by true depreciation\n"
    "<b>/reset</b> — cancel whatever the bot is currently asking you\n\n"
    "<b>Why calibrate?</b> SGCarmart is geofenced to Singapore and nobody outside it can verify its exact "
    "filter parameters. /calibrate lets you teach the bot the real ones from your own phone — set one filter "
    "on sgcarmart.com, paste the plain URL and the one-filter URL, and it works out the rest. Until you do, "
    "links use best-effort guesses."
)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode="HTML")


async def cmd_models(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = get_chat(update.effective_chat.id)
    lines = "\n".join(f"{i + 1}. {esc(m)}" for i, m in enumerate(chat["models"])) or "(none saved)"
    await update.message.reply_text(
        f"<b>Saved models</b>\n{lines}\n\nAdd with <code>/addmodel Toyota Camry</code>, "
        "remove with <code>/delmodel Toyota Camry</code>.", parse_mode="HTML")


async def cmd_addmodel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = " ".join(context.args).strip()
    if not name:
        return await update.message.reply_text("Usage: /addmodel Toyota Camry")
    chat_id = update.effective_chat.id
    chat = get_chat(chat_id)
    if any(m.lower() == name.lower() for m in chat["models"]):
        return await update.message.reply_text(f"{esc(name)} is already saved.", parse_mode="HTML")
    chat["models"].append(name)
    save_chat(chat_id, chat)
    await update.message.reply_text(f"Added {esc(name)}.", parse_mode="HTML")


async def cmd_delmodel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = " ".join(context.args).strip()
    chat_id = update.effective_chat.id
    chat = get_chat(chat_id)
    before = len(chat["models"])
    chat["models"] = [m for m in chat["models"] if m.lower() != name.lower()]
    if len(chat["models"]) == before:
        return await update.message.reply_text(f"Couldn't find {esc(name)} in the saved list.", parse_mode="HTML")
    save_chat(chat_id, chat)
    await update.message.reply_text(f"Removed {esc(name)}.", parse_mode="HTML")


# ------------------------------ /hunt wizard ------------------------------

def types_keyboard(calib: dict, selected: list[int]) -> InlineKeyboardMarkup:
    rows = [[(("☑️ " if i in selected else "▫️ ") + v["name"], f"hw:t:{i}")] for i, v in enumerate(calib["veh"])]
    rows.append([("▶ Next", "hw:tdone")])
    return kb(rows)


def models_keyboard(models: list[str], selected: list[int]) -> InlineKeyboardMarkup:
    rows = [[(("☑️ " if i in selected else "▫️ ") + m, f"hw:m:{i}")] for i, m in enumerate(models)]
    rows.append([("▶ Next", "hw:mdone")])
    return kb(rows)


def age_keyboard() -> InlineKeyboardMarkup:
    return kb([[("PARF only", "hw:age:parf"), ("Renewed only", "hw:age:renewed"), ("Both", "hw:age:both")]])


async def cmd_hunt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chat = get_chat(chat_id)
    chat["session"] = {"step": "wizard", "draft": {"types": [], "models": [], "age": None, "stage": "types"}}
    save_chat(chat_id, chat)
    calib = normalize_calib(chat["calib"])
    await update.message.reply_text("🎯 <b>Vehicle types</b> — tap to toggle, then Next.", parse_mode="HTML",
                                     reply_markup=types_keyboard(calib, []))


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cq = update.callback_query
    chat_id = update.effective_chat.id
    data = cq.data or ""
    chat = get_chat(chat_id)
    try:
        if data.startswith("hw:t:"):
            return await wizard_toggle(cq, chat, chat_id, "types", int(data[5:]))
        if data == "hw:tdone":
            return await wizard_stage(cq, chat, chat_id, "models")
        if data.startswith("hw:m:"):
            return await wizard_toggle(cq, chat, chat_id, "models", int(data[5:]))
        if data == "hw:mdone":
            return await wizard_stage(cq, chat, chat_id, "age")
        if data.startswith("hw:age:"):
            return await wizard_age(cq, chat, chat_id, data[7:])
        if data == "savehunt":
            return await prompt_hunt_name(cq, chat, chat_id)
        if data.startswith("hr:"):
            return await run_saved_hunt(cq, chat, chat_id, int(data[3:]))
        if data.startswith("hd:"):
            return await delete_saved_hunt(cq, chat, chat_id, int(data[3:]))
        if data.startswith("cal:f:"):
            return await pick_calib_field(cq, chat, chat_id, CAL_FIELDS[int(data[6:])][0])
        if data.startswith("cal:v:"):
            calib = normalize_calib(chat["calib"])
            return await pick_calib_field(cq, chat, chat_id, "veh:" + calib["veh"][int(data[6:])]["name"])
        return await cq.answer()
    except Exception:
        log.exception("callback error")
        return await cq.answer("Something went wrong — try /reset")


async def wizard_toggle(cq, chat, chat_id, field, idx):
    d = chat["session"]["draft"]
    if idx in d[field]:
        d[field].remove(idx)
    else:
        d[field].append(idx)
    save_chat(chat_id, chat)
    calib = normalize_calib(chat["calib"])
    if field == "types":
        await cq.edit_message_text("🎯 <b>Vehicle types</b> — tap to toggle, then Next.", parse_mode="HTML",
                                    reply_markup=types_keyboard(calib, d["types"]))
    else:
        await cq.edit_message_text("🚗 <b>Models</b> — tap to toggle, then Next.", parse_mode="HTML",
                                    reply_markup=models_keyboard(chat["models"], d["models"]))
    return await cq.answer()


async def wizard_stage(cq, chat, chat_id, stage):
    chat["session"]["draft"]["stage"] = stage
    save_chat(chat_id, chat)
    if stage == "models":
        await cq.edit_message_text("🚗 <b>Models</b> — tap to toggle, then Next.", parse_mode="HTML",
                                    reply_markup=models_keyboard(chat["models"], chat["session"]["draft"]["models"]))
    if stage == "age":
        await cq.edit_message_text("📋 <b>COE status</b>", parse_mode="HTML", reply_markup=age_keyboard())
    return await cq.answer()


async def wizard_age(cq, chat, chat_id, age):
    chat["session"]["draft"]["age"] = ["parf", "renewed"] if age == "both" else [age]
    chat["session"]["step"] = "hunt_numbers"
    save_chat(chat_id, chat)
    await cq.edit_message_text(f"📋 COE status set: <b>{'Both' if age == 'both' else age}</b>", parse_mode="HTML")
    await cq.message.chat.send_message(
        "💰 Send your numbers as <b>six values in order</b>, space-separated, use <code>-</code> to skip any:\n"
        "<code>price dep coeMin coeMax km owners</code>\n\n"
        "Example: <code>60000 15000 1.5 4 100000 2</code> (max $60k, max $15k/yr dep, 1.5–4 yrs COE left, "
        "max 100,000km, max 2 owners)\n\nSend just <code>-</code> for all six to search with no numeric filters at all.",
        parse_mode="HTML")
    return await cq.answer()


async def handle_hunt_numbers(update: Update, chat: dict, chat_id):
    tokens = update.message.text.strip().split()
    tokens = (tokens + ["-"] * 6)[:6]
    price, dep, coe_min, coe_max, km, owners = (None if t == "-" else num(t) for t in tokens)
    filters_ = {"sort": "DEP_ASC", "price": price, "dep": dep, "coeMin": coe_min, "coeMax": coe_max, "km": km, "owners": owners}
    d = chat["session"]["draft"]
    calib = normalize_calib(chat["calib"])
    model_names = [chat["models"][i] for i in d["models"] if i < len(chat["models"])]
    type_names = [calib["veh"][i]["name"] for i in d["types"] if i < len(calib["veh"])]
    queue = build_queue(calib, filters_, d["age"], model_names, type_names)

    chat["session"] = {"step": None, "draft": None, "lastHunt": {"models": model_names, "types": type_names, "age": d["age"], "filters": filters_}}
    save_chat(chat_id, chat)

    if not queue:
        return await update.message.reply_text("No searches to build — something went wrong, try /hunt again.")
    unverified = ("\n\n⚠️ Not calibrated yet — these links use best-effort guesses. Run /calibrate to make sure they actually work."
                  if not calib["verifiedBase"] else "")
    header = f"✅ <b>{len(queue)} search{'es' if len(queue) != 1 else ''} ready</b>{unverified}"
    await send_queue_as_text(update.get_bot(), chat_id, header, queue)
    await update.message.reply_text("Want to keep this hunt?",
                                     reply_markup=kb([[("💾 Save as hunt", "savehunt")]]))


async def prompt_hunt_name(cq, chat, chat_id):
    if not chat.get("session") or not chat["session"].get("lastHunt"):
        return await cq.answer("That hunt has expired — run /hunt again.")
    chat["session"]["step"] = "hunt_save_name"
    save_chat(chat_id, chat)
    await cq.message.chat.send_message("What should this hunt be called? (e.g. \"Family SUV under 60k\")")
    return await cq.answer()


async def handle_hunt_save_name(update: Update, chat: dict, chat_id):
    import time
    h = chat["session"]["lastHunt"]
    name = update.message.text.strip()
    chat["hunts"].insert(0, {"id": int(time.time() * 1000), "name": name, "tag": "", **h})
    chat["session"] = None
    save_chat(chat_id, chat)
    await update.message.reply_text(f"Saved as “{esc(name)}”. Run it anytime with /hunts.", parse_mode="HTML")


async def cmd_hunts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = get_chat(update.effective_chat.id)
    if not chat["hunts"]:
        return await update.message.reply_text("No saved hunts yet — build one with /hunt, then tap “Save as hunt”.")
    rows = [[(f"▶ {h['name']}", f"hr:{i}"), ("🗑", f"hd:{i}")] for i, h in enumerate(chat["hunts"])]
    await update.message.reply_text(f"<b>Saved hunts</b> ({len(chat['hunts'])})", parse_mode="HTML", reply_markup=kb(rows))


async def run_saved_hunt(cq, chat, chat_id, i):
    if i >= len(chat["hunts"]):
        return await cq.answer("Not found — it may have been deleted.")
    h = chat["hunts"][i]
    calib = normalize_calib(chat["calib"])
    queue = build_queue(calib, h["filters"], h["age"], h["models"], h["types"])
    await send_queue_as_text(cq.get_bot(), chat_id, f"🎯 <b>{esc(h['name'])}</b> — {len(queue)} search{'es' if len(queue) != 1 else ''}", queue)
    return await cq.answer("Links sent")


async def delete_saved_hunt(cq, chat, chat_id, i):
    if i >= len(chat["hunts"]):
        return await cq.answer("Already gone.")
    h = chat["hunts"].pop(i)
    save_chat(chat_id, chat)
    rows = [[(f"▶ {hh['name']}", f"hr:{ii}"), ("🗑", f"hd:{ii}")] for ii, hh in enumerate(chat["hunts"])]
    text = f"<b>Saved hunts</b> ({len(chat['hunts'])})" if chat["hunts"] else "No saved hunts left."
    await cq.edit_message_text(text, parse_mode="HTML", reply_markup=kb(rows))
    return await cq.answer(f"Deleted “{h['name']}”")


# ------------------------------ /calibrate ------------------------------

async def cmd_calibrate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = get_chat(update.effective_chat.id)
    calib = normalize_calib(chat["calib"])
    rows = [[(("✓ " if calib["verified"][key] else "◻ ") + label, f"cal:f:{i}")] for i, (key, label) in enumerate(CAL_FIELDS)]
    for i, v in enumerate(calib["veh"]):
        rows.append([(("✓ " if v["verified"] else "◻ ") + "Vehicle type: " + v["name"], f"cal:v:{i}")])
    await update.message.reply_text(
        "🛠 <b>Calibrate SGCarmart</b>\nPick what to calibrate. Then, on sgcarmart.com (from Singapore), set "
        "<b>exactly one</b> filter and copy the URL — you'll be asked for the plain URL (no filters) and the "
        "one-filter URL.", parse_mode="HTML", reply_markup=kb(rows))


async def pick_calib_field(cq, chat, chat_id, kind):
    chat["session"] = {"step": "calib_base", "draft": {"kind": kind}}
    save_chat(chat_id, chat)
    reuse = f"\n\nOr reply <code>same</code> to reuse: {esc(chat.get('lastBaseUrl'))}" if chat.get("lastBaseUrl") else ""
    await cq.message.chat.send_message(f"Send the <b>plain</b> sgcarmart.com URL — no filters set at all.{reuse}", parse_mode="HTML")
    return await cq.answer()


def _looks_like_sgcarmart(url: str) -> bool:
    return bool(re.match(r"^https://([a-z0-9-]+\.)*sgcarmart\.com/", url, re.IGNORECASE))


async def handle_calib_base(update: Update, chat: dict, chat_id):
    text = update.message.text.strip()
    url = chat["lastBaseUrl"] if (text.lower() == "same" and chat.get("lastBaseUrl")) else text
    if not _looks_like_sgcarmart(url):
        return await update.message.reply_text("That doesn't look like an sgcarmart.com URL — try again, or /reset to cancel.")
    chat["session"]["draft"]["baseUrl"] = url
    chat["lastBaseUrl"] = url
    chat["session"]["step"] = "calib_filled"
    save_chat(chat_id, chat)
    await update.message.reply_text("Now set ONLY that one filter on sgcarmart.com and send the resulting URL.")


async def handle_calib_filled(update: Update, chat: dict, chat_id):
    url = update.message.text.strip()
    if not _looks_like_sgcarmart(url):
        return await update.message.reply_text("That doesn't look like an sgcarmart.com URL — try again, or /reset to cancel.")
    chat["session"]["draft"]["filledUrl"] = url
    if chat["session"]["draft"]["kind"] in CALIB_NEEDS_VALUE:
        chat["session"]["step"] = "calib_rawvalue"
        save_chat(chat_id, chat)
        return await update.message.reply_text("What value did you actually type into that filter on the site? (e.g. 50000)")
    await finish_calib(update, chat, chat_id, None)


async def handle_calib_rawvalue(update: Update, chat: dict, chat_id):
    await finish_calib(update, chat, chat_id, update.message.text.strip())


async def finish_calib(update: Update, chat: dict, chat_id, raw_value):
    draft = chat["session"]["draft"]
    calib, result = calib_apply(chat["calib"], draft["kind"], raw_value, draft["baseUrl"], draft["filledUrl"])
    chat["calib"] = calib
    chat["session"] = None
    save_chat(chat_id, chat)
    if not result.get("ok"):
        return await update.message.reply_text(f"❌ {esc(result['err'])}\n\nTry /calibrate again.", parse_mode="HTML")
    await update.message.reply_text(f"✅ Calibrated: <code>{esc(result['param'])}={esc(result['value'])}</code>. Future hunts use this.", parse_mode="HTML")


# ------------------------------ /add + /shortlist ------------------------------

async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chat = get_chat(chat_id)
    chat["session"] = {"step": "add_blob"}
    save_chat(chat_id, chat)
    await update.message.reply_text(
        "Paste the listing's details block (price, depreciation, reg date, mileage, ARF, owners, road tax) "
        "— copy it straight off the SGCarmart ad.")


async def handle_add_blob(update: Update, chat: dict, chat_id):
    import time
    fields, got = parse_listing(update.message.text)
    chat["session"] = None
    if not got:
        save_chat(chat_id, chat)
        return await update.message.reply_text("Couldn't find any figures in that — try pasting the full details block, or /add to try again.")

    car = {
        "id": int(time.time() * 1000), "name": fields.get("name") or "Unnamed car", "url": fields.get("url") or "",
        "price": fields.get("price"), "dep": fields.get("dep"), "reg": fields.get("reg"),
        "km": fields.get("km"), "arf": fields.get("arf"), "coe": fields.get("coe"),
        "owners": fields.get("owners"), "tax": fields.get("tax"), "deregListed": fields.get("deregListed"),
        "coeType": fields.get("coeType") or "original", "notes": "",
    }
    chat["cars"].insert(0, car)
    save_chat(chat_id, chat)

    d = derive(car)
    fs = flags(car, d)
    lines = [f"<b>{esc(car['name'])}</b>"]
    if car.get("price") is not None:
        lines.append(f"Price: {money(car['price'])}")
    if d.get("effDep") is not None:
        lines.append(f"True dep/yr: <b>{money(d['effDep'])}</b>")
    if car.get("dep") is not None:
        lines.append(f"Listed dep: {money(car['dep'])}")
    if d.get("yearsLeft") is not None:
        lines.append(f"COE left: {d['yearsLeft']:.1f} yrs")
    if fs:
        lines.append("\n⚠️ " + "\n⚠️ ".join(f[1] for f in fs))
    await update.message.reply_text(
        f"Logged (read {len(got)} field{'s' if len(got) != 1 else ''}: {esc(', '.join(got))})\n\n" + "\n".join(lines),
        parse_mode="HTML")


async def cmd_shortlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = get_chat(update.effective_chat.id)
    if not chat["cars"]:
        return await update.message.reply_text("No cars saved yet. Paste one with /add.")
    rows = sorted(score_all(chat["cars"]), key=lambda r: (r["score"] if r["score"] is not None else -1), reverse=True)[:15]
    lines = []
    for r in rows:
        d, c = r["d"], r["c"]
        head = f"<b>{esc(c['name'])}</b>" + (f" — {r['score']}pts" if r["score"] is not None else "")
        bits = f"  {money(c.get('price'))} · true dep {money(d.get('effDep'))}/yr"
        if d.get("yearsLeft") is not None:
            bits += f" · {d['yearsLeft']:.1f}y COE left"
        lines.append(head + "\n" + bits)
    await update.message.reply_text(
        f"<b>Shortlist</b> ({len(chat['cars'])}, top {len(rows)} by score)\n\n" + "\n\n".join(lines), parse_mode="HTML")


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chat = get_chat(chat_id)
    chat["session"] = None
    save_chat(chat_id, chat)
    await update.message.reply_text("Cancelled whatever I was asking. Send /help to see what's next.")


# ============================== free-text routing ==============================

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chat = get_chat(chat_id)
    step = (chat.get("session") or {}).get("step")
    if not step:
        return  # no active flow — don't spam a group chat with unrelated replies
    if step == "hunt_numbers":
        return await handle_hunt_numbers(update, chat, chat_id)
    if step == "hunt_save_name":
        return await handle_hunt_save_name(update, chat, chat_id)
    if step == "calib_base":
        return await handle_calib_base(update, chat, chat_id)
    if step == "calib_filled":
        return await handle_calib_filled(update, chat, chat_id)
    if step == "calib_rawvalue":
        return await handle_calib_rawvalue(update, chat, chat_id)
    if step == "add_blob":
        return await handle_add_blob(update, chat, chat_id)


# ============================== boot ==============================

def build_app() -> Application:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN (env var or .env file) before running — see README_BOT.md")
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler(["start", "help"], cmd_help))
    app.add_handler(CommandHandler("models", cmd_models))
    app.add_handler(CommandHandler("addmodel", cmd_addmodel))
    app.add_handler(CommandHandler("delmodel", cmd_delmodel))
    app.add_handler(CommandHandler("hunt", cmd_hunt))
    app.add_handler(CommandHandler("hunts", cmd_hunts))
    app.add_handler(CommandHandler("calibrate", cmd_calibrate))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("shortlist", cmd_shortlist))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app


if __name__ == "__main__":
    # Optional .env support without adding a dependency: a plain KEY=VALUE
    # file read manually, only if python-dotenv isn't installed.
    if os.path.exists(".env") and "TELEGRAM_BOT_TOKEN" not in os.environ:
        with open(".env", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

    application = build_app()
    log.info("SG Car Scout bot starting (long-polling)...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)
