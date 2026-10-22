import asyncio
import json
import sys

FAKE_KEY = "sk-or-v1-" + "fake" * 10
FAKE_GROQ = "gsk_" + "Fake" * 10


def test_redact_known_and_patterns(core_module):
    redact = core_module.redact
    text = (
        f"key {FAKE_KEY} groq {FAKE_GROQ}\n"
        "Authorization: Bearer abcdefghijkl\n"
        "OPENAI_API_KEY=plainvalue123\n"
        'api_key: "quoted-value"\n'
        '{"token": "t0k3n-value"}\n'
        "https://api.telegram.org/bot123456789:" + "A" * 35 + "/sendMessage\n"
        "https://user:pass@example.org/x?key=abc123&x=1\n"
        "api_key_env: OPENROUTER_API_KEY\n"
        "api_key: null\n"
    )
    out = redact(text, ["my-custom-secret"])
    for leaked in (
        FAKE_KEY,
        FAKE_GROQ,
        "abcdefghijkl",
        "plainvalue123",
        "quoted-value",
        "t0k3n-value",
        "A" * 35,
        "user:pass",
        "abc123",
    ):
        assert leaked not in out
    assert "api_key_env: OPENROUTER_API_KEY" in out  # env var names are fine
    assert "api_key: null" in out
    assert "x=1" in out
    assert redact("prefix my-custom-secret suffix", ["my-custom-secret"]) == "prefix *** suffix"


def test_redact_keeps_model_ids(core_module):
    text = "selection: openrouter/meta/llama-3.3-70b:free (fallbacks: qwen/qwen3:free)"
    assert core_module.redact(text) == text


def test_build_env_only_child(core_module, monkeypatch):
    monkeypatch.setenv("KEEP_ME", "1")
    env = core_module.build_env({"openrouter_api_key": FAKE_KEY, "groq_api_key": ""})
    assert env["OPENROUTER_API_KEY"] == FAKE_KEY
    assert "GROQ_API_KEY" not in env
    assert env["KEEP_ME"] == "1"
    import os

    assert "OPENROUTER_API_KEY" not in os.environ  # AstrBot's env untouched


def test_known_secrets_includes_env(core_module, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", FAKE_GROQ)
    secrets = core_module.known_secrets({"openrouter_api_key": FAKE_KEY})
    assert FAKE_KEY in secrets and FAKE_GROQ in secrets


def test_cli_argv(core_module):
    assert core_module.cli_argv({}, "discover", "--json") == [
        sys.executable,
        "-m",
        "freemodel_sync",
        "discover",
        "--json",
    ]
    argv = core_module.cli_argv({"fms_config_path": "  /etc/fms.yml "}, "sync")
    assert argv[3:] == ["-c", "/etc/fms.yml", "sync"]


def test_format_models(core_module):
    payload = json.dumps(
        [
            {"provider": "openrouter", "id": "a:free", "context_length": 131072, "supports_tools": True},
            {"provider": "groq", "id": "b", "context_length": 1000000, "expiration_date": "2026-12-01"},
            {"provider": "openrouter", "id": "c:free", "context_length": None},
        ]
    )
    out = core_module.format_models(payload, limit=2)
    assert "3 free model(s)" in out
    assert "1. openrouter/a:free (ctx 131k, tools)" in out
    assert "2. groq/b (ctx 1M, expires 2026-12-01)" in out
    assert "... and 1 more" in out
    assert core_module.format_models("[]", 5) == "no free models matched the filters"
    assert "could not parse" in core_module.format_models("not json", 5)


def test_format_ranking_none(core_module):
    out = core_module.format_ranking(json.dumps({"ranking": [], "selection": None}), 5)
    assert "selection: none" in out
    assert "could not parse" in core_module.format_ranking("[]", 5)


def test_clamp_interval(core_module):
    assert core_module.clamp_interval(5) == 30
    assert core_module.clamp_interval("120") == 120
    assert core_module.clamp_interval(None) == 360


def test_truncate(core_module):
    assert core_module.truncate("x" * 10, 100) == "x" * 10
    out = core_module.truncate("x" * 5000, 100)
    assert len(out) <= 100 and out.endswith("(truncated)")


def test_run_cli_timeout_and_missing(core_module):
    res = asyncio.run(
        core_module.run_cli([sys.executable, "-c", "import time; time.sleep(10)"], {}, timeout=0.5)
    )
    assert res.timed_out and not res.ok and res.meaning == "timed out"
    res = asyncio.run(core_module.run_cli(["/nonexistent/fms-binary"], {}, timeout=5))
    assert res.returncode == 127


def test_exit_meanings(core_module):
    assert core_module.CliResult(3, "", "").meaning.startswith("account")
    assert core_module.CliResult(4, "", "").meaning.startswith("another")
    assert core_module.CliResult(9, "", "").meaning == "exit code 9"
