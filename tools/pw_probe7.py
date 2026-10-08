"""Details for wiring RedMart (product card text on a search page), Amazon (general search from Singapore) and
Prime (full-size flyer image). Default Chromium, no stealth, no login. Usage on the NAS (scrape-net): python pw_probe7.py"""
import json
import os
import re

from playwright.sync_api import sync_playwright

CARD = """() => [...document.querySelectorAll("a[href*='/products/']")].filter(a => a.innerText.trim()).slice(0, 5).map(a => {
  let el = a; for (let i = 0; i < 6 && el && !/\\$/.test(el.innerText); i++) el = el.parentElement;
  const img = el ? (el.querySelector('img') || {}).src : '';
  return {href: a.href.split('?')[0], card: el ? el.innerText : '', img: img || ''};
})"""
with sync_playwright() as p:
    browser = p.chromium.connect(os.environ.get("PLAYWRIGHT_WS_URL", "ws://playwright:3000/"))
    ctx = browser.new_context(locale="en-SG", timezone_id="Asia/Singapore")
    page = ctx.new_page()
    page.goto("https://www.lazada.sg/catalog/?q=eggs&service=RM", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(9000)
    print(json.dumps({"redmart_url": page.url[:120], "title": page.title()[:60], "cards": page.evaluate(CARD)}, ensure_ascii=False))
    page.goto("https://www.amazon.sg/s?k=eggs", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(6000)
    body = page.inner_text("body")
    print(json.dumps({"amazon_title": page.title()[:60], "deliver": re.findall(r"Deliver to [^\n]{0,30}", body)[:1],
                      "prices": len(re.findall(r"S\$\s?\d+\.\d\d", body)), "fresh_mentions": len(re.findall(r"Amazon Fresh", body)),
                      "results": [re.sub(r"\s+", " ", t)[:140] for t in page.eval_on_selector_all("div[data-component-type='s-search-result']",
                                                                                                 "els => els.slice(0, 3).map(e => e.innerText)")],
                      "captcha": bool(re.search(r"captcha|enter the characters|robot", body[:3000], re.I))}, ensure_ascii=False))
    page.goto("https://www.primesupermarket.com/advertised-offers/", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(4000)
    imgs = page.eval_on_selector_all("img", "els => els.map(i => [i.currentSrc || i.src, i.naturalWidth, i.naturalHeight])")
    print(json.dumps({"prime_imgs": [x for x in imgs if "uploads/20" in x[0] and "favicon" not in x[0] and "logo" not in x[0]][:6],
                      "prime_text": re.sub(r"\s+", " ", page.inner_text("body"))[:300]}, ensure_ascii=False))
    browser.close()
