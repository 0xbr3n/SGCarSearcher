"""Per-chat state, one JSON file per Telegram chat.id, in ./data/. No
database, no external service — just a folder next to bot.py. Back it up
by copying that folder; restore the same way.
"""
from __future__ import annotations
import json
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)


def empty_chat() -> dict:
    return {
        "models": ["Honda Civic", "Honda City", "Kia Cerato", "Kia Stonic", "Mazda 3", "Toyota Altis"],
        "calib": None,       # filled via calib.normalize_calib() on first use
        "hunts": [],          # [{id,name,tag,models,types,age,filters,created}]
        "cars": [],            # shortlist, same shape as the PWA's cs3_cars
        "session": None,        # ephemeral wizard state: {step, draft, ...}
        "lastBaseUrl": None,
    }


def _path(chat_id) -> str:
    return os.path.join(DATA_DIR, f"{chat_id}.json")


def get_chat(chat_id) -> dict:
    p = _path(chat_id)
    if not os.path.exists(p):
        return empty_chat()
    try:
        with open(p, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return empty_chat()
    d = empty_chat()
    d.update(raw)
    return d


def save_chat(chat_id, data: dict) -> None:
    with open(_path(chat_id), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
