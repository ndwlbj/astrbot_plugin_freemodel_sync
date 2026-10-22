"""AstrBot plugin: admin commands around the freemodel-sync CLI.

Commands (admins only)::

    /fms list [N]      free models after your filters (no key needed)
    /fms rank [N]      current selection + ranking from cached probes (sends nothing)
    /fms dryrun        diff of what a sync would change (cached probes, writes nothing)
    /fms sync          discover, probe and write all targets now
    /fms status        auto-sync state, last and next run
    /fms auto on|off   enable / disable the scheduled auto-sync

freemodel-sync always runs as a separate process (``python -m freemodel_sync``)
so its file lock, exit codes and redacting logger behave exactly as on the
command line, and API keys from the plugin config reach only that process.
Everything sent to chat or written to the log goes through ``redact()``.
"""

import asyncio
import contextlib
import time

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star

from .fms_core import (
    KEY_FIELDS,
    AutoSyncState,
    CliResult,
    RunRecord,
    build_env,
    clamp_interval,
    cli_argv,
    fmt_time,
    format_cli_result,
    format_models,
    format_ranking,
    known_secrets,
    redact,
    run_cli,
    truncate,
)

PLUGIN_NAME = "astrbot_plugin_freemodel_sync"


# Plugin metadata (name, author, version) lives in metadata.yaml; the old
# @register decorator is deprecated in AstrBot v4.
class FreeModelSyncPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config if config is not None else {}
        self.state = AutoSyncState()
        self._run_lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------ life cycle
    async def initialize(self):
        self._restart_scheduler()

    async def terminate(self):
        await self._stop_scheduler()

    # ------------------------------------------------------------------ helpers
    def _secrets(self) -> list[str]:
        return known_secrets(self.config)

    def _safe(self, text: str) -> str:
        return truncate(redact(text, self._secrets()))

    def _limit(self, arg: str | int | None = None) -> int:
        try:
            if arg not in (None, ""):
                return max(1, min(50, int(arg)))
        except (TypeError, ValueError):
            pass
        try:
            return max(1, min(50, int(self.config.get("list_limit", 15))))
        except (TypeError, ValueError):
            return 15

    async def _cli(self, *args: str) -> CliResult:
        try:
            timeout = float(self.config.get("timeout_seconds") or 600)
        except (TypeError, ValueError):
            timeout = 600.0
        return await run_cli(cli_argv(self.config, *args), build_env(self.config), timeout)

    def _save_config(self) -> None:
        save = getattr(self.config, "save_config", None)
        if callable(save):
            try:
                save()
            except Exception as exc:  # pragma: no cover - depends on AstrBot
                logger.warning("freemodel-sync plugin: could not save config: %s", self._safe(str(exc)))

    @staticmethod
    def _deny(event: AstrMessageEvent) -> bool:
        """Second line of defence next to the ADMIN permission filter."""

        is_admin = getattr(event, "is_admin", None)
        return not (callable(is_admin) and is_admin())

    async def _sync(self, trigger: str) -> CliResult:
        """Run one real sync; never two at once from this plugin."""

        if self._run_lock.locked():
            return CliResult(4, "", "a sync started by this plugin is already running")
        async with self._run_lock:
            started = time.time()
            result = await self._cli("sync")
            self.state.record(RunRecord(started, time.time(), trigger, result.meaning, result.returncode))
            log = logger.info if result.ok else logger.warning
            log("freemodel-sync %s sync: %s", trigger, result.meaning)
            return result

    # ------------------------------------------------------------------ scheduler
    def _restart_scheduler(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None
        self.state.next_run_at = None
        if self.config.get("auto_sync_enabled"):
            self._task = asyncio.get_running_loop().create_task(self._auto_loop())

    async def _stop_scheduler(self) -> None:
        task, self._task = self._task, None
        self.state.next_run_at = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _auto_loop(self) -> None:
        while True:
            interval = clamp_interval(self.config.get("auto_sync_interval_minutes", 360)) * 60
            self.state.next_run_at = time.time() + interval
            await asyncio.sleep(interval)
            try:
                result = await self._sync("scheduled")
                await self._notify(format_cli_result("scheduled sync", result, self._secrets()))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # keep the timer alive
                logger.error("freemodel-sync scheduled sync crashed: %s", self._safe(repr(exc)))

    async def _notify(self, text: str) -> None:
        session = str(self.config.get("notify_session") or "")
        if not session or not self.config.get("auto_sync_notify", True):
            return
        try:
            await self.context.send_message(session, MessageChain().message(self._safe(text)))
        except Exception as exc:
            logger.warning("freemodel-sync: could not send the auto-sync report: %s", self._safe(str(exc)))

    # ------------------------------------------------------------------ commands
    @filter.command_group("fms")
    def fms(self):
        """freemodel-sync: list and sync free LLMs (admin only)"""

    @filter.permission_type(filter.PermissionType.ADMIN)
    @fms.command("list")
    async def fms_list(self, event: AstrMessageEvent, limit: str = ""):
        """List free models after the configured filters"""
        if self._deny(event):
            yield event.plain_result("admin only")
            return
        result = await self._cli("discover", "--json")
        if not result.ok:
            yield event.plain_result(format_cli_result("list", result, self._secrets()))
            return
        yield event.plain_result(self._safe(format_models(result.stdout, self._limit(limit))))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @fms.command("rank")
    async def fms_rank(self, event: AstrMessageEvent, limit: str = ""):
        """Show the selection and ranking from cached probes (sends no probes)"""
        if self._deny(event):
            yield event.plain_result("admin only")
            return
        result = await self._cli("rank", "--json", "--no-probe")
        if not result.ok:
            yield event.plain_result(format_cli_result("rank", result, self._secrets()))
            return
        yield event.plain_result(self._safe(format_ranking(result.stdout, self._limit(limit))))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @fms.command("dryrun")
    async def fms_dryrun(self, event: AstrMessageEvent):
        """Show what a sync would change (writes nothing)"""
        if self._deny(event):
            yield event.plain_result("admin only")
            return
        result = await self._cli("sync", "--dry-run", "--no-notify")
        yield event.plain_result(format_cli_result("dry run", result, self._secrets()))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @fms.command("sync")
    async def fms_sync(self, event: AstrMessageEvent):
        """Discover, probe and write all targets now"""
        if self._deny(event):
            yield event.plain_result("admin only")
            return
        yield event.plain_result("freemodel-sync: syncing, this can take a minute ...")
        result = await self._sync("manual")
        yield event.plain_result(format_cli_result("sync", result, self._secrets()))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @fms.command("status")
    async def fms_status(self, event: AstrMessageEvent):
        """Auto-sync state and the last run"""
        if self._deny(event):
            yield event.plain_result("admin only")
            return
        enabled = bool(self.config.get("auto_sync_enabled"))
        interval = clamp_interval(self.config.get("auto_sync_interval_minutes", 360))
        running = self._task is not None and not self._task.done()
        lines = [
            f"auto-sync: {'on' if enabled else 'off'} "
            f"(every {interval} min, timer {'running' if running else 'stopped'})",
            f"next run: {fmt_time(self.state.next_run_at)}",
        ]
        last = self.state.last
        if last is None:
            lines.append("last run: - (none since the plugin was loaded)")
        else:
            lines.append(f"last run: {fmt_time(last.finished_at)} [{last.trigger}] {last.meaning}")
        notify = bool(self.config.get("notify_session")) and self.config.get("auto_sync_notify", True)
        lines.append(f"report to chat: {'yes' if notify else 'no'}")
        configured = [name for name in KEY_FIELDS if str(self.config.get(name) or "").strip()]
        lines.append(f"keys set in plugin config: {', '.join(configured) or 'none'} (values never shown)")
        yield event.plain_result(self._safe("\n".join(lines)))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @fms.command("auto")
    async def fms_auto(self, event: AstrMessageEvent, mode: str = ""):
        """Enable or disable the scheduled auto-sync: /fms auto on|off"""
        if self._deny(event):
            yield event.plain_result("admin only")
            return
        mode = (mode or "").strip().lower()
        if mode in {"on", "enable", "true", "1"}:
            self.config["auto_sync_enabled"] = True
            self.config["notify_session"] = event.unified_msg_origin
            self._save_config()
            self._restart_scheduler()
            interval = clamp_interval(self.config.get("auto_sync_interval_minutes", 360))
            yield event.plain_result(
                f"auto-sync on: every {interval} min; results are reported to this chat"
                if self.config.get("auto_sync_notify", True)
                else f"auto-sync on: every {interval} min"
            )
        elif mode in {"off", "disable", "false", "0"}:
            self.config["auto_sync_enabled"] = False
            self._save_config()
            await self._stop_scheduler()
            yield event.plain_result("auto-sync off")
        else:
            yield event.plain_result("usage: /fms auto on|off")
