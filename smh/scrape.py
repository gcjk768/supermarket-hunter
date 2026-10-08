"""Supermarket search. Cold Storage through Jina Reader (robots.txt allows its search), with the shared Playwright server
on the NAS as fallback; FairPrice search through that browser only (robots.txt disallows /search for crawlers; the owner's rule
of 2026-10-08 allows the NAS browser for such pages, once a day, one page at a time).

Both routes produce the same Markdown shape; each product is one link line:
  FairPrice   ``[<promo> ![img](..) $price [$was] <name>](https://www.fairprice.com.sg/product/...)``
  Cold Storage ``[<N>% off $price [$was] <name>](https://coldstorage.com.sg/product/...)``
Unit price comes from the pack size in the name ("5kg", "24 x 200ml", "10s"), so a 10kg bag and a 5kg bag compare.
Rules: a captcha / challenge page or a 429 in the browser is never worked around (no stealth, proxies or retries); it is logged
and the term returns empty. Sheng Siong is behind Incapsula and Giant's shop moved to foodpanda: flyers only (smh/flyers.py).
"""
from __future__ import annotations

import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from . import vault
from .cards import EMOJI

log = logging.getLogger(__name__)
JINA = "https://r.jina.ai/"
PRICE = re.compile(r"\$\d+(?:\.\d+)?")
UNIT = re.compile(r"(?:(\d+)\s*[xX]\s*)?(\d+(?:\.\d+)?)\s*(kg|g|l|ml|s)\b", re.I)
SKIP = re.compile(r"^Ad\b|^(?-i:Ad(?=[A-Z]))|\bseeds?\b|out of stock", re.I)   # sponsored rows: "Ad …" (browser: "AdSupreme…")
FP_LINE = re.compile(r"^\[(.*)\]\((https://www\.fairprice\.com\.sg/product/[^)]+)\)", re.M)
CS_LINE = re.compile(r"^\[(?:(\d+)% off )?(\$\d+(?:\.\d+)?) (?:(\$\d+(?:\.\d+)?) )?([^\]]+)\]\((https://coldstorage\.com\.sg/product/[^)]+)\)", re.M | re.I)   # the browser says "5% OFF"
RM_LINE = re.compile(r"^\[(.*)\]\((https://www\.lazada\.sg/products/[^)]+)\)", re.M)
CS_IMG = re.compile(r"^\[!\[[^\]]*\]\((https?://[^)\s]+)\)\]\((https://coldstorage\.com\.sg/product/[^)]+)\)", re.M)   # photo line above each product
STORES = {   # name -> (search url, product-line regex, route); tools/ has the research for the other stores
    "Cold Storage": ("https://coldstorage.com.sg/search?q={}", CS_LINE, "jina"),
    "FairPrice": ("https://www.fairprice.com.sg/search?query={}", FP_LINE, "browser"),
    # RedMart lives on Lazada; service=RM keeps RedMart's own groceries. Its search cards keep the price outside the link: "card" mode
    "RedMart": ("https://www.lazada.sg/catalog/?q={}&service=RM", RM_LINE, "browser-card"),
}
DAILY_ONLY = {"FairPrice", "RedMart"}   # browser-only stores: only in full refreshes (every 3 h by day), never the hourly check
ONCE_A_DAY = {"RedMart"}   # Lazada shows its slider check if asked often: RedMart is read at most once a day
COOL_OFF = 24 * 3600       # after a challenge page, leave that site alone this long (logged once)
_blocked_until: dict[str, float] = {}   # host -> time.time() when it may be tried again
_last_read: dict[str, float] = {}       # store -> when its last refresh pass started (ONCE_A_DAY)
_reading: set[str] = set()              # ONCE_A_DAY stores being read in the current refresh pass


def start_pass() -> None:
    """Called at the start of each refresh: ONCE_A_DAY stores read in the last 20 h are skipped for this whole pass."""
    _reading.clear()
    for store in ONCE_A_DAY:
        if time.time() - _last_read.get(store, 0) >= 20 * 3600:
            _reading.add(store)
            _last_read[store] = time.time()


def _host(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.lower()


def _block(url: str, why: str) -> None:
    """A challenge / 403 / 429: stop asking this site for COOL_OFF, and say so once."""
    host = _host(url)
    if _blocked_until.get(host, 0) < time.time():
        vault.log_event("⛔", f"{host} blocked, not worked around", f"{why} · leaving it alone for {COOL_OFF // 3600} h")
    _blocked_until[host] = time.time() + COOL_OFF
PW_WS = os.environ.get("PLAYWRIGHT_WS_URL", "ws://playwright:3000/")
CHALLENGE = re.compile(r"incapsula|request unsuccessful|captcha|verify you are human|access denied|robot check|unusual traffic|"
                       r"slide to verify|sliding verification|/punish", re.I)   # Lazada's slider check counts as a challenge
PROMO = re.compile(r"(?i)^(save \$|\d+% off|any \d|buy \d|\d+ for \$|spend \$|up to)")
NOISE = re.compile(r"(?i)^(add to cart|\d\.\d|\(\d+(\.\d+)?k?\)|singapore)$")   # star rating, review count
SOLD = re.compile(r"(?i)^([\d.]+)\s*([km]?)\+?\s*sold$")   # RedMart cards: "2.0M sold", "95.8K sold"
SOLD_TOKEN = re.compile(r"\s*⟦sold:(\d+)⟧")
ALSO = {"choy sum": ["cai xin", "chye sim"]}   # shops spell it three ways; search all, merge, show as the staple


def _md_line(href: str, text: str, img: str) -> str:
    """One product card from the browser -> the Jina Markdown shape the parsers read: [promo ![Image](img) $price $was name](href).
    Lines are sorted by what they are, not where they sit: FairPrice puts the promo above the price, RedMart the name above.
    "Save $1.46\n$10.49\n$11.95\nEgg White\n500 ML" -> [Save $1.46 ![Image](img) $10.49 $11.95 Egg White 500 ML](href)
    "RedMart 15 Eggs 15 X 60G\n$4.65\n9% Off\n2.0M sold" -> [9% Off ![Image](img) $4.65 RedMart 15 Eggs 15 X 60G](href)"""
    lines = [x.strip() for x in text.splitlines() if x.strip() and not NOISE.fullmatch(x.strip())]
    sold = next((int(float(m[1]) * {"k": 1e3, "m": 1e6}.get(m[2].lower(), 1)) for x in lines if (m := SOLD.match(x))), None)
    lines = [x for x in lines if not SOLD.match(x)]
    prices = [x for x in lines if re.fullmatch(r"\$\s?\d+(?:\.\d+)?", x)]
    if not prices:
        return f"[![Image]({img})]({href})" if img else ""
    promo = " ".join(x for x in lines if PROMO.match(x))
    name = " ".join(x for x in lines if x not in prices and not PROMO.match(x))
    price_txt = " ".join(x.replace(" ", "") for x in prices)
    body = f"{promo} ![Image]({img}) {price_txt} {name}" if img else f"{promo} {price_txt} {name}"
    if sold:
        body += f" ⟦sold:{sold}⟧"
    return f"[{body.strip()}]({href})"


LINKS = "a[href*='/product/'], a[href*='/products/']"
LINKS_JS = """(els, cards) => {
  const imgs = {};
  els.forEach(a => { const i = a.querySelector('img'), h = a.href.split('?')[0]; if (i && i.src && !imgs[h]) imgs[h] = i.src; });
  return els.map(a => {
    const h = a.href.split('?')[0]; let t = a.innerText;
    if (cards && t.trim() && !t.includes('$')) {   // the price sits beside the link: read the whole product card
      let el = a; for (let k = 0; k < 6 && el && !el.innerText.includes('$'); k++) el = el.parentElement;
      if (el) t = el.innerText;
    }
    const own = (a.querySelector('img') || {}).src || '';
    return [h, t, own || (cards && t.trim() ? imgs[h] || '' : '')];
  });
}"""


def browser_md(url: str, cards: bool = False) -> str:
    """The page through the shared Playwright server on the NAS (Docker network scrape-net), as Jina-style Markdown.
    cards=True: take each product's whole card (price outside the link) and its photo from the photo link.
    Default Chromium, no stealth. '' on a challenge page, a 403/429 or any error."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning("playwright client not installed; browser route off")
        return ""
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect(PW_WS, timeout=30000)
            try:
                page = browser.new_context(locale="en-SG", timezone_id="Asia/Singapore").new_page()
                resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                if resp and resp.status in (403, 429):
                    log.warning("%s: HTTP %s in the browser, blocked, not retrying", url, resp.status)
                    _block(url, f"HTTP {resp.status}")
                    return ""
                try:
                    page.wait_for_selector(LINKS, timeout=20000)
                except Exception:   # noqa: BLE001  no products: decided below
                    pass
                m = CHALLENGE.search(page.title() + page.inner_text("body")[:3000] + page.content()[:5000])
                if m:
                    log.warning("%s: challenge page (%s), blocked, not worked around", url, m.group(0))
                    _block(url, f"challenge page ({m.group(0)})")
                    return ""
                links = page.eval_on_selector_all(LINKS, LINKS_JS, cards)
            finally:
                browser.close()
    except Exception as ex:   # noqa: BLE001
        log.warning("%s: browser route failed: %s", url, ex)
        vault.log_event("❌", "browser fetch failed", f"{type(ex).__name__} · {url}")
        return ""
    vault.log_event("🎭", "browser fetch", f"{len(links)} product links · {url}")
    return "\n".join(x for x in (_md_line(h, t, i) for h, t, i in links) if x)


def browser_file(url: str) -> tuple[bytes, str]:
    """One file (a logo) through the NAS browser, for servers Python cannot verify (an incomplete certificate chain:
    Chromium fetches the missing certificate, as Windows does). -> (body, content type); certificates are still checked."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.connect(PW_WS, timeout=30000)
        try:
            resp = browser.new_context().new_page().goto(url, timeout=30000)
            if not resp or resp.status != 200:
                raise ValueError(f"HTTP {resp.status if resp else 0}")
            return resp.body(), resp.headers.get("content-type", "").split(";")[0].strip()
        finally:
            browser.close()


def fetch(url: str, line: re.Pattern, sleep=time.sleep, route: str = "jina") -> str:
    """Jina first; when Jina cannot get the page (error, block, empty render), the NAS browser.
    route='browser' skips Jina; 'browser-card' also reads whole product cards (RedMart)."""
    md = _jina(url, line, sleep) if route == "jina" else ""
    if not md:
        if route == "jina":
            vault.log_event("↪️", "Jina could not read the page, trying the NAS browser", url)
        md = browser_md(url, cards=route == "browser-card")
        if md and not line.search(md):
            log.warning("%s: browser found no products", url)
            md = ""
    return md


def _jina(url: str, line: re.Pattern, sleep=time.sleep) -> str:
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
                log.warning("%s: HTTP %s from Jina, trying the NAS browser", url, ex.code)
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


def _row(store: str, name: str, price: float, was: float | None, promo: str, url: str, image: str = "",
         sold: int | None = None) -> dict | None:
    name = re.sub(r"\s+", " ", name).strip()
    if SKIP.search(name):
        return None
    unit_price, unit = per_unit(name, price)
    return dict(store=store, name=name, price=price, was=was, promo=promo.strip(), url=url, unit_price=unit_price, unit=unit,
                image=image if image.startswith("https://") else "", sold=sold)


def parse_fairprice(md: str, line: re.Pattern = FP_LINE, store: str = "FairPrice") -> list[dict]:
    """[promo ![img](..) $price [$was] name](url) lines: FairPrice (Jina or browser) and RedMart (browser) share this shape."""
    rows = []
    for body, url in line.findall(md):
        sold = SOLD_TOKEN.search(body)
        body = SOLD_TOKEN.sub("", body)
        img = re.search(r"!\[.*?\]\((https://[^)\s]+)\)", body)
        marked = re.sub(r"!\[.*?\]\(.*?\)", "@@", body)
        if "@@" not in marked:   # no photo: the promo is whatever comes before the first price
            # the first price that is not inside a promo phrase ("Save $0.30", "Spend $30", "Any 2 at $5", "2 for $5")
            first = re.search(r"(?<![Ss]ave )(?<![Ss]pend )(?<![Aa]t )(?<!for )\$\d+(?:\.\d+)?", marked)
            marked = marked[:first.start()] + "@@" + marked[first.start():] if first else marked
        promo, _, rest = marked.partition("@@")   # promo | img | prices + name
        rest = re.sub(r"\+\$\d+(?:\.\d+)?\s*deposit", "", rest)                       # BCRS bottle deposit is not price
        prices = PRICE.findall(rest)
        if not prices:
            continue
        name = re.sub(r"^(\s*\$\d+(?:\.\d+)?)+", "", rest).replace("@@", "").strip().replace("•", " | ")
        name = re.sub(r"\s*Add to cart.*$", "", name)   # promotions page appends the button text
        name = re.sub(r"\s+\d\.\d\(\d+\)?$", "", name)   # trailing "4.6(161)" rating
        r = _row(store, name, float(prices[0][1:]), float(prices[1][1:]) if len(prices) > 1 else None,
                 re.sub(r"\*", "", promo), url, img[1] if img else "", int(sold[1]) if sold else None)
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


def parse_redmart(md: str) -> list[dict]:
    return parse_fairprice(md, RM_LINE, "RedMart")


PARSERS = {"FairPrice": parse_fairprice, "Cold Storage": parse_coldstorage, "RedMart": parse_redmart}


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


def search(q: str, stores=STORES, sleep=time.sleep, full: bool = True) -> list[dict]:
    """Products for one search term across all stores, cheapest per unit first (items without a pack size last)."""
    rows, seen, first = [], set(), True
    for term in [q] + ALSO.get(q, []):
        for store, (url, line, route) in stores.items():
            if store in DAILY_ONLY and not full:
                continue
            if _blocked_until.get(_host(url), 0) > time.time():   # cooling off after a challenge page
                continue
            if store in ONCE_A_DAY and store not in _reading:   # read earlier today: not again this pass
                continue
            if not first:
                sleep(3)   # be polite: one page at a time, a few seconds apart
            first = False
            for r in PARSERS[store](fetch(url.format(urllib.parse.quote(term)), line, sleep, route)):
                if r["url"] not in seen and relevant(r["name"], q):   # the same product found under two spellings: once
                    seen.add(r["url"])
                    rows.append(r)
    rows.sort(key=lambda r: r["unit_price"] if r["unit_price"] is not None else 1e9)
    return rows
