// Ported from index.html — same calibration engine, same param guesses,
// same "diff two real URLs" trick. Kept in sync with the PWA on purpose:
// a hunt built in the bot and one built in the app should produce the same
// link. State that lives in localStorage in the PWA is passed in/out of
// these functions explicitly here, since a serverless function has none.

export const TYPES_DEFAULT = [
  ["Sedan (mid-size)","12"],["Luxury sedan","11"],["Hatchback","10"],
  ["SUV","6"],["MPV","5"],["Sports","9"],["Van","7"],["Truck","8"],
  ["Hybrid","13"],["Electric","14"],
];

export const CAL_FIELDS = [
  ["model","Model search box"],["price","Max price"],["dep","Max depreciation /yr"],
  ["km","Max mileage"],["coeMin","Min COE left"],["coeMax","Max COE left"],
  ["owners","Max no. of owners"],
  ["regFrom","First registered from (min reg year) — PARF-only hunts"],
  ["regTo","First registered before (max reg year) — renewed-COE-only hunts"],
];

export function defaultCalib() {
  return {
    base: "https://www.sgcarmart.com/used_cars/listing.php", verifiedBase: false,
    p: { model:"MOD", price:"PR2", dep:"DP2", km:"MI2", coeMin:"COEL", coeMax:"COEH", owners:"OWN", regFrom:"RFR", regTo:"RTO", sort:"ORD", avl:"AVL", rpg:"RPG" },
    verified: { model:false, price:false, dep:false, km:false, coeMin:false, coeMax:false, owners:false, regFrom:false, regTo:false },
    scale: { price:1000, dep:1000, km:1 },
    veh: TYPES_DEFAULT.map(([name, value]) => ({ name, param:"VEH", value, verified:false })),
  };
}

// Fill in any fields missing from a stored (possibly older/partial) calib object.
export function normalizeCalib(c) {
  const d = defaultCalib();
  if (!c) return d;
  c.p = Object.assign(d.p, c.p);
  c.verified = Object.assign(d.verified, c.verified);
  c.scale = Object.assign(d.scale, c.scale);
  if (!c.veh || !c.veh.length) c.veh = d.veh;
  for (const dv of d.veh) if (!c.veh.some(v => v.name === dv.name)) c.veh.push(dv);
  if (c.base == null) { c.base = d.base; c.verifiedBase = false; }
  return c;
}

export function calibDiff(baseUrl, filledUrl) {
  try {
    const bu = new URL(baseUrl.trim()), fu = new URL(filledUrl.trim());
    const host = u => u.hostname.replace(/^www\./, "");
    if (!/sgcarmart\.com$/i.test(host(bu)) || !/sgcarmart\.com$/i.test(host(fu)))
      return { err: "Both URLs must be on sgcarmart.com" };
    const bk = {};
    bu.searchParams.forEach((v, k) => { bk[k] = v; });
    const diffs = [];
    fu.searchParams.forEach((v, k) => { if (bk[k] === undefined || bk[k] !== v) diffs.push([k, v]); });
    if (!diffs.length) return { err: "No difference found — did you set a filter before copying the second URL?" };
    if (diffs.length > 1) return { err: `Found ${diffs.length} changed params (${diffs.map(d=>d[0]).join(", ")}) — set ONLY that one filter, nothing else, then copy the URL` };
    return { ok: true, param: diffs[0][0], value: diffs[0][1], origin: fu.origin + fu.pathname };
  } catch (e) {
    return { err: "That doesn't look like a valid URL" };
  }
}

// kind is a CAL_FIELDS key, or "veh:<Type Name>". Mutates and returns calib.
export function calibApply(calib, kind, rawValue, baseUrl, filledUrl) {
  const d = calibDiff(baseUrl, filledUrl);
  if (!d.ok) return { calib, result: d };
  const c = normalizeCalib(calib);
  c.base = d.origin; c.verifiedBase = true;
  if (kind.startsWith("veh:")) {
    const name = kind.slice(4);
    const v = c.veh.find(x => x.name === name);
    if (v) { v.param = d.param; v.value = d.value; v.verified = true; }
  } else {
    c.p[kind] = d.param; c.verified[kind] = true;
    if ((kind === "price" || kind === "dep" || kind === "km") && rawValue) {
      const raw = num(rawValue), pv = num(d.value);
      if (raw && pv) c.scale[kind] = (raw / pv) >= 900 ? 1000 : 1;
    }
  }
  return { calib: c, result: { ok: true, param: d.param, value: d.value } };
}

function num(v) {
  const n = parseFloat(String(v).replace(/[^0-9.\-]/g, ""));
  return isNaN(n) ? null : n;
}
function scaleOut(v, div) {
  if (!div || div === 1) return String(Math.round(v));
  return String(Math.max(1, Math.ceil(v / div)));
}

// filters: {sort, price, dep, km, coeMin, coeMax, owners}
// age: array subset of ["parf","renewed"] — same PARF/renewed reg-year logic as the PWA.
export function regYearBound(age) {
  const y = new Date().getFullYear();
  if (age.length === 1 && age[0] === "parf") return { from: y - 10 };
  if (age.length === 1 && age[0] === "renewed") return { to: y - 10 };
  return {};
}

export function buildUrl(calibRaw, filters, age, model, vehName) {
  const c = normalizeCalib(calibRaw);
  const p = new URLSearchParams();
  if (model) p.set(c.p.model, model);
  p.set(c.p.avl, "2"); p.set(c.p.rpg, "40"); p.set(c.p.sort, filters.sort || "DEP_ASC");
  if (vehName) {
    const v = c.veh.find(x => x.name === vehName);
    if (v) p.set(v.param, v.value);
  }
  if (filters.price) p.set(c.p.price, scaleOut(filters.price, c.scale.price));
  if (filters.dep) p.set(c.p.dep, scaleOut(filters.dep, c.scale.dep));
  if (filters.km) p.set(c.p.km, scaleOut(filters.km, c.scale.km));
  if (filters.coeMin != null) p.set(c.p.coeMin, String(filters.coeMin));
  if (filters.coeMax != null) p.set(c.p.coeMax, String(filters.coeMax));
  if (filters.owners != null) p.set(c.p.owners, String(filters.owners));
  const rb = regYearBound(age || []);
  if (rb.from != null) p.set(c.p.regFrom, String(rb.from));
  if (rb.to != null) p.set(c.p.regTo, String(rb.to));
  return c.base + "?" + p.toString();
}

// One search per model x vehicle type — same reasoning as the PWA: SGCarmart's
// combined-vehicle-type param can't be verified from outside Singapore, so
// each type gets its own link rather than silently guessing wrong.
export function buildQueue(calib, filters, age, models, types) {
  const ms = models.length ? models : [null];
  const ts = types.length ? types : [null];
  const out = [];
  for (const m of ms) for (const t of ts) {
    out.push({ label: [m, t].filter(Boolean).join(" · ") || "All cars, your filters", url: buildUrl(calib, filters, age, m, t) });
  }
  return out;
}
