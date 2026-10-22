"""AstrBot-independent helpers for the freemodel-sync plugin.

Everything here is plain Python so it can be tested without AstrBot:
running the ``freemodel-sync`` CLI in a subprocess, redacting secrets from
whatever it prints, and formatting the results for chat.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: freemodel-sync exit codes (see its README)
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NO_MODEL = 2
EXIT_ACCOUNT_ERROR = 3
EXIT_LOCKED = 4

EXIT_MEANINGS = {
    EXIT_OK: "ok",
    EXIT_ERROR: "error",
    EXIT_NO_MODEL: "no usable model (nothing was written)",
    EXIT_ACCOUNT_ERROR: "account / quota problem (nothing was written)",
    EXIT_LOCKED: "another freemodel-sync run is in progress",
}

#: plugin config field -> environment variable read by freemodel-sync
KEY_FIELDS = {
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "groq_api_key": "GROQ_API_KEY",
}

#: longest reply sent to chat; longer output is truncated
MAX_CHAT_CHARS = 1800
MIN_INTERVAL_MINUTES = 30
DEFAULT_TIMEOUT_SECONDS = 600

REDACTED = "***"

_SECRET_PATTERNS = [
    # well-known key formats
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bgsk_[A-Za-z0-9]{8,}"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{8,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"),
    # telegram bot token
    re.compile(r"(?<![0-9])\d{6,}:[A-Za-z0-9_\-]{30,}"),
]
# "Authorization: Bearer xxx" / "Bearer xxx"
_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=\-]{6,}")
# KEY=value, "api_key": "value", token: value ... (env files, YAML, JSON, diffs)
_ASSIGNMENT = re.compile(
    r"""(?ix)
    (?P<name>["']?[A-Za-z0-9_.\-]*(?:api[_\-]?key|apikey|token|secret|password|passwd|
        access[_\-]?key|private[_\-]?key|auth)[A-Za-z0-9_.\-]*["']?)
    (?P<sep>\s*[:=]\s*)
    (?P<value>"[^"\n]*"|'[^'\n]*'|[^\s,;}\]\n]+)
    """
)
# secrets in URL query strings or userinfo
_URL_QUERY = re.compile(r"(?i)([?&](?:key|token|api_key|apikey|access_token|secret)=)[^&\s]+")
_URL_USERINFO = re.compile(r"(?i)(\b[a-z][a-z0-9+.\-]*://)[^/\s:@]+:[^/\s@]+@")


def redact(text: str, known_secrets: Iterable[str] = ()) -> str:
    """Return *text* with anything that looks like a credential replaced.

    *known_secrets* (e.g. the keys from the plugin config) are always
    replaced verbatim, whatever their format.
    """

    if not text:
        return text
    for secret in sorted({s for s in known_secrets if s and len(s) >= 4}, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    text = _BEARER.sub(lambda m: f"{m.group(1)} {REDACTED}", text)

    def _assignment(match: re.Match[str]) -> str:
        value = match.group("value")
        if value in (REDACTED, f'"{REDACTED}"', f"'{REDACTED}'"):
            return match.group(0)
        # keep obviously harmless values such as env var *names*, booleans and nulls
        bare = value.strip("\"'")
        if bare == "" or bare.lower() in {"null", "none", "true", "false", "~"}:
            return match.group(0)
        name = match.group("name").lower().strip("\"'")
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", bare) and name.endswith("_env"):
            return match.group(0)
        quote = value[0] if value[:1] in "\"'" else ""
        return f"{match.group('name')}{match.group('sep')}{quote}{REDACTED}{quote}"

    text = _ASSIGNMENT.sub(_assignment, text)
    text = _URL_QUERY.sub(lambda m: m.group(1) + REDACTED, text)
    text = _URL_USERINFO.sub(lambda m: m.group(1) + REDACTED + "@", text)
    return text


def truncate(text: str, limit: int = MAX_CHAT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20].rstrip() + "\n... (truncated)"


def known_secrets(config: Mapping[str, Any]) -> list[str]:
    """Key values from the plugin config and the process environment."""

    values = [str(config.get(name) or "") for name in KEY_FIELDS]
    for env_name in KEY_FIELDS.values():
        values.append(os.environ.get(env_name, ""))
    return [v for v in values if v]


def build_env(config: Mapping[str, Any], base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Environment for the freemodel-sync subprocess.

    Keys set in the plugin config are passed only to the child process;
    AstrBot's own environment is never modified.
    """

    env = dict(os.environ if base is None else base)
    for field_name, env_name in KEY_FIELDS.items():
        value = str(config.get(field_name) or "").strip()
        if value:
            env[env_name] = value
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def cli_argv(config: Mapping[str, Any], *args: str) -> list[str]:
    """``[python, -m, freemodel_sync, (-c CONFIG), *args]``."""

    argv = [sys.executable, "-m", "freemodel_sync"]
    path = str(config.get("fms_config_path") or "").strip()
    if path:
        argv += ["-c", os.path.expanduser(path)]
    argv += list(args)
    return argv


@dataclass
class CliResult:
    returncode: int
    stdout: str
    stderr: str
    duration: float = 0.0
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == EXIT_OK and not self.timed_out

    @property
    def meaning(self) -> str:
        if self.timed_out:
            return "timed out"
        return EXIT_MEANINGS.get(self.returncode, f"exit code {self.returncode}")


async def run_cli(
    argv: Sequence[str],
    env: Mapping[str, str],
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> CliResult:
    """Run *argv* without a shell and capture its output."""

    started = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=dict(env),
        )
    except FileNotFoundError as exc:
        return CliResult(127, "", f"cannot start freemodel-sync: {exc}")
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return CliResult(
            -1,
            "",
            f"freemodel-sync did not finish within {int(timeout)}s",
            time.monotonic() - started,
            timed_out=True,
        )
    return CliResult(
        proc.returncode if proc.returncode is not None else -1,
        out.decode("utf-8", "replace"),
        err.decode("utf-8", "replace"),
        time.monotonic() - started,
    )


def _fmt_context(value: Any) -> str:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return "?"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1000:
        return f"{n // 1000}k"
    return str(n)


def format_models(stdout: str, limit: int) -> str:
    """Format ``freemodel-sync discover --json`` output."""

    try:
        models = json.loads(stdout)
    except ValueError:
        return "could not parse the model list from freemodel-sync"
    if not isinstance(models, list) or not models:
        return "no free models matched the filters"
    lines = [f"{len(models)} free model(s) after filters:"]
    for index, model in enumerate(models[: max(1, limit)], start=1):
        if not isinstance(model, dict):
            continue
        extras = [f"ctx {_fmt_context(model.get('context_length'))}"]
        if model.get("supports_tools"):
            extras.append("tools")
        if model.get("expiration_date"):
            extras.append(f"expires {model['expiration_date']}")
        provider = model.get("provider") or "?"
        lines.append(f"{index}. {provider}/{model.get('id')} ({', '.join(extras)})")
    if len(models) > limit:
        lines.append(f"... and {len(models) - limit} more")
    return "\n".join(lines)


def format_ranking(stdout: str, limit: int) -> str:
    """Format ``freemodel-sync rank --json`` output."""

    try:
        payload = json.loads(stdout)
    except ValueError:
        return "could not parse the ranking from freemodel-sync"
    if not isinstance(payload, dict):
        return "could not parse the ranking from freemodel-sync"
    ranked = payload.get("ranking") or []
    lines = []
    selection = payload.get("selection")
    if isinstance(selection, dict) and selection.get("primary"):
        lines.append(f"selection: {selection.get('primary_key') or selection['primary']}")
        fallbacks = [str(item) for item in selection.get("fallbacks") or []]
        lines.append(f"fallbacks: {', '.join(fallbacks) or '-'}")
        untested = selection.get("untested_fallbacks") or []
        if untested:
            lines.append(f"untested fallbacks: {', '.join(str(u) for u in untested)}")
    else:
        lines.append("selection: none (no working model in the cached results)")
    if ranked:
        lines.append("ranking (cached probes, nothing sent):")
        for index, item in enumerate(ranked[: max(1, limit)], start=1):
            if not isinstance(item, dict):
                continue
            test = item.get("test") or {}
            status = "untested" if not test else ("ok" if test.get("ok") else "failed")
            score = item.get("score")
            score_s = f"{score:.2f}" if isinstance(score, (int, float)) else "?"
            lines.append(f"{index}. {item.get('key') or item.get('id')} score={score_s} {status}")
    return "\n".join(lines)


def format_cli_result(title: str, result: CliResult, secrets: Iterable[str] = ()) -> str:
    """Generic redacted chat summary for a sync / dry-run."""

    secrets = list(secrets)
    lines = [f"{title}: {result.meaning} ({result.duration:.0f}s)"]
    body = (result.stdout.strip() + "\n" + result.stderr.strip()).strip()
    if body:
        lines.append(body)
    return truncate(redact("\n".join(lines), secrets))


def clamp_interval(value: Any) -> int:
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        minutes = 360
    return max(MIN_INTERVAL_MINUTES, minutes)


@dataclass
class RunRecord:
    started_at: float
    finished_at: float
    trigger: str
    meaning: str
    returncode: int


@dataclass
class AutoSyncState:
    """In-memory status shown by ``/fms status``."""

    last: RunRecord | None = None
    next_run_at: float | None = None
    history: list[RunRecord] = field(default_factory=list)

    def record(self, record: RunRecord) -> None:
        self.last = record
        self.history.append(record)
        del self.history[:-10]


def fmt_time(ts: float | None) -> str:
    if not ts:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(ts))
