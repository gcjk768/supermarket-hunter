"""Telegram HTML cards in the house style (MOVIE HUNTER card): header line, one block per item, 🟢/🔴/⚪/🆕 markers,
every dynamic value HTML-escaped, split between blocks under 4096 chars."""
from __future__ import annotations

import html
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Singapore")
LIMIT = 4000
SECTION_TITLES = {"weekly": "🛒", "ask": "🔎", "staples": "📋", "help": "ℹ️", "stockup": "💰", "promo": "🔔", "flyers": "📰"}
STORE_EMOJI = {"FairPrice": "🟦", "Cold Storage": "🟥", "Sheng Siong": "🟧", "Giant": "🟩", "Prime": "🟪"}
EMOJI = {"rice": "🍚", "cooking oil": "🫒", "eggs": "🥚", "chicken": "🍗", "pork": "🥩", "fish fillet": "🐟", "prawns": "🦐",
         "choy sum": "🥬", "tofu": "🧈", "soy sauce": "🫙", "noodles": "🍜", "onion": "🧅", "garlic": "🧄", "tomato": "🍅",
         "milk": "🥛", "bread": "🍞", "potato": "🥔", "cabbage": "🥬", "carrot": "🥕", "beef": "🥩", "salmon": "🐟"}
HELP = (f"{SECTION_TITLES['help']} <b>SUPERMARKET HUNTER</b> · family grocery prices (Cold Storage online)\n\n"
        "🔎 <b>/ask</b> <code>item</code> · cheapest per 100g/100ml, deals, trusted brands ★\n"
        "🛒 <b>/report</b> · run the best-buys report now\n"
        "🔔 <b>/promos</b> · every current promo on the staples (new ones are also pushed automatically)\n"
        "📰 <b>/flyers</b> · FairPrice promotions, Sheng Siong flyer, Giant campaigns, singpromos posts (in the 08:00 report too)\n"
        "📋 <b>/staples</b> · the watched list · <code>/staples add kailan</code> · <code>/staples del tomato</code>\n"
        "ℹ️ <b>/shophelp</b> · this message\n\n"
        "<i>Report every morning at 08:00. Prices are the stores' online prices; shelf prices can differ by a few cents.</i>")


def esc(x) -> str:
    return html.escape(str(x), quote=False)


def today() -> str:
    return datetime.now(TZ).strftime("%a %d %b")


def header(kind: str, title: str, sub: str = "") -> str:
    return f"{SECTION_TITLES[kind]} <b>{esc(title)}</b>" + (f" · {esc(sub)}" if sub else "")


def money(x: float) -> str:
    return f"${x:,.2f}"


def unit(r: dict) -> str:
    return f"{money(r['unit_price'])}{r['unit']}" if r.get("unit_price") is not None else "no pack size"


def deal(r: dict) -> str:
    if not (r.get("promo") or r.get("was")):
        return ""
    bits = [r["promo"]] if r.get("promo") else []
    if r.get("was"):
        bits.append(f"was {money(r['was'])}")
    return " 🟢 <i>" + esc(" · ".join(bits)) + "</i>"


def item(r: dict, star: bool = False, marker: str = "") -> str:
    """One product block: name line, then price · unit · link."""
    name = ("★ " if star else "") + esc(r["name"][:80])
    return (f"{marker}<b>{name}</b>{deal(r)}\n"
            f"💰 {money(r['price'])} · {unit(r)}  ·  <a href=\"{esc(r['url'])}\">{esc(r.get('store', 'FairPrice'))}</a>")


def split(head: str, blocks: list[str], tail: str = "") -> list[str]:
    """Pack blocks into messages under LIMIT, never splitting inside a block; the tail goes on the last message."""
    msgs, cur = [], head
    for b in blocks:
        if len(cur) + len(b) + 2 > LIMIT:
            msgs.append(cur)
            cur = b
        else:
            cur += "\n\n" + b
    if tail and len(cur) + len(tail) + 2 > LIMIT:
        msgs.append(cur)
        cur = tail
    elif tail:
        cur += "\n\n" + tail
    msgs.append(cur)
    return msgs


def ask_cards(q: str, rows: list[dict], brands: list[str], n: int = 5) -> list[str]:
    head = header("ask", q, f"{len(rows)} found · cheapest per unit first")
    if not rows:
        return [head + "\n\n⚪ <i>Nothing found on FairPrice for that. Try another word (e.g. 'cai xin' for choy sum).</i>"]
    blocks = [item(r, star=is_brand(r, brands)) for r in rows[:n]]
    best_deal = next((r for r in rows if (r.get("promo") or r.get("was")) and near(r, rows[0])), None)
    if best_deal and best_deal not in rows[:n]:
        blocks.append("🏷 <b>Best deal further down</b>\n" + item(best_deal, star=is_brand(best_deal, brands)))
    return split(head, blocks)


def near(r: dict, best: dict) -> bool:
    """Same ballpark as the best value: same unit and at most 2x its unit price (ponytail: crude relevance filter)."""
    return (r.get("unit_price") is not None and best.get("unit_price") is not None
            and r["unit"] == best["unit"] and r["unit_price"] <= 2 * best["unit_price"])


def is_brand(r: dict, brands: list[str]) -> bool:
    return any(b.lower() in r["name"].lower() for b in brands)


def marker(now: float | None, prev: float | None) -> str:
    """Price change vs last week, coloured for the buyer: 🟢 fell, 🔴 rose, ⚪ same, 🆕 not seen last week."""
    if now is None:
        return ""
    if prev is None:
        return "🆕 "
    d = now - prev
    if abs(d) < 0.005:
        return "⚪ "
    return f"{'🟢' if d < 0 else '🔴'} <i>{'▼' if d < 0 else '▲'}{money(abs(d))}</i> "


STORE_ORDER = ["Cold Storage", "FairPrice", "Giant", "Sheng Siong", "Prime"]
STORE_NOTE = {"FairPrice": "promo page only"}   # what each store's rows cover, when it is not the whole shelf


def best_of(rows: list[dict], brands: list[str]) -> dict:
    """Cheapest per unit from a trusted brand, else the cheapest (rows are already sorted by unit price)."""
    return next((r for r in rows if is_brand(r, brands)), rows[0]) if brands else rows[0]


def weekly_cards(results: dict[str, dict]) -> list[str]:
    """results: staple -> {rows, brands, prev (url->unit), lows (url->min unit price 8 wk)}.
    Per staple: the best value at EACH store (🏆 on the overall winner), then one extra deal."""
    head = header("weekly", "SUPERMARKET HUNTER", f"best buys per store · {today()}")
    blocks, stock = [], []
    for staple, d in results.items():
        rows, brands, prev = d["rows"], d["brands"], d["prev"]
        em = EMOJI.get(staple, "🛒")
        if not rows:
            blocks.append(f"{em} <b>{esc(staple)}</b> · ⚪ <i>no data today</i>")
            continue
        stores = sorted({r["store"] for r in rows}, key=lambda s: STORE_ORDER.index(s) if s in STORE_ORDER else 99)
        bests = {s: best_of([r for r in rows if r["store"] == s], brands) for s in stores}
        winner = min(bests.values(), key=lambda r: r["unit_price"] if r["unit_price"] is not None else 1e9)
        lines = [f"{em} <b>{esc(staple.upper())}</b> · best at each store"]
        for s in stores:
            b = bests[s]
            note = f" <i>({esc(STORE_NOTE[s])})</i>" if s in STORE_NOTE else ""
            lines.append(f"{STORE_EMOJI.get(s, '🏬')} <b>{esc(s)}</b>{note}{' 🏆' if b is winner and len(stores) > 1 else ''}\n"
                         + item(b, star=is_brand(b, brands), marker=marker(b["unit_price"], prev.get(b["url"]))))
        promo = next((r for r in rows if (r.get("promo") or r.get("was")) and r not in bests.values()
                      and near(r, winner)), None)   # a deal on egg white is not a deal on eggs
        if promo:
            lines.append("🏷 deal: " + item(promo, star=is_brand(promo, brands)))
        blocks.append("\n".join(lines))
        low = d["lows"].get(winner["url"])
        if low is not None and winner["unit_price"] is not None and winner["unit_price"] <= low - 0.005:
            stock.append(f"{em} <b>{esc(winner['name'][:60])}</b> · {unit(winner)} · {esc(winner['store'])} · lowest in 8 weeks (was {money(low)})")
    tail = ""
    if stock:
        tail = "━━━━━━━━━━━━━━━━\n" + header("stockup", "STOCK UP", "cheapest in 8 weeks") + "\n\n" + "\n".join(stock)
    tail += ("\n\n" if tail else "") + ("<blockquote expandable>How to read this: for each staple, the best value at every store we can read, "
                                        "ranked by price per 100g / 100ml / piece so big and small packs compare fairly; 🏆 = cheapest of the stores. "
                                        "FairPrice rows come from its promotions page only. ★ = a brand the family trusts (edit in data/config.json). "
                                        "🟢 price fell since the last report, 🔴 rose, ⚪ same, 🆕 new. The link says which store; "
                                        "deals are their online promos, check the pack in store.</blockquote>")
    return split(head, blocks, tail)


def promo_cards(hits: list[tuple[str, dict, bool]], new_only: bool) -> list[str]:
    """hits: (staple, row, is_new). One block per promo, grouped by staple order; 🆕 marks promos not alerted before."""
    title = "NEW PROMOS" if new_only else "PROMOS NOW"
    head = header("promo", title, f"{len(hits)} on your staples · {today()}")
    if not hits:
        return [head + "\n\n⚪ <i>No promos on the staples right now.</i>"]
    blocks = [f"{EMOJI.get(s, '🛒')} <b>{esc(s.upper())}</b>\n" + item(r, star=star, marker="🆕 " if new else "")
              for s, r, new, star in hits]
    return split(head, blocks)


def flyer_cards(sections: dict[str, list[dict]], new_keys: set[str], max_other: int = 6) -> list[str]:
    """Promo items from the flyer sources. Staple matches first (all of them), then a few other standouts per source.
    🆕 = not shown in an earlier report. Items: {source, store, title, detail, url, staple, key, row?}."""
    total = sum(len(v) for v in sections.values())
    head = header("flyers", "FLYER PROMOS", f"{total} found · {today()}")
    blocks = []
    for src, items in sections.items():
        if not items:
            blocks.append(f"{STORE_EMOJI.get(src, '📰')} <b>{esc(src)}</b> · ⚪ <i>nothing readable today</i>")
            continue
        staple_hits = [i for i in items if i.get("staple")]
        others = [i for i in items if not i.get("staple")][:max_other]
        lines = [f"{STORE_EMOJI.get(items[0]['store'], '📰')} <b>{esc(src.upper())}</b> · {len(staple_hits)} on your staples, {len(items)} total"]
        for i in staple_hits + others:
            mark = "🆕 " if i["key"] in new_keys else ""
            tag = f" <i>({esc(i['staple'])})</i>" if i.get("staple") else ""
            if i.get("row"):   # a real product row: show price and unit like everywhere else
                lines.append(f"{mark}{item(i['row'])}{tag}")
            else:
                det = f" · {esc(i['detail'])}" if i.get("detail") else ""
                lines.append(f"{mark}<a href=\"{esc(i['url'])}\">{esc(i['title'][:90])}</a>{det}{tag}")
        blocks.append("\n".join(lines))
    tail = ("<blockquote expandable>Sources: FairPrice /promotions (page rows), Sheng Siong corporate RSS flyer read by Claude "
            "(check the flyer before relying on a multi-buy), Giant promotion page (campaigns, not shelf prices), singpromos.com posts. "
            "Prime and Hao Mart publish no readable prices.</blockquote>")
    return split(head, blocks, tail)


def staples_card(cfg: dict) -> str:
    return header("staples", "Staples", f"{len(cfg['staples'])} watched") + "\n\n" + \
        "\n".join(f"{EMOJI.get(s, '🛒')} {esc(s)}" + (f"  ·  ★ {esc(', '.join(cfg['brands'][s]))}" if cfg["brands"].get(s) else "")
                  for s in cfg["staples"]) + "\n\n<i>/staples add &lt;item&gt; · /staples del &lt;item&gt;</i>"
