"""Print the pip requirement for the lowest or highest supported Algen Agent Runtime.

CI installs the result so the suite runs against both ends of the range in pyproject.toml:

    python scripts/runtime_requirement.py lowest    # algen-agent-runtime==0.1.0a18
    python scripts/runtime_requirement.py highest   # algen-agent-runtime>=0.1.0a18,<0.2
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

PACKAGE = "algen-agent-runtime"


def runtime_specifier(pyproject: Path) -> str:
    dependencies = tomllib.loads(pyproject.read_text())["project"]["dependencies"]
    for dependency in dependencies:
        if dependency.startswith(PACKAGE):
            return dependency.removeprefix(PACKAGE).strip()
    raise SystemExit(f"{PACKAGE} is not a dependency")


def requirement(bound: str, specifier: str) -> str:
    if bound == "highest":
        return f"{PACKAGE}{specifier}"
    if bound == "lowest":
        match = re.search(r">=\s*([^,\s]+)", specifier)
        if match is None:
            raise SystemExit(f"{PACKAGE}{specifier} has no lower bound")
        return f"{PACKAGE}=={match.group(1)}"
    raise SystemExit("usage: runtime_requirement.py lowest|highest")


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    print(
        requirement(
            sys.argv[1] if len(sys.argv) > 1 else "", runtime_specifier(root / "pyproject.toml")
        )
    )
