"""Bounded `claude -p` calls with a JSON schema (Watchbot / x100bot pattern, trimmed). Auth: CLAUDE_CODE_OAUTH_TOKEN in .env.
Images: save the file in `cwd` and let the model Read it (allowed="Read"). Never --bare: it ignores the OAuth token."""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
NO_TOOLS = "Bash,Edit,Write,Read,Glob,Grep,Agent,NotebookEdit,WebSearch,WebFetch,TodoWrite,Task"
LIMIT = re.compile(r"hit your (session|weekly|Opus|Sonnet) limit|usage limit|rate limit reached", re.I)


class ClaudeFailure(Exception):
    pass


def available() -> bool:
    return bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY"))


def parse_result(stdout: str) -> dict:
    try:
        res = json.loads(stdout)
    except (json.JSONDecodeError, TypeError) as ex:
        raise ClaudeFailure(f"stdout is not JSON: {ex}: {stdout[:160]}")
    if isinstance(res, list):
        res = next((e for e in reversed(res) if isinstance(e, dict) and e.get("type") == "result"), {})
    text = str(res.get("result") or "")
    if LIMIT.search(text):
        raise ClaudeFailure("limit: " + text[:160])
    if res.get("is_error") or res.get("subtype") != "success":
        raise ClaudeFailure(f"is_error={res.get('is_error')} subtype={res.get('subtype')}: {text[:160]}")
    out = res.get("structured_output")
    if not isinstance(out, dict):
        try:
            out = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            raise ClaudeFailure("no structured_output in the result")
    return out


def ask(prompt: str, schema: dict, cwd: Path, *, allowed: str = "", max_turns: int = 4, timeout: int = 240,
        runner=subprocess.run) -> dict:
    """One call -> dict matching `schema`. allowed="Read" lets the model open image files inside cwd."""
    cmd = ["claude", "-p", "--output-format", "json", "--json-schema", json.dumps(schema),
           "--permission-mode", "dontAsk", "--permission-prompts", "none", "--strict-mcp-config",
           "--no-session-persistence", "--model", MODEL, "--max-turns", str(max_turns)]
    if allowed:
        cmd += ["--allowedTools", allowed, "--disallowedTools", ",".join(t for t in NO_TOOLS.split(",") if t not in allowed.split(","))]
    else:
        cmd += ["--tools", "", "--disallowedTools", NO_TOOLS]
    env = {**os.environ, "DISABLE_AUTOUPDATER": "1"}
    cwd.mkdir(parents=True, exist_ok=True)
    try:
        p = runner(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=timeout, env=env, cwd=str(cwd))
    except subprocess.TimeoutExpired:
        raise ClaudeFailure(f"claude -p timed out after {timeout} s")
    except OSError as ex:
        raise ClaudeFailure(f"cannot start claude: {ex}")
    if not (p.stdout or "").strip():
        raise ClaudeFailure(f"empty stdout, exit {p.returncode}: {(p.stderr or '')[:200]}")
    return parse_result(p.stdout)
