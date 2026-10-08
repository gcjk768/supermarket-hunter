"""Family web page: today's promotions, one category per screen (best value + 9 more), one screen per supermarket, flyers.
Rendered live from data/prices.db (+ data/flyers.json from the last flyer run) on every request. Read-only, stdlib only.
Supermarket logos are downloaded once from each store's own website into data/logos/ and served from /logo/<store>
(FairPrice's site forbids embedding its files elsewhere, and a local copy keeps working if a store moves its files).
Env: WEB_PORT (default 8000 inside the container; 0 = off)."""
from __future__ import annotations

import html
import json
import logging
import threading
import urllib.request
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import cards, festive, scrape, vault
from .store import Store

log = logging.getLogger(__name__)
ZH = {"rice": "米", "cooking oil": "食用油", "eggs": "鸡蛋", "chicken": "鸡肉", "pork": "猪肉", "fish fillet": "鱼片", "prawns": "虾",
      "choy sum": "菜心", "tofu": "豆腐", "soy sauce": "酱油", "noodles": "面", "onion": "洋葱", "tomato": "番茄", "milk": "牛奶",
      "garlic": "蒜", "bread": "面包", "potato": "马铃薯", "cabbage": "包菜", "carrot": "红萝卜", "beef": "牛肉", "salmon": "三文鱼",
      "kailan": "芥兰"}
STORE_COLOR = {"FairPrice": "#1d4fa3", "Cold Storage": "#b3261e", "Sheng Siong": "#1f4e9a", "Giant": "#5aa832", "Prime": "#0b7a3b",
               "Hao Mart": "#e06d22", "RedMart": "#e5394b", "Amazon Fresh": "#f08804"}
# store -> (online shop, official logo found on the store's own website, 2026-10-08)
STORE_INFO = {
    "FairPrice": ("https://www.fairprice.com.sg/", "https://www.fairprice.com.sg/static/icons/icon-192x192.png"),
    "Cold Storage": ("https://coldstorage.com.sg/",
                     "https://editor-upload-cdn.optimonk.com/userImages/219654/65b0c36e18b80a0024a815bf/CS-newlogo_1791364795977.png"),
    "Sheng Siong": ("https://shengsiong.com.sg/", "https://s3-ap-southeast-1.amazonaws.com/shengsiongcontent/wp-content/uploads/"
                    "2020/06/01112211/SS_logo_eng_plain-e1590981996889.jpg"),
    "Giant": ("https://giant.sg/", "https://giant.sg/media/BNVQZ9EWW73E66HQMR0N5TYF1G.png"),
    "Prime": ("https://www.primesupermarket.com/", "https://www.primesupermarket.com/wp-content/uploads/2026/08/cropped-favicon-192x192.png"),
    "Hao Mart": ("https://www.haomart.com.sg/", "https://www.haomart.com.sg/w-ebase-uploads/2021/06/hao_logo.svg"),
    "RedMart": ("https://redmart.lazada.sg/", "https://img.lazcdn.com/g/icms/images/ims-web/930f1232-e64c-47c2-88df-8e66ea34b294.png"),
    "Amazon Fresh": ("https://www.amazon.sg/fresh", "https://www.amazon.sg/favicon.ico"),
}
WORDMARK = {"Cold Storage", "Sheng Siong", "Hao Mart"}   # wide logos that already spell the name: shown alone in the list
WHY_NO_PRICES = {   # stores whose shelf prices the page cannot show, and why (see docs/vault/Scraping Playbook.md)
    "Sheng Siong": "its online shop sits behind an anti-bot check, which we never get around",
    "Giant": "its online shop moved to the foodpanda app",
    "Prime": "it has no online shop; its weekly flyer is read instead",
    "Hao Mart": "its online shop lists no products, even in a real browser",
    "Amazon Fresh": "its groceries only show to signed-in Prime members",
}
LOGO_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/svg+xml": "svg", "image/webp": "webp", "image/x-icon": "ico",
              "image/vnd.microsoft.icon": "ico"}
EXT_TYPES = {v: k for k, v in LOGO_TYPES.items()}
PER_STAPLE = 10   # products shown per staple: the best-value tag + a 3x3 grid, so one category fits one screen
HOSTS = {"fairprice.com.sg": "FairPrice", "coldstorage.com.sg": "Cold Storage", "shengsiong.com.sg": "Sheng Siong",
         "giant.sg": "Giant", "primesupermarket.com": "Prime", "haomart.com.sg": "Hao Mart", "redmart.lazada.sg": "RedMart",
         "amazon.sg": "Amazon Fresh", "lazada.sg": "RedMart"}


def esc(x) -> str:
    return html.escape(str(x), quote=True)


def safe_url(u: str) -> str:
    """Only http(s) links reach an href (rows come from scraped pages)."""
    return u if urlparse(u or "").scheme in ("http", "https") else "#"


def slug(store: str) -> str:
    return "".join(ch for ch in store.lower() if ch.isalnum())


def logo_file(data_dir: Path, store: str) -> Path | None:
    return next((data_dir / "logos" / f"{slug(store)}.{e}" for e in EXT_TYPES if (data_dir / "logos" / f"{slug(store)}.{e}").exists()), None)


def ensure_logos(data_dir: Path) -> None:
    """Download each store's logo once (best effort; a missing logo falls back to the store's colour square)."""
    (data_dir / "logos").mkdir(parents=True, exist_ok=True)
    for store, (_, url) in STORE_INFO.items():
        if logo_file(data_dir, store):
            continue
        try:
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (supermarket-hunter; family page)"}),
                                            timeout=30) as r:
                    ctype = r.headers.get_content_type()
                    body = r.read(500_001)
            except Exception as ex:   # noqa: BLE001  e.g. a server missing part of its certificate chain: let the NAS browser try
                log.info("logo for %s: %s, trying the NAS browser", store, ex)
                body, ctype = scrape.browser_file(url)
            if ctype not in LOGO_TYPES or len(body) > 500_000:
                raise ValueError(f"{ctype}, {len(body)} bytes")
            (data_dir / "logos" / f"{slug(store)}.{LOGO_TYPES[ctype]}").write_bytes(body)
            log.info("logo saved: %s", store)
            vault.log_event("🖼", "logo saved", f"{store} · from {url}", store)
        except Exception as ex:   # noqa: BLE001
            log.warning("logo for %s not saved: %s", store, ex)
            vault.log_event("⚠️", "logo not saved", f"{store} · {ex}", store)


def logo_img(data_dir: Path, store: str, cls: str) -> str:
    if logo_file(data_dir, store):
        return f'<img class="{cls}" src="/logo/{slug(store)}" alt="{esc(store)} logo">'
    return f'<span class="{cls} logo-none" style="--store:{STORE_COLOR.get(store, "#555")}" aria-hidden="true">{esc(store[:1])}</span>'


def store_of(url: str) -> str:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return next((s for h, s in HOSTS.items() if host.endswith(h)), host or "Shop")


def staple_data(st: Store, staple: str, brands: list[str]) -> dict | None:
    """Latest saved rows for one staple -> winner, best per store, one deal, the rest, price change, 8-week low."""
    day = st.db.execute("SELECT MAX(day) d FROM prices WHERE query=?", (staple,)).fetchone()["d"]
    if not day:
        return None
    # each store's own latest run (today or yesterday): the 3-hourly refresh reads Cold Storage only, so after midnight
    # FairPrice's rows are still yesterday's until the 08:00 run, and must not vanish from the page
    recent = [dict(r, store=store_of(r["url"])) for r in st.db.execute(
        "SELECT * FROM prices WHERE query=? AND day>=date(?, '-1 day') ORDER BY unit_price IS NULL, unit_price", (staple, day))]
    latest = {}
    for r in recent:
        latest[r["store"]] = max(latest.get(r["store"], ""), r["day"])
    rows = [r for r in recent if r["day"] == latest[r["store"]]]
    rows = [r for r in rows if scrape.relevant(r["name"], staple)]   # rows saved before the relevance filter existed
    if not rows:
        return None
    stores = sorted({r["store"] for r in rows}, key=lambda s: cards.STORE_ORDER.index(s) if s in cards.STORE_ORDER else 99)
    bests = [cards.best_of([r for r in rows if r["store"] == s], brands) for s in stores]
    winner = min(bests, key=lambda r: r["unit_price"] if r["unit_price"] is not None else 1e9)
    deal = next((r for r in rows if (r["promo"] or r["was"]) and r not in bests and cards.near(r, winner)), None)
    shown = bests + ([deal] if deal else [])
    # the rest, cheapest per unit first; same unit and at most 2x the best (cards.near) so egg white never lists under eggs
    more = [r for r in rows if r not in shown and cards.near(r, winner)][:max(0, PER_STAPLE - len(shown))]
    d = date.fromisoformat(day)
    low = st.low(winner["url"], d)
    return dict(day=day, winner=winner, others=[b for b in bests if b is not winner], deal=deal, more=more, count=len(rows), rows=rows,
                prev=st.previous(staple, d).get(winner["url"]),
                stock=low is not None and winner["unit_price"] is not None and winner["unit_price"] <= low - 0.005, low=low)


def change(now: float | None, prev: float | None) -> str:
    if now is None or prev is None:
        return '<span class="chg new">NEW</span>' if now is not None else ""
    d = now - prev
    if abs(d) < 0.005:
        return '<span class="chg same">same</span>'
    return f'<span class="chg {"down" if d < 0 else "up"}">{"▼" if d < 0 else "▲"} {cards.money(abs(d))}</span>'


def deal_text(r: dict) -> str:
    bits = ([r["promo"]] if r.get("promo") else []) + ([f"was {cards.money(r['was'])}"] if r.get("was") else [])
    return " · ".join(bits)


def photo(r: dict, cls: str, em: str) -> str:
    """Product photo from the shop's own image server (no referrer: the page lives on the LAN); the staple emoji if none."""
    if (r.get("image") or "").startswith("https://"):
        return (f'<img class="{cls}" src="{esc(r["image"])}" alt="" loading="lazy" referrerpolicy="no-referrer" '
                f'onerror="this.replaceWith(Object.assign(document.createElement(\'span\'),{{className:\'{cls} emoji\',textContent:\'{em}\'}}))">')
    return f'<span class="{cls} emoji" aria-hidden="true">{em}</span>'


def cell(r: dict, label: str, brands: list[str], em: str) -> str:
    """One product card. Row 1: photo, store, price. Row 2: name. Row 3: per-100g + deal, Open ›."""
    dl = deal_text(r)
    url = esc(safe_url(r["url"]))
    return (f'<div class="cell" style="--store:{STORE_COLOR.get(r["store"], "#555")}">'
            f'<a class="cell-link" href="{url}" target="_blank" rel="noopener"><span class="cell-head">{photo(r, "cell-img", em)}'
            f'<span class="cell-hp"><span class="cell-top"><span class="pill">{esc(r["store"])}</span><span class="cell-label">{esc(label)}</span></span>'
            f'<span class="cell-price">{cards.money(r["price"])}</span></span></span>'
            f'<span class="cell-name">{"★ " if cards.is_brand(r, brands) else ""}{esc(r["name"])}</span></a>'
            f'<span class="cell-actions"><span class="cell-meta">{esc(cards.unit(r))}' + (f' · <b>{esc(dl)}</b>' if dl else "") + '</span>'
            + f'<a class="open" href="{url}" target="_blank" rel="noopener">Open ›</a></span></div>')


def tag_html(w: dict, d: dict, brands: list[str], em: str, kicker: str = "Best value") -> str:
    dl = deal_text(w)
    url = esc(safe_url(w["url"]))
    return (f'<div class="tag" style="--store:{STORE_COLOR.get(w["store"], "#555")}">'
            f'<a class="tag-link" href="{url}" target="_blank" rel="noopener"><span class="hole" aria-hidden="true"></span>'
            f'{photo(w, "tag-img", em)}'
            f'<span class="tag-kicker">{esc(kicker)}{" · ★ trusted brand" if cards.is_brand(w, brands) else ""}</span>'
            f'<span class="tag-name">{esc(w["name"])}</span>'
            f'<span class="tag-price">{cards.money(w["price"])}</span>'
            f'<span class="tag-unit">{esc(cards.unit(w))} {change(w.get("unit_price"), d.get("prev"))}</span>'
            + (f'<span class="tag-deal">🏷 {esc(dl)}</span>' if dl else "")
            + f'<span class="btn">Open at {esc(w["store"])} <span aria-hidden="true">→</span></span></a></div>')


def nav_buttons(prev: tuple[str, str] | None, nxt: tuple[str, str] | None) -> str:
    """Big ‹ previous / next › category buttons: (panel id, label)."""
    out = f'<a class="step" href="#{prev[0]}">‹ {esc(prev[1])}</a>' if prev else '<span></span>'
    return out + (f'<a class="step" href="#{nxt[0]}">{esc(nxt[1])} ›</a>' if nxt else '<span></span>')


def panel(pid: str, em: str, title: str, zh: str, sub: str, body: str, steps: str, extra_cls: str = "", logo: str = "") -> str:
    return (f'<section class="panel {extra_cls}" id="{pid}" aria-label="{esc(title)}"><header class="ph">'
            + (logo or f'<span class="stamp" aria-hidden="true">{em}</span>') + '<div class="ph-t">'
            f'<h2>{esc(title)}<span class="zh">{esc(zh)}</span></h2>'
            f'<div class="ph-sub">{sub}</div></div><nav class="steps">{steps}</nav></header>{body}</section>')


def staple_panel(pid: str, staple: str, d: dict | None, brands: list[str], steps: str, em: str = "", festive: str = "") -> str:
    """One category screen; `festive` (e.g. "🪔 For Deepavali, Sun 08 Nov") heads the subtitle of a seasonal item."""
    em, zh = em or cards.EMOJI.get(staple, "🛒"), ZH.get(staple, "")
    head = f'<b>{esc(festive)}</b> · ' if festive else ""
    if not d:
        return panel(pid, em, staple.title(), zh, head + "No prices yet. Seasonal items are searched at the next refresh.",
                     '<p class="empty">No prices today. We\'ll try again at the next refresh.</p>', steps)
    w = d["winner"]
    items = [(o, "best here") for o in d["others"]] + ([(d["deal"], "on offer")] if d["deal"] else [])
    items += [(r, f"#{n}") for n, r in enumerate(d["more"], len(items) + 2)]
    grid = "".join(cell(r, label, brands, em) for r, label in items)
    sub = head + f'Compared {d["count"]} products · cheapest per 100g / 100ml / piece first'
    if d["stock"]:
        sub += f' <span class="stock">💰 Stock up: cheapest in 8 weeks (was {cards.money(d["low"])}{esc(w["unit"])})</span>'
    return panel(pid, em, staple.title(), zh, sub, f'<div class="shelf">{tag_html(w, d, brands, em)}<div class="grid">{grid}</div></div>', steps)


def promo_panel(staples: list[str], datas: dict, cfg: dict, new_urls: set[str], steps: str) -> str:
    """Every current promotion on the staples that is in the same ballpark as the best value (no egg white under eggs),
    new ones (first seen today) first, then the biggest saving."""
    seen, items = set(), []
    for s_ in staples:
        d = datas.get(s_)
        for r in (d["rows"] if d else []):
            if (r["promo"] or r["was"]) and r["url"] not in seen and cards.near(r, d["winner"]):
                seen.add(r["url"])
                saving = (r["was"] - r["price"]) / r["was"] if r["was"] else 0
                items.append((r["url"] not in new_urls, -saving, s_, r))
    items.sort(key=lambda t: t[:2])
    cells = [cell(r, f"🆕 {s_}" if r["url"] in new_urls else s_, cfg["brands"].get(s_, []), cards.EMOJI.get(s_, "🛒"))
             for _, _, s_, r in items]
    n_new = sum(1 for t in items if not t[0])
    sub = (f"{len(items)} promotions on your staples at FairPrice and Cold Storage" + (f" · 🆕 {n_new} new today" if n_new else "")
           + " · biggest saving first · flyer deals from the other stores are on each supermarket's page")
    many = len(cells) > 15   # more than one screen: normal-height cards, the panel scrolls
    body = (f'<div class="shelf shelf-store"><div class="grid grid-store{" grid-auto" if many else ""}">{"".join(cells)}</div></div>' if cells
            else '<p class="empty">No promotions on your staples right now. New ones show here within a few hours.</p>')
    return panel("promo", "🏷", "Promotions", "促销", sub, body, steps, "panel-scroll" if many else "")


def load_flyers(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("sections", {})
    except Exception:   # noqa: BLE001  no flyer run yet, or a half-written file
        return {}


def flyer_card(it: dict, src: str = "") -> str:
    r = it.get("row") or {}
    price = f'<b>{cards.money(r["price"])}</b>' if r.get("price") is not None else ""
    return (f'<a class="flyer" href="{esc(safe_url(it.get("url", "")))}" target="_blank" rel="noopener" '
            f'style="--store:{STORE_COLOR.get(it.get("store"), "#555")}"><span class="pill">{esc(it.get("store", src))}</span>'
            f'<span class="flyer-title">{esc(it.get("title", "")[:90])}</span>'
            f'<span class="flyer-detail">{price} {esc(it.get("detail") or "")}</span></a>')


def store_panel(pid: str, store: str, staples: list[str], datas: dict, cfg: dict, flyer_items: list[dict],
                steps: str, data_dir: Path) -> str:
    """One supermarket's top items: its best-value pack for every staple (🏆 = cheapest of all the stores),
    or, for stores whose site we cannot read prices from, the items in its flyer."""
    cells = []
    for s in staples:
        d = datas.get(s)
        mine = [r for r in (d["rows"] if d else []) if r["store"] == store]
        if mine:
            b = cards.best_of(mine, cfg["brands"].get(s, []))
            label = f"🏆 {s}" if b is d["winner"] else s   # the subtitle explains 🏆
            cells.append(cell(b, label, cfg["brands"].get(s, []), cards.EMOJI.get(s, "🛒")))
    note = f" ({cards.STORE_NOTE[store]})" if store in cards.STORE_NOTE else ""
    if cells:
        sub = f"Best value at {esc(store)}{esc(note)} for each staple · 🏆 = cheapest of all the stores"
        body = f'<div class="shelf shelf-store"><div class="grid grid-store">{"".join(cells)}</div></div>'
        extra = ""
    else:
        why = WHY_NO_PRICES.get(store, "no prices were read today")
        shop = STORE_INFO.get(store, ("#", ""))[0]
        sub = f"No shelf prices here: {esc(why)}." + (" Below: deals from its flyer and promotion pages." if flyer_items else "")
        body = (f'<div class="flyer-grid">{"".join(flyer_card(it) for it in flyer_items[:24])}</div>' if flyer_items
                else '<p class="empty">No flyer deals read for this store this week.</p>')
        body += f'<p><a class="shop" href="{esc(safe_url(shop))}" target="_blank" rel="noopener">Visit {esc(store)} online →</a></p>'
        extra = "panel-scroll"
    return panel(pid, "", store, "", sub, body, steps, extra, logo=logo_img(data_dir, store, "ph-logo"))


def flyers_panel(sections: dict, steps: str) -> str:
    out = [flyer_card(it, src) for src, items in sections.items() for it in ([i for i in items if i.get("staple")][:8] or items[:4])]
    body = f'<div class="flyer-grid">{"".join(out)}</div>' if out else '<p class="empty">No flyer promos read yet today.</p>'
    return panel("flyers", "📰", "This week's flyers", "本周传单", "Promotions from FairPrice, Sheng Siong, Giant and singpromos",
                 body, steps, "panel-scroll")


def page(data_dir: Path) -> str:
    st = Store(data_dir)
    try:
        cfg = st.config()
        today = datetime.now(cards.TZ).date()
        new_urls = st.new_promo_urls(today)
        last = st.db.execute("SELECT MAX(day) d FROM prices").fetchone()["d"]
        staples = cfg["staples"]
        fests = festive.active(today)   # seasonal categories, on about six weeks before each festival
        fitems = [(f, t) for f in fests for t in f["items"] if t not in staples]
        datas = {s: staple_data(st, s, cfg["brands"].get(s, [])) for s in staples + [t for _, t in fitems]}
        sections = load_flyers(data_dir / "flyers.json")
        flyer_by_store = {}
        for items in sections.values():
            for it in items:
                flyer_by_store.setdefault(it.get("store") or "", []).append(it)
        present = {r["store"] for d in datas.values() if d for r in d["rows"]} | {k for k in flyer_by_store if k}
        stores = sorted(set(cards.STORE_ORDER) | present, key=lambda x: (cards.STORE_ORDER.index(x) if x in cards.STORE_ORDER else 99, x))
        store_ids = {x: "st-" + slug(x) for x in stores}
        order = [("promo", "🏷", "Promotions")] + [(f"f{k}", f["emoji"], t.title()) for k, (f, t) in enumerate(fitems)] \
            + [(f"s{i}", cards.EMOJI.get(s, "🛒"), s.title()) for i, s in enumerate(staples)] \
            + [(store_ids[x], cards.STORE_EMOJI.get(x, "🏬"), x) for x in stores] + [("flyers", "📰", "Flyers")]

        def steps(k):
            return nav_buttons((order[k - 1][0], order[k - 1][2]) if k > 0 else None,
                               (order[k + 1][0], order[k + 1][2]) if k + 1 < len(order) else None)

        panels, side, fside = [promo_panel(staples + [t for _, t in fitems], datas, cfg, new_urls, steps(0))], [], {}
        for k, (f, t) in enumerate(fitems):
            label = f'{f["emoji"]} For {f["name"]}, {f["day"]:%a %d %b}'
            panels.append(staple_panel(f"f{k}", t, datas[t], cfg["brands"].get(t, []), steps(k + 1), em=f["emoji"], festive=label))
            fside.setdefault((f["name"], f["emoji"], f["day"]), []).append((f"f{k}", t))
        for i, s in enumerate(staples):
            d = datas[s]
            panels.append(staple_panel(f"s{i}", s, d, cfg["brands"].get(s, []), steps(len(fitems) + i + 1)))
            side.append((f"s{i}", cards.EMOJI.get(s, "🛒"), s.title(), " 💰" if d and d["stock"] else ""))
        for k, x in enumerate(stores, len(fitems) + len(staples) + 1):
            panels.append(store_panel(store_ids[x], x, staples, datas, cfg, flyer_by_store.get(x, []), steps(k), data_dir))
        panels.append(flyers_panel(sections, steps(len(order) - 1)))
    finally:
        st.db.close()
    nav = ('<a href="#promo" class="side-top">🏷 Promotions</a>'
           # one dropdown per festival (native <details>: works without script); its items stay folded away until opened
           + "".join(f'<details class="fest"><summary><span>{em} {esc(name)}<small>{day:%a %d %b} · {len(items)} items</small></span></summary>'
                     + "".join(f'<a href="#{pid}">{esc(t.title())}</a>' for pid, t in items) + '</details>'
                     for (name, em, day), items in fside.items())
           + ('<span class="side-h">Every day</span>' if fside else "")
           + "".join(f'<a href="#{pid}">{em} {esc(t)}{badge}</a>' for pid, em, t, badge in side)
           + '<span class="side-h">By supermarket</span>'
           + "".join(f'<a href="#{store_ids[x]}" class="side-store" title="{esc(x)}">'
                     + (logo_img(data_dir, x, "side-word") if x in WORDMARK and logo_file(data_dir, x)
                        else f'{logo_img(data_dir, x, "side-logo")} {esc(x)}') + '</a>' for x in stores)
           + '<span class="side-h">More</span><a href="#flyers">📰 Flyers</a>')
    now = datetime.now(cards.TZ)
    checked = date.fromisoformat(last) if last else None
    fresh = f"Prices checked {checked.strftime('%a %d %b')}" if checked else "No prices yet"
    if not checked or (now.date() - checked).days > 1:   # the 08:00 run missed a day: say so instead of looking current
        fresh = f'<span class="stale">⚠ {fresh} · may be old</span>'
    daily = _last_daily(data_dir)
    if daily and daily.date() == now.date():
        fresh = f'<span class="ok">✓ Updated today {daily:%H:%M}</span>'
    elif now.hour >= 10:   # the 08:00 update (with its retries) should be done by now
        fresh = (f'<span class="stale">⚠ Not updated today yet · last update {daily:%a %d %b %H:%M}</span>' if daily
                 else f'<span class="stale">⚠ Not updated today yet</span>')
    return TEMPLATE.format(fresh=fresh, nav=nav, panels="".join(panels), first="promo",
                           updated=esc(now.strftime("%d %b %Y, %H:%M")))


def _last_daily(data_dir: Path) -> datetime | None:
    try:
        return datetime.fromisoformat(json.loads((data_dir / "last_daily.json").read_text())["at"])
    except Exception:   # noqa: BLE001  no successful daily update yet
        return None


class Handler(BaseHTTPRequestHandler):
    data_dir = Path("data")
    seen: dict[str, float] = {}   # visitor ip -> last logged visit (vault gets one line per device per 30 min)

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):   # noqa: N802
        path = self.path.split("?")[0]
        if path.startswith("/logo/"):
            store = next((x for x in STORE_INFO if slug(x) == path[6:]), None)   # only known names: no path tricks
            f = logo_file(self.data_dir, store) if store else None
            if not f:
                self.send_error(404)
                return
            body = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", EXT_TYPES[f.suffix[1:]])
            self.send_header("Cache-Control", "max-age=86400")
            self.send_header("Content-Security-Policy", "script-src 'none'")   # an SVG logo can never run code here
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path not in ("/", "/index.html"):
            self.send_error(404)
            return
        ip, now = self.client_address[0], datetime.now().timestamp()
        if now - Handler.seen.get(ip, 0) > 1800:
            Handler.seen[ip] = now
            vault.log_event("👀", "page opened", f"from {ip}")
        try:
            self._send(200, page(self.data_dir).encode(), "text/html; charset=utf-8")
        except Exception:   # noqa: BLE001
            log.exception("web page failed")
            self._send(500, "<h1>Sorry, the price page is resting. Try again in a minute.</h1>".encode(), "text/html; charset=utf-8")

    def log_message(self, fmt, *args):   # keep the container log quiet
        pass


def start(data_dir: Path, port: int) -> ThreadingHTTPServer:
    Handler.data_dir = data_dir
    threading.Thread(target=ensure_logos, args=(data_dir,), daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("web page on :%d", port)
    return srv


def save_flyers(data_dir: Path, sections: dict) -> None:
    tmp = data_dir / "flyers.json.tmp"
    tmp.write_text(json.dumps({"saved": datetime.now(cards.TZ).isoformat(), "sections": sections}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(data_dir / "flyers.json")


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Today's Best Buys · 今日好价</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🛒</text></svg>">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght,SOFT@9..144,400..900,100&family=Atkinson+Hyperlegible:wght@400;700&family=Noto+Serif+SC:wght@600;900&display=swap" rel="stylesheet">
<style>
:root{{--paper:#f4ead6;--ink:#1f1a14;--muted:#6b5d4a;--chili:#c2391b;--pandan:#2c6e3f;--turmeric:#e6a72a;--card:#fffaf0;--line:#d8c7a4;
  --display:'Fraunces',Georgia,serif;--body:'Atkinson Hyperlegible',system-ui,sans-serif;--zh:'Noto Serif SC',serif;--bar:74px}}
*{{box-sizing:border-box}}
body{{margin:0;color:var(--ink);font:1.05rem/1.4 var(--body);background:var(--paper);
  background-image:radial-gradient(circle at 15% 10%,#fbf3e2 0,transparent 45%),radial-gradient(circle at 90% 60%,#e9d9b8 0,transparent 40%)}}
body::before{{content:"";position:fixed;inset:0;pointer-events:none;z-index:50;opacity:.3;mix-blend-mode:multiply;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='3' stitchTiles='stitch'/%3E%3CfeColorMatrix values='0 0 0 0 .4 0 0 0 0 .3 0 0 0 0 .2 0 0 0 .25 0'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E")}}
a{{color:inherit}}
a:focus-visible,button:focus-visible{{outline:4px solid var(--turmeric);outline-offset:2px}}
/* top bar: awning strip, title, date, legend */
.awning{{height:12px;background:repeating-linear-gradient(90deg,var(--chili) 0 40px,var(--card) 40px 80px)}}
.bar{{height:calc(var(--bar) - 12px);display:flex;align-items:center;gap:1.4rem;padding:0 1.2rem;border-bottom:3px double var(--ink)}}
.bar h1{{margin:0;font:900 2rem/1 var(--display);font-variation-settings:"SOFT" 100,"opsz" 144;letter-spacing:-.02em;white-space:nowrap}}
.bar h1 em{{color:var(--chili)}} .bar h1 .zh{{font-size:.7em}}
.fresh{{color:var(--muted);white-space:nowrap}}
.legend{{margin-left:auto;display:flex;flex-wrap:wrap;gap:.45rem;align-items:center;font-size:.95rem}}
.legend b{{font-weight:700;color:var(--muted);margin-right:.2rem}}
.ok{{background:#d9f0dc;color:var(--pandan);font-weight:700;padding:.25rem .7rem;border-radius:8px}}
.stale{{background:#f8d9d2;color:var(--chili);font-weight:700;padding:.25rem .7rem;border-radius:8px}}
.chg{{font-size:.9rem;font-weight:700;padding:.12rem .55rem;border-radius:999px;white-space:nowrap}}
.chg.down{{background:#d9f0dc;color:var(--pandan)}} .chg.up{{background:#f8d9d2;color:var(--chili)}}
.chg.same{{background:#ece3d0;color:var(--muted)}} .chg.new{{background:var(--turmeric);color:var(--ink)}}
.chg.star{{background:var(--card);border:1px solid var(--line)}}
/* app: category list + one panel */
.app{{display:grid;grid-template-columns:220px 1fr}}
.side{{display:flex;flex-direction:column;gap:.3rem;padding:.8rem .6rem;border-right:2px solid var(--line);overflow-y:auto}}
.side a{{text-decoration:none;font-weight:700;padding:.45rem .8rem;border-radius:12px;border:2px solid transparent;white-space:nowrap}}
.side a:hover{{background:var(--card);border-color:var(--line)}}
.side a[aria-current]{{background:var(--ink);color:var(--paper)}}
.side .side-top{{border-color:var(--turmeric);margin-bottom:.3rem}}
.fest{{border-radius:12px;background:#fbefe9;border:2px solid #f0c9b8;margin:.15rem 0}}
.fest summary{{cursor:pointer;font-weight:700;color:var(--chili);padding:.4rem .7rem;list-style:none;display:flex;justify-content:space-between;gap:.4rem}}
.fest summary::-webkit-details-marker{{display:none}}
.fest summary::after{{content:"▸";transition:transform .2s}}
.fest[open] summary::after{{transform:rotate(90deg)}}
.fest a{{display:block;padding-left:1.4rem}}
.fest small{{display:block;font-weight:400;color:var(--muted);font-size:.8rem}}
.side-h{{font-size:.75rem;font-weight:700;letter-spacing:.16em;text-transform:uppercase;color:var(--muted);margin:.7rem .8rem .1rem}}
.shelf.shelf-store{{grid-template-columns:1fr}}   /* more specific than the width rules for .shelf/.grid below */
.grid.grid-store{{grid-template-columns:repeat(5,minmax(0,1fr))}}
.grid.grid-auto{{grid-auto-rows:auto}}
.js .shelf:has(.grid-auto){{flex:none}}
.grid-store .pill{{display:none}}   /* every card is the same store: the staple label matters instead */
.grid-store .cell-label{{font-weight:700;color:var(--ink);font-size:.8rem}}
@media (max-height:900px){{.side{{gap:.12rem}}.side a{{padding:.28rem .7rem}}.side-h{{margin:.45rem .8rem 0}}}}
@media (max-width:1100px){{.grid.grid-store{{grid-template-columns:repeat(3,minmax(0,1fr))}}}}
@media (max-width:899px){{.grid.grid-store{{grid-template-columns:1fr}}}}
.panel{{padding:.9rem 1.3rem 1rem;display:flex;flex-direction:column;gap:.8rem;min-width:0}}
.js .panel[hidden]{{display:none}}
.panel{{animation:rise .45s cubic-bezier(.2,.8,.2,1)}}
@keyframes rise{{from{{opacity:0;transform:translateY(10px)}}}}
.ph{{display:flex;align-items:center;gap:.9rem}}
.stamp{{width:56px;height:56px;flex:none;display:grid;place-items:center;font-size:1.9rem;border-radius:50%;background:var(--card);
  border:3px solid var(--chili);box-shadow:inset 0 0 0 4px var(--card),inset 0 0 0 6px var(--chili);transform:rotate(-8deg)}}
.ph-t{{min-width:0}}
.ph h2{{margin:0;font:800 2.2rem/1 var(--display);font-variation-settings:"SOFT" 100}}
.zh{{font:900 .6em var(--zh);color:var(--chili);margin-left:.5rem;letter-spacing:.08em}}
.ph-sub{{color:var(--muted);font-size:.95rem;margin-top:.25rem}}
.stock{{background:#d9f0dc;color:var(--pandan);font-weight:700;padding:.1rem .6rem;border-radius:8px;margin-left:.4rem}}
.steps{{margin-left:auto;display:flex;gap:.6rem}}
.step{{text-decoration:none;font-weight:700;font-size:1.05rem;padding:.6rem 1.1rem;min-height:48px;display:flex;align-items:center;border-radius:999px;
  background:var(--card);border:2px solid var(--ink);box-shadow:3px 3px 0 var(--ink);white-space:nowrap;transition:transform .15s,box-shadow .15s}}
.step:hover{{transform:translate(-2px,-2px);box-shadow:5px 5px 0 var(--ink)}}
/* shelf: price tag + 3x3 grid */
.shelf{{display:grid;grid-template-columns:330px 1fr;gap:1.2rem;flex:1;min-height:0}}
.tag{{position:relative;display:flex;flex-direction:column;gap:.5rem;padding:1rem 1rem 1rem 2.6rem;background:var(--card);
  border:3px solid var(--ink);border-radius:10px 22px 22px 10px;box-shadow:7px 7px 0 var(--store);transform:rotate(-.8deg);min-height:0}}
.tag::before{{content:"";position:absolute;left:0;top:0;bottom:0;width:1.5rem;background:var(--store);border-radius:7px 0 0 7px}}
.hole{{position:absolute;left:.3rem;top:50%;width:.9rem;height:.9rem;margin-top:-.45rem;border-radius:50%;background:var(--paper);box-shadow:inset 1px 1px 2px rgba(0,0,0,.4)}}
.tag-link{{display:flex;flex-direction:column;gap:.3rem;text-decoration:none;flex:1;min-height:0;overflow:hidden}}
.tag-link>*{{flex:none}}
.tag-img{{width:100%;height:clamp(90px,18vh,170px);object-fit:contain;background:#fff;border-radius:10px;border:2px solid #eadfc8;padding:.4rem;flex:none}}
.emoji{{display:grid;place-items:center;background:radial-gradient(circle,#fff 0,#fbf3e2 75%);font-size:3.2rem}}
.tag-img.emoji{{font-size:4.5rem}}
.cell-img.emoji{{font-size:2.3rem;overflow:hidden}}
.tag-kicker{{font-weight:700;font-size:.8rem;letter-spacing:.16em;text-transform:uppercase;color:var(--pandan)}}
.tag-name{{font-size:1.15rem;font-weight:700;line-height:1.25;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}}
.tag-price{{font:900 clamp(2.6rem,7vh,3.8rem)/1 var(--display);font-variation-settings:"SOFT" 100,"opsz" 144;color:var(--chili);letter-spacing:-.03em}}
.tag-unit{{color:var(--muted);display:flex;flex-wrap:wrap;gap:.5rem;align-items:center}}
.tag-deal{{align-self:flex-start;background:var(--turmeric);font-weight:700;padding:.2rem .7rem;border-radius:6px;transform:rotate(-1.5deg)}}
.btn{{margin-top:auto;text-align:center;font-weight:700;font-size:1.15rem;padding:.7rem;border-radius:12px;background:var(--ink);color:var(--paper)}}
.tag-link:hover .btn{{background:var(--store)}}
.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));grid-auto-rows:minmax(0,1fr);gap:.7rem;min-height:0}}
.cell{{display:flex;flex-direction:column;background:var(--card);border-radius:14px;border:2px solid var(--line);border-left:7px solid var(--store);
  padding:.55rem .7rem;min-height:0;overflow:hidden;transition:border-color .2s,box-shadow .2s}}
.cell:hover .cell-name{{text-decoration:underline}}
.cell:hover{{border-color:var(--store);box-shadow:0 8px 18px -10px rgba(60,30,10,.45)}}
.cell-link{{display:flex;flex-direction:column;gap:.25rem;text-decoration:none;flex:1;min-height:0;overflow:hidden}}
.cell-link>*{{flex:none}}   /* never squash the name to zero height: the card clips at the bottom instead */
.cell-head{{display:flex;gap:.6rem;align-items:center}}
.cell-img{{width:64px;height:64px;flex:none;object-fit:contain;background:#fff;border-radius:10px;border:1px solid #eadfc8;padding:.2rem}}
.cell-hp{{display:flex;flex-direction:column;gap:.15rem;min-width:0}}
.cell-top{{display:flex;gap:.4rem;align-items:center;overflow:hidden;white-space:nowrap}}
.pill{{background:var(--store);color:#fff;font-weight:700;font-size:.75rem;padding:.1rem .55rem;border-radius:999px;white-space:nowrap}}
.cell-label{{font-size:.75rem;color:var(--muted);text-transform:uppercase;letter-spacing:.1em}}
.cell-price{{font:800 1.5rem/1 var(--display)}}
.cell-name{{font-weight:700;font-size:.98rem;line-height:1.25;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}
.cell-meta{{color:var(--muted);font-size:.88rem;min-width:0;flex:1;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}
.cell-meta b{{color:var(--chili)}}
.open{{font-weight:700;color:var(--store);text-decoration:none;white-space:nowrap;padding:.2rem .1rem}}
.ph-logo{{height:56px;max-width:220px;object-fit:contain;background:#fff;border-radius:12px;padding:.35rem .6rem;border:2px solid var(--line);flex:none}}
.side-word{{height:24px;max-width:150px;object-fit:contain;object-position:left;vertical-align:middle}}
.side-logo{{width:26px;height:26px;object-fit:contain;background:#fff;border-radius:6px;vertical-align:middle;margin-right:.15rem}}
.logo-none{{display:inline-grid;place-items:center;background:var(--store);color:#fff;font-weight:700}}
.ph-logo.logo-none{{width:56px;font-size:1.8rem}}
.shop{{display:inline-block;margin-top:.6rem;font-weight:700;text-decoration:none;padding:.6rem 1.1rem;border-radius:999px;background:var(--ink);color:var(--paper)}}
@media (max-height:800px){{.cell-meta{{-webkit-line-clamp:1}}.cell-img{{width:50px;height:50px}}.cell-img.emoji{{font-size:1.8rem}}.cell-label{{display:none}}.cell-price{{font-size:1.3rem}}
  .cell{{padding:.4rem .6rem}}.cell-link{{gap:.1rem}}.cell-actions{{padding-top:.1rem}}.cell-name{{font-size:.95rem;line-height:1.2}}
    .ph h2{{font-size:1.9rem}}.stamp{{width:48px;height:48px;font-size:1.6rem}}}}
.cell-actions{{display:flex;align-items:center;justify-content:space-between;gap:.5rem;margin-top:auto;padding-top:.3rem}}
.tag /* top 10 + flyers */
.flyer-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:.8rem}}
.flyer{{display:grid;gap:.4rem;align-content:start;text-decoration:none;background:var(--card);padding:.8rem;border-radius:12px;border:2px solid var(--ink);box-shadow:4px 4px 0 var(--store)}}
.flyer:hover{{transform:translate(-2px,-2px)}}
.flyer .pill{{justify-self:start}}
.flyer-title{{font-weight:700}} .flyer-detail{{color:var(--muted);font-size:.95rem}} .flyer-detail b{{font:800 1.3rem var(--display);color:var(--chili)}}
.empty{{color:var(--muted)}}
footer{{color:var(--muted);font-size:.85rem;padding:.3rem 1.3rem .6rem;text-align:right}}
/* one screen per category on a laptop/desktop; normal scrolling on phones and very short windows */
@media (min-width:900px) and (min-height:600px){{
  .js body{{height:100vh;overflow:hidden;display:flex;flex-direction:column}}
  .js .app{{flex:1;min-height:0}}
  .js .side{{max-height:calc(100vh - var(--bar) - 28px)}}
  .js main{{min-height:0;display:flex;flex-direction:column}}
  .js .panel{{flex:1;min-height:0}}
  .js .panel-scroll{{overflow-y:auto}}
  .js .grid:not(.grid-auto){{grid-template-rows:repeat(3,minmax(0,1fr))}}
}}
@media (max-width:1400px){{.app{{grid-template-columns:185px 1fr}}.side a{{padding:.4rem .6rem;font-size:.98rem}}.shelf{{grid-template-columns:265px 1fr}}}}
@media (max-width:1100px){{.legend{{display:none}}.grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
@media (max-width:899px){{
  .bar{{height:auto;flex-wrap:wrap;padding:.6rem 1rem;gap:.4rem 1rem}} .legend{{display:flex;margin-left:0}}
  .app{{grid-template-columns:1fr}} .side{{flex-direction:row;flex-wrap:wrap;border-right:0;border-bottom:2px solid var(--line)}}
  .side a{{background:var(--card);border-color:var(--line)}}
  .shelf{{grid-template-columns:1fr}} .grid{{grid-template-columns:1fr}} .ph{{flex-wrap:wrap}} .steps{{margin-left:0}}
}}
@media (prefers-reduced-motion:reduce){{*{{animation:none!important;transition:none!important}}}}
</style></head>
<body>
<div class="awning" aria-hidden="true"></div>
<header class="bar">
  <h1>Today's <em>Best</em> Buys <span class="zh">今日好价</span></h1>
  <span class="fresh">{fresh}</span>
  <div class="legend" aria-label="What the marks mean"><b>Key:</b><span class="chg down">▼ cheaper</span><span class="chg up">▲ dearer</span>
    <span class="chg new">NEW</span><span class="chg star">★ trusted brand</span></div>
</header>
<div class="app">
  <nav class="side" aria-label="Categories">{nav}</nav>
  <main>{panels}</main>
</div>
<footer>Online prices; the shelf price can differ by a few cents · page opened {updated} · new prices every morning at 08:00</footer>
<script>
/* one category per screen: the URL hash picks the panel (#s0, #top, #flyers); arrow keys step through them */
document.documentElement.classList.add('js');
var panels=[].slice.call(document.querySelectorAll('.panel')), links=[].slice.call(document.querySelectorAll('.side a'));
function show(){{
  var id=location.hash.slice(1); if(!document.getElementById(id)||!panels.some(function(p){{return p.id===id}})) id='{first}';
  panels.forEach(function(p){{p.hidden=p.id!==id}});
  links.forEach(function(a){{if(a.getAttribute('href')==='#'+id){{a.setAttribute('aria-current','page');var f=a.closest('details');if(f)f.open=true;a.scrollIntoView({{block:'nearest'}});}}else a.removeAttribute('aria-current')}});
  window.scrollTo(0,0);
}}
window.addEventListener('hashchange',show); show();
document.addEventListener('keydown',function(e){{
  if(e.key!=='ArrowLeft'&&e.key!=='ArrowRight'||e.target.closest('input,textarea'))return;
  var cur=panels.filter(function(p){{return !p.hidden}})[0], s=cur&&cur.querySelectorAll('.step');
  var a=[].slice.call(s||[]).filter(function(x){{return e.key==='ArrowLeft'?x.textContent.trim().charAt(0)==='‹':x.textContent.trim().slice(-1)==='›'}})[0];
  if(a)location.hash=a.getAttribute('href');
}});
</script>
</body></html>"""
