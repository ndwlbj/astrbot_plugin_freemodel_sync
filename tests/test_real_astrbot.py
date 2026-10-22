"""Optional check against a real AstrBot install.

Runs in a subprocess (the other tests stub ``astrbot``). Skipped unless
AstrBot is importable in the current interpreter, e.g.::

    pip install AstrBot   # or: pip install /path/to/AstrBot checkout
"""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = textwrap.dedent(
    """
    import importlib, json, os, sys
    root, workdir = sys.argv[1], sys.argv[2]
    os.makedirs(os.path.join(workdir, "data", "plugins"), exist_ok=True)
    link = os.path.join(workdir, "data", "plugins", "astrbot_plugin_freemodel_sync")
    if not os.path.exists(link):
        os.symlink(root, link)
    sys.path.insert(0, workdir)
    from astrbot.core.star.star_handler import star_handlers_registry
    from astrbot.core.star.filter.permission import PermissionType, PermissionTypeFilter
    from astrbot.core.star.filter.command import CommandFilter
    from astrbot.core.star.star import star_map
    importlib.import_module("data.plugins.astrbot_plugin_freemodel_sync.main")
    out = {}
    for md in star_handlers_registry:
        if not md.handler_module_path.startswith("data.plugins.astrbot_plugin_freemodel_sync"):
            continue
        names, admin = [], False
        for f in md.event_filters:
            if isinstance(f, CommandFilter):
                names += sorted(f.get_complete_command_names())
            if isinstance(f, PermissionTypeFilter) and f.permission_type == PermissionType.ADMIN:
                admin = True
        out[md.handler_name] = {"commands": names, "admin": admin}
    meta = star_map.get("data.plugins.astrbot_plugin_freemodel_sync.main")
    out["__star__"] = meta.star_cls_type.__name__ if meta else None
    print(json.dumps(out))
    """
)


def _astrbot_available() -> bool:
    probe = subprocess.run(
        [sys.executable, "-c", "import astrbot.api.event, astrbot.api.star"],
        capture_output=True,
        timeout=120,
    )
    return probe.returncode == 0


@pytest.mark.skipif(not _astrbot_available(), reason="AstrBot is not installed")
def test_handlers_register_with_real_astrbot(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-c", SCRIPT, str(ROOT), str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data.pop("__star__") == "FreeModelSyncPlugin"
    commands = {name: info for name, info in data.items() if info["commands"]}
    assert {c for info in commands.values() for c in info["commands"]} >= {
        "fms list",
        "fms rank",
        "fms dryrun",
        "fms sync",
        "fms status",
        "fms auto",
    }
    for name, info in commands.items():
        assert info["admin"], f"{name} is not admin-only"
