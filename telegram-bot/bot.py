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

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Update
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters,
)

from carmath import derive, flags, money, num, score_all
from calib import CAL_FIELDS, build_queue, calib_apply, normalize_calib
from parse import parse_listing
from store import get_chat, save_chat
from models_catalog import BRANDS, CATALOG

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
    "<b>/auto</b> — run every saved hunt at once (still no auto-scraping — you tap through, I just save the clicking)\n"
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


def age_keyboard() -> InlineKeyboardMarkup:
    return kb([[("PARF only", "hw:age:parf"), ("Renewed only", "hw:age:renewed"), ("Both", "hw:age:both")]])


# ------------------------------ model browser ------------------------------
# Three views, all button-driven: a root menu (saved list / browse by brand /
# type a name), a per-brand model list, and the saved-models list — plus a
# free-text escape hatch for anything not in the catalog (rare trims, AMG/M
# variants not listed, etc.). Selections are tracked by full name string
# ("Brand Model"), not by index, so switching between views never desyncs.

def _models_done_label(selected: list[str]) -> str:
    return f"✅ Done ({len(selected)} picked)" if selected else "✅ Done (search all models)"


def models_root_keyboard(selected: list[str]) -> InlineKeyboardMarkup:
    rows = [[("⭐ My saved models", "hw:msaved")]]
    for i in range(0, len(BRANDS), 2):
        row = [(BRANDS[i], f"hw:mbrand:{i}")]
        if i + 1 < len(BRANDS):
            row.append((BRANDS[i + 1], f"hw:mbrand:{i + 1}"))
        rows.append(row)
    rows.append([("✏️ Type a model", "hw:mcustom")])
    rows.append([(_models_done_label(selected), "hw:mdone")])
    return kb(rows)


def models_brand_keyboard(brand: str, selected: list[str]) -> InlineKeyboardMarkup:
    rows = [[(("☑️ " if f"{brand} {m}" in selected else "▫️ ") + m, f"hw:mt:{brand} {m}")] for m in CATALOG[brand]]
    rows.append([(f"✏️ Add a specific {brand} model", "hw:mcustom")])
    rows.append([("◀ Brands", "hw:mback")])
    rows.append([(_models_done_label(selected), "hw:mdone")])
    return kb(rows)


def models_saved_keyboard(models: list[str], selected: list[str]) -> InlineKeyboardMarkup:
    rows = [[(("☑️ " if m in selected else "▫️ ") + m, f"hw:mt:{m}")] for m in models] or [[("(none saved — use /addmodel)", "noop")]]
    rows.append([("◀ Brands", "hw:mback")])
    rows.append([(_models_done_label(selected), "hw:mdone")])
    return kb(rows)


MODELS_ROOT_TEXT = "🚗 <b>Models</b> — browse by brand, use your saved list, or type one directly. Pick any number, then Done."


async def cmd_hunt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chat = get_chat(chat_id)
    chat["session"] = {"step": "wizard", "draft": {"types": [], "modelNames": [], "age": None, "stage": "types", "curView": None}}
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
        if data == "noop":
            return await cq.answer()
        if data.startswith("hw:t:"):
            return await wizard_toggle_type(cq, chat, chat_id, int(data[5:]))
        if data == "hw:tdone":
            return await wizard_stage(cq, chat, chat_id, "models")
        if data.startswith("hw:mbrand:"):
            return await wizard_show_brand(cq, chat, chat_id, int(data[10:]))
        if data == "hw:msaved":
            return await wizard_show_saved(cq, chat, chat_id)
        if data == "hw:mback":
            return await wizard_show_models_root(cq, chat, chat_id)
        if data.startswith("hw:mt:"):
            return await wizard_toggle_model_name(cq, chat, chat_id, data[6:])
        if data == "hw:mcustom":
            return await prompt_custom_models(cq, chat, chat_id)
        if data == "hw:mdone":
            return await wizard_stage(cq, chat, chat_id, "age")
        if data.startswith("hw:age:"):
            return await wizard_age(cq, chat, chat_id, data[7:])
        if data.startswith("hw:n:"):
            _, _, fi, oi = data.split(":")
            return await wizard_num_pick(cq, chat, chat_id, int(fi), int(oi))
        if data.startswith("hw:ncustom:"):
            return await wizard_num_custom_prompt(cq, chat, chat_id, int(data[11:]))
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
        if data.startswith("sl:"):
            return await show_user_shortlist(cq, chat, chat_id, data[3:])
        if data == "add_done":
            return await finish_add(cq, chat, chat_id)
        if data.startswith("car_del:"):
            _, car_id_s, view_sel = data.split(":", 2)
            return await delete_own_car(cq, chat, chat_id, int(car_id_s), view_sel)
        return await cq.answer()
    except Exception:
        log.exception("callback error")
        return await cq.answer("Something went wrong — try /reset")


async def wizard_toggle_type(cq, chat, chat_id, idx):
    d = chat["session"]["draft"]
    if idx in d["types"]:
        d["types"].remove(idx)
    else:
        d["types"].append(idx)
    save_chat(chat_id, chat)
    calib = normalize_calib(chat["calib"])
    await cq.edit_message_text("🎯 <b>Vehicle types</b> — tap to toggle, then Next.", parse_mode="HTML",
                                reply_markup=types_keyboard(calib, d["types"]))
    return await cq.answer()


async def wizard_stage(cq, chat, chat_id, stage):
    chat["session"]["draft"]["stage"] = stage
    if stage == "models":
        chat["session"]["draft"]["curView"] = "root"
    save_chat(chat_id, chat)
    if stage == "models":
        await cq.edit_message_text(MODELS_ROOT_TEXT, parse_mode="HTML",
                                    reply_markup=models_root_keyboard(chat["session"]["draft"]["modelNames"]))
    if stage == "age":
        await cq.edit_message_text("📋 <b>COE status</b>", parse_mode="HTML", reply_markup=age_keyboard())
    return await cq.answer()


async def wizard_show_brand(cq, chat, chat_id, brand_idx):
    brand = BRANDS[brand_idx]
    chat["session"]["draft"]["curView"] = f"brand:{brand}"
    save_chat(chat_id, chat)
    sel = chat["session"]["draft"]["modelNames"]
    await cq.edit_message_text(f"🚗 <b>{esc(brand)}</b> — tap to toggle.", parse_mode="HTML",
                                reply_markup=models_brand_keyboard(brand, sel))
    return await cq.answer()


async def wizard_show_saved(cq, chat, chat_id):
    chat["session"]["draft"]["curView"] = "saved"
    save_chat(chat_id, chat)
    sel = chat["session"]["draft"]["modelNames"]
    await cq.edit_message_text("⭐ <b>Your saved models</b> — tap to toggle.", parse_mode="HTML",
                                reply_markup=models_saved_keyboard(chat["models"], sel))
    return await cq.answer()


async def wizard_show_models_root(cq, chat, chat_id):
    chat["session"]["draft"]["curView"] = "root"
    save_chat(chat_id, chat)
    sel = chat["session"]["draft"]["modelNames"]
    await cq.edit_message_text(MODELS_ROOT_TEXT, parse_mode="HTML", reply_markup=models_root_keyboard(sel))
    return await cq.answer()


async def wizard_toggle_model_name(cq, chat, chat_id, name):
    d = chat["session"]["draft"]
    sel = d["modelNames"]
    if name in sel:
        sel.remove(name)
    else:
        sel.append(name)
    save_chat(chat_id, chat)
    view = d.get("curView") or "root"
    if view.startswith("brand:"):
        brand = view[6:]
        await cq.edit_message_text(f"🚗 <b>{esc(brand)}</b> — tap to toggle.", parse_mode="HTML",
                                    reply_markup=models_brand_keyboard(brand, sel))
    elif view == "saved":
        await cq.edit_message_text("⭐ <b>Your saved models</b> — tap to toggle.", parse_mode="HTML",
                                    reply_markup=models_saved_keyboard(chat["models"], sel))
    else:
        await cq.edit_message_text(MODELS_ROOT_TEXT, parse_mode="HTML", reply_markup=models_root_keyboard(sel))
    return await cq.answer()


async def prompt_custom_models(cq, chat, chat_id):
    chat["session"]["step"] = "hunt_custom_models"
    save_chat(chat_id, chat)
    view = chat["session"]["draft"].get("curView") or "root"
    if view.startswith("brand:"):
        brand = view[6:]
        text = (f"Type one or more {esc(brand)} model names or trims, separated by commas or new lines — "
                f"just the model (e.g. <code>S5, RS3</code>), I'll add “{esc(brand)}” automatically.")
    else:
        text = ("Type one or more model names, separated by commas or new lines — e.g. "
                "<code>Mercedes-AMG E63, Ferrari 488</code>.")
    await cq.message.chat.send_message(text, parse_mode="HTML")
    return await cq.answer()


async def handle_custom_models(update: Update, chat: dict, chat_id):
    d = chat["session"]["draft"]
    view = d.get("curView") or "root"
    brand_prefix = view[6:] + " " if view.startswith("brand:") else ""
    names = [n.strip() for n in re.split(r"[,\n]", update.message.text) if n.strip()]
    names = [n if (not brand_prefix or n.lower().startswith(brand_prefix.lower())) else brand_prefix + n for n in names]
    sel = d["modelNames"]
    added = [n for n in names if n not in sel]
    sel.extend(added)
    chat["session"]["step"] = "wizard"
    save_chat(chat_id, chat)
    await update.message.reply_text(f"Added: {esc(', '.join(added)) if added else '(nothing new)'}", parse_mode="HTML")
    if view.startswith("brand:"):
        brand = view[6:]
        await update.message.reply_text(f"🚗 <b>{esc(brand)}</b> — tap to toggle more, or Done.", parse_mode="HTML",
                                         reply_markup=models_brand_keyboard(brand, sel))
    else:
        d["curView"] = "root"
        save_chat(chat_id, chat)
        await update.message.reply_text(MODELS_ROOT_TEXT, parse_mode="HTML", reply_markup=models_root_keyboard(sel))


async def wizard_age(cq, chat, chat_id, age):
    chat["session"]["draft"]["age"] = ["parf", "renewed"] if age == "both" else [age]
    chat["session"]["draft"]["numIdx"] = 0
    chat["session"]["draft"]["nums"] = {}
    chat["session"]["step"] = "wizard"
    save_chat(chat_id, chat)
    await cq.edit_message_text(f"📋 COE status set: <b>{'Both' if age == 'both' else age}</b>", parse_mode="HTML")
    await send_num_field(cq.get_bot(), chat_id, 0)
    return await cq.answer()


# ------------------------------ numbers, one field at a time ------------------------------
# Quick-pick buttons cover the common cases; "Custom value" drops into a
# single free-text reply for that one field only, then resumes the button
# flow for the next field.
NUM_FIELDS = [
    ("price", "💰 Max price", [("$30k", "30000"), ("$50k", "50000"), ("$70k", "70000"), ("$100k", "100000"), ("$150k", "150000"), ("No limit", "-")]),
    ("dep", "📉 Max depreciation/yr", [("$8k", "8000"), ("$12k", "12000"), ("$15k", "15000"), ("$20k", "20000"), ("$30k", "30000"), ("No limit", "-")]),
    ("coeMin", "⏳ Min COE left", [("0 yrs", "0"), ("1 yr", "1"), ("2 yrs", "2"), ("3 yrs", "3"), ("5 yrs", "5"), ("No limit", "-")]),
    ("coeMax", "⏳ Max COE left", [("3 yrs", "3"), ("5 yrs", "5"), ("7 yrs", "7"), ("10 yrs", "10"), ("No limit", "-")]),
    ("km", "🛣 Max mileage", [("50,000km", "50000"), ("80,000km", "80000"), ("100,000km", "100000"), ("150,000km", "150000"), ("200,000km", "200000"), ("No limit", "-")]),
    ("owners", "👤 Max owners", [("1", "1"), ("2", "2"), ("3", "3"), ("5", "5"), ("No limit", "-")]),
]


def num_field_keyboard(field_idx: int) -> InlineKeyboardMarkup:
    _, _, opts = NUM_FIELDS[field_idx]
    rows, row = [], []
    for i, (text, _) in enumerate(opts):
        row.append((text, f"hw:n:{field_idx}:{i}"))
        if len(row) == 3:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([("✏️ Custom value", f"hw:ncustom:{field_idx}")])
    return kb(rows)


async def send_num_field(bot, chat_id, field_idx):
    _, label, _ = NUM_FIELDS[field_idx]
    await bot.send_message(chat_id, f"{label} — pick one, or enter a custom value.", parse_mode="HTML",
                            reply_markup=num_field_keyboard(field_idx))


async def wizard_num_pick(cq, chat, chat_id, field_idx, opt_idx):
    key, label, opts = NUM_FIELDS[field_idx]
    opt_label, raw = opts[opt_idx]
    value = None if raw == "-" else num(raw)
    chat["session"]["draft"]["nums"][key] = value
    next_idx = field_idx + 1
    await cq.answer(opt_label)
    if next_idx < len(NUM_FIELDS):
        chat["session"]["draft"]["numIdx"] = next_idx
        save_chat(chat_id, chat)
        await send_num_field(cq.get_bot(), chat_id, next_idx)
    else:
        save_chat(chat_id, chat)
        await finalize_hunt(chat, chat_id, cq.get_bot())


async def wizard_num_custom_prompt(cq, chat, chat_id, field_idx):
    chat["session"]["step"] = "hunt_custom_number"
    chat["session"]["draft"]["numIdx"] = field_idx
    save_chat(chat_id, chat)
    _, label, _ = NUM_FIELDS[field_idx]
    await cq.message.chat.send_message(f"Type the exact number for {label}.", parse_mode="HTML")
    return await cq.answer()


async def handle_custom_number(update: Update, chat: dict, chat_id):
    idx = chat["session"]["draft"]["numIdx"]
    key, label, _ = NUM_FIELDS[idx]
    val = num(update.message.text.strip())
    if val is None:
        return await update.message.reply_text("That doesn't look like a number — try again, or /reset to cancel.")
    chat["session"]["draft"]["nums"][key] = val
    chat["session"]["step"] = "wizard"
    next_idx = idx + 1
    if next_idx < len(NUM_FIELDS):
        chat["session"]["draft"]["numIdx"] = next_idx
        save_chat(chat_id, chat)
        await send_num_field(update.get_bot(), chat_id, next_idx)
    else:
        save_chat(chat_id, chat)
        await finalize_hunt(chat, chat_id, update.get_bot())


async def finalize_hunt(chat: dict, chat_id, bot):
    d = chat["session"]["draft"]
    nums = d.get("nums", {})
    filters_ = {"sort": "DEP_ASC", "price": nums.get("price"), "dep": nums.get("dep"),
                "coeMin": nums.get("coeMin"), "coeMax": nums.get("coeMax"), "km": nums.get("km"), "owners": nums.get("owners")}
    calib = normalize_calib(chat["calib"])
    model_names = d.get("modelNames", [])
    type_names = [calib["veh"][i]["name"] for i in d.get("types", []) if i < len(calib["veh"])]
    queue = build_queue(calib, filters_, d["age"], model_names, type_names)

    chat["session"] = {"step": None, "draft": None, "lastHunt": {"models": model_names, "types": type_names, "age": d["age"], "filters": filters_}}
    save_chat(chat_id, chat)

    if not queue:
        return await bot.send_message(chat_id, "No searches to build — something went wrong, try /hunt again.")
    unverified = ("\n\n⚠️ Not calibrated yet — these links use best-effort guesses. Run /calibrate to make sure they actually work."
                  if not calib["verifiedBase"] else "")
    header = f"✅ <b>{len(queue)} search{'es' if len(queue) != 1 else ''} ready</b>{unverified}"
    await send_queue_as_text(bot, chat_id, header, queue)
    await bot.send_message(chat_id, "Want to keep this hunt?", parse_mode="HTML",
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


async def cmd_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Runs every saved hunt in one go and posts all the links. This is NOT
    automated checking — the bot still never fetches SGCarmart itself (see
    the module docstring); it just saves you from tapping ▶ on each saved
    hunt individually in /hunts. A human still has to open each link to see
    what's new, then /add anything worth logging."""
    chat_id = update.effective_chat.id
    chat = get_chat(chat_id)
    if not chat["hunts"]:
        return await update.message.reply_text("No saved hunts to run yet — build one with /hunt, then tap “Save as hunt”, then /auto will run all of them at once.")
    calib = normalize_calib(chat["calib"])
    bot_api = update.get_bot()
    await update.message.reply_text(f"🔔 Running {len(chat['hunts'])} saved hunt{'s' if len(chat['hunts']) != 1 else ''} — tap through and check for anything new, then /add what's worth logging.")
    for h in chat["hunts"]:
        queue = build_queue(calib, h["filters"], h["age"], h["models"], h["types"])
        await send_queue_as_text(bot_api, chat_id, f"🎯 <b>{esc(h['name'])}</b> — {len(queue)} search{'es' if len(queue) != 1 else ''}", queue)


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
    # Two different groups have now independently miscalibrated coeMin/
    # coeMax against SGCarmart's Registration Year field, because the real
    # site has no separate "COE years left" control -- Registration Year
    # (a from-year/to-year picker) is the only year-range filter that
    # exists, so it's the natural thing to reach for by mistake. Head this
    # off at the point of choice, before any URLs get collected.
    if kind in ("coeMin", "coeMax"):
        await cq.message.chat.send_message(
            "⚠️ Heads up: SGCarmart's real filter panel has <b>no separate \"COE years left\" control</b> — only "
            "Price, Depreciation, Registration Year, and Vehicle Type. For PARF-only hunts, min/max COE left "
            "already works without calibrating it — it's translated into a Registration Year range automatically "
            "(COE expires exactly 10 years after registration, so the two are mathematically the same thing for a "
            "never-renewed car). If the only year-range filter you can find on the site is <b>Registration Year</b>, "
            "that's a <i>different</i> field — <code>regFrom</code>/<code>regTo</code> below — don't calibrate COE "
            "Min/Max against it, or it'll break both. Only continue here if you've genuinely found a distinct "
            "\"years of COE left\" filter elsewhere on the site.", parse_mode="HTML")
    if kind in ("regFrom", "regTo"):
        await cq.message.chat.send_message(
            "ℹ️ This is SGCarmart's <b>Registration Year</b> filter (a from-year/to-year picker) — used "
            "automatically to tell PARF cars from renewed-COE ones.", parse_mode="HTML")
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
    kind = draft["kind"]
    calib, result = calib_apply(chat["calib"], kind, raw_value, draft["baseUrl"], draft["filledUrl"])

    if result.get("needValue"):
        # More than one param changed and there's no test value yet to tell
        # which is the dynamic one (e.g. Owners = a comparator param PLUS a
        # number param). Don't clear the session -- ask for the value and
        # retry with the same base/filled URLs already on file.
        names = ", ".join(k for k, _ in result["diffs"])
        chat["session"]["step"] = "calib_rawvalue"
        save_chat(chat_id, chat)
        return await update.message.reply_text(
            f"Found {len(result['diffs'])} changed params ({esc(names)}) for this filter — some SGCarmart filters "
            "genuinely need more than one (e.g. a comparator plus a number). Send the exact value you typed into "
            "that filter on the site so I can tell which param is which.", parse_mode="HTML")

    chat["calib"] = calib
    chat["session"] = None
    save_chat(chat_id, chat)
    if not result.get("ok"):
        return await update.message.reply_text(f"❌ {esc(result['err'])}\n\nTry /calibrate again.", parse_mode="HTML")
    extra_note = ""
    if result.get("extras"):
        extra_bits = ", ".join(f"{esc(p)}={esc(v)}" for p, v in result["extras"])
        if kind == "owners" and ["own_c", "<"] in result["extras"]:
            extra_note = (f" Companion param captured (<code>{extra_bits}</code> = “Less than”) — corrected for "
                           "automatically, so “max N owners” now sends N+1 under the hood to actually mean "
                           "“N or fewer” (SGCarmart has no native ≤ mode).")
        else:
            extra_note = (f" A fixed companion param was also captured (<code>{extra_bits}</code>) and will be sent "
                           "alongside it every time — worth double-checking a real search with this filter actually "
                           "gives what you expect, since comparator filters (less-than vs. less-than-or-equal, etc) "
                           "can be off by one.")
    # Only price/dep/km actually store anything derived from the test value
    # (a thousands-vs-raw-dollars scale factor) — every other field only
    # learns the parameter NAME. Say so explicitly so it's clear a search
    # value like "civic" or "50" used only to detect the param isn't locked
    # in as what future hunts search for.
    if kind in CALIB_NEEDS_VALUE:
        await update.message.reply_text(
            f"✅ Calibrated: parameter <code>{esc(result['param'])}</code> (detected from your test value "
            f"{esc(result['value'])}).{extra_note} Future hunts send your own numbers through this param.", parse_mode="HTML")
    else:
        await update.message.reply_text(
            f"✅ Calibrated: parameter is <code>{esc(result['param'])}</code>. Only the parameter name was learned — "
            f"future hunts use whatever you actually search for, not “{esc(result['value'])}”.{extra_note}", parse_mode="HTML")


# ------------------------------ /add + /shortlist ------------------------------

async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chat = get_chat(chat_id)
    chat["session"] = {"step": "add_blob", "carId": None}
    save_chat(chat_id, chat)
    await update.message.reply_text(
        "Paste the listing's details block (price, depreciation, reg date, mileage, ARF, owners, road tax) "
        "and/or the <b>listing URL</b> — in any order, across as many messages as you like (e.g. paste the link "
        "now, the details block after). Each paste fills in whatever's still missing on the same car. Tap "
        "✅ Done when you're finished, or just move on to another command.", parse_mode="HTML")


def _is_search_url(url: str) -> bool:
    """True for a SGCarmart *search results* URL (many cars), as opposed to
    one specific listing's page. These are exactly the endpoint shapes our
    own calib.py builds hunts against — /used-cars/listing and the legacy
    /used_cars/listing.php — vs. an individual ad's page (typically under
    /used-cars/info/ or /used_cars/info.php)."""
    path = re.sub(r"^https?://[^/]+", "", url).split("?")[0].lower()
    return path.rstrip("/").endswith(("/used-cars/listing", "/used_cars/listing.php"))


_ADD_FIELD_KEYS = ("name", "url", "price", "dep", "reg", "km", "arf", "coe", "owners", "tax", "deregListed")


def _add_missing_bits(car: dict) -> list[str]:
    missing = []
    if not car.get("url"):
        missing.append("the listing URL")
    if car.get("price") is None and car.get("dep") is None:
        missing.append("price/depreciation")
    if car.get("reg") is None:
        missing.append("reg date")
    return missing


async def handle_add_blob(update: Update, chat: dict, chat_id):
    import time
    fields, got = parse_listing(update.message.text)

    if fields.get("url") and _is_search_url(fields["url"]):
        save_chat(chat_id, chat)
        return await update.message.reply_text(
            "That's a <b>search results</b> link (many cars), not one specific listing — /add is for logging a "
            "single car you've found. Open the link, pick a listing you're interested in, then paste <i>that</i> "
            "ad's URL (or its details block) here instead.\n\nLooking to save the search itself? Use /hunt → "
            "💾 Save as hunt.", parse_mode="HTML")

    if not got:
        save_chat(chat_id, chat)
        return await update.message.reply_text("Couldn't find any figures in that — try pasting the full details block or a listing URL, or /add to try again.")

    car_id = chat["session"].get("carId")
    car = next((c for c in chat["cars"] if c["id"] == car_id), None) if car_id else None
    is_new = car is None

    if is_new:
        user = update.effective_user
        # name stays None (not a placeholder string) until actually found --
        # a placeholder here would be truthy and permanently block a real
        # name from a later paste under the "fill gaps only" merge below.
        car = {"id": int(time.time() * 1000), "name": None, "url": "", "notes": "",
               "coeType": "original", "addedBy": {"id": user.id, "name": user.full_name} if user else None}
        chat["cars"].insert(0, car)

    # Fill gaps only — a later paste never clobbers something already known
    # from an earlier one, so pasting the details block after the URL (or
    # vice versa) always merges into the same entry instead of overwriting.
    for k in _ADD_FIELD_KEYS:
        v = fields.get(k)
        if v is not None and v != "" and not car.get(k):
            car[k] = v
    if fields.get("coeType") and fields["coeType"] != "original":
        car["coeType"] = fields["coeType"]

    chat["session"]["carId"] = car["id"]
    save_chat(chat_id, chat)

    d = derive(car)
    fs = flags(car, d)
    lines = [f"<b>{esc(car.get('name') or 'Unnamed car')}</b>"]
    if car.get("url"):
        lines.append(f"🔗 <a href=\"{esc(car['url'])}\">link saved</a>")
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

    missing = _add_missing_bits(car)
    verb = "Saved" if is_new else "Updated"
    footer = f"\n\nStill missing: {', '.join(missing)} — paste that too, or tap Done." if missing else "\n\n✅ Looks complete."
    await update.message.reply_text(
        f"{verb} (read {len(got)} field{'s' if len(got) != 1 else ''}: {esc(', '.join(got))})\n\n"
        + "\n".join(lines) + footer,
        parse_mode="HTML", link_preview_options=NO_PREVIEW, reply_markup=kb([[("✅ Done", "add_done")]]))


async def finish_add(cq, chat, chat_id):
    chat["session"] = None
    save_chat(chat_id, chat)
    await cq.answer("Saved")
    try:
        await cq.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass


def _render_shortlist(cars: list[dict], title: str) -> str:
    if not cars:
        return f"<b>{esc(title)}</b>\n\n(nothing here yet)"
    rows = sorted(score_all(cars), key=lambda r: (r["score"] if r["score"] is not None else -1), reverse=True)[:15]
    lines = []
    for r in rows:
        d, c = r["d"], r["c"]
        head = f"<b>{esc(c.get('name') or 'Unnamed car')}</b>" + (f" — {r['score']}pts" if r["score"] is not None else "")
        if d.get("reg") is None:
            bits = "  🔖 bookmark, no figures yet — paste details with /add to complete it"
        else:
            bits = f"  {money(c.get('price'))} · true dep {money(d.get('effDep'))}/yr"
            if d.get("yearsLeft") is not None:
                bits += f" · {d['yearsLeft']:.1f}y COE left"
        lines.append(head + "\n" + bits)
    return f"<b>{esc(title)}</b> ({len(cars)}, top {len(rows)} by score)\n\n" + "\n\n".join(lines)


def _distinct_contributors(cars: list[dict]) -> list[tuple[int, str, int]]:
    seen: dict[int, dict] = {}
    for c in cars:
        ab = c.get("addedBy")
        if not ab:
            continue
        entry = seen.setdefault(ab["id"], {"name": ab["name"], "count": 0})
        entry["count"] += 1
    return [(uid, v["name"], v["count"]) for uid, v in seen.items()]


def _cars_for_view(chat: dict, sel: str) -> tuple[list[dict], str]:
    if sel == "all":
        multi = len(_distinct_contributors(chat["cars"])) > 1
        return chat["cars"], ("Everyone's shortlist" if multi else "Shortlist")
    uid = int(sel)
    cars = [c for c in chat["cars"] if (c.get("addedBy") or {}).get("id") == uid]
    name = next((c["addedBy"]["name"] for c in chat["cars"] if (c.get("addedBy") or {}).get("id") == uid), "Someone")
    return cars, f"{name}'s shortlist"


def _shortlist_delete_keyboard(cars: list[dict], viewer_id, view_sel: str):
    mine = [c for c in cars if (c.get("addedBy") or {}).get("id") == viewer_id]
    if not mine:
        return None
    rows = [[(f"🗑 {(c.get('name') or 'Unnamed car')[:30]}", f"car_del:{c['id']}:{view_sel}")] for c in mine]
    return kb(rows)


def _shortlist_view(chat: dict, sel: str, viewer_id):
    cars, title = _cars_for_view(chat, sel)
    text = _render_shortlist(cars, title)
    delkb = _shortlist_delete_keyboard(cars, viewer_id, sel) if viewer_id else None
    return text, delkb


async def cmd_shortlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = get_chat(update.effective_chat.id)
    if not chat["cars"]:
        return await update.message.reply_text("No cars saved yet. Paste one with /add.")
    contributors = _distinct_contributors(chat["cars"])
    # Only worth asking "whose?" once more than one person has actually
    # added something — a solo chat or one where nobody's attributed yet
    # (older entries, before this feature) just shows everyone's, as before.
    if len(contributors) <= 1:
        viewer_id = update.effective_user.id if update.effective_user else None
        text, delkb = _shortlist_view(chat, "all", viewer_id)
        return await update.message.reply_text(text, parse_mode="HTML", reply_markup=delkb)
    rows = [[("👥 Everyone", "sl:all")]]
    rows += [[(f"{name} ({count})", f"sl:{uid}")] for uid, name, count in contributors]
    await update.message.reply_text("Whose shortlist do you want to see?", reply_markup=kb(rows))


async def show_user_shortlist(cq, chat, chat_id, sel: str):
    text, delkb = _shortlist_view(chat, sel, cq.from_user.id)
    await cq.message.chat.send_message(text, parse_mode="HTML", reply_markup=delkb)
    return await cq.answer()


async def delete_own_car(cq, chat, chat_id, car_id, view_sel):
    car = next((c for c in chat["cars"] if c["id"] == car_id), None)
    if not car:
        return await cq.answer("Already gone.")
    owner_id = (car.get("addedBy") or {}).get("id")
    if owner_id != cq.from_user.id:
        return await cq.answer("You can only delete your own entries.", show_alert=True)
    chat["cars"] = [c for c in chat["cars"] if c["id"] != car_id]
    save_chat(chat_id, chat)
    text, delkb = _shortlist_view(chat, view_sel, cq.from_user.id)
    await cq.edit_message_text(text, parse_mode="HTML", reply_markup=delkb)
    return await cq.answer(f"Deleted {car.get('name') or 'that entry'}")


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
    if step == "hunt_custom_models":
        return await handle_custom_models(update, chat, chat_id)
    if step == "hunt_custom_number":
        return await handle_custom_number(update, chat, chat_id)
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

# Populates Telegram's native "/" command menu (the autocomplete list next to
# the text box) and the bot's profile description — otherwise Telegram shows
# raw command names with no explanation, and the chat is blank before anyone
# starts it. Runs on every startup so it can never drift from the actual
# command list below.
COMMANDS = [
    BotCommand("hunt", "Build a SGCarmart search — types, models, numbers"),
    BotCommand("hunts", "List, run, or delete saved hunts"),
    BotCommand("auto", "Run every saved hunt at once"),
    BotCommand("calibrate", "Teach the bot SGCarmart's real filter params"),
    BotCommand("add", "Paste a listing, get true depreciation + red flags"),
    BotCommand("shortlist", "Everyone's saved cars, ranked"),
    BotCommand("models", "Show the saved model list"),
    BotCommand("addmodel", "Add a model to the saved list"),
    BotCommand("delmodel", "Remove a model from the saved list"),
    BotCommand("reset", "Cancel whatever the bot is currently asking"),
    BotCommand("help", "Show what this bot can do"),
]

SHORT_DESCRIPTION = "Builds SGCarmart search links for your group — no scraping, just faster searching."
FULL_DESCRIPTION = (
    "SG Car Scout builds SGCarmart search links for your group and works out true "
    "depreciation on listings you paste in. It never scrapes or auto-fetches SGCarmart — "
    "every result is a link a human taps, same as the site's own terms expect.\n\n"
    "Send /help for the full command list, or /hunt to get started."
)


async def _post_init(application: Application) -> None:
    await application.bot.set_my_commands(COMMANDS)
    await application.bot.set_my_short_description(SHORT_DESCRIPTION)
    await application.bot.set_my_description(FULL_DESCRIPTION)
    log.info("Command menu and bot description set.")


def build_app() -> Application:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN (env var or .env file) before running — see README_BOT.md")
    app = Application.builder().token(token).post_init(_post_init).build()

    app.add_handler(CommandHandler(["start", "help"], cmd_help))
    app.add_handler(CommandHandler("models", cmd_models))
    app.add_handler(CommandHandler("addmodel", cmd_addmodel))
    app.add_handler(CommandHandler("delmodel", cmd_delmodel))
    app.add_handler(CommandHandler("hunt", cmd_hunt))
    app.add_handler(CommandHandler("hunts", cmd_hunts))
    app.add_handler(CommandHandler("auto", cmd_auto))
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
