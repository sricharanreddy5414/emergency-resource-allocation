"""Build the Lambda zip files. --check validates them and deletes the output."""

import argparse
import sys
import tempfile
import zipfile
from pathlib import Path

from lambda_manifest import ROOT, package_map


def build_zip(function_name, destination):
    mapping = package_map()[function_name]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for arcname, relative in mapping.items():
            source = ROOT / relative
            if not source.is_file():
                raise SystemExit(f"Missing source for {function_name}: {relative}")
            archive.write(source, arcname)
    return destination


def check_zip(path, function_name):
    expected = set(package_map()[function_name])
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if names != expected:
            raise SystemExit(f"{function_name} zip entries {sorted(names)} != {sorted(expected)}")
        for info in archive.infolist():
            if info.filename.endswith((".env", ".pem")) or "secret" in info.filename.lower():
                raise SystemExit(f"Refusing archive entry {info.filename}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    if args.check:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            for name in package_map():
                path = build_zip(name, folder / f"{name}.zip")
                check_zip(path, name)
                print(f"ok {name} {path.stat().st_size} bytes")
        return 0
    if not args.output:
        raise SystemExit("Pass --check or --output DIR")
    folder = Path(args.output)
    for name in package_map():
        path = build_zip(name, folder / f"{name}.zip")
        check_zip(path, name)
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
