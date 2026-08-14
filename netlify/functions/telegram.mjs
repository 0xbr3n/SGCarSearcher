// Telegram webhook entry point. Default Netlify Functions path for this
// file is /.netlify/functions/telegram — that's the exact URL you register
// with Telegram's setWebhook (see BOT_SETUP.md).
//
// Design: every command/callback/free-text message is handled per-chat,
// state lives in Netlify Blobs (lib/store.mjs), and every SGCarmart link
// this bot ever produces is built by the SAME calib.mjs used to describe
// this project's stance: no scraping, ever — the bot only ever builds a
// link and hands it to a human, exactly like the PWA does.
import { getChat, setChat } from "./lib/store.mjs";
import * as TG from "./lib/tg.mjs";
import { normalizeCalib, calibApply, buildQueue, CAL_FIELDS } from "./lib/calib.mjs";
import { derive, flags, scoreAll, money, num } from "./lib/carmath.mjs";
import { parseListing } from "./lib/parse.mjs";

const WEBHOOK_SECRET = process.env.TELEGRAM_WEBHOOK_SECRET;

export default async (req) => {
  try {
    if (req.method !== "POST") return new Response("ok", { status: 200 });
    if (WEBHOOK_SECRET) {
      const got = req.headers.get("x-telegram-bot-api-secret-token");
      if (got !== WEBHOOK_SECRET) { console.warn("webhook secret mismatch, ignoring"); return new Response("ok", { status: 200 }); }
    }
    const update = await req.json();
    if (update.message) await onMessage(update.message);
    else if (update.callback_query) await onCallback(update.callback_query);
  } catch (e) {
    console.error("webhook error:", e);
  }
  // Always 200 — a non-200 makes Telegram retry the same update repeatedly.
  return new Response("ok", { status: 200 });
};

/* ============================== messages ============================== */

async function onMessage(msg) {
  const chatId = msg.chat.id;
  const text = (msg.text || "").trim();
  if (!text) return;

  if (text.startsWith("/")) return onCommand(chatId, text);

  const chat = await getChat(chatId);
  const step = chat.session && chat.session.step;
  if (!step) return; // free text with no active flow — say nothing, don't spam a group chat

  if (step === "hunt_numbers") return handleHuntNumbers(chatId, chat, text);
  if (step === "hunt_save_name") return handleHuntSaveName(chatId, chat, text);
  if (step === "calib_base") return handleCalibBase(chatId, chat, text);
  if (step === "calib_filled") return handleCalibFilled(chatId, chat, text);
  if (step === "calib_rawvalue") return handleCalibRawValue(chatId, chat, text);
  if (step === "add_blob") return handleAddBlob(chatId, chat, text);
}

async function onCommand(chatId, text) {
  const [cmd, ...rest] = text.split(/\s+/);
  const arg = rest.join(" ");
  switch (cmd.replace(/@\w+$/, "").toLowerCase()) {
    case "/start":
    case "/help": return cmdHelp(chatId);
    case "/models": return cmdModels(chatId);
    case "/addmodel": return cmdAddModel(chatId, arg);
    case "/delmodel": return cmdDelModel(chatId, arg);
    case "/hunt": return cmdHunt(chatId);
    case "/hunts": return cmdHunts(chatId);
    case "/calibrate": return cmdCalibrate(chatId);
    case "/add": return cmdAdd(chatId);
    case "/shortlist": return cmdShortlist(chatId);
    case "/reset": return cmdReset(chatId);
    default: return TG.sendMessage(chatId, "Unknown command. Send /help to see what I can do.");
  }
}

function cmdHelp(chatId) {
  return TG.sendMessage(chatId,
`🕷️ <b>SG Car Scout bot</b> — builds SGCarmart search links for the group, no scraping, ever. This never fetches listings itself; it only builds a link and hands it to whoever taps it, same as sgcarmart.com wants.

<b>/hunt</b> — pick vehicle types + models + PARF/renewed, send your numbers, get links
<b>/hunts</b> — list, run, or delete saved hunts (shared with everyone in this chat)
<b>/models</b> — show the saved model list · <b>/addmodel</b> <i>name</i> · <b>/delmodel</b> <i>name</i>
<b>/calibrate</b> — teach the bot SGCarmart's real filter params (see below)
<b>/add</b> — paste a listing's details block, get true depreciation + red flags
<b>/shortlist</b> — everyone's saved cars, ranked by true depreciation
<b>/reset</b> — cancel whatever the bot is currently asking you

<b>Why calibrate?</b> SGCarmart is geofenced to Singapore and nobody outside it can verify its exact filter parameters. /calibrate lets you teach the bot the real ones from your own phone — set one filter on sgcarmart.com, paste the plain URL and the one-filter URL, and it works out the rest. Until you do, links use best-effort guesses.`);
}

/* ============================== models ============================== */

async function cmdModels(chatId) {
  const chat = await getChat(chatId);
  const lines = chat.models.map((m, i) => `${i + 1}. ${esc(m)}`).join("\n") || "(none saved)";
  return TG.sendMessage(chatId, `<b>Saved models</b>\n${lines}\n\nAdd with <code>/addmodel Toyota Camry</code>, remove with <code>/delmodel Toyota Camry</code>.`);
}
async function cmdAddModel(chatId, name) {
  name = name.trim();
  if (!name) return TG.sendMessage(chatId, "Usage: /addmodel Toyota Camry");
  const chat = await getChat(chatId);
  if (chat.models.some(m => m.toLowerCase() === name.toLowerCase())) return TG.sendMessage(chatId, `${esc(name)} is already saved.`);
  chat.models.push(name);
  await setChat(chatId, chat);
  return TG.sendMessage(chatId, `Added ${esc(name)}.`);
}
async function cmdDelModel(chatId, name) {
  name = name.trim();
  const chat = await getChat(chatId);
  const before = chat.models.length;
  chat.models = chat.models.filter(m => m.toLowerCase() !== name.toLowerCase());
  if (chat.models.length === before) return TG.sendMessage(chatId, `Couldn't find ${esc(name)} in the saved list.`);
  await setChat(chatId, chat);
  return TG.sendMessage(chatId, `Removed ${esc(name)}.`);
}

/* ============================== /hunt wizard ============================== */

async function cmdHunt(chatId) {
  const chat = await getChat(chatId);
  chat.session = { step: "wizard", draft: { types: [], models: [], age: null, stage: "types" } };
  await setChat(chatId, chat);
  const calib = normalizeCalib(chat.calib);
  return TG.sendMessage(chatId, "🎯 <b>Vehicle types</b> — tap to toggle, then Next.", typesKeyboard(calib, []));
}

function typesKeyboard(calib, selected) {
  const rows = calib.veh.map((v, i) => [TG.btn((selected.includes(i) ? "☑️ " : "▫️ ") + v.name, `hw:t:${i}`)]);
  rows.push([TG.btn("▶ Next", "hw:tdone")]);
  return TG.kb(rows);
}
function modelsKeyboard(models, selected) {
  const rows = models.map((m, i) => [TG.btn((selected.includes(i) ? "☑️ " : "▫️ ") + m, `hw:m:${i}`)]);
  rows.push([TG.btn("▶ Next", "hw:mdone")]);
  return TG.kb(rows);
}
function ageKeyboard() {
  return TG.kb([[TG.btn("PARF only", "hw:age:parf"), TG.btn("Renewed only", "hw:age:renewed"), TG.btn("Both", "hw:age:both")]]);
}

async function onCallback(cq) {
  const chatId = cq.message.chat.id;
  const messageId = cq.message.message_id;
  const data = cq.data || "";
  const chat = await getChat(chatId);

  try {
    if (data.startsWith("hw:t:")) return await wizardToggle(chat, chatId, messageId, "types", +data.slice(5), cq.id);
    if (data === "hw:tdone") return await wizardStage(chat, chatId, messageId, "models", cq.id);
    if (data.startsWith("hw:m:")) return await wizardToggle(chat, chatId, messageId, "models", +data.slice(5), cq.id);
    if (data === "hw:mdone") return await wizardStage(chat, chatId, messageId, "age", cq.id);
    if (data.startsWith("hw:age:")) return await wizardAge(chat, chatId, messageId, data.slice(7), cq.id);
    if (data === "savehunt") return await promptHuntName(chat, chatId, cq.id);
    if (data.startsWith("hr:")) return await runSavedHunt(chat, chatId, +data.slice(3), cq.id);
    if (data.startsWith("hd:")) return await deleteSavedHunt(chat, chatId, messageId, +data.slice(3), cq.id);
    if (data.startsWith("cal:f:")) return await pickCalibField(chat, chatId, CAL_FIELDS[+data.slice(6)][0], cq.id);
    if (data.startsWith("cal:v:")) {
      const calib = normalizeCalib(chat.calib);
      return await pickCalibField(chat, chatId, "veh:" + calib.veh[+data.slice(6)].name, cq.id);
    }
    return TG.answerCallbackQuery(cq.id);
  } catch (e) {
    console.error("callback error:", e);
    return TG.answerCallbackQuery(cq.id, "Something went wrong — try /reset");
  }
}

async function wizardToggle(chat, chatId, messageId, field, idx, cqId) {
  const d = chat.session.draft;
  const i = d[field].indexOf(idx);
  if (i >= 0) d[field].splice(i, 1); else d[field].push(idx);
  await setChat(chatId, chat);
  const calib = normalizeCalib(chat.calib);
  const kb = field === "types" ? typesKeyboard(calib, d.types) : modelsKeyboard(chat.models, d.models);
  await TG.editMessageText(chatId, messageId, field === "types" ? "🎯 <b>Vehicle types</b> — tap to toggle, then Next." : "🚗 <b>Models</b> — tap to toggle, then Next.", kb);
  return TG.answerCallbackQuery(cqId);
}
async function wizardStage(chat, chatId, messageId, stage, cqId) {
  chat.session.draft.stage = stage;
  await setChat(chatId, chat);
  if (stage === "models") await TG.editMessageText(chatId, messageId, "🚗 <b>Models</b> — tap to toggle, then Next.", modelsKeyboard(chat.models, chat.session.draft.models));
  if (stage === "age") await TG.editMessageText(chatId, messageId, "📋 <b>COE status</b>", ageKeyboard());
  return TG.answerCallbackQuery(cqId);
}
async function wizardAge(chat, chatId, messageId, age, cqId) {
  chat.session.draft.age = age === "both" ? ["parf", "renewed"] : [age];
  chat.session.step = "hunt_numbers";
  await setChat(chatId, chat);
  await TG.editMessageText(chatId, messageId, "📋 COE status set: <b>" + (age === "both" ? "Both" : age) + "</b>");
  await TG.sendMessage(chatId,
`💰 Send your numbers as <b>six values in order</b>, space-separated, use <code>-</code> to skip any:
<code>price dep coeMin coeMax km owners</code>

Example: <code>60000 15000 1.5 4 100000 2</code> (max $60k, max $15k/yr dep, 1.5–4 yrs COE left, max 100,000km, max 2 owners)

Send just <code>-</code> for all six to search with no numeric filters at all.`);
  return TG.answerCallbackQuery(cqId);
}

async function handleHuntNumbers(chatId, chat, text) {
  const tokens = text.trim().split(/\s+/);
  const [price, dep, coeMin, coeMax, km, owners] = tokens.map(t => (t === "-" ? null : num(t)));
  const filters = { sort: "DEP_ASC", price, dep, coeMin, coeMax, km, owners };
  const d = chat.session.draft;
  const calib = normalizeCalib(chat.calib);
  const modelNames = d.models.map(i => chat.models[i]).filter(Boolean);
  const typeNames = d.types.map(i => calib.veh[i] && calib.veh[i].name).filter(Boolean);
  const queue = buildQueue(calib, filters, d.age, modelNames, typeNames);

  chat.session = { step: null, draft: null, lastHunt: { models: modelNames, types: typeNames, age: d.age, filters } };
  await setChat(chatId, chat);

  if (!queue.length) return TG.sendMessage(chatId, "No searches to build — something went wrong, try /hunt again.");
  const rows = queue.slice(0, 20).map(q => [TG.urlBtn("🔗 " + (q.label.length > 40 ? q.label.slice(0, 39) + "…" : q.label), q.url)]);
  rows.push([TG.btn("💾 Save as hunt", "savehunt")]);
  const extra = queue.length > 20 ? `\n\n(showing first 20 of ${queue.length} — narrow your models/types for fewer)` : "";
  const unverified = !calib.verifiedBase ? "\n\n⚠️ Not calibrated yet — these links use best-effort guesses. Run /calibrate to make sure they actually work." : "";
  return TG.sendMessage(chatId, `✅ <b>${queue.length} search${queue.length === 1 ? "" : "es"} ready.</b> Tap to open.${extra}${unverified}`, TG.kb(rows));
}

async function promptHuntName(chat, chatId, cqId) {
  if (!chat.session || !chat.session.lastHunt) return TG.answerCallbackQuery(cqId, "That hunt has expired — run /hunt again.");
  chat.session.step = "hunt_save_name";
  await setChat(chatId, chat);
  await TG.sendMessage(chatId, "What should this hunt be called? (e.g. \"Family SUV under 60k\")");
  return TG.answerCallbackQuery(cqId);
}
async function handleHuntSaveName(chatId, chat, text) {
  const h = chat.session.lastHunt;
  chat.hunts.unshift({ id: Date.now(), name: text.trim(), tag: "", ...h, created: new Date().toISOString() });
  chat.session = null;
  await setChat(chatId, chat);
  return TG.sendMessage(chatId, `Saved as “${esc(text.trim())}”. Run it anytime with /hunts.`);
}

async function cmdHunts(chatId) {
  const chat = await getChat(chatId);
  if (!chat.hunts.length) return TG.sendMessage(chatId, "No saved hunts yet — build one with /hunt, then tap “Save as hunt”.");
  const rows = chat.hunts.map((h, i) => [TG.btn(`▶ ${h.name}`, `hr:${i}`), TG.btn("🗑", `hd:${i}`)]);
  return TG.sendMessage(chatId, `<b>Saved hunts</b> (${chat.hunts.length})`, TG.kb(rows));
}
async function runSavedHunt(chat, chatId, i, cqId) {
  const h = chat.hunts[i];
  if (!h) return TG.answerCallbackQuery(cqId, "Not found — it may have been deleted.");
  const calib = normalizeCalib(chat.calib);
  const queue = buildQueue(calib, h.filters, h.age, h.models, h.types);
  const rows = queue.slice(0, 20).map(q => [TG.urlBtn("🔗 " + (q.label.length > 40 ? q.label.slice(0, 39) + "…" : q.label), q.url)]);
  await TG.sendMessage(chatId, `🎯 <b>${esc(h.name)}</b> — ${queue.length} search${queue.length === 1 ? "" : "es"}`, TG.kb(rows));
  return TG.answerCallbackQuery(cqId, "Links sent");
}
async function deleteSavedHunt(chat, chatId, messageId, i, cqId) {
  const h = chat.hunts[i];
  if (!h) return TG.answerCallbackQuery(cqId, "Already gone.");
  chat.hunts.splice(i, 1);
  await setChat(chatId, chat);
  const rows = chat.hunts.map((hh, ii) => [TG.btn(`▶ ${hh.name}`, `hr:${ii}`), TG.btn("🗑", `hd:${ii}`)]);
  await TG.editMessageText(chatId, messageId, chat.hunts.length ? `<b>Saved hunts</b> (${chat.hunts.length})` : "No saved hunts left.", TG.kb(rows));
  return TG.answerCallbackQuery(cqId, `Deleted “${h.name}”`);
}

/* ============================== /calibrate ============================== */

async function cmdCalibrate(chatId) {
  const chat = await getChat(chatId);
  const calib = normalizeCalib(chat.calib);
  const rows = CAL_FIELDS.map(([key, label], i) => [TG.btn((calib.verified[key] ? "✓ " : "◻ ") + label, `cal:f:${i}`)]);
  calib.veh.forEach((v, i) => rows.push([TG.btn((v.verified ? "✓ " : "◻ ") + "Vehicle type: " + v.name, `cal:v:${i}`)]));
  return TG.sendMessage(chatId,
`🛠 <b>Calibrate SGCarmart</b>
Pick what to calibrate. Then, on sgcarmart.com (from Singapore), set <b>exactly one</b> filter and copy the URL — you'll be asked for the plain URL (no filters) and the one-filter URL.`, TG.kb(rows));
}
async function pickCalibField(chat, chatId, kind, cqId) {
  chat.session = { step: "calib_base", draft: { kind } };
  await setChat(chatId, chat);
  const reuse = chat.lastBaseUrl ? `\n\nOr reply <code>same</code> to reuse: ${esc(chat.lastBaseUrl)}` : "";
  await TG.sendMessage(chatId, `Send the <b>plain</b> sgcarmart.com URL — no filters set at all.${reuse}`);
  return TG.answerCallbackQuery(cqId);
}
const CALIB_NEEDS_VALUE = ["price", "dep", "km"];

async function handleCalibBase(chatId, chat, text) {
  const url = (text.trim().toLowerCase() === "same" && chat.lastBaseUrl) ? chat.lastBaseUrl : text.trim();
  if (!/^https:\/\/([a-z0-9-]+\.)*sgcarmart\.com\//i.test(url)) return TG.sendMessage(chatId, "That doesn't look like an sgcarmart.com URL — try again, or /reset to cancel.");
  chat.session.draft.baseUrl = url;
  chat.lastBaseUrl = url;
  chat.session.step = "calib_filled";
  await setChat(chatId, chat);
  return TG.sendMessage(chatId, "Now set ONLY that one filter on sgcarmart.com and send the resulting URL.");
}
async function handleCalibFilled(chatId, chat, text) {
  const url = text.trim();
  if (!/^https:\/\/([a-z0-9-]+\.)*sgcarmart\.com\//i.test(url)) return TG.sendMessage(chatId, "That doesn't look like an sgcarmart.com URL — try again, or /reset to cancel.");
  chat.session.draft.filledUrl = url;
  if (CALIB_NEEDS_VALUE.includes(chat.session.draft.kind)) {
    chat.session.step = "calib_rawvalue";
    await setChat(chatId, chat);
    return TG.sendMessage(chatId, "What value did you actually type into that filter on the site? (e.g. 50000)");
  }
  return finishCalib(chatId, chat, null);
}
async function handleCalibRawValue(chatId, chat, text) {
  return finishCalib(chatId, chat, text.trim());
}
async function finishCalib(chatId, chat, rawValue) {
  const { kind, baseUrl, filledUrl } = chat.session.draft;
  const { calib, result } = calibApply(chat.calib, kind, rawValue, baseUrl, filledUrl);
  chat.calib = calib;
  chat.session = null;
  await setChat(chatId, chat);
  if (!result.ok) return TG.sendMessage(chatId, `❌ ${esc(result.err)}\n\nTry /calibrate again.`);
  return TG.sendMessage(chatId, `✅ Calibrated: <code>${esc(result.param)}=${esc(result.value)}</code>. Future hunts use this.`);
}

/* ============================== /add + /shortlist ============================== */

async function cmdAdd(chatId) {
  const chat = await getChat(chatId);
  chat.session = { step: "add_blob" };
  await setChat(chatId, chat);
  return TG.sendMessage(chatId, "Paste the listing's details block (price, depreciation, reg date, mileage, ARF, owners, road tax) — copy it straight off the SGCarmart ad.");
}
async function handleAddBlob(chatId, chat, text) {
  const { fields, got } = parseListing(text);
  chat.session = null;
  if (!got.length) { await setChat(chatId, chat); return TG.sendMessage(chatId, "Couldn't find any figures in that — try pasting the full details block, or /add to try again."); }

  const or0 = v => (v == null ? null : v);
  const car = {
    id: Date.now(), name: fields.name || "Unnamed car", url: fields.url || "",
    price: or0(fields.price), dep: or0(fields.dep), reg: fields.reg || null,
    km: or0(fields.km), arf: or0(fields.arf), coe: or0(fields.coe),
    owners: or0(fields.owners), tax: or0(fields.tax), deregListed: or0(fields.deregListed),
    coeType: fields.coeType || "original", notes: "", added: new Date().toISOString(),
  };
  chat.cars.unshift(car);
  await setChat(chatId, chat);

  const d = derive(car);
  const fs = flags(car, d);
  const lines = [
    `<b>${esc(car.name)}</b>`,
    car.price != null ? `Price: ${money(car.price)}` : null,
    d.effDep != null ? `True dep/yr: <b>${money(d.effDep)}</b>` : null,
    car.dep != null ? `Listed dep: ${money(car.dep)}` : null,
    d.yearsLeft != null ? `COE left: ${d.yearsLeft.toFixed(1)} yrs` : null,
    fs.length ? "\n⚠️ " + fs.map(f => f[1]).join("\n⚠️ ") : null,
  ].filter(Boolean);
  return TG.sendMessage(chatId, `Logged (read ${got.length} field${got.length === 1 ? "" : "s"}: ${esc(got.join(", "))})\n\n${lines.join("\n")}`);
}

async function cmdShortlist(chatId) {
  const chat = await getChat(chatId);
  if (!chat.cars.length) return TG.sendMessage(chatId, "No cars saved yet. Paste one with /add.");
  const rows = scoreAll(chat.cars).sort((a, b) => (b.score == null ? -1 : b.score) - (a.score == null ? -1 : a.score)).slice(0, 15);
  const lines = rows.map(r => {
    const d = r.d, c = r.c;
    return `<b>${esc(c.name)}</b>${r.score != null ? ` — ${r.score}pts` : ""}\n` +
      `  ${money(c.price)} · true dep ${money(d.effDep)}/yr` + (d.yearsLeft != null ? ` · ${d.yearsLeft.toFixed(1)}y COE left` : "");
  });
  return TG.sendMessage(chatId, `<b>Shortlist</b> (${chat.cars.length}, top ${rows.length} by score)\n\n${lines.join("\n\n")}`);
}

async function cmdReset(chatId) {
  const chat = await getChat(chatId);
  chat.session = null;
  await setChat(chatId, chat);
  return TG.sendMessage(chatId, "Cancelled whatever I was asking. Send /help to see what's next.");
}
function esc(s) { return String(s == null ? "" : s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }
