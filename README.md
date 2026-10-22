# astrbot_plugin_freemodel_sync

[简体中文](README.zh-CN.md)

An [AstrBot](https://github.com/AstrBotDevs/AstrBot) plugin that puts
[freemodel-sync](https://github.com/ndwlbj/freemodel-sync) behind a few
**admin-only** chat commands: see which free LLMs are available right now,
sync the best working one (plus fallbacks) into your tool configs, and run
that sync on a schedule.

The plugin is a thin wrapper: every command runs `python -m freemodel_sync`
as a separate process, so you get exactly the CLI's behaviour (file lock,
backups, exit codes, redacting logger). What gets synced where is configured
in the normal freemodel-sync YAML config.

## Commands

All commands require an AstrBot admin (`PermissionType.ADMIN`); others are
refused before anything runs.

| Command | What it does | Spends free requests? |
| --- | --- | --- |
| `/fms list [N]` | Free models after your filters (`discover --json`) | No (model list only) |
| `/fms rank [N]` | Current selection + ranking from cached probes (`rank --json --no-probe`) | No |
| `/fms dryrun` | What a sync would change (`sync --dry-run --no-notify`) | No |
| `/fms sync` | Discover, probe and write all targets now (`sync`) | Yes, a few probes |
| `/fms status` | Auto-sync on/off, interval, last and next run | No |
| `/fms auto on` / `off` | Start/stop the scheduled sync; `on` also makes this chat the report target | – |

`/fms` alone shows the command tree.

## Install

1. Install the plugin into AstrBot (WebUI → Plugins → install from a local
   folder/zip, or copy this directory to `data/plugins/`). AstrBot installs
   `requirements.txt` (`freemodel-sync>=0.2.0`) into its own environment.
2. Create a freemodel-sync config and test it once from a shell, as described
   in the freemodel-sync README (`freemodel-sync init`, then `sync --dry-run`).
3. In the plugin settings set **freemodel-sync config file** to that path
   (empty means freemodel-sync's default: `$FREEMODEL_SYNC_CONFIG` or
   `~/.config/freemodel-sync/config.yml`).
4. Provide the API key either as an environment variable of the AstrBot
   process (`OPENROUTER_API_KEY`, `GROQ_API_KEY`) or in the plugin settings.

If AstrBot runs in Docker, the config file and every target path must exist
*inside* the container.

## Configuration (`_conf_schema.json`)

| Key | Default | Meaning |
| --- | --- | --- |
| `fms_config_path` | `""` | freemodel-sync YAML config |
| `openrouter_api_key` | `""` | Optional; passed only to the subprocess as `OPENROUTER_API_KEY` |
| `groq_api_key` | `""` | Optional; passed only as `GROQ_API_KEY` (needs freemodel-sync with the Groq provider) |
| `auto_sync_enabled` | `false` | Scheduled sync |
| `auto_sync_interval_minutes` | `360` | Interval, minimum 30 |
| `auto_sync_notify` | `true` | Report scheduled results to the chat where `/fms auto on` was run |
| `list_limit` | `15` | Rows shown by `list` / `rank` |
| `timeout_seconds` | `600` | Kill freemodel-sync after this long |

The first scheduled run happens one interval after the plugin loads (or after
`/fms auto on`), not immediately. Overlapping runs are refused (by the plugin
and by freemodel-sync's own lock, exit code 4).

## Secrets

- Key fields are marked `"secret": true`, so the WebUI masks them. AstrBot
  still stores them in plain text in `data/config/`; prefer environment
  variables if that matters to you.
- Keys from the plugin config are passed to the freemodel-sync child process
  only; AstrBot's own environment is never changed.
- Every reply and log line goes through a redactor: the configured key values
  and anything that looks like a key, bearer token, `*_KEY=…`/`token: …`
  assignment, bot token or credential in a URL is replaced with `***`
  (dry-run diffs of `.env` files included). Replies are truncated to about
  1800 characters.
- The plugin itself sends no network requests and has no telemetry; only
  freemodel-sync talks to the providers you configured.

## AstrBot model providers

If one of your freemodel-sync targets is an AstrBot config, AstrBot usually
needs a restart or a provider reload before it uses the new model. The plugin
does not change AstrBot's provider settings in memory.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install pytest pyyaml ruff
ruff check . && ruff format --check .
pytest
```

The tests stub the AstrBot API (`tests/conftest.py`) and replace the CLI with
`tests/fake_cli.py`, so no AstrBot install, network access or API key is
needed. If AstrBot is installed in the same interpreter,
`tests/test_real_astrbot.py` also loads the plugin with the real AstrBot
decorators and checks every command is registered as admin-only.

## License

MIT
