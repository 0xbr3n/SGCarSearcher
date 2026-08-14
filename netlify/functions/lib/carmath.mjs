// Ported from index.html's inline script — keep this file numerically
// identical to the PWA's PARF/COE math. If you fix a bug in one, fix it in
// both, or the app and the bot will quietly disagree with each other.

export const SCHED_OLD = [[5,.75],[6,.70],[7,.65],[8,.60],[9,.55],[10,.50]];
export const SCHED_NEW = [[5,.30],[6,.25],[7,.20],[8,.15],[9,.10],[10,.05]];
export const CUT_NEW = new Date("2026-02-13");
export const CUT_CAP = new Date("2023-02-15");

export function ageYears(reg, at) { return (at - reg) / (365.25*24*3600*1000); }

export function parfPct(reg, at) {
  const a = ageYears(reg, at);
  if (a >= 10 || a < 0) return 0;
  const s = (reg >= CUT_NEW) ? SCHED_NEW : SCHED_OLD;
  for (const [yr, pct] of s) if (a < yr) return pct;
  return 0;
}
export function parfCap(reg) {
  if (reg < CUT_CAP) return Infinity;
  if (reg < CUT_NEW) return 60000;
  return 30000;
}
export function parfRebate(reg, at, arf) {
  if (!arf) return null;
  return Math.min(arf * parfPct(reg, at), parfCap(reg));
}
export function coeExpiry(reg, type) {
  const y = type === "renewed5" ? 15 : (type === "renewed10" ? 20 : 10);
  const d = new Date(reg.getTime());
  d.setFullYear(d.getFullYear() + y);
  return d;
}
export function coeRebate(reg, at, qp, type) {
  if (!qp) return null;
  if (type === "renewed5") return 0;
  const expiry = coeExpiry(reg, type);
  const months = (expiry - at) / (30.4375*24*3600*1000);
  if (months <= 0) return 0;
  return qp * Math.min(months, 120) / 120;
}

export function derive(c, now = new Date()) {
  const o = {};
  const reg = c.reg ? new Date(c.reg) : null;
  o.reg = reg;
  if (!reg) return o;
  o.age = ageYears(reg, now);
  o.expiry = coeExpiry(reg, c.coeType || "original");
  o.yearsLeft = Math.max(0, (o.expiry - now) / (365.25*24*3600*1000));
  o.parfCar = (c.coeType || "original") === "original";
  o.parfNow = o.parfCar ? parfRebate(reg, now, c.arf) : 0;
  o.coeNow = coeRebate(reg, now, c.coe, c.coeType || "original");
  o.deregNow = (o.parfNow || 0) + (o.coeNow || 0);
  const eve = new Date(o.expiry.getTime() - 86400000);
  o.parfEnd = o.parfCar ? parfRebate(reg, eve, c.arf) : 0;
  o.deregEnd = o.parfEnd || 0;
  if (c.price && o.yearsLeft > 0.1) {
    o.effDep = (c.price - o.deregEnd) / o.yearsLeft;
    o.burn = c.price - o.deregEnd;
  }
  if (c.km && o.age > 0.3) o.kmYr = c.km / o.age;
  return o;
}

export function flags(c, d) {
  const f = [];
  if (d.kmYr != null) {
    if (d.kmYr > 25000) f.push(["bad", Math.round(d.kmYr).toLocaleString() + " km/yr — very high, ask if it was private hire"]);
    else if (d.kmYr > 19000) f.push(["", "Above-average mileage at " + Math.round(d.kmYr).toLocaleString() + " km/yr"]);
    else if (d.kmYr < 6000 && d.age > 3) f.push(["", "Unusually low at " + Math.round(d.kmYr).toLocaleString() + " km/yr — check for long idle periods"]);
  }
  if (c.owners >= 4) f.push(["bad", c.owners + " previous owners"]);
  else if (c.owners === 3) f.push(["", "Three previous owners"]);
  if (d.yearsLeft != null && d.yearsLeft < 2.5 && d.yearsLeft > 0) f.push(["bad", "Only " + d.yearsLeft.toFixed(1) + " yrs of COE — scrap or pay PQP after that"]);
  if (!d.parfCar) f.push(["", "Renewed COE: no ARF rebate at the end, so nothing comes back"]);
  if (!c.arf && d.parfCar) f.push(["", "No ARF entered — true depreciation can't be worked out. Ask for the log card"]);
  if (c.deregListed && d.deregNow) {
    const diff = Math.abs(c.deregListed - d.deregNow) / c.deregListed;
    if (diff > 0.15) f.push(["", "Listed dereg value " + money(c.deregListed) + " vs " + money(d.deregNow) + " estimated here — check the ARF and COE figures on the log card"]);
  }
  if (c.dep && d.effDep && d.effDep > c.dep * 1.18) f.push(["bad", "Listed dep " + money(c.dep) + " looks optimistic — works out closer to " + money(d.effDep)]);
  if (d.age >= 9.2 && d.age < 10 && d.parfCar) f.push(["", "Crosses 10 yrs in " + Math.round((10 - d.age) * 12) + " months, when the ARF rebate disappears"]);
  return f;
}

export const WDEF = [
  { k: "eff",   label: "Low true depreciation", w: 5 },
  { k: "price", label: "Low cash outlay",        w: 3 },
  { k: "km",    label: "Low mileage",            w: 3 },
  { k: "left",  label: "Long COE remaining",     w: 4 },
  { k: "own",   label: "Few previous owners",    w: 2 },
];

export function scoreAll(cars, weights = WDEF) {
  const rows = cars.map(c => {
    const d = derive(c);
    return { c, d, m: {
      eff: d.effDep != null ? -d.effDep : null,
      price: c.price ? -c.price : null,
      km: d.kmYr != null ? -d.kmYr : null,
      left: d.yearsLeft != null ? d.yearsLeft : null,
      own: c.owners != null ? -c.owners : null,
    }};
  });
  const rng = {};
  for (const w of weights) {
    const v = rows.map(r => r.m[w.k]).filter(x => x != null);
    rng[w.k] = v.length ? [Math.min(...v), Math.max(...v)] : null;
  }
  for (const r of rows) {
    let tot = 0, used = 0;
    for (const w of weights) {
      if (!w.w || r.m[w.k] == null || !rng[w.k]) continue;
      const [lo, hi] = rng[w.k];
      const n = hi === lo ? 1 : (r.m[w.k] - lo) / (hi - lo);
      tot += n * w.w; used += w.w;
    }
    r.score = used ? Math.round(tot / used * 100) : null;
  }
  return rows;
}

export function money(n) {
  return n == null || isNaN(n) ? "—" : "$" + Math.round(n).toLocaleString("en-SG");
}
export function num(v) {
  if (v == null) return null;
  const n = parseFloat(String(v).replace(/[^0-9.\-]/g, ""));
  return isNaN(n) ? null : n;
}
