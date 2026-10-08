"""Prime, Hao Mart, RedMart and Amazon Fresh in the NAS browser (Singapore IP): do product prices render?
Default Chromium, no stealth, no login. A captcha / challenge page is reported BLOCKED and left alone.
Usage on the NAS (scrape-net): python pw_probe6.py"""
import json
import os
import re

from playwright.sync_api import sync_playwright

CHALLENGE = re.compile(r"captcha|verify you are human|access denied|robot check|unusual traffic|press & hold|slide to verify|"
                       r"enter the characters you see|sorry, we just need to make sure", re.I)
PRICE = re.compile(r"(?:S\$|\$)\s?\d+(?:\.\d\d)?")
CARDS = """sel => [...document.querySelectorAll(sel)].slice(0, 4).map(a => ({href: a.href, text: a.innerText.replace(/\\s+/g, ' ').slice(0, 160),
          img: (a.querySelector('img') || {}).src || ''}))"""
PAGES = [
    ("Hao Mart search", "https://www.haomart.com.sg/?s=eggs&post_type=product", "a[href*='/product/']"),
    ("Hao Mart shop", "https://www.haomart.com.sg/shop/", "a[href*='/product/']"),
    ("Hao Mart promotions", "https://www.haomart.com.sg/promotions/", "a[href*='/product/']"),
    ("RedMart home", "https://redmart.lazada.sg/", "a[href*='.html']"),
    ("Lazada RedMart search", "https://www.lazada.sg/catalog/?q=eggs&service=RM", "a[href*='.html']"),
    ("Amazon Fresh search", "https://www.amazon.sg/s?k=eggs&i=amazonfresh", "div[data-asin] h2 a, a[href*='/dp/']"),
    ("Prime offers", "https://www.primesupermarket.com/advertised-offers/", "a[href$='.jpg'], a[href$='.png']"),
]

with sync_playwright() as p:
    browser = p.chromium.connect(os.environ.get("PLAYWRIGHT_WS_URL", "ws://playwright:3000/"))
    for name, url, sel in PAGES:
        ctx = browser.new_context(locale="en-SG", timezone_id="Asia/Singapore")
        page = ctx.new_page()
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(9000)
            body = page.inner_text("body") if page.query_selector("body") else ""
            m = CHALLENGE.search(page.title() + body[:4000])
            info = {"store": name, "status": resp.status if resp else 0, "final_url": page.url[:120], "title": page.title()[:60],
                    "challenge": m.group(0) if m else None, "prices": len(PRICE.findall(body)), "cards": page.evaluate(CARDS, sel)}
            if name == "RedMart home":   # where does its search go?
                info["search_links"] = sorted({h for h in page.eval_on_selector_all("a[href]", "e => e.map(a => a.href)")
                                               if re.search(r"catalog|search|\?q=", h)})[:6]
            if name == "Prime offers":
                info["flyer_images"] = sorted(set(re.findall(r"https://www\.primesupermarket\.com/wp-content/uploads/20\d\d/\d\d/[^\"' ]+\.(?:jpe?g|png)",
                                                             page.content())))[:8]
            print(json.dumps(info, ensure_ascii=False))
        except Exception as ex:   # noqa: BLE001
            print(json.dumps({"store": name, "error": f"{type(ex).__name__}: {str(ex)[:100]}"}))
        finally:
            ctx.close()
    browser.close()
