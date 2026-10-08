"""Cold Storage category prices + Giant promo list via Jina Reader (robots.txt allows both).
Usage: python cs_giant_scrape.py [cold-storage-category-slug ...]   (default: dairy-chilled-eggs-1583)
ponytail: Giant search/category pages render no products in Jina (JS-loaded), so only its promo page is read.
Sheng Siong: site renders empty in Jina (iframe/JS) -> not scraped; use Exa snippets or paste by hand.
"""
import csv, re, sys, time, urllib.request

JINA = "https://r.jina.ai/"
CS = re.compile(r"^\[(?:(\d+)% off )?(\$\d+(?:\.\d+)?) (?:(\$\d+(?:\.\d+)?) )?([^\]]+)\]\((https://coldstorage\.com\.sg/product/[^)]+)\)", re.M)
GIANT = re.compile(r"^\[\s*(.+?) Subtitle (.+?)\]\((https://giant\.sg/[^) ]+)", re.M)


def fetch(url, selector):
    req = urllib.request.Request(JINA + url, headers={"X-Wait-For-Selector": selector})
    for attempt in (1, 2):  # one retry on transient failure only
        try:
            return urllib.request.urlopen(req, timeout=90).read().decode()
        except Exception as e:
            if attempt == 2:
                print(f"{url} failed: {e}", file=sys.stderr)
    return ""


def cold_storage(slug):
    md = fetch(f"https://coldstorage.com.sg/category/{slug}", "a[href*='/product/']")
    return [dict(store="Cold Storage", name=n.strip(), price=p, was=w or "", promo=f"{d}% off" if d else "", url=u)
            for d, p, w, n, u in CS.findall(md)]


def giant():
    md = fetch("https://giant.sg/promotion-page", "a[href*='promotion']")
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md)  # images first: they break the title match
    return [dict(store="Giant", name=t, price="", was="", promo=re.sub(r"\*", "", d), url=u)
            for t, d, u in GIANT.findall(md)]


if __name__ == "__main__":
    rows = []
    for slug in sys.argv[1:] or ["dairy-chilled-eggs-1583"]:
        rows += cold_storage(slug); time.sleep(2)  # be polite
    rows += giant()
    with open("prices_cs_giant.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, ["store", "name", "price", "was", "promo", "url"])
        w.writeheader(); w.writerows(rows)
    for r in rows:
        print(f"{r['store']:12} {r['price']:>7} {('was '+r['was']) if r['was'] else '':10} {r['promo'][:70]:70} {r['name'][:45]}")
    assert any(r["store"] == "Cold Storage" for r in rows) and any(r["store"] == "Giant" for r in rows), "a store returned nothing"
