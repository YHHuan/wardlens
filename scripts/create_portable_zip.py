from __future__ import annotations

import argparse
import zipfile
from pathlib import Path


def create_portable_zip(package_root: Path, output: Path) -> None:
    package_root = package_root.resolve()
    output = output.resolve()
    if not package_root.is_dir():
        raise SystemExit(f"Package root does not exist: {package_root}")
    if output == package_root or package_root in output.parents:
        raise SystemExit("Output ZIP cannot be inside the packaged directory.")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    with zipfile.ZipFile(
        output,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as archive:
        for source in sorted(package_root.rglob("*")):
            if source.is_symlink():
                raise SystemExit(f"Symlinks are forbidden in the portable package: {source}")
            if not source.is_file():
                continue
            relative = source.relative_to(package_root)
            archive_name = (Path(package_root.name) / relative).as_posix()
            archive.write(source, archive_name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("package_root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    create_portable_zip(args.package_root, args.output)


if __name__ == "__main__":
    main()
