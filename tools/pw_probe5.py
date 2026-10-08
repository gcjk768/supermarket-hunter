"""Can the NAS browser read Giant and Sheng Siong product prices by normal browsing? Default Chromium, no stealth.
A page that still shows a captcha / challenge after loading normally is reported BLOCKED and left alone.
Usage on the NAS (scrape-net): python pw_probe5.py"""
import json
import os
import re

from playwright.sync_api import sync_playwright

CHALLENGE = re.compile(r"incapsula|request unsuccessful|captcha|verify you are human|access denied|robot check|unusual traffic|"
                       r"press & hold|slide to verify", re.I)
PRICE = re.compile(r"\$\s?\d+\.\d\d")


def look(page, url, wait=12000):
    resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(wait)   # let the page finish loading on its own, as a person would wait
    body = page.inner_text("body") if page.query_selector("body") else ""
    html = page.content()
    m = CHALLENGE.search(page.title() + body[:3000] + html[:6000])
    links = page.eval_on_selector_all("a[href]", "els => els.map(a => a.href)")
    return {"url": page.url, "status": resp.status if resp else 0, "title": page.title()[:60], "challenge": m.group(0) if m else None,
            "prices": len(PRICE.findall(body)), "product_links": len({h for h in links if re.search(r"/product", h)}),
            "body": re.sub(r"\s+", " ", body)[:240]}, links


with sync_playwright() as p:
    browser = p.chromium.connect(os.environ.get("PLAYWRIGHT_WS_URL", "ws://playwright:3000/"))
    ctx = browser.new_context(locale="en-SG", timezone_id="Asia/Singapore")
    page = ctx.new_page()
    # Giant: start at the home page and follow its own menu links
    info, links = look(page, "https://giant.sg/", 8000)
    print(json.dumps({"giant_home": info}, ensure_ascii=False))
    cands = sorted({h for h in links if re.search(r"giant\.sg/(category|categories|shop|c/|search|products?)", h)})[:15]
    print(json.dumps({"giant_candidate_links": cands}))
    for u in cands[:4] + ["https://giant.sg/search?keyword=eggs", "https://giant.sg/search/eggs"]:
        try:
            info, _ = look(page, u, 8000)
            print(json.dumps({"giant": info}, ensure_ascii=False))
        except Exception as ex:   # noqa: BLE001
            print(json.dumps({"giant": u, "error": str(ex)[:100]}))
    # Sheng Siong: online shop (wait for its check to finish on its own) and the corporate flyer feed
    for u in ["https://shengsiong.com.sg/", "https://shengsiong.com.sg/search/eggs"]:
        try:
            info, _ = look(page, u, 20000)
            print(json.dumps({"shengsiong": info}, ensure_ascii=False))
        except Exception as ex:   # noqa: BLE001
            print(json.dumps({"shengsiong": u, "error": str(ex)[:100]}))
    try:
        r = page.goto("https://corporate.shengsiong.com.sg/category/promotions/feed/", timeout=45000)
        print(json.dumps({"ss_feed": r.status if r else 0, "start": (r.text() if r else "")[:300]}, ensure_ascii=False))
    except Exception as ex:   # noqa: BLE001
        print(json.dumps({"ss_feed_error": str(ex)[:100]}))
    browser.close()
