"""Promo sources that are not per-item search pages: FairPrice /promotions (the owner's named personal-use exception, 2026-10-08),
Sheng Siong's official RSS flyer (JPG read by Claude), Giant's promotion page (campaigns), singpromos.com tag pages (allowed by robots).
Each source returns a list of dicts: {source, title, detail, url, price?, store, key}. Best effort: a failing source logs and returns []."""
from __future__ import annotations

import hashlib
import html
import logging
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from . import claude, scrape

log = logging.getLogger(__name__)
JINA = "https://r.jina.ai/"
SS_FEED = "https://corporate.shengsiong.com.sg/category/promotions/feed/"
GIANT_LINE = re.compile(r"^\[\s*(.+?) Subtitle (.+?)\]\((https://giant\.sg/[^) ]+)", re.M)
SP_POST = re.compile(r"\[([^\]]{15,160})\]\((https://singpromos\.com/[a-z0-9-]+/[^)\s]*\d{5,}/?)\)")
SP_TAGS = {"FairPrice": "ntuc-fairprice", "Sheng Siong": "sheng-siong", "Giant": "giant", "Cold Storage": "cold-storage", "Prime": "prime-supermarket"}
FLYER_SCHEMA = {"type": "object", "properties": {"valid": {"type": "string"}, "items": {"type": "array", "items": {
    "type": "object", "properties": {"name": {"type": "string"}, "price": {"type": "string"}, "offer": {"type": "string"},
                                     "staple": {"type": "string"}}, "required": ["name", "price", "offer", "staple"]}}},
                "required": ["valid", "items"]}


def _get(url: str, timeout: int = 90) -> bytes:
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "supermarket-hunter/1.0 (+personal use)"}),
                                  timeout=timeout).read()


def _key(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


# ---- FairPrice /promotions: real product rows, parsed like a search page ----
def fairprice(staples: list[str]) -> list[dict]:
    md = scrape.fetch("https://www.fairprice.com.sg/promotions", scrape.FP_LINE)
    rows = scrape.parse_fairprice(md)
    out = []
    for r in rows:
        if r["promo"] or r["was"]:
            out.append(dict(source="FairPrice promotions", store="FairPrice", title=r["name"], url=r["url"], row=r,
                            detail=" · ".join(x for x in (r["promo"], f"was ${r['was']:.2f}" if r["was"] else "") if x),
                            staple=_match(r["name"], staples), key=_key("fp", r["url"], r["promo"], r["price"])))
    log.info("FairPrice promotions: %d promo rows", len(out))
    return out


def _match(name: str, staples: list[str]) -> str:
    """Whole words only: 'rice' must not match 'FairPrice' or 'prices'."""
    n = name.lower()
    for s in staples:
        words = [s.lower()] + [w for w in s.lower().split() if len(w) > 3]
        if any(re.search(rf"\b{re.escape(w)}s?\b", n) for w in words):
            return s
    return ""


# ---- Sheng Siong: official RSS -> flyer JPG -> Claude reads it ----
def shengsiong_flyers(days: int = 21, now: datetime | None = None) -> list[dict]:
    """Recent promo posts with a flyer image: [{title, published, image, link}]."""
    raw = _get(SS_FEED, 30)
    if b"incapsula" in raw[:4000].lower() or not raw.lstrip().startswith(b"<?xml"):
        # the feed answers the NAS with an anti-bot page (seen 2026-10-09): blocked, not worked around
        from . import vault
        vault.log_event("⛔", "Sheng Siong flyer feed blocked", "anti-bot page instead of RSS, skipped", "Sheng Siong")
        return []
    root = ET.fromstring(raw)
    out, cutoff = [], (now or datetime.now(timezone.utc)) - timedelta(days=days)
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        pub = it.findtext("pubDate") or ""
        try:
            when = parsedate_to_datetime(pub)
        except (TypeError, ValueError):
            continue
        if when < cutoff or not re.search(r"promotion|special|deal|offer", title, re.I):
            continue
        body = "".join(e.text or "" for e in it.iter() if e.tag.endswith("encoded")) or (it.findtext("description") or "")
        img = re.search(r"https://[^\s\"']+\.(?:jpe?g|png)", html.unescape(body))
        if img:
            out.append(dict(title=title, published=when.date().isoformat(), image=img.group(0), link=(it.findtext("link") or "").strip()))
    return out


def read_flyer(store: str, f: dict, staples: list[str], data_dir: Path) -> list[dict]:
    """One flyer picture {title, image, link} -> items, read by Claude (the image is downloaded once and kept)."""
    folder = data_dir / "flyers" / _key(store, f["image"])
    folder.mkdir(parents=True, exist_ok=True)
    img = folder / "flyer.jpg"
    if not img.exists():
        img.write_bytes(_get(f["image"]))
    prompt = (f"Read flyer.jpg in this folder (a {store} supermarket promotion flyer: '{f['title']}'). "
                  f"List every item on it that matches one of these staples: {', '.join(staples)}. "
              "For each give the product name as printed, the promo price as printed (e.g. '$2.50' or '2 for $5'), the offer wording, "
              "and which staple it matches. Also add up to 5 standout deals that are not staples, with staple set to ''. "
              "Set valid to the offer period printed on the flyer. Do not invent items you cannot read.")
    try:
        res = claude.ask(prompt, FLYER_SCHEMA, folder, allowed="Read", max_turns=4)
    except claude.ClaudeFailure as ex:
        log.warning("%s flyer %s: %s", store, f["title"], ex)
        return []
    return [dict(source=f"{store} flyer · {f['title'][:40]}", store=store, title=it["name"],
                 detail=" · ".join(x for x in (it.get("price"), it.get("offer"), f"till {res.get('valid')}" if res.get("valid") else "") if x),
                 url=f["link"], staple=it.get("staple", ""), key=_key(store, f["image"], it["name"], it.get("price")))
            for it in res.get("items", [])]


def shengsiong(staples: list[str], data_dir: Path) -> list[dict]:
    if not claude.available():
        log.warning("Sheng Siong flyer skipped: no CLAUDE_CODE_OAUTH_TOKEN")
        return []
    out = [it for f in shengsiong_flyers()[:2] for it in read_flyer("Sheng Siong", f, staples, data_dir)]   # monthly + short special
    log.info("Sheng Siong flyers: %d items", len(out))
    return out


# ---- Prime: no online shop; its weekly flyer is a picture on the Advertised Offers page ----
PRIME_OFFERS = "https://www.primesupermarket.com/advertised-offers/"
PRIME_FLYER = re.compile(r"https://www\.primesupermarket\.com/wp-content/uploads/20\d\d/\d\d/(\d{8})_ST_[^\s\"')]+?\.jpe?g")


def prime(staples: list[str], data_dir: Path) -> list[dict]:
    """The English (ST) edition of the current flyer, full size (the page also links resized copies "-307x1024")."""
    if not claude.available():
        log.warning("Prime flyer skipped: no CLAUDE_CODE_OAUTH_TOKEN")
        return []
    md = _get(PRIME_OFFERS).decode("utf-8", "replace")   # plain HTML holds the image addresses (robots.txt allows it)
    found = {re.sub(r"-\d+x\d+(?=\.jpe?g$)", "", m.group(0)): m.group(1) for m in PRIME_FLYER.finditer(md)}
    if not found:
        log.warning("Prime flyer: no flyer image on the offers page")
        return []
    url, day = max(found.items(), key=lambda kv: kv[1])   # the newest date in the file name
    f = dict(title=f"weekly offers from {day[6:]}/{day[4:6]}/{day[:4]}", image=url, link=PRIME_OFFERS)
    out = read_flyer("Prime", f, staples, data_dir)
    log.info("Prime flyer: %d items", len(out))
    return out


# ---- Giant: campaign page (bank/card discounts, brand bundles; no shelf prices) ----
def giant(staples: list[str]) -> list[dict]:
    md = scrape.fetch("https://giant.sg/promotion-page", re.compile(r"giant\.sg/"))
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md)   # images first: they break the title match
    out = []
    for desc, title, url in GIANT_LINE.findall(md):
        out.append(dict(source="Giant promotions", store="Giant", title=title.strip(), detail=re.sub(r"\*", "", desc).strip()[:120],
                        url=url, staple=_match(title + " " + desc, staples), key=_key("giant", url, title)))
    log.info("Giant campaigns: %d", len(out))
    return out


# ---- singpromos.com: one tag page per supermarket, post titles say the deal and the dates ----
def singpromos(staples: list[str], sleep=time.sleep) -> list[dict]:
    out, seen = [], set()
    for store, tag in SP_TAGS.items():
        try:
            md = _get(f"{JINA}https://singpromos.com/tag/{tag}/").decode()
        except Exception as ex:   # noqa: BLE001
            log.warning("singpromos %s: %s", tag, ex)
            continue
        for title, url in SP_POST.findall(md):
            if url in seen or store.split()[0].lower() not in title.lower() or title.lstrip().upper().startswith("(EXPIRED"):
                continue
            seen.add(url)
            out.append(dict(source="singpromos.com", store=store, title=html.unescape(title).strip(), detail="", url=url,
                            staple=_match(title, staples), key=_key("sp", url)))
        sleep(2)
    log.info("singpromos: %d posts", len(out))
    return out


def collect(staples: list[str], data_dir: Path, sleep=time.sleep) -> dict[str, list[dict]]:
    """source name -> items. Every source is best effort."""
    sections = {}
    for name, fn in (("FairPrice", lambda: fairprice(staples)), ("Sheng Siong", lambda: shengsiong(staples, data_dir)),
                     ("Prime", lambda: prime(staples, data_dir)),
                     ("Giant", lambda: giant(staples)), ("singpromos", lambda: singpromos(staples, sleep))):
        try:
            sections[name] = fn()
        except Exception as ex:   # noqa: BLE001
            log.warning("flyer source %s failed: %s", name, ex)
            sections[name] = []
        sleep(2)
    return sections
