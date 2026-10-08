"""Find each supermarket's official logo on its own home page (shared Playwright server on the NAS), and show how a
Cold Storage search page looks to a real browser. Rules: default Chromium, no stealth; a challenge page is reported, never bypassed.
Usage (NAS, on scrape-net): PLAYWRIGHT_WS_URL=ws://playwright:3000/ python pw_logos.py"""
import json
import os
import re

from playwright.sync_api import sync_playwright

HOMES = {"FairPrice": "https://www.fairprice.com.sg/", "Cold Storage": "https://coldstorage.com.sg/",
         "Sheng Siong": "https://shengsiong.com.sg/", "Giant": "https://giant.sg/", "Prime": "https://www.primesupermarket.com/",
         "Hao Mart": "https://www.haomart.com.sg/", "RedMart": "https://redmart.lazada.sg/", "Amazon Fresh": "https://www.amazon.sg/fresh"}
CHALLENGE = re.compile(r"incapsula|request unsuccessful|captcha|verify you are human|access denied|robot check|unusual traffic", re.I)
JS = """() => {
  const abs = u => { try { return new URL(u, location.href).href } catch(e) { return null } };
  const out = {og: null, icons: [], logos: []};
  const og = document.querySelector('meta[property="og:image"]'); if (og) out.og = abs(og.content);
  document.querySelectorAll('link[rel*="icon"]').forEach(l => out.icons.push({href: abs(l.href), sizes: l.sizes ? l.sizes.value : ''}));
  document.querySelectorAll('img, svg image').forEach(i => {
    const s = i.currentSrc || i.src || i.getAttribute('href') || '';
    const hint = [s, i.alt || '', i.className && i.className.baseVal !== undefined ? i.className.baseVal : (i.className || ''), i.id || '',
                  (i.closest('header,a[href="/"],a[class*=logo],div[class*=logo]') || {}).className || ''].join(' ').toLowerCase();
    if (/logo|brand/.test(hint)) out.logos.push({src: abs(s), alt: i.alt || '', w: i.naturalWidth || 0, h: i.naturalHeight || 0});
  });
  out.logos = out.logos.slice(0, 6); out.icons = out.icons.slice(0, 6); return out;
}"""

with sync_playwright() as p:
    browser = p.chromium.connect(os.environ.get("PLAYWRIGHT_WS_URL", "ws://playwright:3000/"))
    for name, url in HOMES.items():
        ctx = browser.new_context(locale="en-SG", timezone_id="Asia/Singapore")
        page = ctx.new_page()
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(5000)
            status = resp.status if resp else 0
            text = page.title() + "\n" + page.inner_text("body")[:3000]
            m = CHALLENGE.search(text)
            if m or status in (403, 429):
                print(json.dumps({"store": name, "blocked": m.group(0) if m else status}))
            else:
                print(json.dumps({"store": name, "status": status, **page.evaluate(JS)}))
        except Exception as ex:   # noqa: BLE001
            print(json.dumps({"store": name, "error": f"{type(ex).__name__}: {str(ex)[:80]}"}))
        finally:
            ctx.close()
    # how Cold Storage product links look in a real browser (for the Jina -> Playwright fallback)
    ctx = browser.new_context(locale="en-SG")
    page = ctx.new_page()
    page.goto("https://coldstorage.com.sg/search?q=eggs", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_selector("a[href*='/product/']", timeout=30000)
    links = page.eval_on_selector_all("a[href*='/product/']", "els => els.slice(0, 6).map(a => ({href: a.href, text: a.innerText, img: (a.querySelector('img') || {}).src || ''}))")
    print(json.dumps({"cs_links": links}, ensure_ascii=False))
    ctx.close()
    browser.close()
