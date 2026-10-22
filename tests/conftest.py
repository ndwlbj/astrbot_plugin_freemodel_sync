"""Minimal stand-ins for the AstrBot APIs the plugin uses.

Only the surface documented in AstrBot's plugin guide is modelled:
``filter.command_group`` / ``.command``, ``filter.permission_type``,
``PermissionType.ADMIN``, ``Star``, ``Context.send_message``,
``MessageChain().message()``, ``event.plain_result`` / ``is_admin`` /
``unified_msg_origin`` and ``AstrBotConfig.save_config``.
"""

from __future__ import annotations

import enum
import importlib
import importlib.util
import logging
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG = "astrbot_plugin_freemodel_sync"


class PermissionType(enum.Enum):
    ADMIN = "admin"
    MEMBER = "member"


class _Group:
    def __init__(self, name):
        self.name = name
        self.commands = {}

    def command(self, name, alias=None, **kwargs):
        def decorator(fn):
            fn._fms_command = (self.name, name)
            self.commands[name] = fn
            return fn

        return decorator


def command_group(name, alias=None, **kwargs):
    def decorator(fn):
        return _Group(name)

    return decorator


def command(name, alias=None, **kwargs):
    def decorator(fn):
        fn._fms_command = (None, name)
        return fn

    return decorator


def permission_type(ptype, raise_error=True, **kwargs):
    def decorator(fn):
        fn._fms_permission = ptype
        return fn

    return decorator


class MessageChain:
    def __init__(self):
        self.chain = []

    def message(self, text):
        self.chain.append(text)
        return self


class AstrMessageEvent:
    def __init__(self, admin=True, origin="test:FriendMessage:1"):
        self.role = "admin" if admin else "member"
        self.unified_msg_origin = origin

    def is_admin(self):
        return self.role == "admin"

    def plain_result(self, text):
        return text


class AstrBotConfig(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.saved = 0

    def save_config(self, replace_config=None):
        self.saved += 1


class Context:
    def __init__(self):
        self.sent = []

    async def send_message(self, session, chain):
        self.sent.append((session, "".join(chain.chain)))
        return True


class Star:
    def __init__(self, context, config=None):
        self.context = context


def _install_stubs():
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.logger = logging.getLogger("astrbot")
    api.AstrBotConfig = AstrBotConfig
    event = types.ModuleType("astrbot.api.event")
    flt = types.SimpleNamespace(
        command=command,
        command_group=command_group,
        permission_type=permission_type,
        PermissionType=PermissionType,
    )
    event.filter = flt
    event.AstrMessageEvent = AstrMessageEvent
    event.MessageChain = MessageChain
    star = types.ModuleType("astrbot.api.star")
    star.Context = Context
    star.Star = Star
    astrbot.api = api
    api.event = event
    api.star = star
    sys.modules.update(
        {"astrbot": astrbot, "astrbot.api": api, "astrbot.api.event": event, "astrbot.api.star": star}
    )


def _load_plugin():
    """Import the repo as package ``astrbot_plugin_freemodel_sync`` whatever
    the checkout directory is called (AstrBot imports ``data.plugins.<name>.main``)."""

    if f"{PKG}.main" in sys.modules:
        return sys.modules[f"{PKG}.main"]
    _install_stubs()
    spec = importlib.util.spec_from_file_location(
        PKG, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[PKG] = package
    if (ROOT / "__init__.py").exists():
        spec.loader.exec_module(package)

    return importlib.import_module(f"{PKG}.main")


@pytest.fixture(scope="session")
def plugin_module():
    return _load_plugin()


@pytest.fixture(scope="session")
def core_module(plugin_module):
    return sys.modules[f"{PKG}.fms_core"]


@pytest.fixture
def stubs():
    return types.SimpleNamespace(
        AstrBotConfig=AstrBotConfig,
        AstrMessageEvent=AstrMessageEvent,
        Context=Context,
        PermissionType=PermissionType,
    )


@pytest.fixture(autouse=True)
def _no_real_keys(monkeypatch):
    for name in ("OPENROUTER_API_KEY", "GROQ_API_KEY", "SILICONFLOW_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
