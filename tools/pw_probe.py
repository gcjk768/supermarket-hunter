"""Probe which supermarket search pages render products in a plain headless Chromium (shared Playwright server on the NAS).
Rules: real Chromium, default user agent, no stealth, no cookies. A challenge/anti-bot page => reported as BLOCKED, never worked around.
Usage: PLAYWRIGHT_WS_URL=ws://playwright:3000/ python pw_probe.py
"""
import os, re, time
from playwright.sync_api import sync_playwright

SITES = [   # (name, url, product-link pattern on href)
    ("Giant q=",        "https://giant.sg/search?q=eggs", r"/product/"),
    ("Giant keyword=",  "https://giant.sg/search?keyword=eggs", r"/product/"),
    ("Giant /search/",  "https://giant.sg/search/eggs", r"/product/"),
    ("Giant category",  "https://giant.sg/category/dairy-chilled-eggs", r"/product/"),
    ("Cold Storage",    "https://coldstorage.com.sg/search?q=eggs", r"/product/"),
    ("Sheng Siong",     "https://shengsiong.com.sg/search?q=eggs", r"/product"),
    ("Prime offers",    "https://www.primesupermarket.com/advertised-offers/", r"wp-content/uploads/20\d\d/\d\d/[^\"' ]+\.(?:jpe?g|png)"),
    ("Hao Mart",        "https://www.haomart.com.sg/?s=eggs&post_type=product", r"/product/"),
    ("RedMart",         "https://redmart.lazada.sg/redmart-search/?q=eggs", r"/products/[^\"']+\.html"),
    ("Amazon Fresh",    "https://www.amazon.sg/s?k=eggs&i=amazonfresh", r"/dp/[A-Z0-9]{10}"),
]
# judged on what a human would see (title + visible text), not on script/CDN names
CHALLENGE = re.compile(r"incapsula|request unsuccessful|captcha|verify you are human|access denied|robot check|slide to verify|"
                       r"checking your browser|attention required|unusual traffic", re.I)

with sync_playwright() as p:
    browser = p.chromium.connect(os.environ.get("PLAYWRIGHT_WS_URL", "ws://playwright:3000/"))
    for name, url, pat in SITES:
        ctx = browser.new_context(locale="en-SG", timezone_id="Asia/Singapore")
        page = ctx.new_page()
        t0 = time.time()
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            status = resp.status if resp else 0
            page.wait_for_timeout(7000)   # let the SPA load its products
            hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
            links = sorted({h for h in hrefs if h and re.search(pat, h)})
            if "uploads" in pat:   # flyer images, not links
                links = sorted(set(re.findall(pat, page.content())))
            text = page.inner_text("body")
            title = page.title()
            prices = len(re.findall(r"\$\s?\d+\.\d\d", text))
            m = CHALLENGE.search(title + "\n" + text[:3000])
            block = bool(m) or status in (403, 429)
            verdict = f"BLOCKED ({m.group(0) if m else status}) do not bypass" if block else ("OK" if links else "no products rendered")
            print(f"{name:15} HTTP {status:<4} products={len(links):<3} prices={prices:<3} {time.time()-t0:4.1f}s  {verdict} | {title[:50]!r}")
            for h in links[:2]:
                print(f"{'':15}   e.g. {h[:110]}")
        except Exception as ex:   # noqa: BLE001
            print(f"{name:15} ERROR {type(ex).__name__}: {str(ex)[:90]}")
        finally:
            ctx.close()
        time.sleep(2)
    browser.close()
