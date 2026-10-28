from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHERS = (
    ROOT / "packaging/Start-WardLens.bat",
    ROOT / "packaging/Diagnose-WardLens.bat",
)
NATIVE_SUFFIXES = {".com", ".dll", ".exe", ".pyd", ".sys"}
GENERATED_SUFFIXES = {".pyc", ".pyo"}
EXPECTED_PACKAGES = {
    "beautifulsoup4",
    "certifi",
    "charset-normalizer",
    "idna",
    "requests",
    "soupsieve",
    "typing-extensions",
    "urllib3",
}


def _check_launchers() -> list[str]:
    errors: list[str] = []
    for path in LAUNCHERS:
        data = path.read_bytes()
        try:
            text = data.decode("ascii")
        except UnicodeDecodeError:
            errors.append(f"{path.name} must contain ASCII only")
            continue
        if b"\r\n" not in data or b"\n" in data.replace(b"\r\n", b""):
            errors.append(f"{path.name} must use CRLF line endings only")
        lowered = text.lower()
        for forbidden in ("executionpolicy", "powershell", "bitsadmin", "certutil"):
            if forbidden in lowered:
                errors.append(f"{path.name} contains forbidden launcher command: {forbidden}")
        required_tokens = ("runtime\\python.exe", " -i ", " -b ", "-x utf8", "-m wardlens")
        if any(token not in lowered for token in required_tokens):
            errors.append(f"{path.name} must invoke the isolated bundled Python runtime")
    return errors


def _check_runtime_manifest() -> list[str]:
    errors: list[str] = []
    path = ROOT / "packaging/portable-runtime.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema_version",
        "implementation",
        "version",
        "architecture",
        "publisher",
        "url",
        "sha256",
        "spdx_url",
    }
    if set(payload) != required:
        errors.append("portable-runtime.json fields do not match the locked schema")
    if payload.get("schema_version") != 1:
        errors.append("portable runtime schema_version must be 1")
    if payload.get("publisher") != "Python Software Foundation":
        errors.append("portable runtime publisher must be Python Software Foundation")
    version = str(payload.get("version", ""))
    expected_prefix = f"https://www.python.org/ftp/python/{version}/python-{version}-"
    if not str(payload.get("url", "")).startswith(expected_prefix):
        errors.append("portable runtime URL must be version-pinned on python.org")
    if not str(payload.get("spdx_url", "")).startswith(expected_prefix):
        errors.append("portable runtime SPDX URL must be version-pinned on python.org")
    if not re.fullmatch(r"[0-9a-f]{64}", str(payload.get("sha256", ""))):
        errors.append("portable runtime SHA-256 must be 64 lowercase hex characters")
    return errors


def _check_requirements() -> list[str]:
    errors: list[str] = []
    path = ROOT / "packaging/portable-requirements.txt"
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    requirements = [line for line in lines if line and not line.startswith("#")]
    if not requirements:
        errors.append("portable requirements cannot be empty")
    names: set[str] = set()
    for requirement in requirements:
        match = re.fullmatch(
            r"([a-z0-9-]+)==([^\s]+) --hash=sha256:([0-9a-f]{64})", requirement
        )
        if not match or any(token in requirement for token in ("http:", "https:", "@")):
            errors.append(f"portable dependency is not exactly pinned: {requirement}")
            continue
        name = match.group(1)
        if name in names:
            errors.append(f"portable dependency is duplicated: {name}")
        names.add(name)
    if names != EXPECTED_PACKAGES:
        errors.append("portable dependency names do not match the reviewed allowlist")
    return errors


def _check_package(path: Path) -> list[str]:
    errors: list[str] = []
    required = (
        "WardLens.exe",
        "drivers/README.txt",
        "Start-WardLens.bat",
        "Diagnose-WardLens.bat",
        "README-FIRST.txt",
        "runtime/python.exe",
        "runtime/pythonw.exe",
        "runtime/python314.dll",
        "runtime/python314._pth",
        "runtime/DLLs/python.cat",
        "app/wardlens/__main__.py",
        "packages/requests/__init__.py",
        "packages/bs4/__init__.py",
        "THIRD-PARTY/PYTHON-RUNTIME-SPDX.json",
        "THIRD-PARTY/PYTHON-PACKAGES.txt",
        "BUILD_PROVENANCE.txt",
    )
    for relative in required:
        if not (path / relative).is_file():
            errors.append(f"portable package is missing {relative}")
    for candidate in path.iterdir():
        if candidate.is_file() and candidate.suffix.lower() in NATIVE_SUFFIXES and candidate.name != "WardLens.exe":
            errors.append(f"unrecognized root native binary: {candidate.name}")
    if (path / "drivers").exists():
        for candidate in (path / "drivers").rglob("*"):
            if candidate.is_file() and candidate.suffix.lower() in NATIVE_SUFFIXES:
                errors.append("Public release must not redistribute Microsoft WebDriver; use personal preparation instead")
    for parent in (path / "app", path / "packages"):
        if not parent.exists():
            continue
        for candidate in parent.rglob("*"):
            if candidate.is_file() and candidate.suffix.lower() in NATIVE_SUFFIXES:
                errors.append(f"non-runtime native binary is forbidden: {candidate.relative_to(path)}")
            if candidate.is_file() and candidate.suffix.lower() in GENERATED_SUFFIXES:
                errors.append(f"generated Python bytecode is forbidden: {candidate.relative_to(path)}")
    expected_pth = ".\nLib\nDLLs\n..\\app\n..\\packages\nimport site\n"
    pth_path = path / "runtime/python314._pth"
    if pth_path.is_file() and pth_path.read_text(encoding="ascii") != expected_pth:
        errors.append("python314._pth does not contain the locked isolated search path")
    packaged_requirements = path / "THIRD-PARTY/PYTHON-PACKAGES.txt"
    if packaged_requirements.is_file() and packaged_requirements.read_bytes() != (
        ROOT / "packaging/portable-requirements.txt"
    ).read_bytes():
        errors.append("packaged dependency lock differs from the source lock")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path)
    args = parser.parse_args()
    errors = _check_launchers() + _check_runtime_manifest() + _check_requirements()
    if args.package:
        errors.extend(_check_package(args.package.resolve()))
    if errors:
        raise SystemExit("Portable validation failed:\n- " + "\n- ".join(errors))
    print("Portable source/package validation passed.")


if __name__ == "__main__":
    main()
