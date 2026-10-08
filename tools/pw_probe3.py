"""Giant only: close any pop-up, type in the search box, and record which URLs/APIs the site calls for the search.
Plain Chromium, default UA; stop if a challenge appears."""
import os, re, time
from playwright.sync_api import sync_playwright

calls = []
with sync_playwright() as p:
    browser = p.chromium.connect(os.environ.get("PLAYWRIGHT_WS_URL", "ws://playwright:3000/"))
    c = browser.new_context(locale="en-SG", timezone_id="Asia/Singapore")
    page = c.new_page()
    page.on("response", lambda r: calls.append((r.status, r.request.resource_type, r.url)) if re.search(r"search|product|catalog|api", r.url, re.I) else None)
    page.goto("https://giant.sg/", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(6000)
    print("title:", page.title(), "| url:", page.url)
    for sel in ["button[aria-label*='lose']", "button:has-text('Close')", "[class*='close']", "[class*='modal'] button"]:
        for el in page.query_selector_all(sel)[:3]:
            try:
                el.click(timeout=1500); print("clicked", sel)
            except Exception:   # noqa: BLE001
                pass
    page.keyboard.press("Escape")
    page.wait_for_timeout(1000)
    forms = page.eval_on_selector_all("form", "els => els.map(e => [e.action, e.method, e.querySelector('input') && e.querySelector('input').name])")
    print("forms:", forms[:5])
    sel = "input[placeholder*='Search for a product']"
    try:
        page.fill(sel, "eggs", force=True, timeout=10000)
        page.press(sel, "Enter")
        page.wait_for_timeout(9000)
    except Exception as ex:   # noqa: BLE001
        print("type failed:", type(ex).__name__, str(ex)[:120])
        try:
            page.evaluate("""() => { const i = document.querySelector("input[placeholder*='Search for a product']");
                              i.value = 'eggs'; i.dispatchEvent(new Event('input', {bubbles: true}));
                              i.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', bubbles: true}));
                              if (i.form) i.form.submit(); }""")
            page.wait_for_timeout(9000)
        except Exception as ex2:   # noqa: BLE001
            print("js submit failed:", str(ex2)[:120])
    print("after search url:", page.url)
    hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
    links = sorted({h for h in hrefs if h and "/product/" in h})
    print("product links:", len(links), links[:3])
    text = page.inner_text("body")
    print("prices:", len(re.findall(r"\$\s?\d+\.\d\d", text)), "| challenge:", bool(re.search(r"incapsula|captcha|access denied|verify you are human", text, re.I)))
    print("network calls matching search/product/api:")
    for s, t, u in calls[:25]:
        print(f"  {s} {t:8} {u[:140]}")
    c.close(); browser.close()
