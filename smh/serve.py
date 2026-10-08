"""Background jobs that keep the family web page fresh. Nothing is sent anywhere: the web page is the only output.
Env: REPORT_AT ("daily 08:00" SGT: the daily update, every store + flyers, must happen), PROMO_CHECK_HOURS (how often to
check for something new, default 1 h), FULL_EVERY_HOURS (every store + flyers this often between 07:00 and 22:00, default 3),
DATA_DIR, VAULT_DIR, WEB_PORT."""
from __future__ import annotations

import json
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
        self.lock = threading.Lock()   # the daily update and the regular checks take turns, never overlap
        self.last_full = last_daily(self.store.cfg_path.parent)   # when every store + flyers were last read

    def refresh(self, full: bool = True, sleep=time.sleep) -> dict[str, int]:
        with self.lock:
            return self._refresh(full, sleep)

    def _refresh(self, full: bool = True, sleep=time.sleep) -> dict[str, int]:
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
        if full:
            scrape.start_pass()   # once-a-day stores (RedMart) join this pass only if not read in the last 20 h
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
        ok = sum(1 for n in counts.values() if n)
        vault.log_event("✅", "refresh done", f"{ok}/{len(counts)} staples with prices")
        if full and ok * 2 >= len(counts):   # at least half the items found: today's daily update counts as done
            mark_daily(self.store.cfg_path.parent, ok, len(counts))
        if full:
            self.last_full = datetime.now(vault.TZ)
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


def mark_daily(data_dir: Path, ok: int, total: int) -> None:
    (data_dir / "last_daily.json").write_text(json.dumps({"at": datetime.now(vault.TZ).isoformat(), "ok": ok, "total": total}))


def last_daily(data_dir: Path) -> datetime | None:
    """When the last successful daily (full) update finished, or None."""
    try:
        return datetime.fromisoformat(json.loads((data_dir / "last_daily.json").read_text())["at"])
    except Exception:   # noqa: BLE001
        return None


def due(spec: str, now: datetime, last: datetime | None) -> bool:
    """Has the most recent scheduled update time passed without a successful update since? (catch-up after a restart)"""
    prev = next_run(spec, now) - (timedelta(days=1) if spec.lower().startswith("daily") else timedelta(days=7))
    return last is None or last < prev


def health_problem(data_dir: Path, port: int, now: datetime | None = None) -> str:
    """'' when all is well; otherwise the problem in one line (the family page is down, or no daily update by 10:00)."""
    import urllib.request
    try:
        if urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=15).status != 200:
            return "family web page not answering"
    except Exception as ex:   # noqa: BLE001
        return f"family web page not answering: {type(ex).__name__}"
    now = now or datetime.now(vault.TZ)
    last = last_daily(data_dir)
    if now.hour >= 10 and (last is None or last.date() < now.date()):
        return f"no daily price update today (last: {last:%a %d %b %H:%M})" if last else "no daily price update yet"
    return ""


def _wait(app: App, seconds: float) -> None:
    end = time.time() + seconds
    while (left := end - time.time()) > 0:
        time.sleep(min(left, 60))


def scheduler(app: App, spec: str, tries: int = 3, retry_s: float = 1800) -> None:
    """The daily update must happen: it runs at REPORT_AT, catches up straight away if the NAS or container was down at
    that time, and retries a failed or empty run up to `tries` times, `retry_s` apart. Every outcome goes to the vault."""
    data = app.store.cfg_path.parent
    while True:
        now = datetime.now(vault.TZ)
        if due(spec, now, last_daily(data)):
            if now - (next_run(spec, now) - timedelta(days=1)) > timedelta(minutes=5):
                vault.log_event("⏰", "daily update catch-up", "the scheduled run was missed (NAS or container down); running now")
            for attempt in range(1, tries + 1):
                try:
                    app.refresh(full=True)
                except Exception:   # noqa: BLE001
                    log.exception("daily update failed")
                if not due(spec, datetime.now(vault.TZ), last_daily(data)):
                    vault.log_event("✅", "daily update done", f"attempt {attempt}")
                    break
                vault.log_event("❌", "daily update failed", f"attempt {attempt}/{tries}" + (f", retrying in {retry_s / 60:.0f} min" if attempt < tries else ", giving up until the next scheduled run"))
                if attempt < tries:
                    _wait(app, retry_s)
        nxt = next_run(spec, datetime.now(vault.TZ))
        log.info("next daily update at %s", nxt)
        _wait(app, (nxt - datetime.now(vault.TZ)).total_seconds())


def full_due(now: datetime, last_full: datetime | None, every_h: float) -> bool:
    """Every store + flyers again? Daytime only (07:00-22:00), at most every `every_h` hours."""
    return every_h > 0 and 7 <= now.hour <= 22 and (last_full is None or now - last_full >= timedelta(hours=every_h))


def price_watcher(app: App, hours: float, full_every: float = 3) -> None:
    """Regular checks so anything new reaches the page without waiting for tomorrow: Cold Storage every `hours`
    (cheap, through Jina); every store and the flyers every `full_every` hours in the daytime (the NAS browser stays gentle)."""
    time.sleep(120)   # let the container settle / avoid clashing with a refresh at start-up
    while True:
        try:
            app.refresh(full=full_due(datetime.now(vault.TZ), app.last_full, full_every))
        except Exception:   # noqa: BLE001
            log.exception("regular check failed")
            vault.log_event("❌", "regular check failed", "see container log")
        time.sleep(hours * 3600)


def main() -> None:
    app = App()
    if port := int(os.environ.get("WEB_PORT", "8000")):
        web.start(app.store.cfg_path.parent, port)
    spec = os.environ.get("REPORT_AT", "daily 08:00")
    threading.Thread(target=scheduler, args=(app, spec), daemon=True).start()
    hours = float(os.environ.get("PROMO_CHECK_HOURS", "1"))
    full_every = float(os.environ.get("FULL_EVERY_HOURS", "3"))
    if hours > 0:
        threading.Thread(target=price_watcher, args=(app, hours, full_every), daemon=True).start()
    vault.log_event("🚀", "supermarket hunter started", f"full refresh {spec} SGT, prices every {hours:g} h, web :{port}")
    vault.write_home()
    threading.Event().wait()
