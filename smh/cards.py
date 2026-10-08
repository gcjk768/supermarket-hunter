"""Shared item helpers: money and unit-price text, trusted brands, best value, the supermarket list."""
from __future__ import annotations

from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Singapore")
# every Singapore supermarket the page lists, in sidebar order; only some have prices we may read (see scrape.STORES)
STORE_ORDER = ["FairPrice", "Cold Storage", "Sheng Siong", "Giant", "Prime", "Hao Mart", "RedMart", "Amazon Fresh"]
STORE_EMOJI = {"FairPrice": "🟦", "Cold Storage": "🟥", "Sheng Siong": "🟧", "Giant": "🟩", "Prime": "🟪", "Hao Mart": "🟫",
               "RedMart": "🟥", "Amazon Fresh": "🟨"}
STORE_NOTE = {}   # store -> what its rows cover, when it is not the whole shelf
EMOJI = {"rice": "🍚", "cooking oil": "🫒", "eggs": "🥚", "chicken": "🍗", "pork": "🥩", "fish fillet": "🐟", "prawns": "🦐",
         "choy sum": "🥬", "tofu": "🧈", "soy sauce": "🫙", "noodles": "🍜", "onion": "🧅", "garlic": "🧄", "tomato": "🍅",
         "milk": "🥛", "bread": "🍞", "potato": "🥔", "cabbage": "🥬", "carrot": "🥕", "beef": "🥩", "salmon": "🐟"}


def money(x: float) -> str:
    return f"${x:,.2f}"


def unit(r: dict) -> str:
    return f"{money(r['unit_price'])}{r['unit']}" if r.get("unit_price") is not None else "no pack size"


def near(r: dict, best: dict) -> bool:
    """Same ballpark as the best value: same unit and at most 2x its unit price (ponytail: crude relevance filter)."""
    return (r.get("unit_price") is not None and best.get("unit_price") is not None
            and r["unit"] == best["unit"] and r["unit_price"] <= 2 * best["unit_price"])


def is_brand(r: dict, brands: list[str]) -> bool:
    return any(b.lower() in r["name"].lower() for b in brands)


def best_of(rows: list[dict], brands: list[str]) -> dict:
    """Cheapest per unit from a trusted brand, else the cheapest (rows are already sorted by unit price)."""
    return next((r for r in rows if is_brand(r, brands)), rows[0]) if brands else rows[0]
