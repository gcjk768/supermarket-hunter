"""What product links look like in a real browser on each store's search page (for the Jina -> Playwright fallback).
Default Chromium, no stealth; a challenge page is reported, never bypassed. Usage on the NAS (scrape-net): python pw_probe4.py"""
import json
import os
import re

from playwright.sync_api import sync_playwright

PAGES = {"FairPrice": ("https://www.fairprice.com.sg/search?query=eggs", "/product/"),
         "Giant": ("https://giant.sg/search?q=eggs", "/product/"),
         "Sheng Siong": ("https://shengsiong.com.sg/search/eggs", "/product/")}
CHALLENGE = re.compile(r"incapsula|request unsuccessful|captcha|verify you are human|access denied|robot check|unusual traffic", re.I)
with sync_playwright() as p:
    browser = p.chromium.connect(os.environ.get("PLAYWRIGHT_WS_URL", "ws://playwright:3000/"))
    for name, (url, pat) in PAGES.items():
        ctx = browser.new_context(locale="en-SG", timezone_id="Asia/Singapore")
        page = ctx.new_page()
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_selector(f"a[href*='{pat}']", timeout=15000)
            except Exception:   # noqa: BLE001
                pass
            body = page.inner_text("body")
            html = page.content()
            m = CHALLENGE.search(page.title() + body[:3000] + html[:5000])
            links = page.eval_on_selector_all(f"a[href*='{pat}']", "els => els.slice(0, 4).map(a => ({href: a.href, text: a.innerText, img: (a.querySelector('img') || {}).src || ''}))")
            print(json.dumps({"store": name, "status": resp.status if resp else 0, "challenge": m.group(0) if m else None,
                              "n": len(links), "prices_on_page": len(re.findall(r"\$\s?\d+\.\d\d", body)), "links": links,
                              "body": body[:300]}, ensure_ascii=False))
        except Exception as ex:   # noqa: BLE001
            print(json.dumps({"store": name, "error": f"{type(ex).__name__}: {str(ex)[:100]}"}))
        finally:
            ctx.close()
    browser.close()
