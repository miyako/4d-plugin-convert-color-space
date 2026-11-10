#!/usr/bin/env python3
"""Compile actual plugin/wrappers and full unmodified SDK; no project build phases."""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SDK_SHA256 = "355ee208ad6c61dcc68e8432546fc42484888211dd07ed8fa3fdb4440f3edbd8"
MODES = ("path", "bytes", "copy", "gray", "size", "thumbnail", "arrays", "scalar", "dispatcher")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", help="immutable full 40-character source commit SHA")
    args = parser.parse_args()
    if args.baseline and (len(args.baseline) != 40 or
                          any(c not in "0123456789abcdef" for c in args.baseline)):
        parser.error("--baseline must be a full lowercase commit SHA")

    with tempfile.TemporaryDirectory(prefix="color-space-ownership-") as directory:
        temp = Path(directory)
        source = ROOT
        if args.baseline:
            source = temp / "baseline"
            files = subprocess.check_output(
                ["git", "ls-tree", "-r", "--name-only", args.baseline], cwd=ROOT,
                text=True).splitlines()
            for name in files:
                if name.endswith((".c", ".cpp", ".h", ".pch")):
                    target = source / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(subprocess.check_output(
                        ["git", "show", f"{args.baseline}:{name}"], cwd=ROOT))
        sdk = source / "4D Plugin API"
        if hashlib.sha256((sdk / "4DPluginAPI.c").read_bytes()).hexdigest() != SDK_SHA256:
            raise SystemExit("SDK provenance changed: review the shim contract before running")
        binary = temp / "native-ownership"
        sources = [sdk / "4DPluginAPI.c", *sorted((sdk / "Classes").glob("*.cpp")),
                   source / "4DPlugin.cpp", ROOT / "tests/native_ownership.cpp"]
        command = [
            "xcrun", "clang++", "-std=c++11", "-x", "objective-c++",
            "-include", str(source / "4D Plugin_Prefix.pch"),
            "-I", str(sdk), "-I", str(sdk / "Classes"),
            "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-g", "-O1",
            "-Wno-deprecated-declarations", "-Wno-ignored-attributes",
            *map(str, sources), "-framework", "Cocoa", "-framework", "Carbon",
            "-o", str(binary),
        ]
        subprocess.run(command, cwd=ROOT, check=True)
        env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0:halt_on_error=1",
                   UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1")
        failures = []
        for mode in MODES:
            for empty in ("allocated", "null"):
                result = subprocess.run([str(binary), mode, empty], env=env,
                                        text=True, capture_output=True)
                if result.returncode:
                    failures.append((mode, empty))
                    print(f"FAIL {mode} ({empty} empty)")
                    print(result.stderr)
                else:
                    print(result.stdout.strip())
        print(f"{len(MODES) * 2 - len(failures)}/{len(MODES) * 2} cases passed")
        return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
