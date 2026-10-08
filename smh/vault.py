"""Obsidian vault (VAULT_DIR): the bot's movement log and memory, per the NAS vault standard.

* ``Activity/YYYY/MM/YYYY-MM-DD.md``: one line per event, ``- HH:MM emoji **what** · detail · [[entity]]`` (SGT)
* ``Items/<staple>.md``: one note per staple, append-only ``## History`` (best value each week)
* ``Reports/YYYY/MM/YYYY-MM-DD Weekly.md``: the weekly report as plain text
* ``Home.md``: what this is, the current month, the latest day notes

Best effort throughout: any error is logged and ignored, the vault never breaks a run or loses a post.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Singapore")
_BAD_NAME = re.compile(r'[\\/:*?"<>|#^\[\]]+')


def root() -> Path | None:
    r = os.environ.get("VAULT_DIR")
    return Path(r) if r else None


def note_name(title: str) -> str:
    return re.sub(r"\s+", " ", _BAD_NAME.sub(" ", str(title))).strip(" .")[:120] or "Untitled"


def _field(text) -> str:
    return " ".join(str(text).split()).replace("·", ",")


def _day_path(r: Path, day: date) -> Path:
    return r / "Activity" / f"{day:%Y}" / f"{day:%m}" / f"{day:%Y-%m-%d}.md"


def _write(path: Path, text: str) -> None:
    """Atomic (tmp + rename), 664 so the owner can edit it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o664)
    except OSError:
        pass


def log_event(emoji: str, what: str, detail: str = "", entity: str | None = None) -> None:
    r = root()
    if r is None:
        return
    try:
        now = datetime.now(TZ)
        day = _day_path(r, now.date())
        if not day.exists():
            _write(day, f"---\ntags: [log]\nupdated: {now:%Y-%m-%d}\n---\n# {now:%a %d %b %Y}\n\n")
        line = f"- {now:%H:%M} {emoji} **{_field(what)}**" + (f" · {_field(detail)}" if detail else "") + \
            (f" · [[{note_name(entity)}]]" if entity else "")
        with day.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as ex:   # noqa: BLE001
        log.warning("vault write skipped: %s", ex)


def history(name: str, line: str, summary: str = "") -> None:
    """Append one dated line to Items/<name>.md, creating the note with its summary on first use."""
    r = root()
    if r is None:
        return
    try:
        now = datetime.now(TZ)
        path = r / "Items" / f"{note_name(name)}.md"
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        hist = old.split("## History\n", 1)[1].rstrip("\n").splitlines() if "## History\n" in old else []
        hist.append(f"- {now:%Y-%m-%d %H:%M} {_field(line)}")
        body = ["---", "tags: [active]", f"updated: {now:%Y-%m-%d}", "---", f"# {note_name(name)}", "",
                summary or "Tracked staple.", "", "## History", *hist]
        _write(path, "\n".join(body) + "\n")
    except Exception as ex:   # noqa: BLE001
        log.warning("vault note skipped: %s", ex)


def report(name: str, text: str) -> None:
    r = root()
    if r is None:
        return
    try:
        now = datetime.now(TZ)
        _write(r / "Reports" / f"{now:%Y}" / f"{now:%m}" / f"{now:%Y-%m-%d} {note_name(name)}.md",
               f"---\ntags: [report]\nupdated: {now:%Y-%m-%d}\n---\n# {name} {now:%Y-%m-%d}\n\n{text}\n")
    except Exception as ex:   # noqa: BLE001
        log.warning("vault report skipped: %s", ex)


def write_home() -> None:
    r = root()
    if r is None:
        return
    try:
        now = datetime.now(TZ)
        latest = [d for d in (now.date() - timedelta(days=i) for i in range(30)) if _day_path(r, d).is_file()][:7]
        items = sorted(p.stem for p in (r / "Items").glob("*.md")) if (r / "Items").is_dir() else []
        lines = ["---", "tags: [active]", f"updated: {now:%Y-%m-%d}", "---", "# Supermarket Hunter", "",
                 "Written by Supermarket Hunter, the family grocery price bot (FairPrice via Jina Reader). "
                 "`Activity/YYYY/MM/` is the movement log (reports, /ask answers, staples changes, errors); "
                 "`Items/` holds one note per staple with the weekly best value; `Reports/` keeps each weekly report.", "",
                 f"- **This month:** `Activity/{now:%Y/%m}/` · today [[{now:%Y-%m-%d}]]",
                 "- **Latest notes:** " + (", ".join(f"[[{d.isoformat()}]]" for d in latest) or "none yet"), "",
                 "## Items", *[f"- [[{w}]]" for w in items]]
        _write(r / "Home.md", "\n".join(lines) + "\n")
    except Exception as ex:   # noqa: BLE001
        log.warning("vault home skipped: %s", ex)
