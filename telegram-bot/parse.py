"""Ported from car-scout's index.html readBlob() — same regexes, same field
labels, so a listing pasted into the bot extracts the same figures it
would in the app.
"""
from __future__ import annotations
import re
from carmath import num

_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def _grab(t: str, labels: list[str], min_val: float | None = None):
    for label in labels:
        m = re.search(label + r"[^0-9A-Za-z$]{0,12}\$?\s*([0-9][0-9,\.]*)", t, re.IGNORECASE)
        if m:
            n = num(m.group(1))
            if n is not None and (not min_val or n >= min_val):
                return n
    return None


def _grab_date(t: str, labels: list[str]):
    for label in labels:
        m = re.search(label + r"[^0-9]{0,14}(\d{1,2})[\-\/\s]([A-Za-z]{3,9}|\d{1,2})[\-\/\s](\d{4})", t, re.IGNORECASE)
        if m:
            day, mon, year = m.group(1), m.group(2), m.group(3)
            mo = int(mon) if mon.isdigit() else (_MONTHS.index(mon[:3].lower()) + 1 if mon[:3].lower() in _MONTHS else 0)
            if mo > 0:
                return f"{year}-{mo:02d}-{int(day):02d}"
    return None


def parse_listing(t: str) -> tuple[dict, list[str]]:
    """Returns (fields, got) — got is the list of field-labels successfully
    extracted, same as the toast the PWA shows ("Read N fields: price, dep, ...")."""
    fields: dict = {}
    got: list[str] = []

    def set_(key, v, tag):
        if v is not None and v != "":
            fields[key] = v
            got.append(tag)

    url_m = re.search(r"https://(?:[a-z0-9-]+\.)*sgcarmart\.com/\S+", t, re.IGNORECASE)
    if url_m:
        fields["url"] = re.sub(r"[),.]+$", "", url_m.group(0))
        got.append("listing URL")

    set_("price", _grab(t, ["price", "asking"], 1000), "price")
    set_("dep", _grab(t, ["depreciation", "depre"], 100), "dep")
    set_("km", _grab(t, ["mileage", "milage", "odometer"], 100), "mileage")
    set_("arf", _grab(t, [r"\barf\b"], 100), "ARF")
    dv = _grab(t, ["dereg(?:istration)? value", "paper value"], 100)
    if dv is not None:
        fields["deregListed"] = dv
        got.append("dereg value")
    set_("owners", _grab(t, [r"no\.? of owners", "owners", "previous owners"]), "owners")
    set_("tax", _grab(t, ["road tax"], 50), "road tax")
    reg = _grab_date(t, ["reg(?:istration)? date", "registered", "reg date"])
    set_("reg", reg, "reg date")
    fields["coeType"] = "renewed10" if (re.search(r"renew|extend", t, re.IGNORECASE) and re.search("coe", t, re.IGNORECASE)) else "original"

    if "name" not in fields:
        lines = [l.strip() for l in t.strip().split("\n") if l.strip()]
        first = next((l for l in lines if len(l) < 60 and "$" not in l and not re.match(r"^https?://", l, re.IGNORECASE)), None)
        if first:
            fields["name"] = first
    return fields, got
