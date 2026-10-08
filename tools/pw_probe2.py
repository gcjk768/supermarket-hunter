"""Follow-up probe (see pw_probe.py): find Giant's real search URL via its search box, list Prime's offer images,
try the Lazada catalog URL for RedMart and a plain Amazon search. Same rules: plain Chromium, stop on any challenge."""
import os, re, time
from playwright.sync_api import sync_playwright

CHALLENGE = re.compile(r"incapsula|request unsuccessful|captcha|verify you are human|access denied|robot check|slide to verify|"
                       r"checking your browser|attention required|unusual traffic", re.I)


def report(name, page, pat, t0):
    text, title = page.inner_text("body"), page.title()
    hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
    links = sorted({h for h in hrefs if h and re.search(pat, h)})
    m = CHALLENGE.search(title + "\n" + text[:3000])
    print(f"{name:14} url={page.url[:90]}\n{'':14} products={len(links)} prices={len(re.findall(r'\\$\\s?\\d+\\.\\d\\d', text))} "
          f"{time.time()-t0:4.1f}s {'BLOCKED '+m.group(0) if m else ('OK' if links else 'none')} | {title[:40]!r}")
    for h in links[:3]:
        print(f"{'':14}   {h[:110]}")
    return links


with sync_playwright() as p:
    browser = p.chromium.connect(os.environ.get("PLAYWRIGHT_WS_URL", "ws://playwright:3000/"))

    def ctx():
        return browser.new_context(locale="en-SG", timezone_id="Asia/Singapore")

    # Giant: use the site's own search box, then read the URL it navigates to
    c = ctx(); page = c.new_page(); t0 = time.time()
    try:
        page.goto("https://giant.sg/", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(6000)
        box = page.query_selector("input[type='search'], input[placeholder*='earch'], input[name*='search'], input[name='q']")
        print("Giant search box:", bool(box), (box.get_attribute("placeholder") if box else None))
        if box:
            box.click(); box.fill("eggs"); box.press("Enter")
            page.wait_for_timeout(8000)
        report("Giant", page, r"/product/", t0)
    except Exception as ex:   # noqa: BLE001
        print("Giant ERROR", type(ex).__name__, str(ex)[:100])
    c.close(); time.sleep(2)

    # Prime: which images are on the offers page (flyers?)
    c = ctx(); page = c.new_page(); t0 = time.time()
    try:
        page.goto("https://www.primesupermarket.com/advertised-offers/", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(8000)
        imgs = page.eval_on_selector_all("img", "els => els.map(e => e.currentSrc || e.src)")
        imgs = sorted({i for i in imgs if i and not re.search(r"logo|favicon|icon|banner", i, re.I)})
        print(f"Prime offers   images={len(imgs)} {time.time()-t0:4.1f}s")
        for i in imgs[:12]:
            print(f"{'':14}   {i[:120]}")
        print(f"{'':14} text sample: {page.inner_text('body')[:300]!r}")
    except Exception as ex:   # noqa: BLE001
        print("Prime ERROR", type(ex).__name__, str(ex)[:100])
    c.close(); time.sleep(2)

    for name, url, pat in [("RedMart/Lazada", "https://www.lazada.sg/catalog/?q=eggs&from=redmart", r"/products/[^\"']+\.html"),
                           ("Amazon plain", "https://www.amazon.sg/s?k=eggs", r"/dp/[A-Z0-9]{10}"),
                           ("Sheng Siong", "https://shengsiong.com.sg/", r"/product")]:
        c = ctx(); page = c.new_page(); t0 = time.time()
        try:
            r = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(7000)
            print(f"{name:14} HTTP {r.status if r else 0}")
            report(name, page, pat, t0)
            if name == "Sheng Siong":
                print(f"{'':14} html={len(page.content())}B text={len(page.inner_text('body'))}B "
                      f"iframes={page.eval_on_selector_all('iframe', 'els => els.map(e => e.src)')[:2]}")
        except Exception as ex:   # noqa: BLE001
            print(name, "ERROR", type(ex).__name__, str(ex)[:100])
        c.close(); time.sleep(2)
    browser.close()
