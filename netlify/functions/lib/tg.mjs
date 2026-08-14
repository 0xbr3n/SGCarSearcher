// Thin wrapper over the Telegram Bot API — plain fetch, no SDK, so there's
// nothing extra to bundle for this half of the bot.
const TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const API = TOKEN ? `https://api.telegram.org/bot${TOKEN}` : null;

async function call(method, payload) {
  if (!API) throw new Error("TELEGRAM_BOT_TOKEN is not set");
  const r = await fetch(`${API}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const j = await r.json().catch(() => null);
  if (!j || !j.ok) console.error(`tg ${method} failed:`, j && j.description);
  return j;
}

export function sendMessage(chatId, text, extra = {}) {
  return call("sendMessage", { chat_id: chatId, text, parse_mode: "HTML", disable_web_page_preview: true, ...extra });
}
export function editMessageText(chatId, messageId, text, extra = {}) {
  return call("editMessageText", { chat_id: chatId, message_id: messageId, text, parse_mode: "HTML", disable_web_page_preview: true, ...extra });
}
export function answerCallbackQuery(id, text) {
  return call("answerCallbackQuery", { callback_query_id: id, text, show_alert: false });
}
export function setWebhook(url, secret) {
  return call("setWebhook", { url, secret_token: secret, allowed_updates: ["message", "callback_query"] });
}
export function deleteWebhook() {
  return call("deleteWebhook", {});
}

// Telegram limits inline keyboard callback_data to 64 bytes — every button
// builder below uses short prefixes + numeric indices, never raw names.
export function kb(rows) { return { reply_markup: { inline_keyboard: rows } }; }
export function btn(text, data) { return { text, callback_data: data }; }
export function urlBtn(text, url) { return { text, url }; }
