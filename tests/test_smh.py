"""The smallest checks that fail if the logic breaks: unit price maths, markdown parsing, card limits, access rule."""
from datetime import date, datetime

from smh import cards, scrape, serve
from smh.store import Store

MD = """[Save $0.30 ![Image](https://x/a.jpg) $7.80 $8.10 FairPrice Thailand Rice - Fragrant White 5kg](https://www.fairprice.com.sg/product/rice-1)
[![Image](https://x/b.jpg) $17.95 FairPrice Jasmine Rice 10kg](https://www.fairprice.com.sg/product/rice-2)
[![Image](https://x/c.jpg) $6.00 +$0.10 deposit [BCRS] Farm Fresh Milk 2L](https://www.fairprice.com.sg/product/milk-1)
[![Image](https://x/d.jpg) $2.52 Chinese Flowering Cabbage Seeds 1 S](https://www.fairprice.com.sg/product/seed-1)
[Any 2 At $11.90 ![Image](https://x/e.jpg) $7.35 Ad CP Frozen Ready Meal 350g](https://www.fairprice.com.sg/product/ad-1)
[5% off $3.70 $3.90 Farm Choice Singapore Fresh Eggs 12s 660g](https://coldstorage.com.sg/product/farm-choice-eggs)
[$7.95 Farm Choice Farm Fresh Eggs 30s 1650g](https://coldstorage.com.sg/product/farm-choice-30)
"""


def test_per_unit():
    assert scrape.per_unit("Milo 24 x 200ml", 24) == (0.5, "/100ml")
    assert scrape.per_unit("Rice 5kg", 10) == (0.2, "/100g")
    assert scrape.per_unit("Eggs 10s", 5) == (0.5, "each")
    assert scrape.per_unit("Something", 5) == (None, "")
    u, lbl = scrape.per_unit("Farm Choice Eggs 12s 660g", 3.30)
    assert (round(u, 2), lbl) == (0.5, "/100g")   # weight beats count


def test_parse():
    rows = scrape.parse(MD)
    assert [r["name"][:20] for r in rows] == ["FairPrice Thailand R", "FairPrice Jasmine Ri", "[BCRS] Farm Fresh Mi",
                                              "Farm Choice Singapor", "Farm Choice Farm Fre"]   # seeds + Ad skipped
    assert rows[0]["price"] == 7.80 and rows[0]["was"] == 8.10 and rows[0]["promo"] == "Save $0.30" and rows[0]["store"] == "FairPrice"
    assert rows[2]["price"] == 6.00 and rows[2]["was"] is None and round(rows[2]["unit_price"], 2) == 0.30
    cs = rows[3]
    assert cs["store"] == "Cold Storage" and cs["price"] == 3.70 and cs["was"] == 3.90 and cs["promo"] == "5% off"
    assert rows[4]["promo"] == "" and rows[4]["was"] is None
    assert "Cold Storage</a>" in cards.item(cs) and "FairPrice</a>" in cards.item(rows[0])


def test_search_merges_stores(monkeypatch):
    monkeypatch.setattr(scrape, "fetch", lambda url, line, sleep: MD)
    monkeypatch.setattr(scrape, "relevant", lambda name, q: True)   # merging only; relevance has its own test
    two = {"FairPrice": ("https://example.test/fp?q={}", scrape.FP_LINE), "Cold Storage": ("https://example.test/cs?q={}", scrape.CS_LINE)}
    rows = scrape.search("eggs", stores=two, sleep=lambda s: None)
    assert {r["store"] for r in rows} == {"FairPrice", "Cold Storage"} and len(rows) == 5   # each parser keeps its own store's lines
    assert [r["unit_price"] is None for r in rows] == sorted(r["unit_price"] is None for r in rows)   # sized first


def test_cards_and_store(tmp_path):
    st = Store(tmp_path)
    rows = scrape.parse(MD)
    st.save(date(2026, 10, 1), "rice", [dict(r, unit_price=r["unit_price"] + 0.02) for r in rows])   # last week dearer
    st.save(date(2026, 10, 8), "rice", rows)
    prev = st.previous("rice", date(2026, 10, 8))
    assert round(prev[rows[0]["url"]], 3) == round(rows[0]["unit_price"] + 0.02, 3)
    assert st.low(rows[0]["url"], date(2026, 10, 8)) > rows[0]["unit_price"]
    texts = cards.weekly_cards({"rice": dict(rows=rows, brands=["FairPrice"], prev=prev,
                                             lows={rows[0]["url"]: st.low(rows[0]["url"], date(2026, 10, 8))})})
    assert all(len(t) <= 4096 for t in texts) and "🟢" in texts[0] and "STOCK UP" in texts[-1]
    assert texts[0].count("<b>FairPrice</b>") == 1 and texts[0].count("<b>Cold Storage</b>") == 1 and "🏆" in texts[0]   # one best per store
    assert "&amp;" in cards.ask_cards("a & b", rows, [])[0]   # escaped
    assert cards.marker(1.0, None) == "🆕 " and cards.marker(1.0, 1.0) == "⚪ " and cards.marker(1.1, 1.0).startswith("🔴")


def test_promo_alert_once(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(scrape, "search", lambda q: scrape.parse(MD))
    app = serve.App()
    first = app.promos(new_only=True, sleep=lambda s: None)
    assert first and "🆕" in first[0] and "Save $0.30" in first[0]
    assert app.promos(new_only=True, sleep=lambda s: None) == []            # same promo: not announced twice
    assert "Save $0.30" in app.promos(new_only=False, sleep=lambda s: None)[0]   # /promos still lists it


FP_PROMO = ("[Save $2.75 ![Image 6: Seara](https://x/a.jpg) $8.95$11.70 ![Image 7: campaign label](https://x/b.jpg)"
            "Seara Frozen Chicken - Legs (Boneless) 2kg•Halal 4.4(715) Add to cart](https://www.fairprice.com.sg/product/seara-13176928)\n")
SS_RSS = """<?xml version="1.0"?><rss xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
<item><title>Monthly Promotion 02 Oct 2026 – 15 Oct 2026</title><link>https://corporate.shengsiong.com.sg/p/1</link>
<pubDate>Thu, 01 Oct 2026 14:01:17 +0000</pubDate><content:encoded><![CDATA[<img src="https://cdn.example/SSAD26-scaled.jpg">]]></content:encoded></item>
<item><title>CHAS Card Discounts</title><link>https://corporate.shengsiong.com.sg/p/2</link><pubDate>Wed, 09 Sep 2026 14:47:46 +0000</pubDate>
<content:encoded><![CDATA[<img src="https://cdn.example/chas.jpg">]]></content:encoded></item></channel></rss>"""
SP_MD = ("[FairPrice 50% Off New Zealand Natural Ice Cream for Members Till 14 Oct 2026](https://singpromos.com/dining-restaurants-food/fairprice-50-off-306001/)\n"
         "[McDonald’s S’pore Spicy Chicken McNuggets Return from 8 Oct 2026](https://singpromos.com/dining-restaurants-food/mcd-306002/)\n")


def test_flyer_sources(monkeypatch, tmp_path):
    from smh import flyers
    rows = scrape.parse_fairprice(FP_PROMO)
    assert rows[0]["name"] == "Seara Frozen Chicken - Legs (Boneless) 2kg | Halal" and rows[0]["was"] == 11.70 and rows[0]["unit"] == "/100g"
    monkeypatch.setattr(flyers, "_get", lambda url, timeout=90: SS_RSS.encode() if "feed" in url else SP_MD.encode())
    from datetime import timezone
    ss = flyers.shengsiong_flyers(now=datetime(2026, 10, 8, tzinfo=timezone.utc))
    assert [f["image"] for f in ss] == ["https://cdn.example/SSAD26-scaled.jpg"]   # the CHAS post is too old / not a promo
    sp = flyers.singpromos(["ice cream", "rice"], sleep=lambda s: None)
    assert len(sp) == 1 and sp[0]["store"] == "FairPrice" and sp[0]["staple"] == "ice cream"   # McDonald's out; same url once
    assert flyers._match("FairPrice Prices You'll Love", ["rice"]) == "" and flyers._match("Thai Rice 5kg", ["rice"]) == "rice"
    texts = cards.flyer_cards({"FairPrice": [dict(source="FairPrice promotions", store="FairPrice", title=rows[0]["name"], url=rows[0]["url"],
                                                  row=rows[0], detail="", staple="chicken", key="k1")], "Giant": []}, {"k1"})
    assert "🆕" in texts[0] and "nothing readable" in texts[0] and all(len(t) <= 4096 for t in texts)


def test_claude_parse():
    from smh import claude
    assert claude.parse_result('{"type":"result","subtype":"success","is_error":false,"result":"{\\"items\\":[]}"}') == {"items": []}
    try:
        claude.parse_result('{"type":"result","subtype":"success","is_error":false,"result":"You have hit your weekly limit"}')
        assert False
    except claude.ClaudeFailure as ex:
        assert "limit" in str(ex)


def test_allowed_and_schedule(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100123")
    monkeypatch.setenv("TELEGRAM_THREAD_ID", "77")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "5")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    app, TOPIC = serve.App(), 77   # fake ids
    assert app.allowed({"chat": {"id": -100123, "type": "supergroup"}, "message_thread_id": TOPIC})
    assert not app.allowed({"chat": {"id": -100123, "type": "supergroup"}, "message_thread_id": TOPIC + 1})
    assert app.allowed({"chat": {"id": 5, "type": "private"}, "from": {"id": 5}})
    assert not app.allowed({"chat": {"id": 6, "type": "private"}, "from": {"id": 6}})
    assert serve.next_run("mon 08:00", datetime(2026, 10, 8, 9, 0)) == datetime(2026, 10, 12, 8, 0)   # Thu -> next Mon
    assert serve.next_run("thu 10:00", datetime(2026, 10, 8, 9, 0)) == datetime(2026, 10, 8, 10, 0)   # later today
    assert serve.next_run("daily 08:00", datetime(2026, 10, 8, 9, 0)) == datetime(2026, 10, 9, 8, 0)   # tomorrow
    assert serve.next_run("daily 08:00", datetime(2026, 10, 8, 7, 0)) == datetime(2026, 10, 8, 8, 0)   # today


def test_web_page(tmp_path):
    from smh import web
    st = Store(tmp_path)
    st.save_config({"staples": ["eggs", "tofu"], "brands": {}})
    rows = [dict(r, name=r["name"] + ' <script>"') for r in scrape.parse(MD) if "Eggs" in r["name"]]
    rows.append(dict(rows[0], url="javascript:alert(1)", unit_price=99.0))
    st.save(date(2026, 10, 8), "eggs", rows)
    st.db.close()
    page = web.page(tmp_path)
    assert "Farm Choice" in page and "Open at Cold Storage" in page   # winner card links to its store
    assert '<script>"' not in page and "&lt;script&gt;" in page and "javascript:" not in page        # scraped text escaped, only http(s) hrefs
    assert "No prices today" in page                                   # tofu has no rows
    eggs = page.split('id="s0"')[1].split('id="s1"')[0]
    assert 1 < eggs.count('class="cell"') + 1 <= web.PER_STAPLE                    # winner + more cards, capped
    assert "Prices checked Thu 08 Oct" in page            # the data date, not today


def test_product_photo():
    md = ("[![Image 6: Eggs 10s](https://coldstorage.com.sg/_next/image?url=x&w=640)](https://coldstorage.com.sg/product/eggs-1)\n"
          "[$3.75 Chew's Eggs 10s 600g](https://coldstorage.com.sg/product/eggs-1)\n")
    assert scrape.parse_coldstorage(md)[0]["image"] == "https://coldstorage.com.sg/_next/image?url=x&w=640"
    assert scrape.parse(MD)[0]["image"].startswith("https://x/")   # FairPrice inline image


def test_relevant():
    assert not scrape.relevant("Farm Choice Farm Fresh Eggs 10s 550g", "chicken")   # a different staple
    assert not scrape.relevant("China Loose Garlic 500g", "onion")
    assert scrape.relevant("Simply Finest Baby Cai Xin 300g", "choy sum")           # synonym, names no other item
    assert scrape.relevant("Blush Cocktail Truss Tomatoes 250g", "tomato")          # plural
    assert scrape.relevant("Kampong Chicken Eggs 10s", "eggs")                     # names both: kept


def test_bought_and_top10(tmp_path):
    from smh import web
    st = Store(tmp_path)
    rows = scrape.parse(MD)
    st.save(date(2026, 10, 8), "eggs", rows)
    u = rows[0]["url"]
    assert st.toggle_bought(u, date(2026, 10, 8)) == {"bought": True, "times": 1}
    assert st.toggle_bought(u, date(2026, 10, 8)) == {"bought": False, "times": 0}     # second tap same day = undo
    st.toggle_bought(u, date(2026, 10, 7)); st.toggle_bought(u, date(2026, 10, 8)); st.toggle_bought(rows[1]["url"], date(2026, 10, 8))
    assert [t["times"] for t in st.top_bought()] == [2, 1]
    assert st.toggle_bought("https://evil.test/x", date(2026, 10, 8)) is None             # only products we have priced
    st.db.close()
    assert "bought 2×" in web.page(tmp_path)
