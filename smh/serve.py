"""Weekly report job + Telegram listener. Env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_THREAD_ID,
TELEGRAM_ALLOWED_USERS (private chats), REPORT_AT ("daily 08:00" SGT), DATA_DIR, VAULT_DIR."""
from __future__ import annotations

import html
import logging
import os
import re
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from . import cards, flyers, scrape, vault, web
from .store import Store
from .telegram import Telegram

log = logging.getLogger(__name__)
BUTTONS = [("🔄 Run again", "report"), ("📋 Staples", "staples")]
CALLBACKS = {b for _, b in BUTTONS}   # the only callback_data accepted (client-supplied, untrusted)
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class App:
    def __init__(self):
        self.store = Store(Path(os.environ.get("DATA_DIR", "data")))
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        thread = os.environ.get("TELEGRAM_THREAD_ID", "").strip()
        self.thread_id = int(thread) if thread.isdigit() else None
        self.allowed_users = {int(x) for x in os.environ.get("TELEGRAM_ALLOWED_USERS", "").replace(",", " ").split() if x.isdigit()}
        self.tg = Telegram(token, self.chat_id, self.thread_id) if token else None

    # ---- the weekly report ----
    def weekly(self, sleep=time.sleep) -> list[str]:
        cfg, today, results = self.store.config(), datetime.now(vault.TZ).date(), {}
        vault.log_event("🛒", "weekly report started", f"{len(cfg['staples'])} staples")
        try:   # FairPrice: only its promotions page is allowed, so those rows join the per-store comparison
            fp_rows = [i["row"] for i in flyers.fairprice(cfg["staples"]) if i["staple"]]
        except Exception as ex:   # noqa: BLE001
            log.warning("FairPrice promotions rows skipped: %s", ex)
            fp_rows = []
        for staple in cfg["staples"]:
            rows = scrape.search(staple) + [r for r in fp_rows if flyers._match(r["name"], [staple])]
            rows.sort(key=lambda r: r["unit_price"] if r["unit_price"] is not None else 1e9)
            prev = self.store.previous(staple, today)
            lows = {r["url"]: self.store.low(r["url"], today) for r in rows[:3]}
            self.store.save(today, staple, rows)
            results[staple] = dict(rows=rows, brands=cfg["brands"].get(staple, []), prev=prev, lows=lows)
            for r in rows:   # promos shown here count as announced: the promo watcher won't repeat them
                if (r["promo"] or r["was"]) and cards.near(r, rows[0]):
                    self.store.promo_mark(r["url"], r["promo"] or f"was {r['was']}", today)
            if rows:
                b = rows[0]
                vault.history(staple, f"best {cards.money(b['price'])} ({cards.unit(b)}) {b['name'][:60]}"
                              + (f" · {b['promo']}" if b["promo"] else ""), f"Weekly best value for {staple} on FairPrice.")
            else:
                vault.log_event("⚠️", "no data", staple, staple)
            sleep(2)   # be polite to Jina / FairPrice
        texts = cards.weekly_cards(results)
        vault.report("Weekly", "\n\n".join(html.unescape(re.sub(r"<[^>]+>", "", t)) for t in texts))
        vault.log_event("✅", "weekly report built", f"{sum(1 for r in results.values() if r['rows'])}/{len(results)} staples, "
                        f"{len(texts)} message(s)")
        vault.write_home()
        return texts

    def promos(self, new_only: bool, sleep=time.sleep) -> list[str]:
        """Every promo in the same ballpark as each staple's best value; with new_only, just those not alerted in 14 days."""
        cfg, today, hits = self.store.config(), datetime.now(vault.TZ).date(), []
        for staple in cfg["staples"]:
            rows = scrape.search(staple)
            self.store.save(today, staple, rows)
            brands = cfg["brands"].get(staple, [])
            for r in rows:
                if not (r["promo"] or r["was"]) or not cards.near(r, rows[0]):
                    continue
                key = r["promo"] or f"was {r['was']}"
                new = not self.store.promo_seen(r["url"], key, today)
                if new:
                    self.store.promo_mark(r["url"], key, today)
                if new or not new_only:
                    hits.append((staple, r, new, cards.is_brand(r, brands)))
            sleep(2)
        vault.log_event("🔔", "promo check", f"{len(hits)} {'new' if new_only else 'current'} promo(s)")
        return cards.promo_cards(hits, new_only) if hits or not new_only else []

    def send_promos(self) -> None:
        texts = self.promos(new_only=True)
        if not texts:
            return
        if self.tg and self.chat_id:
            self.tg.post(texts)
            vault.log_event("📨", "promo alert sent", f"{len(texts)} message(s)")
        else:
            print("\n\n".join(texts))

    def flyer_texts(self, sleep=time.sleep) -> list[str]:
        """Flyer/promo-page sources; items already shown in an earlier report lose the 🆕 mark (alerts table, 14 days)."""
        cfg, today = self.store.config(), datetime.now(vault.TZ).date()
        sections = flyers.collect(cfg["staples"], self.store.cfg_path.parent, sleep)
        try:
            web.save_flyers(self.store.cfg_path.parent, sections)
        except Exception as ex:   # noqa: BLE001  the web page is a nice-to-have, never lose the report over it
            log.warning("flyers.json not saved: %s", ex)
        new = set()
        for items in sections.values():
            for i in items:
                if not self.store.promo_seen(i["url"], i["key"], today):
                    new.add(i["key"])
                    self.store.promo_mark(i["url"], i["key"], today)
        for name, items in sections.items():
            vault.log_event("📰", f"flyers {name}", f"{len(items)} item(s), {sum(1 for i in items if i['key'] in new)} new")
        return cards.flyer_cards(sections, new)

    def send_weekly(self) -> None:
        texts = self.weekly()
        try:
            texts += self.flyer_texts()
        except Exception:   # noqa: BLE001  the price report must go out even if a flyer source breaks
            log.exception("flyers failed")
            vault.log_event("❌", "flyers failed", "see container log")
        if self.tg and self.chat_id:
            self.tg.post(texts, BUTTONS)
            vault.log_event("📨", "weekly report sent", f"{len(texts)} message(s)")
        else:
            print("\n\n".join(texts))

    # ---- commands ----
    def allowed(self, m: dict) -> bool:
        """Own group topic: anyone there (the parents too). Private chat: listed users only."""
        chat = m.get("chat", {})
        if chat.get("type") == "private":
            return m.get("from", {}).get("id") in self.allowed_users
        return str(chat.get("id")) == self.chat_id and (not self.thread_id or m.get("message_thread_id") == self.thread_id)

    def handle(self, u: dict) -> None:
        if cq := u.get("callback_query"):
            msg = cq.get("message") or {}
            ok = cq.get("data") in CALLBACKS and self.allowed({**msg, "from": cq.get("from", {})})
            self.tg.call("answerCallbackQuery", callback_query_id=cq["id"])   # always stop the spinner
            if ok:
                self.run(cq["data"], "", msg)
            return
        m = u.get("message") or {}
        text = (m.get("text") or "").strip()
        if not text.startswith("/") or not self.allowed(m):
            return
        cmd, _, arg = text.partition(" ")
        self.run(cmd[1:].split("@")[0].lower(), arg.strip(), m)

    def run(self, cmd: str, arg: str, m: dict) -> None:
        fn = getattr(self, "cmd_" + cmd, None)
        if not fn:
            return   # other bots' commands in the shared group: stay silent
        vault.log_event("💬", "command", f"/{cmd} {arg}".strip())
        chat, thread = m["chat"]["id"], m.get("message_thread_id")
        texts = fn(arg)
        self.tg.post(texts, BUTTONS if cmd in ("report", "shophelp", "start") else None, chat_id=chat, thread_id=thread)

    def cmd_ask(self, arg):
        if not arg:
            return ["Use /ask <code>item</code>, for example /ask <code>eggs</code>"]
        cfg = self.store.config()
        rows = scrape.search(arg)
        self.store.save(datetime.now(vault.TZ).date(), arg.lower(), rows)
        vault.log_event("🔎", "/ask", f"{arg} · {len(rows)} found" + (f" · best {rows[0]['name'][:50]}" if rows else ""), arg.lower())
        return cards.ask_cards(arg, rows, cfg["brands"].get(arg.lower(), []))

    cmd_askretailer = cmd_price = cmd_ask

    def cmd_report(self, arg):
        return self.weekly()

    def cmd_promos(self, arg):
        return self.promos(new_only=False)

    def cmd_flyers(self, arg):
        return self.flyer_texts()

    def cmd_staples(self, arg):
        cfg = self.store.config()
        op, _, name = arg.partition(" ")
        name = name.strip().lower()
        if op == "add" and name:
            if name not in cfg["staples"]:
                cfg["staples"].append(name)
                self.store.save_config(cfg)
                vault.log_event("➕", "staple added", name, name)
            return [f"🆕 Watching <b>{cards.esc(name)}</b>. It joins the next weekly report."]
        if op == "del" and name:
            if name in cfg["staples"]:
                cfg["staples"].remove(name)
                self.store.save_config(cfg)
                vault.log_event("❌", "staple removed", name, name)
                return [f"❌ Stopped watching <b>{cards.esc(name)}</b>."]
            return ["Not on the list."]
        return [cards.staples_card(cfg)]

    def cmd_shophelp(self, arg):
        return [cards.HELP]

    cmd_start = cmd_shophelp


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
        now = datetime.now(vault.TZ)
        nxt = next_run(spec, now)
        log.info("next report at %s", nxt)
        while (left := (nxt - datetime.now(vault.TZ)).total_seconds()) > 0:
            heartbeat(app.store.cfg_path.parent)
            time.sleep(min(left, 60))
        try:
            app.send_weekly()
        except Exception:   # noqa: BLE001
            log.exception("report failed")
            vault.log_event("❌", "report failed", "see container log")


def promo_watcher(app: App, hours: float) -> None:
    """Every `hours`, push promos not alerted before. The daily report's own promos are marked too (same table),
    so a promo is announced once: by whichever runs first."""
    time.sleep(120)   # let the container settle / avoid clashing with a report at start-up
    while True:
        try:
            app.send_promos()
        except Exception:   # noqa: BLE001
            log.exception("promo check failed")
            vault.log_event("❌", "promo check failed", "see container log")
        time.sleep(hours * 3600)


def main() -> None:
    app = App()
    if port := int(os.environ.get("WEB_PORT", "8000")):
        web.start(app.store.cfg_path.parent, port)
    spec = os.environ.get("REPORT_AT", "daily 08:00")
    threading.Thread(target=scheduler, args=(app, spec), daemon=True).start()
    hours = float(os.environ.get("PROMO_CHECK_HOURS", "3"))
    if hours > 0:
        threading.Thread(target=promo_watcher, args=(app, hours), daemon=True).start()
    vault.log_event("🚀", "supermarket hunter started", f"report {spec} SGT, promo check every {hours:g} h")
    vault.write_home()
    if not app.tg:
        log.warning("no TELEGRAM_BOT_TOKEN: the listener is off, reports print to stdout")
        threading.Event().wait()
    app.tg.poll(app.store, app.handle)
