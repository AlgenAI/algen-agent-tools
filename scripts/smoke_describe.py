"""Check an installed wheel end to end: every plugin it ships loads by entry point and
``algen-agent-runtime describe`` lists its tools.

Run it with the interpreter of a fresh virtualenv, from outside the source tree's ``src``:

    /tmp/venv/bin/python scripts/smoke_describe.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from importlib import metadata
from pathlib import Path

GROUP = "algen_agent_runtime.plugins"
PREFIX = "algen_agent_tools"


def main() -> None:
    import algen_agent_tools

    if "site-packages" not in (algen_agent_tools.__file__ or ""):
        raise SystemExit(f"algen_agent_tools was imported from {algen_agent_tools.__file__}")
    plugins = sorted(
        entry.name
        for entry in metadata.entry_points(group=GROUP)
        if entry.name.startswith(f"{PREFIX}.")
    )
    if f"{PREFIX}.sample" not in plugins:
        raise SystemExit(f"the sample plugin is not installed: {plugins}")
    config = {
        "providers": {"demo": {"type": "mock", "default_model": "m"}},
        "agents": [
            {
                "name": "smoke",
                "version": "1.0.0",
                "description": "Loads every installed connector plugin.",
                "system_instructions": "Unused.",
                "default_model": {"name": "m", "provider": "demo"},
                "domain_plugins": plugins,
                "enabled_tools": ["sample.echo"],
                "tool_permissions": ["sample.read"],
            }
        ],
        "security": {"trusted_plugin_prefixes": [PREFIX]},
    }
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "agent.yaml"
        path.write_text(json.dumps(config))  # JSON is valid YAML
        cli = Path(sys.executable).with_name("algen-agent-runtime")
        output = subprocess.run(
            [str(cli), "describe", "--config", str(path), "--json"],
            check=True,
            capture_output=True,
            text=True,
            cwd=directory,
        ).stdout
    inventory = json.loads(output)
    sources = {tool["name"]: tool["source"] for tool in inventory["tools"]}
    if sources.get("sample.echo") != f"{PREFIX}.sample":
        raise SystemExit(f"describe did not list sample.echo from the plugin: {sources}")
    loaded = sorted(plugin["reference"] for plugin in inventory["plugins"])
    if loaded != plugins:
        raise SystemExit(f"describe loaded {loaded}, expected {plugins}")
    ours = sorted(name for name, source in sources.items() if source.startswith(PREFIX))
    print(f"described {len(ours)} tools from {len(plugins)} plugins: {', '.join(ours)}")


if __name__ == "__main__":
    main()
