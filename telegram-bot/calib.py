"""Ported from car-scout's index.html — same calibration engine, same param
guesses, same "diff two real URLs" trick, kept in sync with the PWA on
purpose. State that lives in localStorage in the PWA is passed in/out of
these functions explicitly here, since this bot keeps its own per-chat
JSON file instead (see store.py).
"""
from __future__ import annotations
from urllib.parse import urlsplit, parse_qsl, urlencode
from datetime import date

TYPES_DEFAULT = [
    ("Sedan (mid-size)", "12"), ("Luxury sedan", "11"), ("Hatchback", "10"),
    ("SUV", "6"), ("MPV", "5"), ("Sports", "9"), ("Van", "7"), ("Truck", "8"),
    ("Hybrid", "13"), ("Electric", "14"),
]

CAL_FIELDS = [
    ("model", "Model search box"), ("price", "Max price"), ("dep", "Max depreciation /yr"),
    ("km", "Max mileage"), ("coeMin", "Min COE left"), ("coeMax", "Max COE left"),
    ("owners", "Max no. of owners"),
    ("regFrom", "First registered from (min reg year) — PARF-only hunts"),
    ("regTo", "First registered before (max reg year) — renewed-COE-only hunts"),
]


def default_calib() -> dict:
    return {
        "base": "https://www.sgcarmart.com/used_cars/listing.php", "verifiedBase": False,
        "p": {"model": "MOD", "price": "PR2", "dep": "DP2", "km": "MI2", "coeMin": "COEL", "coeMax": "COEH",
              "owners": "OWN", "regFrom": "RFR", "regTo": "RTO", "sort": "ORD", "avl": "AVL", "rpg": "RPG"},
        "verified": {"model": False, "price": False, "dep": False, "km": False, "coeMin": False,
                     "coeMax": False, "owners": False, "regFrom": False, "regTo": False},
        "scale": {"price": 1000, "dep": 1000, "km": 1},
        "veh": [{"name": name, "param": "VEH", "value": value, "verified": False} for name, value in TYPES_DEFAULT],
    }


def normalize_calib(c: dict | None) -> dict:
    d = default_calib()
    if not c:
        return d
    d["p"].update(c.get("p") or {})
    d["verified"].update(c.get("verified") or {})
    d["scale"].update(c.get("scale") or {})
    veh = c.get("veh") or []
    have = {v["name"] for v in veh}
    for dv in d["veh"]:
        if dv["name"] not in have:
            veh.append(dv)
    d["veh"] = veh
    d["base"] = c.get("base") or d["base"]
    d["verifiedBase"] = bool(c.get("verifiedBase"))
    return d


def _host(url: str) -> str:
    h = urlsplit(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def calib_diff(base_url: str, filled_url: str) -> dict:
    try:
        bu, fu = urlsplit(base_url.strip()), urlsplit(filled_url.strip())
        if not _host(base_url).endswith("sgcarmart.com") or not _host(filled_url).endswith("sgcarmart.com"):
            return {"err": "Both URLs must be on sgcarmart.com"}
        bk = dict(parse_qsl(bu.query))
        fk = parse_qsl(fu.query)
        diffs = [(k, v) for k, v in fk if bk.get(k) != v]
        if not diffs:
            return {"err": "No difference found — did you set a filter before copying the second URL?"}
        if len(diffs) > 1:
            names = ", ".join(k for k, _ in diffs)
            return {"err": f"Found {len(diffs)} changed params ({names}) — set ONLY that one filter, nothing else, then copy the URL"}
        param, value = diffs[0]
        origin = f"{fu.scheme}://{fu.netloc}{fu.path}"
        return {"ok": True, "param": param, "value": value, "origin": origin}
    except Exception:
        return {"err": "That doesn't look like a valid URL"}


def calib_apply(calib: dict | None, kind: str, raw_value, base_url: str, filled_url: str) -> tuple[dict, dict]:
    d = calib_diff(base_url, filled_url)
    if not d.get("ok"):
        return normalize_calib(calib), d
    c = normalize_calib(calib)
    c["base"] = d["origin"]
    c["verifiedBase"] = True
    if kind.startswith("veh:"):
        name = kind[4:]
        for v in c["veh"]:
            if v["name"] == name:
                v["param"], v["value"], v["verified"] = d["param"], d["value"], True
    else:
        c["p"][kind] = d["param"]
        c["verified"][kind] = True
        if kind in ("price", "dep", "km") and raw_value:
            from carmath import num
            raw, pv = num(raw_value), num(d["value"])
            if raw and pv:
                c["scale"][kind] = 1000 if (raw / pv) >= 900 else 1
    return c, {"ok": True, "param": d["param"], "value": d["value"]}


def _scale_out(v: float, div: float) -> str:
    if not div or div == 1:
        return str(round(v))
    import math
    return str(max(1, math.ceil(v / div)))


def reg_year_bound(age: list[str]) -> dict:
    y = date.today().year
    if age == ["parf"]:
        return {"from": y - 10}
    if age == ["renewed"]:
        return {"to": y - 10}
    return {}


def build_url(calib_raw: dict | None, filters: dict, age: list[str], model: str | None, veh_name: str | None) -> str:
    c = normalize_calib(calib_raw)
    p = []
    if model:
        p.append((c["p"]["model"], model))
    p.append((c["p"]["avl"], "2"))
    p.append((c["p"]["rpg"], "40"))
    p.append((c["p"]["sort"], filters.get("sort") or "DEP_ASC"))
    if veh_name:
        v = next((x for x in c["veh"] if x["name"] == veh_name), None)
        if v:
            p.append((v["param"], v["value"]))
    if filters.get("price"):
        p.append((c["p"]["price"], _scale_out(filters["price"], c["scale"]["price"])))
    if filters.get("dep"):
        p.append((c["p"]["dep"], _scale_out(filters["dep"], c["scale"]["dep"])))
    if filters.get("km"):
        p.append((c["p"]["km"], _scale_out(filters["km"], c["scale"]["km"])))
    if filters.get("coeMin") is not None:
        p.append((c["p"]["coeMin"], str(filters["coeMin"])))
    if filters.get("coeMax") is not None:
        p.append((c["p"]["coeMax"], str(filters["coeMax"])))
    if filters.get("owners") is not None:
        p.append((c["p"]["owners"], str(filters["owners"])))
    rb = reg_year_bound(age or [])
    if rb.get("from") is not None:
        p.append((c["p"]["regFrom"], str(rb["from"])))
    if rb.get("to") is not None:
        p.append((c["p"]["regTo"], str(rb["to"])))
    return c["base"] + "?" + urlencode(p)


def build_queue(calib: dict | None, filters: dict, age: list[str], models: list[str], types: list[str]) -> list[dict]:
    ms = models or [None]
    ts = types or [None]
    out = []
    for m in ms:
        for t in ts:
            label = " · ".join(x for x in (m, t) if x) or "All cars, your filters"
            out.append({"label": label, "url": build_url(calib, filters, age, m, t)})
    return out
