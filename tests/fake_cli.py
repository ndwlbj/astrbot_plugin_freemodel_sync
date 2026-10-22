"""Stand-in for ``python -m freemodel_sync`` used by the tests.

Behaviour is driven by FAKE_FMS_MODE; the received argv and the key it got
are printed so the tests can check what reached the subprocess (and that the
plugin redacts it before it reaches chat).
"""

import json
import os
import sys
import time

args = sys.argv[1:]
mode = os.environ.get("FAKE_FMS_MODE", "ok")
log = os.environ.get("FAKE_FMS_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"argv": args, "key": os.environ.get("OPENROUTER_API_KEY", "")}) + "\n")

if mode == "sleep":
    time.sleep(30)
if mode == "account":
    print("no usable model: probing was stopped by an account/quota problem", file=sys.stderr)
    sys.exit(3)
if mode == "leak":
    key = os.environ.get("OPENROUTER_API_KEY", "")
    print(f"debug: Authorization: Bearer {key}")
    print("-OPENAI_API_KEY=old-secret-value-123")
    print("+OPENAI_MODEL=meta/llama:free")
    sys.exit(1)

if "discover" in args:
    print(
        json.dumps(
            [
                {
                    "provider": "openrouter",
                    "id": "meta/llama-3.3-70b:free",
                    "name": "Llama",
                    "context_length": 131072,
                    "supports_tools": True,
                    "expiration_date": None,
                },
                {
                    "provider": "openrouter",
                    "id": "qwen/qwen3:free",
                    "name": "Qwen",
                    "context_length": 32768,
                    "supports_tools": False,
                    "expiration_date": "2026-12-01",
                },
                {
                    "provider": "groq",
                    "id": "llama-3.1-8b-instant",
                    "name": "Llama 8B",
                    "context_length": 1000000,
                    "supports_tools": True,
                    "expiration_date": None,
                },
            ]
        )
    )
elif "rank" in args:
    print(
        json.dumps(
            {
                "providers": ["openrouter"],
                "ranking": [
                    {
                        "key": "openrouter/meta/llama-3.3-70b:free",
                        "id": "meta/llama-3.3-70b:free",
                        "score": 0.91,
                        "test": {"ok": True},
                    },
                    {
                        "key": "openrouter/qwen/qwen3:free",
                        "id": "qwen/qwen3:free",
                        "score": 0.5,
                        "test": None,
                    },
                ],
                "selection": {
                    "provider": "openrouter",
                    "primary": "meta/llama-3.3-70b:free",
                    "primary_key": "openrouter/meta/llama-3.3-70b:free",
                    "fallbacks": ["qwen/qwen3:free"],
                    "untested_fallbacks": ["qwen/qwen3:free"],
                },
            }
        )
    )
elif "sync" in args:
    print("selection: openrouter/meta/llama-3.3-70b:free (fallbacks: qwen/qwen3:free)")
    print("updated: openai-env ~/project/.env")
else:
    print("unknown", file=sys.stderr)
    sys.exit(1)
