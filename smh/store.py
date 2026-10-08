"""SQLite price history (data/prices.db) and the editable staples list (data/config.json)."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DEFAULT = {
    # what the parents cook with every week; edit here or with /staples add|del
    "staples": ["rice", "cooking oil", "eggs", "chicken", "pork", "fish fillet", "prawns", "choy sum",
                "tofu", "soy sauce", "noodles", "onion", "tomato", "milk"],
    # brands the family trusts: marked with a star so a cheap unknown brand never hides a good one
    "brands": {"rice": ["Thai Hom Mali", "Royal Umbrella", "SongHe", "Golden Peony"],
               "cooking oil": ["Knife", "Naturel", "Simply"],
               "eggs": ["Pasar", "Seng Choon", "Chew"],
               "soy sauce": ["Kikkoman", "Lee Kum Kee", "Tai Hua"],
               "tofu": ["Unicurd", "Fortune", "Yeo"],
               "milk": ["Meiji", "Marigold", "Farmhouse"]},
}


class Store:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.cfg_path = data_dir / "config.json"
        self.db = sqlite3.connect(data_dir / "prices.db", isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE IF NOT EXISTS prices(day TEXT, query TEXT, name TEXT, price REAL, was REAL, "
                        "promo TEXT, url TEXT, unit_price REAL, unit TEXT, PRIMARY KEY(day, url))")
        if "image" not in {c["name"] for c in self.db.execute("PRAGMA table_info(prices)")}:   # added 2026-10-08 (web page photos)
            self.db.execute("ALTER TABLE prices ADD COLUMN image TEXT DEFAULT ''")
        self.db.execute("CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS alerts(url TEXT, promo TEXT, day TEXT, PRIMARY KEY(url, promo))")
        # what the family actually bought (the web page's "I bought this" button): one row per product per day
        self.db.execute("CREATE TABLE IF NOT EXISTS purchases(day TEXT, url TEXT, staple TEXT, name TEXT, price REAL, image TEXT, "
                        "PRIMARY KEY(day, url))")

    # ---- promo alerts: one alert per (product, promo text), re-alert only after `days` ----
    def promo_seen(self, url: str, promo: str, today: date, days: int = 14) -> bool:
        row = self.db.execute("SELECT day FROM alerts WHERE url=? AND promo=?", (url, promo)).fetchone()
        return bool(row) and row["day"] >= (today - timedelta(days=days)).isoformat()

    def promo_mark(self, url: str, promo: str, today: date) -> None:
        self.db.execute("INSERT OR REPLACE INTO alerts VALUES(?,?,?)", (url, promo, today.isoformat()))

    # ---- purchases (family's own; feeds the Top 10 page) ----
    def latest_row(self, url: str):
        return self.db.execute("SELECT * FROM prices WHERE url=? ORDER BY day DESC LIMIT 1", (url,)).fetchone()

    def toggle_bought(self, url: str, today: date) -> dict | None:
        """Tap = bought today; a second tap the same day undoes it (mis-taps). Only products we have seen are accepted."""
        row = self.latest_row(url)
        if not row:
            return None
        if self.db.execute("DELETE FROM purchases WHERE day=? AND url=?", (today.isoformat(), url)).rowcount == 0:
            self.db.execute("INSERT INTO purchases VALUES(?,?,?,?,?,?)",
                            (today.isoformat(), url, row["query"], row["name"], row["price"], row["image"] or ""))
            bought = True
        else:
            bought = False
        times = self.db.execute("SELECT COUNT(*) n FROM purchases WHERE url=?", (url,)).fetchone()["n"]
        return {"bought": bought, "times": times}

    def bought_on(self, day: date) -> set[str]:
        return {r["url"] for r in self.db.execute("SELECT url FROM purchases WHERE day=?", (day.isoformat(),))}

    def top_bought(self, n: int = 10) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT url, staple, COUNT(*) times, MAX(day) last FROM purchases GROUP BY url ORDER BY times DESC, last DESC LIMIT ?", (n,))]

    # ---- config ----
    def config(self) -> dict:
        if not self.cfg_path.exists():
            self.save_config(DEFAULT)
        return json.loads(self.cfg_path.read_text(encoding="utf-8"))

    def save_config(self, cfg: dict) -> None:
        tmp = self.cfg_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.cfg_path)

    # ---- kv (Telegram offset etc.) ----
    def get(self, key: str, default: str = "") -> str:
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO kv(key, value) VALUES(?, ?)", (key, value))

    # ---- prices ----
    def save(self, day: date, query: str, rows: list[dict]) -> None:
        self.db.executemany(
            "INSERT OR REPLACE INTO prices(day, query, name, price, was, promo, url, unit_price, unit, image) VALUES(?,?,?,?,?,?,?,?,?,?)",
            [(day.isoformat(), query, r["name"], r["price"], r["was"], r["promo"], r["url"], r["unit_price"], r["unit"], r.get("image") or "")
             for r in rows])

    def previous(self, query: str, before: date) -> dict[str, float]:
        """url -> unit price from the most recent run before `before` (last week's report, normally)."""
        row = self.db.execute("SELECT MAX(day) d FROM prices WHERE query=? AND day<?", (query, before.isoformat())).fetchone()
        if not row or not row["d"]:
            return {}
        return {r["url"]: r["unit_price"] for r in self.db.execute(
            "SELECT url, unit_price FROM prices WHERE query=? AND day=? AND unit_price IS NOT NULL", (query, row["d"]))}

    def low(self, url: str, before: date, weeks: int = 8) -> float | None:
        """Lowest unit price seen for this product in the `weeks` before `before` (None if never seen)."""
        row = self.db.execute("SELECT MIN(unit_price) m FROM prices WHERE url=? AND day<? AND day>=?",
                              (url, before.isoformat(), (before - timedelta(weeks=weeks)).isoformat())).fetchone()
        return row["m"] if row else None
