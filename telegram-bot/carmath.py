"""Ported from car-scout's index.html inline script — keep this numerically
identical to the PWA's PARF/COE math. If you fix a bug in one, fix it in
both, or the app and the bot will quietly disagree with each other.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta

SCHED_OLD = [(5, .75), (6, .70), (7, .65), (8, .60), (9, .55), (10, .50)]
SCHED_NEW = [(5, .30), (6, .25), (7, .20), (8, .15), (9, .10), (10, .05)]
CUT_NEW = date(2026, 2, 13)
CUT_CAP = date(2023, 2, 15)

YEAR = 365.25


def age_years(reg: date, at: date) -> float:
    return (at - reg).days / YEAR


def parf_pct(reg: date, at: date) -> float:
    a = age_years(reg, at)
    if a >= 10 or a < 0:
        return 0.0
    sched = SCHED_NEW if reg >= CUT_NEW else SCHED_OLD
    for yr, pct in sched:
        if a < yr:
            return pct
    return 0.0


def parf_cap(reg: date):
    if reg < CUT_CAP:
        return float("inf")
    if reg < CUT_NEW:
        return 60000
    return 30000


def parf_rebate(reg: date, at: date, arf):
    if not arf:
        return None
    return min(arf * parf_pct(reg, at), parf_cap(reg))


def coe_expiry(reg: date, coe_type: str) -> date:
    years = 15 if coe_type == "renewed5" else (20 if coe_type == "renewed10" else 10)
    try:
        return reg.replace(year=reg.year + years)
    except ValueError:
        # Feb 29 registration on a non-leap target year
        return reg.replace(year=reg.year + years, day=28)


def coe_rebate(reg: date, at: date, qp, coe_type: str):
    if not qp:
        return None
    if coe_type == "renewed5":
        return 0
    expiry = coe_expiry(reg, coe_type)
    months = (expiry - at).days / 30.4375
    if months <= 0:
        return 0
    return qp * min(months, 120) / 120


def derive(c: dict, now: date | None = None) -> dict:
    """Full derived picture for one saved car. c: dict with reg/price/arf/coe/
    coeType/km/owners/dep/deregListed."""
    now = now or date.today()
    o = {}
    reg = _parse_date(c.get("reg"))
    o["reg"] = reg
    if not reg:
        return o
    coe_type = c.get("coeType") or "original"
    o["age"] = age_years(reg, now)
    o["expiry"] = coe_expiry(reg, coe_type)
    o["yearsLeft"] = max(0.0, (o["expiry"] - now).days / YEAR)
    o["parfCar"] = coe_type == "original"
    o["parfNow"] = parf_rebate(reg, now, c.get("arf")) if o["parfCar"] else 0
    o["coeNow"] = coe_rebate(reg, now, c.get("coe"), coe_type)
    o["deregNow"] = (o["parfNow"] or 0) + (o["coeNow"] or 0)
    eve = o["expiry"] - timedelta(days=1)
    o["parfEnd"] = parf_rebate(reg, eve, c.get("arf")) if o["parfCar"] else 0
    o["deregEnd"] = o["parfEnd"] or 0
    price = c.get("price")
    if price and o["yearsLeft"] > 0.1:
        o["effDep"] = (price - o["deregEnd"]) / o["yearsLeft"]
        o["burn"] = price - o["deregEnd"]
    km = c.get("km")
    if km and o["age"] > 0.3:
        o["kmYr"] = km / o["age"]
    return o


def flags(c: dict, d: dict) -> list[tuple[str, str]]:
    if d.get("reg") is None:
        return []  # no registration date -> derive() bailed early -> nothing here is actually known
    f = []
    km_yr = d.get("kmYr")
    age = d.get("age") or 0
    if km_yr is not None:
        if km_yr > 25000:
            f.append(("bad", f"{round(km_yr):,} km/yr — very high, ask if it was private hire"))
        elif km_yr > 19000:
            f.append(("", f"Above-average mileage at {round(km_yr):,} km/yr"))
        elif km_yr < 6000 and age > 3:
            f.append(("", f"Unusually low at {round(km_yr):,} km/yr — check for long idle periods"))
    owners = c.get("owners")
    if owners is not None and owners >= 4:
        f.append(("bad", f"{owners} previous owners"))
    elif owners == 3:
        f.append(("", "Three previous owners"))
    years_left = d.get("yearsLeft")
    if years_left is not None and 0 < years_left < 2.5:
        f.append(("bad", f"Only {years_left:.1f} yrs of COE — scrap or pay PQP after that"))
    if not d.get("parfCar"):
        f.append(("", "Renewed COE: no ARF rebate at the end, so nothing comes back"))
    if not c.get("arf") and d.get("parfCar"):
        f.append(("", "No ARF entered — true depreciation can't be worked out. Ask for the log card"))
    dereg_listed, dereg_now = c.get("deregListed"), d.get("deregNow")
    if dereg_listed and dereg_now:
        diff = abs(dereg_listed - dereg_now) / dereg_listed
        if diff > 0.15:
            f.append(("", f"Listed dereg value {money(dereg_listed)} vs {money(dereg_now)} estimated here — check the ARF and COE figures on the log card"))
    dep, eff_dep = c.get("dep"), d.get("effDep")
    if dep and eff_dep and eff_dep > dep * 1.18:
        f.append(("bad", f"Listed dep {money(dep)} looks optimistic — works out closer to {money(eff_dep)}"))
    if 9.2 <= age < 10 and d.get("parfCar"):
        f.append(("", f"Crosses 10 yrs in {round((10 - age) * 12)} months, when the ARF rebate disappears"))
    return f


WDEF = [
    {"k": "eff", "label": "Low true depreciation", "w": 5},
    {"k": "price", "label": "Low cash outlay", "w": 3},
    {"k": "km", "label": "Low mileage", "w": 3},
    {"k": "left", "label": "Long COE remaining", "w": 4},
    {"k": "own", "label": "Few previous owners", "w": 2},
]


def score_all(cars: list[dict], weights=WDEF) -> list[dict]:
    rows = []
    for c in cars:
        d = derive(c)
        m = {
            "eff": -d["effDep"] if d.get("effDep") is not None else None,
            "price": -c["price"] if c.get("price") else None,
            "km": -d["kmYr"] if d.get("kmYr") is not None else None,
            "left": d.get("yearsLeft"),
            "own": -c["owners"] if c.get("owners") is not None else None,
        }
        rows.append({"c": c, "d": d, "m": m})
    rng = {}
    for w in weights:
        vals = [r["m"][w["k"]] for r in rows if r["m"][w["k"]] is not None]
        rng[w["k"]] = (min(vals), max(vals)) if vals else None
    for r in rows:
        tot = used = 0
        for w in weights:
            if not w["w"] or r["m"][w["k"]] is None or not rng[w["k"]]:
                continue
            lo, hi = rng[w["k"]]
            n = 1 if hi == lo else (r["m"][w["k"]] - lo) / (hi - lo)
            tot += n * w["w"]
            used += w["w"]
        r["score"] = round(tot / used * 100) if used else None
    return rows


def money(n) -> str:
    if n is None:
        return "—"
    return f"${round(n):,}"


def num(v):
    if v is None:
        return None
    import re
    s = re.sub(r"[^0-9.\-]", "", str(v))
    try:
        return float(s) if s not in ("", "-", ".") else None
    except ValueError:
        return None


def _parse_date(v):
    if not v:
        return None
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(v, "%Y-%m-%d").date()
    except ValueError:
        return None
