#!/usr/bin/env python3
"""Source-only version bumps and create-only, fail-closed publication."""

import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import tempfile


PROJECT = Path("Convert Color Space.xcodeproj/project.pbxproj")
PLIST = Path("Info.plist")
VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
KEYS = ("MARKETING_VERSION", "CURRENT_PROJECT_VERSION")
REFERENCES = {f"$({key})" for key in KEYS}


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE, **kwargs).stdout


def version_fields(text, key):
    fields = list(re.finditer(rf"\b{key}\s*=\s*([^;\n]+);", text))
    assignments = re.findall(rf"\b{key}\s*=", text)
    if not fields or len(fields) != len(assignments):
        raise ValueError(f"Missing or malformed {key} in {PROJECT}")
    values = [field[1].strip() for field in fields]
    if any(not re.fullmatch(VERSION, value) for value in values):
        raise ValueError(f"Invalid {key}: {values}")
    return fields, values


def read_version():
    text = PROJECT.read_text()
    values = []
    counts = []
    for key in KEYS:
        fields, versions = version_fields(text, key)
        values.extend(versions)
        counts.append(len(fields))
    if counts[0] != counts[1]:
        raise ValueError("Missing coordinated Xcode version fields")
    data = plistlib.loads(PLIST.read_bytes())
    for key in ("CFBundleShortVersionString", "CFBundleVersion"):
        if key not in data:
            if key == "CFBundleShortVersionString":
                raise ValueError(f"Missing {key} in {PLIST}")
            continue
        value = data[key]
        if not isinstance(value, str):
            raise ValueError(f"Invalid {key}: expected a string")
        if value in REFERENCES:
            continue
        if not re.fullmatch(VERSION, value):
            raise ValueError(f"Invalid {key}: {value!r}")
        values.append(value)
    if len(set(values)) != 1:
        raise ValueError(f"Coordinated source versions have drifted: {values}")
    return values[0]


def bump_version(mode):
    current = read_version()
    parts = list(map(int, current.split(".")))
    index = ("major", "minor", "patch").index(mode)
    parts[index] += 1
    parts[index + 1:] = [0] * (2 - index)
    new = ".".join(map(str, parts))
    text = PROJECT.read_text()
    for key in KEYS:
        text = re.sub(rf"(\b{key}\s*=\s*)[^;\n]+;", rf"\g<1>{new};", text)
    # Keep plist formatting, absent CFBundleVersion, and valid Xcode references.
    plist = PLIST.read_text()
    for key in ("CFBundleShortVersionString", "CFBundleVersion"):
        plist = re.sub(
            rf"(<key>{key}</key>\s*<string>){VERSION}(</string>)",
            rf"\g<1>{new}\g<2>", plist,
        )
    PROJECT.write_text(text)
    PLIST.write_text(plist)
    if read_version() != new:
        raise ValueError("Version rewrite did not update all coordinated metadata")
    return new


def require_sha(sha):
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Expected an immutable 40-character commit SHA")
    if run("git", "rev-parse", "HEAD").strip() != sha:
        raise ValueError("Checkout does not match the selected github.sha")


def remote_refs(*refs):
    output = run("git", "ls-remote", "origin", *refs)
    result = {}
    for line in output.splitlines():
        match = re.fullmatch(r"([0-9a-f]{40})\t(refs/\S+)", line)
        if not match or match[2] in result:
            raise ValueError("Malformed or duplicate remote ref response")
        result[match[2]] = match[1]
    return result


def require_branch(branch, sha):
    ref = f"refs/heads/{branch}"
    run("git", "check-ref-format", ref)
    if remote_refs(ref) != {ref: sha}:
        raise ValueError("Selected branch moved or disappeared; dispatch Bump version again")


def bump_branch(branch, sha, mode):
    require_sha(sha)
    require_branch(branch, sha)
    if run("git", "status", "--porcelain").strip():
        raise ValueError("Bump requires a clean checkout")
    new = bump_version(mode)
    run("git", "add", "--", str(PROJECT), str(PLIST))
    run(
        "git", "commit", "-m", f"chore: bump version to {new}",
        "-m", "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>",
    )
    require_branch(branch, sha)
    # Check Git's advertised old SHA too: a last-moment deletion/rewind must
    # not become an otherwise-valid branch creation/fast-forward.
    with tempfile.TemporaryDirectory() as hooks:
        hook = Path(hooks) / "pre-push"
        hook.write_text(
            "#!/usr/bin/env python3\nimport sys\n"
            "rows = [line.split() for line in sys.stdin]\n"
            f"expected = {('refs/heads/' + branch, sha)!r}\n"
            "if len(rows) != 1 or len(rows[0]) != 4 or tuple(rows[0][2:]) != expected:\n"
            "    sys.exit('Selected branch changed at push; dispatch Bump version again')\n"
        )
        hook.chmod(0o755)
        # Non-force fast-forward only; override even a global push.followTags=true.
        run("git", "-c", "push.followTags=false", "-c", f"core.hooksPath={hooks}",
            "push", "--porcelain", "origin", f"HEAD:refs/heads/{branch}")
    return new


def repository():
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Missing or invalid GITHUB_REPOSITORY")
    if not os.environ.get("GH_TOKEN"):
        raise ValueError("Authenticated GH_TOKEN is required to include draft releases")
    return repo


def api(method, endpoint, payload=None):
    args = ["gh", "api", "--method", method, endpoint]
    if payload is not None:
        args += ["--input", "-"]
    raw = run(*args, input=json.dumps(payload) if payload is not None else None)
    return json.loads(raw)


def release_record(record):
    if (
        not isinstance(record, dict)
        or type(record.get("id")) is not int or record["id"] <= 0
        or not isinstance(record.get("tag_name"), str) or not record["tag_name"]
        or type(record.get("draft")) is not bool
        or type(record.get("prerelease")) is not bool
    ):
        raise ValueError("Malformed release API record")
    return record


def available(tag):
    repo = repository()
    if remote_refs(f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"):
        raise ValueError(f"Remote tag {tag} already exists; nothing will be overwritten")
    seen = set()
    page = 1
    while True:
        records = api("GET", f"repos/{repo}/releases?per_page=100&page={page}")
        if not isinstance(records, list) or len(records) > 100:
            raise ValueError("Malformed releases API page")
        if not records:
            return
        for item in records:
            record = release_record(item)
            if record["id"] in seen:
                raise ValueError("Repeated release ID across API pages")
            seen.add(record["id"])
            if record["tag_name"] == tag:
                raise ValueError(f"Release {tag} already exists (including drafts)")
        page += 1


def publish(sha, tag, assets):
    require_sha(sha)
    if tag != f"v{read_version()}":
        raise ValueError("Publication tag differs from coordinated source version")
    for asset in assets:
        if not Path(asset).is_file() or Path(asset).stat().st_size == 0:
            raise ValueError(f"Missing or empty release asset: {asset}")
    # This is deliberately the last check before creating any remote state.
    available(tag)
    output = run("git", "-c", "push.followTags=false", "push", "--porcelain",
                 "origin", f"{sha}:refs/tags/{tag}")
    # Git succeeds with '=' on a same-SHA creation race: reject that too.
    statuses = [line.split("\t") for line in output.splitlines() if "\t" in line]
    if len(statuses) != 1 or statuses[0][0] != "*":
        raise ValueError("Tag was not newly created; possible concurrent publisher")
    if remote_refs(f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}") != {f"refs/tags/{tag}": sha}:
        raise ValueError("Remote tag target does not equal selected github.sha")
    repo = repository()
    record = release_record(api("POST", f"repos/{repo}/releases", {
        "tag_name": tag,
        "target_commitish": sha,
        "name": tag,
        "draft": False,
        "prerelease": False,
        "body": "",
    }))
    if (record["tag_name"] != tag or record["draft"] or record["prerelease"]
            or record.get("name") != tag or record.get("target_commitish") != sha):
        raise ValueError("Created release response differs from requested publication")
    print(f"Created release ID {record['id']} for {tag}", flush=True)
    # No --clobber, temporary draft, deletion, update, rollback, or retry.
    run("gh", "release", "upload", tag, *assets, "--repo", repo)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("version")
    bump = sub.add_parser("bump")
    bump.add_argument("--branch", required=True)
    bump.add_argument("--sha", required=True)
    bump.add_argument("--mode", choices=("patch", "minor", "major"), required=True)
    check = sub.add_parser("check")
    check.add_argument("--sha", required=True)
    release = sub.add_parser("publish")
    release.add_argument("--sha", required=True)
    release.add_argument("--tag", required=True)
    release.add_argument("assets", nargs=2)
    args = parser.parse_args()
    if args.command == "version":
        print(read_version())
    elif args.command == "bump":
        print(bump_branch(args.branch, args.sha, args.mode))
    elif args.command == "check":
        require_sha(args.sha)
        version = read_version()
        available(f"v{version}")
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(f"version={version}\ntag=v{version}\n")
    else:
        publish(args.sha, args.tag, args.assets)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"::error::{error}", file=sys.stderr)
        print("Stopped without rollback or retry. Inspect remote tag, releases (including "
              "drafts), and assets before any manual recovery; partial state is preserved.",
              file=sys.stderr)
        sys.exit(1)
