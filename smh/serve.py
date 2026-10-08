"""Background jobs that keep the family web page fresh. Nothing is sent anywhere: the web page is the only output.
Env: REPORT_AT ("daily 08:00" SGT: full refresh, every store + flyers), PROMO_CHECK_HOURS (quick price refresh, 0 = off),
DATA_DIR, VAULT_DIR, WEB_PORT."""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import cards, festive, flyers, scrape, vault, web
from .store import Store

log = logging.getLogger(__name__)
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class App:
    def __init__(self):
        self.store = Store(Path(os.environ.get("DATA_DIR", "data")))

    def refresh(self, full: bool = True, sleep=time.sleep) -> dict[str, int]:
        """Search every staple and save the rows. full = also the daily-only stores (FairPrice via the NAS browser) and the
        flyer sources. New promos near the best value are marked in `alerts`, so the page can show 🆕 on the day they appear."""
        cfg, today = self.store.config(), datetime.now(vault.TZ).date()
        vault.log_event("🛒", "refresh started", f"{'full' if full else 'prices'} · {len(cfg['staples'])} staples")
        try:   # FairPrice /promotions page (named exception): promo rows for the staples, read through Jina
            fp_rows = [i["row"] for i in flyers.fairprice(cfg["staples"]) if i["staple"]] if full else []
        except Exception as ex:   # noqa: BLE001
            log.warning("FairPrice promotions rows skipped: %s", ex)
            fp_rows = []
        counts, table, per_store = {}, [], {}
        for f in festive.active(today):
            vault.log_event(f["emoji"], f"festive season: {f['name']}", f"{f['day']:%a %d %b} – searching {', '.join(f['items'])}")
        for staple in list(dict.fromkeys(cfg["staples"] + festive.terms(today))):   # staples + this season's festive items
            rows = scrape.search(staple, full=full) + [r for r in fp_rows if flyers._match(r["name"], [staple])]
            rows.sort(key=lambda r: r["unit_price"] if r["unit_price"] is not None else 1e9)
            self.store.save(today, staple, rows)
            promos = 0
            for r in rows:
                per_store.setdefault(r["store"], {}).setdefault(staple, 0)
                per_store[r["store"]][staple] += 1
                if (r["promo"] or r["was"]) and cards.near(r, rows[0]):
                    promos += 1
                    key = r["promo"] or f"was {r['was']}"
                    if not self.store.promo_seen(r["url"], key, today):
                        self.store.promo_mark(r["url"], key, today)   # first seen today -> 🆕 on the Promotions tab
                        vault.log_event("🆕", "new promotion", f"{r['name'][:60]} · {cards.money(r['price'])} · {key} · {r['store']}", staple)
            if rows:
                b = rows[0]
                by_store = ", ".join(f"{st_} {n}" for st_, n in sorted({r["store"]: sum(1 for x in rows if x["store"] == r["store"])
                                                                         for r in rows}.items()))
                vault.log_event("🔎", staple, f"{len(rows)} products ({by_store}) · best {cards.money(b['price'])} "
                                              f"{b['name'][:50]} · {b['store']} · {promos} promos", staple)
                vault.history(staple, f"best {cards.money(b['price'])} ({cards.unit(b)}) {b['name'][:60]} · {b['store']}",
                              f"Best value for {staple}.")
                table.append(f"| {staple} | {b['name'][:50]} | {cards.money(b['price'])} | {cards.unit(b)} | {b['store']} | {promos} |")
            else:
                vault.log_event("⚠️", "no data", staple, staple)
                table.append(f"| {staple} | no data | | | | |")
            counts[staple] = len(rows)
            sleep(2)   # be polite
        for st_, by in per_store.items():
            vault.history(st_, f"{'full' if full else 'quick'} refresh: {sum(by.values())} products for {len(by)} staples",
                          f"{st_}: what Supermarket Hunter read from this supermarket.", folder="Stores")
        if full:
            vault.report("Daily prices", "| Staple | Best value | Price | Per unit | Store | Promos |\n|---|---|---|---|---|---|\n"
                         + "\n".join(table))
            try:
                sections = flyers.collect(cfg["staples"], self.store.cfg_path.parent, sleep)
                web.save_flyers(self.store.cfg_path.parent, sections)
                for src, items in sections.items():
                    vault.log_event("📰", f"flyers {src}", f"{len(items)} item(s), {sum(1 for i in items if i.get('staple'))} on the staples")
            except Exception:   # noqa: BLE001  prices are saved already; a broken flyer source must not undo that
                log.exception("flyers failed")
                vault.log_event("❌", "flyers failed", "see container log")
        vault.log_event("✅", "refresh done", f"{sum(1 for n in counts.values() if n)}/{len(counts)} staples with prices")
        vault.write_home()
        return counts


def next_run(spec: str, now: datetime) -> datetime:
    """'daily 08:00' or 'mon 08:00' -> the next such moment after now (same tz as now)."""
    day, hhmm = spec.lower().split()
    hour, minute = map(int, hhmm.split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if day == "daily":
        return target if target > now else target + timedelta(days=1)
    target += timedelta(days=(DAYS.index(day) - now.weekday()) % 7)
    if target <= now:
        target += timedelta(days=7)
    return target


def heartbeat(data_dir: Path) -> None:
    (data_dir / "heartbeat").write_text(str(time.time()))


def scheduler(app: App, spec: str) -> None:
    while True:
        nxt = next_run(spec, datetime.now(vault.TZ))
        log.info("next full refresh at %s", nxt)
        while (left := (nxt - datetime.now(vault.TZ)).total_seconds()) > 0:
            heartbeat(app.store.cfg_path.parent)
            time.sleep(min(left, 60))
        try:
            app.refresh(full=True)
        except Exception:   # noqa: BLE001
            log.exception("refresh failed")
            vault.log_event("❌", "refresh failed", "see container log")


def price_watcher(app: App, hours: float) -> None:
    """Quick refresh between the daily runs, so new promos reach the page within a few hours."""
    time.sleep(120)   # let the container settle / avoid clashing with a refresh at start-up
    while True:
        try:
            app.refresh(full=False)
        except Exception:   # noqa: BLE001
            log.exception("price refresh failed")
            vault.log_event("❌", "price refresh failed", "see container log")
        time.sleep(hours * 3600)


def main() -> None:
    app = App()
    if port := int(os.environ.get("WEB_PORT", "8000")):
        web.start(app.store.cfg_path.parent, port)
    spec = os.environ.get("REPORT_AT", "daily 08:00")
    threading.Thread(target=scheduler, args=(app, spec), daemon=True).start()
    hours = float(os.environ.get("PROMO_CHECK_HOURS", "3"))
    if hours > 0:
        threading.Thread(target=price_watcher, args=(app, hours), daemon=True).start()
    vault.log_event("🚀", "supermarket hunter started", f"full refresh {spec} SGT, prices every {hours:g} h, web :{port}")
    vault.write_home()
    threading.Event().wait()
