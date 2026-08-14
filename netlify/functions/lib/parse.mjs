// Ported from index.html's readBlob() — same regexes, same field labels,
// so a listing pasted into the bot extracts the same figures it would in
// the app.
import { num } from "./carmath.mjs";

function grab(t, labels, opts = {}) {
  for (const label of labels) {
    const re = new RegExp(label + "[^0-9A-Za-z$]{0,12}\\$?\\s*([0-9][0-9,\\.]*)", "i");
    const m = t.match(re);
    if (m) {
      const n = num(m[1]);
      if (n != null && (!opts.min || n >= opts.min)) return n;
    }
  }
  return null;
}
function grabDate(t, labels) {
  for (const label of labels) {
    const m = t.match(new RegExp(label + "[^0-9]{0,14}(\\d{1,2})[\\-\\/\\s]([A-Za-z]{3,9}|\\d{1,2})[\\-\\/\\s](\\d{4})", "i"));
    if (m) {
      const monthNames = ["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"];
      const mo = isNaN(+m[2]) ? monthNames.indexOf(m[2].slice(0,3).toLowerCase()) + 1 : +m[2];
      if (mo > 0) return `${m[3]}-${String(mo).padStart(2,"0")}-${String(+m[1]).padStart(2,"0")}`;
    }
  }
  return null;
}

// Returns { fields: {...parsed}, got: [...labels found] } — same shape of
// info the PWA's toast shows ("Read N fields: price, dep, ...").
export function parseListing(t) {
  const fields = {};
  const got = [];
  const set = (key, v, tag) => { if (v != null && v !== "") { fields[key] = v; got.push(tag); } };

  const urlM = t.match(/https:\/\/(?:[a-z0-9-]+\.)*sgcarmart\.com\/\S+/i);
  if (urlM) { fields.url = urlM[0].replace(/[),.]+$/, ""); got.push("listing URL"); }

  set("price", grab(t, ["price","asking"], { min: 1000 }), "price");
  set("dep", grab(t, ["depreciation","depre"], { min: 100 }), "dep");
  set("km", grab(t, ["mileage","milage","odometer"], { min: 100 }), "mileage");
  set("arf", grab(t, ["\\barf\\b"], { min: 100 }), "ARF");
  const dv = grab(t, ["dereg(?:istration)? value","paper value"], { min: 100 });
  if (dv != null) { fields.deregListed = dv; got.push("dereg value"); }
  set("owners", grab(t, ["no\\.? of owners","owners","previous owners"]), "owners");
  set("tax", grab(t, ["road tax"], { min: 50 }), "road tax");
  const reg = grabDate(t, ["reg(?:istration)? date","registered","reg date"]);
  set("reg", reg, "reg date");
  fields.coeType = (/renew|extend/i.test(t) && /coe/i.test(t)) ? "renewed10" : "original";

  if (!fields.name) {
    const lines = t.trim().split("\n").map(s => s.trim()).filter(Boolean);
    const first = lines.find(l => l.length < 60 && !/\$/.test(l) && !/^https?:\/\//i.test(l));
    if (first) fields.name = first;
  }
  return { fields, got };
}
