"""Family web page: today's best buys per staple, big type, one tap to the shop's product page.
Rendered live from data/prices.db (+ data/flyers.json from the last flyer run) on every request. Read-only, stdlib only.
Env: WEB_PORT (default 8000 inside the container; 0 = off)."""
from __future__ import annotations

import html
import json
import logging
import os
import threading
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import cards, scrape
from .store import Store

log = logging.getLogger(__name__)
ZH = {"rice": "米", "cooking oil": "食用油", "eggs": "鸡蛋", "chicken": "鸡肉", "pork": "猪肉", "fish fillet": "鱼片", "prawns": "虾",
      "choy sum": "菜心", "tofu": "豆腐", "soy sauce": "酱油", "noodles": "面", "onion": "洋葱", "tomato": "番茄", "milk": "牛奶",
      "garlic": "蒜", "bread": "面包", "potato": "马铃薯", "cabbage": "包菜", "carrot": "红萝卜", "beef": "牛肉", "salmon": "三文鱼",
      "kailan": "芥兰"}
STORE_COLOR = {"FairPrice": "#1d4fa3", "Cold Storage": "#b3261e", "Sheng Siong": "#d9731a", "Giant": "#2f7d3b", "Prime": "#6b3fa0"}
PER_STAPLE = 10   # products shown per staple ("up to 10 per category")
HOSTS = {"fairprice.com.sg": "FairPrice", "coldstorage.com.sg": "Cold Storage", "shengsiong.com.sg": "Sheng Siong",
         "giant.sg": "Giant"}


def esc(x) -> str:
    return html.escape(str(x), quote=True)


def safe_url(u: str) -> str:
    """Only http(s) links reach an href (rows come from scraped pages)."""
    return u if urlparse(u or "").scheme in ("http", "https") else "#"


def store_of(url: str) -> str:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return next((s for h, s in HOSTS.items() if host.endswith(h)), host or "Shop")


def staple_data(st: Store, staple: str, brands: list[str]) -> dict | None:
    """Latest saved rows for one staple -> winner, best per store, one deal, price change, 8-week low."""
    day = st.db.execute("SELECT MAX(day) d FROM prices WHERE query=?", (staple,)).fetchone()["d"]
    if not day:
        return None
    rows = [dict(r, store=store_of(r["url"])) for r in st.db.execute(
        "SELECT * FROM prices WHERE query=? AND day=? ORDER BY unit_price IS NULL, unit_price", (staple, day))]
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
    return dict(day=day, winner=winner, others=[b for b in bests if b is not winner], deal=deal, more=more, count=len(rows),
                prev=st.previous(staple, d).get(winner["url"]),
                stock=low is not None and winner["unit_price"] is not None and winner["unit_price"] <= low - 0.005, low=low)


def change(now: float | None, prev: float | None) -> str:
    if now is None or prev is None:
        return '<span class="chg new">NEW</span>' if now is not None else ""
    d = now - prev
    if abs(d) < 0.005:
        return '<span class="chg same">same as last time</span>'
    return f'<span class="chg {"down" if d < 0 else "up"}">{"▼ cheaper" if d < 0 else "▲ dearer"} {cards.money(abs(d))}</span>'


def deal_text(r: dict) -> str:
    bits = ([r["promo"]] if r.get("promo") else []) + ([f"was {cards.money(r['was'])}"] if r.get("was") else [])
    return " · ".join(bits)


def photo(r: dict, cls: str) -> str:
    """Product photo from the shop's own image server; no referrer (the page lives on the LAN), hidden if it fails to load."""
    if not (r.get("image") or "").startswith("https://"):
        return ""
    return (f'<img class="{cls}" src="{esc(r["image"])}" alt="{esc(r["name"])}" loading="lazy" referrerpolicy="no-referrer" '
            f'onerror="this.remove()">')


def small_card(r: dict, label: str, brands: list[str], em: str = "🛒") -> str:
    dl = deal_text(r)
    return (f'<a class="mini" href="{esc(safe_url(r["url"]))}" target="_blank" rel="noopener" style="--store:{STORE_COLOR.get(r["store"], "#555")}">'
            f'{photo(r, "mini-img") or f'<span class="mini-img mini-emoji" aria-hidden="true">{em}</span>'}<span class="mini-body"><span class="mini-top"><span class="pill">{esc(r["store"])}</span><span class="mini-label">{esc(label)}</span></span>'
            f'<span class="mini-name">{"★ " if cards.is_brand(r, brands) else ""}{esc(r["name"])}</span>'
            f'<span class="mini-price"><b>{cards.money(r["price"])}</b> <small>{esc(cards.unit(r))}</small></span>'
            + (f'<span class="mini-deal">{esc(dl)}</span>' if dl else "")
            + '<span class="mini-go">Open ›</span></span></a>')


def section(i: int, staple: str, d: dict | None, brands: list[str]) -> str:
    em, zh = cards.EMOJI.get(staple, "🛒"), ZH.get(staple, "")
    head = (f'<header class="sh"><span class="stamp" aria-hidden="true">{em}</span>'
            f'<h2>{esc(staple.title())}<span class="zh">{esc(zh)}</span></h2></header>')
    if not d:
        return f'<section class="staple" id="s{i}" style="--i:{i}">{head}<p class="empty">No prices today. We\'ll try again tomorrow morning.</p></section>'
    w = d["winner"]
    dl = deal_text(w)
    tag = (f'<a class="tag" href="{esc(safe_url(w["url"]))}" target="_blank" rel="noopener" style="--store:{STORE_COLOR.get(w["store"], "#555")}">'
           f'<span class="hole" aria-hidden="true"></span>{photo(w, "tag-img") or f'<span class="tag-img tag-emoji" aria-hidden="true">{em}</span>'}'
           f'<span class="tag-kicker">Best value {"· ★ trusted brand" if cards.is_brand(w, brands) else ""}</span>'
           f'<span class="tag-name">{esc(w["name"])}</span>'
           f'<span class="tag-price">{cards.money(w["price"])}</span>'
           f'<span class="tag-unit">{esc(cards.unit(w))} {change(w["unit_price"], d["prev"])}</span>'
           + (f'<span class="tag-deal">🏷 {esc(dl)}</span>' if dl else "")
           + f'<span class="btn">Open at {esc(w["store"])} <span aria-hidden="true">→</span></span></a>')
    minis = [small_card(o, "also good here", brands, em) for o in d["others"]]
    if d["deal"]:
        minis.append(small_card(d["deal"], "on offer", brands, em))
    more = "".join(small_card(r, f"#{n} value", brands, em) for n, r in enumerate(d["more"], len(minis) + 2))
    more = (f'<h3 class="more-h">More choices · cheapest per 100g first</h3><div class="carousel">'
            f'<button class="nav prev" type="button" aria-label="Scroll left">‹</button><div class="more">{more}</div>'
            f'<button class="nav next" type="button" aria-label="Scroll right">›</button></div>') if more else ""
    stock = (f'<p class="stock">💰 <b>Stock up:</b> cheapest in 8 weeks (was {cards.money(d["low"])}{esc(w["unit"])})</p>'
             if d["stock"] else "")
    return (f'<section class="staple" id="s{i}" style="--i:{i}">{head}{stock}<div class="shelf">{tag}'
            f'<div class="minis">{"".join(minis)}</div></div>{more}'
            f'<p class="meta">Compared {d["count"]} products · price per 100g / 100ml / piece</p></section>')


def flyers_html(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:   # noqa: BLE001  no flyer run yet, or a half-written file: just leave the section out
        return ""
    cards_ = []
    for src, items in data.get("sections", {}).items():
        for it in [i for i in items if i.get("staple")][:8] or items[:4]:
            r = it.get("row") or {}
            price = f'<b>{cards.money(r["price"])}</b>' if r.get("price") is not None else ""
            cards_.append(f'<a class="flyer" href="{esc(safe_url(it.get("url", "")))}" target="_blank" rel="noopener" '
                          f'style="--store:{STORE_COLOR.get(it.get("store"), "#555")}"><span class="pill">{esc(it.get("store", src))}</span>'
                          f'<span class="flyer-title">{esc(it.get("title", "")[:90])}</span>'
                          f'<span class="flyer-detail">{price} {esc(it.get("detail") or "")}</span></a>')
    if not cards_:
        return ""
    return (f'<section class="flyers" id="flyers"><h2 class="band">This week\'s flyers<span class="zh">本周传单</span></h2>'
            f'<div class="flyer-grid">{"".join(cards_)}</div></section>')


def page(data_dir: Path) -> str:
    st = Store(data_dir)
    try:
        cfg = st.config()
        secs, chips, stock = [], [], []
        last = st.db.execute("SELECT MAX(day) d FROM prices").fetchone()["d"]
        for i, s in enumerate(cfg["staples"]):
            brands = cfg["brands"].get(s, [])
            d = staple_data(st, s, brands)
            secs.append(section(i, s, d, brands))
            chips.append(f'<a href="#s{i}">{cards.EMOJI.get(s, "🛒")} {esc(s.title())}</a>')
            if d and d["stock"]:
                stock.append(f'<a href="#s{i}">{cards.EMOJI.get(s, "🛒")} {esc(s.title())}</a>')
    finally:
        st.db.close()
    now = datetime.now(cards.TZ)
    checked = date.fromisoformat(last) if last else None
    fresh = (f"Prices checked {checked.strftime('%A, %d %B')}" if checked else "No prices yet")
    if not checked or (now.date() - checked).days > 1:   # the 08:00 run missed a day: say so instead of looking current
        fresh = f'<span class="stale">⚠ {fresh}. Today&#39;s check has not run yet, prices may be old.</span>'
    stock_html = (f'<div class="stockbar"><span>💰 Cheapest in 8 weeks, good time to stock up:</span> {" ".join(stock)}</div>'
                  if stock else "")
    return TEMPLATE.format(date=fresh, chips="".join(chips), stock=stock_html,
                           sections="".join(secs), flyers=flyers_html(data_dir / "flyers.json"),
                           updated=esc(now.strftime("%d %b %Y, %H:%M")))


class Handler(BaseHTTPRequestHandler):
    data_dir = Path("data")

    def do_GET(self):   # noqa: N802
        if self.path.split("?")[0] not in ("/", "/index.html"):
            self.send_error(404)
            return
        try:
            body, code = page(self.data_dir).encode(), 200
        except Exception:   # noqa: BLE001
            log.exception("web page failed")
            body, code = "<h1>Sorry, the price page is resting. Try again in a minute.</h1>".encode(), 500
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):   # keep the container log quiet
        pass


def start(data_dir: Path, port: int) -> ThreadingHTTPServer:
    Handler.data_dir = data_dir
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
:root{{--paper:#f4ead6;--paper2:#ecdfc4;--ink:#1f1a14;--muted:#6b5d4a;--chili:#c2391b;--pandan:#2c6e3f;--turmeric:#e6a72a;--card:#fffaf0;
  --display:'Fraunces',Georgia,serif;--body:'Atkinson Hyperlegible',system-ui,sans-serif;--zh:'Noto Serif SC',serif}}
*{{box-sizing:border-box}}
html{{scroll-behavior:smooth;scroll-padding-top:9rem}}
@media (max-width:820px){{html{{scroll-padding-top:1rem}}}}
body{{margin:0;color:var(--ink);font:1.2rem/1.5 var(--body);background:var(--paper);
  background-image:radial-gradient(circle at 15% 10%,#fbf3e2 0,transparent 45%),radial-gradient(circle at 90% 60%,#e9d9b8 0,transparent 40%);}}
body::before{{content:"";position:fixed;inset:0;pointer-events:none;z-index:50;opacity:.35;mix-blend-mode:multiply;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='3' stitchTiles='stitch'/%3E%3CfeColorMatrix values='0 0 0 0 .4 0 0 0 0 .3 0 0 0 0 .2 0 0 0 .25 0'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E")}}
a{{color:inherit}}
.wrap{{max-width:1180px;margin:0 auto;padding:0 1.25rem}}
/* masthead: shophouse awning stripes + newspaper title */
.awning{{position:relative;height:26px;background:repeating-linear-gradient(90deg,var(--chili) 0 56px,var(--card) 56px 112px)}}
.awning::after{{content:"";position:absolute;left:0;right:0;top:26px;height:20px;filter:drop-shadow(0 5px 3px rgba(60,30,10,.2));
  background:radial-gradient(circle at 28px 0,var(--chili) 27px,transparent 28px) 0 0/112px 20px repeat-x,radial-gradient(circle at 84px 0,var(--card) 27px,transparent 28px) 0 0/112px 20px repeat-x}}
.mast{{text-align:center;padding:2.2rem 0 1.4rem;border-bottom:3px double var(--ink)}}
.kicker{{font:700 .95rem/1 var(--body);letter-spacing:.32em;text-transform:uppercase;color:var(--chili)}}
.mast h1{{font:900 clamp(3rem,10vw,7.2rem)/.9 var(--display);font-variation-settings:"SOFT" 100,"opsz" 144;margin:.6rem 0 .2rem;letter-spacing:-.02em}}
.mast h1 em{{font-style:italic;color:var(--chili)}}
.mast .zhbig{{font:900 clamp(1.6rem,4vw,2.4rem)/1 var(--zh);letter-spacing:.4em;color:var(--pandan)}}
.mast .date{{margin-top:1rem;font-size:1.15rem;color:var(--muted)}}
.stale{{display:inline-block;background:#f8d9d2;color:var(--chili);font-weight:700;padding:.4rem .9rem;border-radius:8px}}
/* staple chips */
.chips{{position:sticky;top:0;z-index:20;background:rgba(244,234,214,.92);backdrop-filter:blur(8px);border-bottom:1px solid #d8c7a4}}
.chips .wrap{{display:flex;flex-wrap:wrap;justify-content:center;gap:.6rem;padding:.8rem 1.25rem}}
.chips a{{flex:none;text-decoration:none;font-weight:700;padding:.55rem 1rem;min-height:48px;display:flex;align-items:center;border-radius:999px;
  background:var(--card);border:2px solid var(--ink);box-shadow:3px 3px 0 var(--ink);transition:transform .15s,box-shadow .15s}}
.chips a:hover,.chips a:focus-visible{{transform:translate(-2px,-2px);box-shadow:5px 5px 0 var(--ink)}}
.stockbar{{margin:1.6rem 0 0;padding:1rem 1.2rem;border-radius:14px;background:var(--pandan);color:#fff;font-weight:700;display:flex;flex-wrap:wrap;gap:.6rem;align-items:center}}
.stockbar a{{background:#fff;color:var(--pandan);padding:.35rem .8rem;border-radius:999px;text-decoration:none}}
/* staple sections */
.staple{{padding:3rem 0 2.2rem;border-bottom:2px dashed #cdb98f;animation:rise .8s cubic-bezier(.2,.8,.2,1)}}  /* no fill, no delay: if animations never run the page is still fully visible */
@keyframes rise{{from{{opacity:0;transform:translateY(24px)}}}}
.sh{{display:flex;align-items:center;gap:1rem;margin-bottom:1.3rem}}
.stamp{{width:78px;height:78px;flex:none;display:grid;place-items:center;font-size:2.6rem;border-radius:50%;background:var(--card);
  border:3px solid var(--chili);box-shadow:inset 0 0 0 5px var(--card),inset 0 0 0 7px var(--chili);transform:rotate(-8deg)}}
.sh h2{{margin:0;font:800 clamp(2.2rem,5.5vw,3.4rem)/1 var(--display);font-variation-settings:"SOFT" 100;letter-spacing:-.01em}}
.zh{{font:900 .55em var(--zh);color:var(--chili);margin-left:.6rem;letter-spacing:.1em}}
.shelf{{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);gap:1.6rem;align-items:start}}
@media (max-width:820px){{.nav.prev{{left:-8px}}.nav.next{{right:-8px}}.more .mini{{flex-basis:200px}}.mini-img{{width:72px;height:72px}}.chips{{position:static}}.chips a{{padding:.45rem .8rem;font-size:1rem}}.shelf{{grid-template-columns:1fr}}.tag{{transform:rotate(-.5deg);padding-left:2.9rem;margin-right:10px}}.mast h1{{font-size:clamp(2.6rem,12vw,4rem)}}}}
/* the hero price tag */
.tag{{position:relative;display:flex;flex-direction:column;gap:.35rem;text-decoration:none;padding:1.6rem 1.6rem 1.5rem 3.4rem;background:var(--card);
  border:3px solid var(--ink);border-radius:10px 26px 26px 10px;box-shadow:8px 8px 0 var(--store);transform:rotate(-1.2deg);transform-origin:12% 50%;
  transition:transform .35s cubic-bezier(.3,1.6,.5,1),box-shadow .2s}}
.tag::before{{content:"";position:absolute;left:0;top:0;bottom:0;width:1.9rem;background:var(--store);border-radius:7px 0 0 7px}}
.hole{{position:absolute;left:.45rem;top:50%;width:1rem;height:1rem;margin-top:-.5rem;border-radius:50%;background:var(--paper);box-shadow:inset 1px 1px 2px rgba(0,0,0,.4)}}
.tag:hover,.tag:focus-visible{{transform:rotate(.6deg) translateY(-3px);box-shadow:12px 12px 0 var(--store)}}
.tag-img{{width:100%;height:220px;object-fit:contain;background:#fff;border-radius:12px;border:2px solid #eadfc8;padding:.6rem;margin-bottom:.4rem;
  transition:transform .4s cubic-bezier(.3,1.6,.5,1)}}
.tag-emoji{{display:grid;place-items:center;height:150px;font-size:5.5rem;background:radial-gradient(circle,#fff 0,#fbf3e2 70%)}}
.tag:hover .tag-img{{transform:scale(1.04) rotate(-1deg)}}
.tag-kicker{{font-weight:700;font-size:.9rem;letter-spacing:.18em;text-transform:uppercase;color:var(--pandan)}}
.tag-name{{font-size:1.35rem;font-weight:700;line-height:1.3}}
.tag-price{{font:900 clamp(3.4rem,9vw,5.4rem)/1 var(--display);font-variation-settings:"SOFT" 100,"opsz" 144;color:var(--chili);letter-spacing:-.03em;margin-top:.3rem}}
.tag-unit{{font-size:1.15rem;color:var(--muted);display:flex;flex-wrap:wrap;gap:.6rem;align-items:center}}
.tag-deal{{align-self:flex-start;background:var(--turmeric);color:var(--ink);font-weight:700;padding:.3rem .8rem;border-radius:6px;transform:rotate(-1.5deg)}}
.btn{{margin-top:.9rem;align-self:stretch;text-align:center;font-weight:700;font-size:1.25rem;padding:1rem;border-radius:12px;background:var(--ink);color:var(--paper);min-height:56px}}
.tag:hover .btn{{background:var(--store)}}
.chg{{font-size:.95rem;font-weight:700;padding:.15rem .6rem;border-radius:999px}}
.chg.down{{background:#d9f0dc;color:var(--pandan)}} .chg.up{{background:#f8d9d2;color:var(--chili)}}
.chg.same{{background:#ece3d0;color:var(--muted)}} .chg.new{{background:var(--turmeric);color:var(--ink)}}
/* smaller cards */
.minis{{display:grid;gap:1rem}}
.mini{{display:flex;gap:1rem;align-items:center;text-decoration:none;background:var(--card);padding:1rem 1.1rem;border-radius:14px;border:2px solid #d8c7a4;border-left:8px solid var(--store);
  transition:transform .2s,border-color .2s,box-shadow .2s}}
.mini:hover,.mini:focus-visible{{transform:translateX(4px);border-color:var(--store);box-shadow:0 8px 20px -10px rgba(60,30,10,.4)}}
.mini-img{{width:96px;height:96px;flex:none;object-fit:contain;background:#fff;border-radius:10px;border:1px solid #eadfc8;padding:.3rem}}
.mini-emoji{{display:grid;place-items:center;font-size:2.4rem;background:radial-gradient(circle,#fff 0,#fbf3e2 75%)}}
.more .mini-emoji{{font-size:4rem}}
.mini-body{{display:grid;gap:.25rem;flex:1;min-width:0}}
.mini-top{{display:flex;justify-content:space-between;gap:.5rem;align-items:center}}
.pill{{background:var(--store);color:#fff;font-weight:700;font-size:.85rem;padding:.2rem .65rem;border-radius:999px;justify-self:start}}
.more-h{{font:700 1rem var(--body);letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin:2rem 0 .8rem}}
.carousel{{position:relative}}
.more{{display:flex;gap:1rem;overflow-x:auto;scroll-snap-type:x mandatory;scroll-behavior:smooth;padding:.4rem .3rem 1rem;scrollbar-width:thin}}
.more .mini{{flex:0 0 230px;scroll-snap-align:start;flex-direction:column;align-items:stretch;gap:.6rem}}
.nav{{position:absolute;top:38%;z-index:2;width:56px;height:56px;border-radius:50%;border:3px solid var(--ink);background:var(--card);color:var(--ink);
  font:900 2.2rem/1 var(--display);box-shadow:3px 3px 0 var(--ink);cursor:pointer;transition:opacity .2s,transform .15s}}
.nav:hover{{transform:translate(-1px,-1px);box-shadow:4px 4px 0 var(--ink)}}
.nav.prev{{left:-20px}} .nav.next{{right:-20px}}
.nav[disabled]{{opacity:0;pointer-events:none}}
.more .mini-img{{width:100%;height:150px}}
.more .mini-top{{flex-wrap:wrap}}
.mini-label{{font-size:.85rem;color:var(--muted);text-transform:uppercase;letter-spacing:.12em}}
.mini-name{{font-weight:700}}
.mini-price b{{font:800 1.8rem var(--display);color:var(--ink)}} .mini-price small{{color:var(--muted);font-size:1rem}}
.mini-deal{{color:var(--chili);font-weight:700}}
.mini-go{{justify-self:end;font-weight:700;color:var(--store)}}
.stock{{display:inline-block;background:#d9f0dc;color:var(--pandan);padding:.5rem 1rem;border-radius:10px;margin:0 0 1rem}}
.meta,.empty{{color:var(--muted);font-size:1rem;margin:1.1rem 0 0}}
/* flyers */
.band{{font:900 clamp(2rem,5vw,3rem)/1 var(--display);margin:3.5rem 0 1.4rem;padding:.9rem 1.2rem;background:var(--ink);color:var(--paper);border-radius:10px;transform:rotate(-.6deg)}}
.flyer-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:1rem}}
.flyer{{display:grid;gap:.5rem;align-content:start;text-decoration:none;background:var(--card);padding:1rem;border-radius:12px;border:2px solid var(--ink);box-shadow:4px 4px 0 var(--store);transition:transform .2s}}
.flyer:hover,.flyer:focus-visible{{transform:translate(-2px,-2px) rotate(-.6deg)}}
.flyer-title{{font-weight:700}} .flyer-detail{{color:var(--muted);font-size:1rem}} .flyer-detail b{{font:800 1.4rem var(--display);color:var(--chili)}}
footer{{text-align:center;color:var(--muted);font-size:1rem;padding:3rem 1rem 4rem}}
footer .legend{{display:flex;flex-wrap:wrap;justify-content:center;gap:.8rem;margin-bottom:1rem}}
a:focus-visible{{outline:4px solid var(--turmeric);outline-offset:3px}}
@media (prefers-reduced-motion:reduce){{*{{animation:none!important;transition:none!important}}.more{{scroll-behavior:auto}}}}
</style></head>
<body>
<div class="awning" aria-hidden="true"></div>
<header class="mast wrap">
  <div class="kicker">Supermarket Hunter · for the family kitchen</div>
  <h1>Today's <em>Best</em> Buys</h1>
  <div class="zhbig">今日好价</div>
  <div class="date">{date}</div>
</header>
<nav class="chips" aria-label="Jump to an item"><div class="wrap">{chips}<a href="#flyers">📰 Flyers</a></div></nav>
<main class="wrap">{stock}{sections}{flyers}</main>
<footer class="wrap">
  <div class="legend"><span class="chg down">▼ cheaper</span><span class="chg up">▲ dearer</span><span class="chg new">NEW</span><span>★ trusted brand</span></div>
  Tap any card to open the product in the shop's website. Online prices; the shelf price can differ by a few cents.<br>
  Page opened {updated} · new prices every morning at 08:00
</footer>
<script>
/* More choices carousel: arrows scroll ~one screen of cards; each arrow hides at its end */
document.querySelectorAll('.carousel').forEach(function(c){{
  var m=c.querySelector('.more'),p=c.querySelector('.prev'),n=c.querySelector('.next');
  function upd(){{p.disabled=m.scrollLeft<8;n.disabled=m.scrollLeft+m.clientWidth>=m.scrollWidth-8;}}
  p.onclick=function(){{m.scrollBy({{left:-m.clientWidth*.9}});}};
  n.onclick=function(){{m.scrollBy({{left:m.clientWidth*.9}});}};
  m.addEventListener('scroll',upd,{{passive:true}});window.addEventListener('resize',upd);upd();
}});
</script>
</body></html>"""
