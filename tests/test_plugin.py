import asyncio
import json
import sys
from pathlib import Path

import pytest

FAKE_CLI = Path(__file__).with_name("fake_cli.py")
FAKE_KEY = "sk-or-v1-" + "fake" * 10

COMMANDS = {"list", "rank", "dryrun", "sync", "status", "auto"}


@pytest.fixture
def make_plugin(plugin_module, stubs, monkeypatch, tmp_path):
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_FMS_LOG", str(log))
    monkeypatch.setattr(
        plugin_module, "cli_argv", lambda config, *args: [sys.executable, str(FAKE_CLI), *args]
    )

    def factory(**config):
        cfg = stubs.AstrBotConfig(
            {
                "list_limit": 15,
                "auto_sync_interval_minutes": 360,
                "auto_sync_notify": True,
                "timeout_seconds": 20,
                **config,
            }
        )
        plugin = plugin_module.FreeModelSyncPlugin(stubs.Context(), cfg)
        plugin.calls = lambda: (
            [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        )
        return plugin

    return factory


async def _collect(gen):
    return [item async for item in gen]


def run(gen):
    return asyncio.run(_collect(gen))


def test_all_commands_are_admin_only(plugin_module, stubs):
    cls = plugin_module.FreeModelSyncPlugin
    found = {}
    for value in vars(cls).values():
        cmd = getattr(value, "_fms_command", None)
        if cmd:
            found[cmd[1]] = value
            assert cmd[0] == "fms"
            assert getattr(value, "_fms_permission", None) is stubs.PermissionType.ADMIN, cmd
    assert set(found) == COMMANDS
    assert set(cls.fms.commands) == COMMANDS


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_non_admin_is_refused_without_running(make_plugin, stubs, name):
    plugin = make_plugin()
    handler = getattr(
        plugin,
        {
            "list": "fms_list",
            "rank": "fms_rank",
            "dryrun": "fms_dryrun",
            "sync": "fms_sync",
            "status": "fms_status",
            "auto": "fms_auto",
        }[name],
    )
    out = run(handler(stubs.AstrMessageEvent(admin=False)))
    assert out == ["admin only"]
    assert plugin.calls() == []


def test_list(make_plugin, stubs):
    plugin = make_plugin(list_limit=2)
    out = run(plugin.fms_list(stubs.AstrMessageEvent()))
    assert len(out) == 1
    assert "3 free model(s)" in out[0]
    assert "openrouter/meta/llama-3.3-70b:free" in out[0]
    assert "... and 1 more" in out[0]
    assert plugin.calls()[0]["argv"] == ["discover", "--json"]
    out = run(plugin.fms_list(stubs.AstrMessageEvent(), "3"))
    assert "groq/llama-3.1-8b-instant" in out[0]


def test_rank_uses_cached_probes_only(make_plugin, stubs):
    plugin = make_plugin()
    out = run(plugin.fms_rank(stubs.AstrMessageEvent()))
    assert "selection: openrouter/meta/llama-3.3-70b:free" in out[0]
    assert "untested fallbacks: qwen/qwen3:free" in out[0]
    assert "score=0.91 ok" in out[0]
    assert plugin.calls()[0]["argv"] == ["rank", "--json", "--no-probe"]


def test_dryrun_args(make_plugin, stubs):
    plugin = make_plugin()
    out = run(plugin.fms_dryrun(stubs.AstrMessageEvent()))
    assert out[0].startswith("dry run: ok")
    assert plugin.calls()[0]["argv"] == ["sync", "--dry-run", "--no-notify"]


def test_sync_records_state_and_passes_key(make_plugin, stubs):
    plugin = make_plugin(openrouter_api_key=FAKE_KEY)
    out = run(plugin.fms_sync(stubs.AstrMessageEvent()))
    assert out[0].startswith("freemodel-sync: syncing")
    assert out[1].startswith("sync: ok")
    assert "selection: openrouter/meta/llama-3.3-70b:free" in out[1]
    call = plugin.calls()[0]
    assert call["argv"] == ["sync"]
    assert call["key"] == FAKE_KEY  # reached the subprocess ...
    assert all(FAKE_KEY not in line for line in out)  # ... but never the chat
    assert plugin.state.last.trigger == "manual" and plugin.state.last.returncode == 0


def test_output_is_redacted(make_plugin, stubs, monkeypatch):
    monkeypatch.setenv("FAKE_FMS_MODE", "leak")
    plugin = make_plugin(openrouter_api_key=FAKE_KEY)
    for handler in (plugin.fms_sync, plugin.fms_dryrun, plugin.fms_list, plugin.fms_rank):
        text = "\n".join(run(handler(stubs.AstrMessageEvent())))
        assert FAKE_KEY not in text
        assert "old-secret-value-123" not in text
        assert "error" in text


def test_account_error_is_reported(make_plugin, stubs, monkeypatch):
    monkeypatch.setenv("FAKE_FMS_MODE", "account")
    plugin = make_plugin()
    out = run(plugin.fms_sync(stubs.AstrMessageEvent()))
    assert "account / quota problem" in out[1]
    assert plugin.state.last.returncode == 3


def test_status_never_shows_key(make_plugin, stubs):
    plugin = make_plugin(openrouter_api_key=FAKE_KEY)
    out = run(plugin.fms_status(stubs.AstrMessageEvent()))[0]
    assert "auto-sync: off" in out
    assert "keys set in plugin config: openrouter_api_key (values never shown)" in out
    assert FAKE_KEY not in out


def test_auto_on_off(make_plugin, stubs):
    plugin = make_plugin()

    async def scenario():
        on = await _collect(plugin.fms_auto(stubs.AstrMessageEvent(origin="qq:Group:42"), "on"))
        running = plugin._task is not None and not plugin._task.done()
        status = await _collect(plugin.fms_status(stubs.AstrMessageEvent()))
        off = await _collect(plugin.fms_auto(stubs.AstrMessageEvent(), "off"))
        bad = await _collect(plugin.fms_auto(stubs.AstrMessageEvent(), "maybe"))
        return on, running, status, off, bad

    on, running, status, off, bad = asyncio.run(scenario())
    assert "auto-sync on: every 360 min" in on[0]
    assert running
    assert "timer running" in status[0]
    assert plugin.config["notify_session"] == "qq:Group:42"
    assert plugin.config["auto_sync_enabled"] is False
    assert plugin.config.saved == 2
    assert off == ["auto-sync off"] and plugin._task is None
    assert bad == ["usage: /fms auto on|off"]


def test_scheduled_sync_runs_and_notifies(make_plugin, stubs, plugin_module, monkeypatch):
    plugin = make_plugin(auto_sync_enabled=True, notify_session="qq:Group:42", openrouter_api_key=FAKE_KEY)
    monkeypatch.setattr(plugin_module, "clamp_interval", lambda value: 0.0005)  # 0.03 s

    async def scenario():
        await plugin.initialize()
        for _ in range(200):
            if plugin.context.sent:
                break
            await asyncio.sleep(0.05)
        await plugin.terminate()

    asyncio.run(scenario())
    assert plugin.context.sent, "scheduled sync did not report"
    session, text = plugin.context.sent[0]
    assert session == "qq:Group:42"
    assert text.startswith("scheduled sync: ok")
    assert FAKE_KEY not in text
    assert plugin.state.last.trigger == "scheduled"
    assert plugin._task is None


def test_disabled_auto_sync_starts_no_timer(make_plugin):
    plugin = make_plugin(auto_sync_enabled=False)

    async def scenario():
        await plugin.initialize()
        task = plugin._task
        await plugin.terminate()
        return task

    assert asyncio.run(scenario()) is None


def test_overlapping_sync_refused(make_plugin, stubs):
    plugin = make_plugin()

    async def scenario():
        await plugin._run_lock.acquire()
        try:
            return await plugin._sync("manual")
        finally:
            plugin._run_lock.release()

    result = asyncio.run(scenario())
    assert result.returncode == 4
    assert plugin.calls() == []
