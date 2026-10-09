"""Bundled provider metadata must survive a real pre-bootstrap interpreter.

Regression for #134107: disable site processing rather than intercepting
selected SDK imports, so newly introduced application dependencies are covered.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


_DISCOVER = """
import importlib.util
import json
import sys

sys.path.insert(0, sys.argv[1])
if sys.flags.no_site:
    assert importlib.util.find_spec("ruamel") is None
    assert importlib.util.find_spec("httpx") is None

from providers import _SOURCES, list_providers
print(json.dumps(sorted(
    profile.name for profile in list_providers()
    if _SOURCES.get(profile.name) == "bundled"
)))
"""


def test_bundled_discovery_matches_bootstrapped_registry_without_site_packages(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    registries = []
    for label, flags in (("bootstrapped", ()), ("prebootstrap", ("-S",))):
        home = tmp_path / label
        home.mkdir()
        env = dict(os.environ, HOME=str(home), USERPROFILE=str(home),
                   HERMES_HOME=str(home / "hermes"))
        result = subprocess.run(
            [sys.executable, "-I", "-B", *flags, "-c", _DISCOVER, str(repo)],
            env=env, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        registries.append(json.loads(result.stdout.strip().splitlines()[-1]))
        assert registries[-1], "Provider discovery must not return an empty registry"
    assert registries[1] == registries[0], (
        f"Providers lost before bootstrap: {sorted(set(registries[0]) - set(registries[1]))}"
    )
