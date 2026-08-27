from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    project_version = str(
        tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    )
    init_text = (ROOT / "src/wardlens/__init__.py").read_text(encoding="utf-8")
    init_match = re.search(r'^__version__\s*=\s*"([^"]+)"', init_text, re.MULTILINE)
    version_info = (ROOT / "scripts/version_info.txt").read_text(encoding="utf-8")
    file_match = re.search(r"StringStruct\('FileVersion', '([^']+)'\)", version_info)
    product_match = re.search(r"StringStruct\('ProductVersion', '([^']+)'\)", version_info)
    versions = {
        "pyproject.toml": project_version,
        "src/wardlens/__init__.py": init_match.group(1) if init_match else "missing",
        "scripts/version_info.txt FileVersion": file_match.group(1) if file_match else "missing",
        "scripts/version_info.txt ProductVersion": product_match.group(1)
        if product_match
        else "missing",
    }
    mismatched = {name: value for name, value in versions.items() if value != project_version}
    if mismatched:
        detail = ", ".join(f"{name}={value}" for name, value in mismatched.items())
        raise SystemExit(f"Version mismatch: expected {project_version}; {detail}")
    tag = os.getenv("GITHUB_REF_NAME", "")
    if tag.startswith("v") and tag[1:] != project_version:
        raise SystemExit(f"Tag {tag} does not match project version {project_version}.")
    print(f"Version consistency passed: {project_version}")


if __name__ == "__main__":
    main()
