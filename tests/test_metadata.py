import ast
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_TYPES = {"string", "text", "int", "float", "bool", "object", "list", "dict", "template_list", "file"}


def test_metadata_required_fields():
    meta = yaml.safe_load((ROOT / "metadata.yaml").read_text(encoding="utf-8"))
    for field in ("name", "desc", "version", "author"):  # AstrBot PLUGIN_METADATA_REQUIRED_FIELDS
        assert isinstance(meta.get(field), str) and meta[field]
    assert meta["name"] == "astrbot_plugin_freemodel_sync"
    assert not meta["astrbot_version"].startswith("v")
    # plugin-publish docs: semantic version without a leading "v", repo URL required
    assert re.fullmatch(r"\d+\.\d+\.\d+", meta["version"])
    assert meta["repo"] == "https://github.com/ndwlbj/astrbot_plugin_freemodel_sync"
    assert meta["social_link"].startswith("https://")
    assert isinstance(meta["tags"], list) and all(isinstance(t, str) and t for t in meta["tags"])


def test_conf_schema_valid():
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    for name, item in schema.items():
        assert item["type"] in SCHEMA_TYPES, name
        assert "description" in item, name
    for name in ("openrouter_api_key", "groq_api_key"):
        assert schema[name]["secret"] is True
        assert schema[name]["default"] == ""
    assert schema["auto_sync_enabled"]["default"] is False


def test_requirements_pin():
    lines = [line.strip() for line in (ROOT / "requirements.txt").read_text().splitlines() if line.strip()]
    assert lines == ["freemodel-sync>=0.3.0"]


def test_main_uses_only_documented_astrbot_api():
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("astrbot"):
            imported |= {(node.module, alias.name) for alias in node.names}
    assert imported <= {
        ("astrbot.api", "AstrBotConfig"),
        ("astrbot.api", "logger"),
        ("astrbot.api.event", "AstrMessageEvent"),
        ("astrbot.api.event", "MessageChain"),
        ("astrbot.api.event", "filter"),
        ("astrbot.api.star", "Context"),
        ("astrbot.api.star", "Star"),
    }
