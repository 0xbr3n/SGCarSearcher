// Per-chat state, one JSON blob per Telegram chat.id, using Netlify's
// first-party Blobs store — no external database or signup needed, and no
// npm install step required: Netlify's function bundler resolves
// @netlify/blobs automatically. See BOT_SETUP.md if this ever fails to
// bundle on your deploy path (fix: connect the repo via GitHub instead of
// drag-and-drop, which guarantees a normal npm install).
import { getStore } from "@netlify/blobs";

function store() {
  return getStore({ name: "carscout", consistency: "strong" });
}

export function emptyChat() {
  return {
    models: ["Honda Civic","Honda City","Kia Cerato","Kia Stonic","Mazda 3","Toyota Altis"],
    calib: null,          // filled from calib.normalizeCalib() on first use
    hunts: [],             // [{id,name,tag,models,types,age,filters,created}]
    cars: [],               // shortlist, same shape as the PWA's cs3_cars
    session: null,          // ephemeral wizard state: {step, draft}
  };
}

export async function getChat(chatId) {
  const raw = await store().get(String(chatId), { type: "json" });
  if (!raw) return emptyChat();
  const d = emptyChat();
  return Object.assign(d, raw);
}

export async function setChat(chatId, data) {
  await store().setJSON(String(chatId), data);
}
