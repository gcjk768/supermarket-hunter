"""Supermarket search via Jina Reader. Stores: FairPrice and Cold Storage (robots.txt allow their search pages; checked 2026-10-07/08).

Jina returns the page as Markdown; each product is one link line:
  FairPrice   ``[<promo> ![img](..) $price [$was] <name>](https://www.fairprice.com.sg/product/...)``
  Cold Storage ``[<N>% off $price [$was] <name>](https://coldstorage.com.sg/product/...)``
Unit price comes from the pack size in the name ("5kg", "24 x 200ml", "10s"), so a 10kg bag and a 5kg bag compare.
Rule (CLAUDE.md 2026-10-04): a 403/429 is never retried or routed around; it is logged once and the term returns empty.
"""
from __future__ import annotations

import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from .cards import EMOJI

log = logging.getLogger(__name__)
JINA = "https://r.jina.ai/"
PRICE = re.compile(r"\$\d+(?:\.\d+)?")
UNIT = re.compile(r"(?:(\d+)\s*[xX]\s*)?(\d+(?:\.\d+)?)\s*(kg|g|l|ml|s)\b", re.I)
SKIP = re.compile(r"^Ad\b|\bseeds?\b|out of stock", re.I)   # sponsored rows start with "Ad "
FP_LINE = re.compile(r"^\[(.*)\]\((https://www\.fairprice\.com\.sg/product/[^)]+)\)", re.M)
CS_LINE = re.compile(r"^\[(?:(\d+)% off )?(\$\d+(?:\.\d+)?) (?:(\$\d+(?:\.\d+)?) )?([^\]]+)\]\((https://coldstorage\.com\.sg/product/[^)]+)\)", re.M)
CS_IMG = re.compile(r"^\[!\[[^\]]*\]\((https?://[^)\s]+)\)\]\((https://coldstorage\.com\.sg/product/[^)]+)\)", re.M)   # photo line above each product
STORES = {   # name -> (search url, product-line regex); tools/ has the research for Giant, Sheng Siong, Prime, Hao Mart
    # FairPrice search is OFF: robots.txt has "Disallow: /search" (verified 2026-10-08). Category pages are allowed; see the playbook.
    "Cold Storage": ("https://coldstorage.com.sg/search?q={}", CS_LINE),
}


def fetch(url: str, line: re.Pattern, sleep=time.sleep) -> str:
    headers = {"X-Wait-For-Selector": "a[href*='/product/']"}   # without it JS-rendered pages come back empty
    if key := os.environ.get("JINA_API_KEY"):
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(JINA + url, headers=headers)
    for attempt in (1, 2):   # one retry on a plain failure (timeout, 5xx, empty render) only
        try:
            md = urllib.request.urlopen(req, timeout=90).read().decode()
            if line.search(md):
                return md
            log.warning("%s: empty render (attempt %d)", url, attempt)
        except urllib.error.HTTPError as ex:
            if ex.code in (403, 429):
                log.warning("%s: HTTP %s, blocked, not retrying", url, ex.code)
                return ""
            log.warning("%s: HTTP %s (attempt %d)", url, ex.code, attempt)
        except Exception as ex:   # noqa: BLE001
            log.warning("%s: %s (attempt %d)", url, ex, attempt)
        sleep(3)
    return ""


def per_unit(name: str, price: float) -> tuple[float | None, str]:
    """-> (price per 100g / 100ml / piece, label) or (None, '') when the name has no pack size."""
    found = list(UNIT.finditer(name))
    if not found:
        return None, ""
    # weight/volume beats a count ("Eggs 12s 660g" -> per 100g), so the same item compares across stores
    m = next((f for f in found if f[3].lower() != "s"), found[0])
    n, q, u = int(m[1] or 1), float(m[2]), m[3].lower()
    q = {"kg": q * 1000, "l": q * 1000}.get(u, q) * n
    if q <= 0:
        return None, ""
    if u == "s":
        return price / q, "each"
    return price / q * 100, "/100" + ("ml" if u in ("l", "ml") else "g")


def _row(store: str, name: str, price: float, was: float | None, promo: str, url: str, image: str = "") -> dict | None:
    name = re.sub(r"\s+", " ", name).strip()
    if SKIP.search(name):
        return None
    unit_price, unit = per_unit(name, price)
    return dict(store=store, name=name, price=price, was=was, promo=promo.strip(), url=url, unit_price=unit_price, unit=unit,
                image=image if image.startswith("https://") else "")


def parse_fairprice(md: str) -> list[dict]:
    rows = []
    for body, url in FP_LINE.findall(md):
        img = re.search(r"!\[.*?\]\((https://[^)\s]+)\)", body)
        promo, _, rest = re.sub(r"!\[.*?\]\(.*?\)", "@@", body).partition("@@")   # promo | img | prices + name
        rest = re.sub(r"\+\$\d+(?:\.\d+)?\s*deposit", "", rest)                       # BCRS bottle deposit is not price
        prices = PRICE.findall(rest)
        if not prices:
            continue
        name = re.sub(r"^(\s*\$\d+(?:\.\d+)?)+", "", rest).replace("@@", "").strip().replace("•", " | ")
        name = re.sub(r"\s*Add to cart.*$", "", name)   # promotions page appends the button text
        name = re.sub(r"\s+\d\.\d\(\d+\)?$", "", name)   # trailing "4.6(161)" rating
        r = _row("FairPrice", name, float(prices[0][1:]), float(prices[1][1:]) if len(prices) > 1 else None,
                 re.sub(r"\*", "", promo), url, img[1] if img else "")
        if r:
            rows.append(r)
    return rows


def parse_coldstorage(md: str) -> list[dict]:
    rows, imgs = [], {u: i for i, u in CS_IMG.findall(md)}
    for off, price, was, name, url in CS_LINE.findall(md):
        r = _row("Cold Storage", name, float(price[1:]), float(was[1:]) if was else None, f"{off}% off" if off else "", url,
                 imgs.get(url, ""))
        if r:
            rows.append(r)
    return rows


PARSERS = {"FairPrice": parse_fairprice, "Cold Storage": parse_coldstorage}


def parse(md: str) -> list[dict]:
    """Any store's markdown (used by tests and tools)."""
    return parse_fairprice(md) + parse_coldstorage(md)


def _mentions(name: str, item: str) -> bool:
    """Whole words, singular or plural: 'eggs' matches 'Egg' and 'Eggs', 'tomato' matches 'Tomatoes', 'rice' not 'FairPrice'."""
    stems = [w[:-1] if w.endswith("s") else w for w in item.lower().split() if len(w) > 2]
    return any(re.search(rf"\b{re.escape(w)}(?:e?s)?\b", name.lower()) for w in stems)


def relevant(name: str, q: str, known=EMOJI) -> bool:
    """Drop a product that names a DIFFERENT known item and not the one asked for: Farm Choice eggs come back for
    'chicken', garlic for 'onion'. Synonyms stay (Cai Xin for choy sum, Tau Kwa for tofu).
    ponytail: only items in `known` are caught; add a word to cards.EMOJI when a new stray shows up."""
    return _mentions(name, q) or not any(_mentions(name, k) for k in known if k != q)


def search(q: str, stores=STORES, sleep=time.sleep) -> list[dict]:
    """Products for one search term across all stores, cheapest per unit first (items without a pack size last)."""
    rows = []
    for i, (store, (url, line)) in enumerate(stores.items()):
        if i:
            sleep(2)   # be polite
        rows += [r for r in PARSERS[store](fetch(url.format(urllib.parse.quote(q)), line, sleep)) if relevant(r["name"], q)]
    rows.sort(key=lambda r: r["unit_price"] if r["unit_price"] is not None else 1e9)
    return rows
