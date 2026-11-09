import contextlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import lifecycle as lc


ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 40
FIXTURE_VERSION = "2.7.9"
FIXTURE_PROJECT = """buildSettings = {
    MARKETING_VERSION = 2.7.9;
    CURRENT_PROJECT_VERSION = 2.7.9;
};
buildSettings = {
    MARKETING_VERSION = 2.7.9;
    CURRENT_PROJECT_VERSION = 2.7.9;
};
"""


@contextlib.contextmanager
def directory(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def fixture():
    lc.PROJECT.parent.mkdir(parents=True, exist_ok=True)
    lc.PROJECT.write_text(FIXTURE_PROJECT)
    lc.PLIST.write_bytes(plistlib.dumps({
        "CFBundleShortVersionString": FIXTURE_VERSION,
        "CFBundleVersion": FIXTURE_VERSION,
        "CFBundleInfoDictionaryVersion": "6.0",
    }))


def record(tag="v0.0.1", number=1, **extra):
    return {"id": number, "tag_name": tag, "draft": False, "prerelease": False, **extra}


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cwd = directory(self.temp.name)
        self.cwd.__enter__()
        self.addCleanup(self.cwd.__exit__, None, None, None)
        fixture()

    def test_patch_minor_major_stable_fixtures(self):
        for mode, expected in (("patch", "2.7.10"), ("minor", "2.8.0"), ("major", "3.0.0")):
            with self.subTest(mode=mode):
                fixture()
                self.assertEqual(lc.bump_version(mode), expected)
                self.assertEqual(lc.read_version(), expected)
                self.assertEqual(lc.PROJECT.read_text().count(expected), 4)
                data = plistlib.loads(lc.PLIST.read_bytes())
                self.assertEqual(data["CFBundleVersion"], expected)
                self.assertEqual(data["CFBundleInfoDictionaryVersion"], "6.0")

    def test_invalid_and_drifted_project_values(self):
        for value in ("2.7", "v2.7.9", "2.7.9-beta", "02.7.9", "$(OTHER)", "2.7.8"):
            with self.subTest(value=value):
                fixture()
                lc.PROJECT.write_text(FIXTURE_PROJECT.replace(FIXTURE_VERSION, value, 1))
                before = lc.PROJECT.read_bytes(), lc.PLIST.read_bytes()
                with self.assertRaises(ValueError):
                    lc.bump_version("patch")
                self.assertEqual(before, (lc.PROJECT.read_bytes(), lc.PLIST.read_bytes()))

    def test_missing_fields(self):
        for key in lc.KEYS:
            fixture()
            lc.PROJECT.write_text(FIXTURE_PROJECT.replace(f"    {key} = 2.7.9;\n", "", 1))
            with self.assertRaises(ValueError):
                lc.read_version()
        fixture()
        lc.PLIST.write_bytes(plistlib.dumps({"CFBundleVersion": FIXTURE_VERSION}))
        with self.assertRaises(ValueError):
            lc.read_version()

    def test_malformed_assignment_is_not_ignored(self):
        lc.PROJECT.write_text(FIXTURE_PROJECT + "\nMARKETING_VERSION = broken\n")
        with self.assertRaises(ValueError):
            lc.read_version()

    def test_plist_invalid_drift_and_unknown_references(self):
        for key in ("CFBundleShortVersionString", "CFBundleVersion"):
            for value in ("2.7.8", "2.7", "02.7.9", "$(UNKNOWN)", "${MARKETING_VERSION}", 279):
                with self.subTest(key=key, value=value):
                    fixture()
                    data = plistlib.loads(lc.PLIST.read_bytes())
                    data[key] = value
                    lc.PLIST.write_bytes(plistlib.dumps(data))
                    with self.assertRaises(ValueError):
                        lc.read_version()

    def test_known_references_preserved(self):
        for short in lc.REFERENCES:
            for build in lc.REFERENCES:
                fixture()
                data = plistlib.loads(lc.PLIST.read_bytes())
                data.update(CFBundleShortVersionString=short, CFBundleVersion=build)
                lc.PLIST.write_bytes(plistlib.dumps(data))
                original = lc.PLIST.read_bytes()
                self.assertEqual(lc.bump_version("minor"), "2.8.0")
                self.assertEqual(lc.PLIST.read_bytes(), original)

    def test_absent_bundle_version_not_added(self):
        data = plistlib.loads(lc.PLIST.read_bytes())
        del data["CFBundleVersion"]
        lc.PLIST.write_bytes(plistlib.dumps(data))
        lc.bump_version("patch")
        self.assertNotIn("CFBundleVersion", plistlib.loads(lc.PLIST.read_bytes()))


@patch.dict(os.environ, {"GH_TOKEN": "fixture-token", "GITHUB_REPOSITORY": "example/plugin"})
class PublicationTests(unittest.TestCase):
    def test_all_pages_including_draft_collision(self):
        pages = [[record(number=n) for n in range(1, 101)], [record("v2.7.9", 101, draft=True)]]
        with patch.object(lc, "remote_refs", return_value={}), patch.object(lc, "api", side_effect=pages) as api:
            with self.assertRaisesRegex(ValueError, "including drafts"):
                lc.available("v2.7.9")
            self.assertIn("page=2", api.call_args.args[1])

    def test_success_requires_complete_empty_page(self):
        with patch.object(lc, "remote_refs", return_value={}), patch.object(
            lc, "api", side_effect=[[record()], [record(number=2)], []]
        ) as api:
            lc.available("v2.7.9")
            self.assertEqual(api.call_count, 3)

    def test_api_errors_and_malformed_pages_fail_closed(self):
        failures = [
            subprocess.CalledProcessError(1, ["gh", "api"], stderr="HTTP 404"),
            subprocess.CalledProcessError(1, ["gh", "api"], stderr="HTTP 401"),
            subprocess.CalledProcessError(1, ["gh", "api"], stderr="network failure"),
            json.JSONDecodeError("invalid", "", 0),
            {"message": "Not Found"}, None, [None], [{}],
            [record(draft="false")], [record(prerelease=0)], [record(number=True)],
        ]
        for failure in failures:
            for later in (False, True):
                with self.subTest(failure=failure, later=later):
                    responses = ([ [record()] ] if later else []) + [failure]
                    with patch.object(lc, "remote_refs", return_value={}), patch.object(
                        lc, "api", side_effect=responses
                    ):
                        with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                            lc.available("v2.7.9")

    def test_repeated_page_is_not_success(self):
        with patch.object(lc, "remote_refs", return_value={}), patch.object(
            lc, "api", side_effect=[[record()], [record()]]
        ):
            with self.assertRaisesRegex(ValueError, "Repeated"):
                lc.available("v2.7.9")

    def test_existing_remote_tag_rejected_without_api(self):
        with patch.object(lc, "remote_refs", return_value={"refs/tags/v2.7.9": SHA}), patch.object(lc, "api") as api:
            with self.assertRaisesRegex(ValueError, "already exists"):
                lc.available("v2.7.9")
            api.assert_not_called()

    def test_authentication_required(self):
        with patch.dict(os.environ, {"GH_TOKEN": ""}), patch.object(lc, "remote_refs") as refs:
            with self.assertRaisesRegex(ValueError, "Authenticated"):
                lc.available("v2.7.9")
            refs.assert_not_called()

    def test_remote_errors_and_malformed_refs_are_not_absence(self):
        failure = subprocess.CalledProcessError(1, ["git", "ls-remote"])
        for response in (failure, "not a ref\n", f"{SHA}\trefs/tags/v2.7.9\n" * 2):
            with self.subTest(response=response):
                with patch.object(lc, "run", side_effect=[response]), patch.object(lc, "api") as api:
                    with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                        lc.available("v2.7.9")
                    api.assert_not_called()

    def test_api_is_explicit_authenticated_get_and_create_only_post(self):
        with patch.object(lc, "run", return_value="[]") as run:
            self.assertEqual(lc.api("GET", "repos/example/plugin/releases?page=2"), [])
            self.assertEqual(run.call_args.args, (
                "gh", "api", "--method", "GET", "repos/example/plugin/releases?page=2",
            ))
        with patch.object(lc, "run", return_value="{}") as run:
            lc.api("POST", "repos/example/plugin/releases", {"draft": False})
            self.assertEqual(run.call_args.args[-2:], ("--input", "-"))
            self.assertEqual(json.loads(run.call_args.kwargs["input"]), {"draft": False})


class GitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remote = self.root / "remote.git"
        self.work = self.root / "work"
        subprocess.run(["git", "init", "--bare", "-q", str(self.remote)], check=True)
        self.work.mkdir()
        self.cwd = directory(self.work)
        self.cwd.__enter__()
        self.addCleanup(self.cwd.__exit__, None, None, None)
        lc.run("git", "init", "-q", "-b", "selected")
        lc.run("git", "config", "user.name", "Fixture")
        lc.run("git", "config", "user.email", "fixture@example.invalid")
        lc.run("git", "config", "push.followTags", "true")
        lc.run("git", "remote", "add", "origin", str(self.remote))
        fixture()
        Path("dependency-version.txt").write_text("1.0\n")
        lc.run("git", "add", ".")
        lc.run("git", "commit", "-qm", "fixture")
        self.sha = lc.run("git", "rev-parse", "HEAD").strip()
        lc.run("git", "-c", "push.followTags=false", "push", "-q", "origin", "HEAD:refs/heads/selected")
        lc.run("git", "tag", "-a", "unrelated-tag", "-m", "must remain local")
        self.env = patch.dict(os.environ, {"GH_TOKEN": "fixture-token", "GITHUB_REPOSITORY": "example/plugin"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def refs(self):
        return lc.run("git", "ls-remote", "origin")

    def test_source_only_bump_push_disables_follow_tags(self):
        self.assertEqual(lc.bump_branch("selected", self.sha, "patch"), "2.7.10")
        head = lc.run("git", "rev-parse", "HEAD").strip()
        self.assertIn(f"{head}\trefs/heads/selected", self.refs())
        self.assertNotIn("refs/tags/", self.refs())
        files = lc.run("git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines()
        self.assertEqual(set(files), {str(lc.PROJECT), str(lc.PLIST)})
        self.assertIn("Co-authored-by: Copilot App", lc.run("git", "log", "-1", "--format=%B"))
        self.assertEqual(Path("dependency-version.txt").read_text(), "1.0\n")

    def test_wrong_sha_and_existing_branch_race_rejected(self):
        with self.assertRaises(ValueError):
            lc.bump_branch("selected", SHA, "patch")
        original = lc.run

        def race(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[:2] == ("git", "commit"):
                newer = original("git", "rev-parse", "HEAD").strip()
                original("git", "--git-dir", str(self.remote), "fetch", "--no-tags", str(self.work), newer)
                original("git", "--git-dir", str(self.remote), "update-ref", "refs/heads/selected", newer)
            return result

        with patch.object(lc, "run", side_effect=race):
            with self.assertRaisesRegex(ValueError, "branch moved"):
                lc.bump_branch("selected", self.sha, "patch")
        self.assertNotIn("refs/tags/", self.refs())

    def test_last_moment_nonfastforward_branch_race(self):
        # A concurrent sibling commit arrives after the final branch check.
        original = lc.run
        sibling = original("git", "commit-tree", "HEAD^{tree}", "-p", self.sha, input="concurrent\n").strip()
        original("git", "--git-dir", str(self.remote), "fetch", "--no-tags", str(self.work), sibling)

        def race(*args, **kwargs):
            if "push" in args and "--porcelain" in args:
                original("git", "--git-dir", str(self.remote), "update-ref", "refs/heads/selected", sibling)
            return original(*args, **kwargs)

        with patch.object(lc, "run", side_effect=race):
            with self.assertRaises(subprocess.CalledProcessError):
                lc.bump_branch("selected", self.sha, "minor")
        self.assertIn(f"{sibling}\trefs/heads/selected", self.refs())
        self.assertNotIn("refs/tags/", self.refs())

    def test_last_moment_branch_deletion_is_not_recreated(self):
        original = lc.run

        def race(*args, **kwargs):
            if "push" in args and "--porcelain" in args:
                original("git", "--git-dir", str(self.remote), "update-ref", "-d", "refs/heads/selected")
            return original(*args, **kwargs)

        with patch.object(lc, "run", side_effect=race):
            with self.assertRaises(subprocess.CalledProcessError):
                lc.bump_branch("selected", self.sha, "patch")
        self.assertNotIn("refs/heads/selected", self.refs())
        self.assertNotIn("refs/tags/", self.refs())

    def test_last_moment_branch_rewind_is_rejected(self):
        original = lc.run
        # An empty-history parent already exists on the remote. After a later
        # selected commit, rewinding to that parent would normally fast-forward.
        original("git", "commit", "--allow-empty", "-qm", "selected tip")
        original("git", "-c", "push.followTags=false", "push", "-q", "origin", "HEAD:refs/heads/selected")
        selected = original("git", "rev-parse", "HEAD").strip()

        def race(*args, **kwargs):
            if "push" in args and "--porcelain" in args:
                original("git", "--git-dir", str(self.remote), "update-ref", "refs/heads/selected", self.sha)
            return original(*args, **kwargs)

        with patch.object(lc, "run", side_effect=race):
            with self.assertRaises(subprocess.CalledProcessError):
                lc.bump_branch("selected", selected, "patch")
        self.assertIn(f"{self.sha}\trefs/heads/selected", self.refs())
        self.assertNotIn("refs/tags/", self.refs())

    def assets(self):
        for name in ("plugin.zip", "plugin.dmg"):
            Path(name).write_bytes(b"fixture asset")
        return ["plugin.zip", "plugin.dmg"]

    def publish(self):
        lc.publish(self.sha, "v2.7.9", self.assets())

    def test_publication_creates_exact_tag_and_explicit_release(self):
        events = []
        original = lc.run

        def run(*args, **kwargs):
            events.append(args)
            if args[:3] == ("gh", "release", "upload"):
                return ""
            return original(*args, **kwargs)

        def api(method, endpoint, payload=None):
            events.append((method, endpoint))
            if method == "GET":
                return []
            self.assertEqual(payload, {
                "tag_name": "v2.7.9", "target_commitish": self.sha, "name": "v2.7.9",
                "draft": False, "prerelease": False, "body": "",
            })
            self.assertIn(f"{self.sha}\trefs/tags/v2.7.9", self.refs())
            return record("v2.7.9", name="v2.7.9", target_commitish=self.sha)

        with patch.object(lc, "api", side_effect=api), patch.object(lc, "run", side_effect=run):
            self.publish()
        refs = self.refs()
        self.assertIn(f"{self.sha}\trefs/tags/v2.7.9", refs)
        self.assertNotIn("unrelated-tag", refs)
        get = next(i for i, event in enumerate(events) if event[0] == "GET")
        push = next(i for i, event in enumerate(events) if event[:4] == ("git", "-c", "push.followTags=false", "push"))
        post = next(i for i, event in enumerate(events) if event[0] == "POST")
        upload = next(i for i, event in enumerate(events) if event[:3] == ("gh", "release", "upload"))
        self.assertLess(get, push)
        self.assertLess(push, post)
        self.assertLess(post, upload)
        self.assertNotIn("--clobber", events[upload])

    def test_same_sha_and_different_sha_tag_creation_races(self):
        original = lc.run
        for target in (self.sha, original("git", "commit-tree", "HEAD^{tree}", input="other\n").strip()):
            with self.subTest(target=target):
                original("git", "--git-dir", str(self.remote), "update-ref", "-d", "refs/tags/v2.7.9")
                original("git", "--git-dir", str(self.remote), "fetch", "--no-tags", str(self.work), target)

                def race(*args, **kwargs):
                    if args[:5] == ("git", "-c", "push.followTags=false", "push", "--porcelain"):
                        original("git", "--git-dir", str(self.remote), "update-ref", "refs/tags/v2.7.9", target)
                    return original(*args, **kwargs)

                with patch.object(lc, "api", return_value=[]) as api, patch.object(lc, "run", side_effect=race):
                    with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                        self.publish()
                    self.assertTrue(all(call.args[0] == "GET" for call in api.call_args_list))
                self.assertIn(f"{target}\trefs/tags/v2.7.9", self.refs())

    def test_post_failure_retains_tag_without_upload_or_retry(self):
        original = lc.run
        failure = subprocess.CalledProcessError(1, ["gh", "api"])
        with patch.object(lc, "api", side_effect=[[], failure]) as api, patch.object(lc, "run", wraps=original) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                self.publish()
            self.assertEqual(api.call_count, 2)
            self.assertFalse(any(call.args[:2] == ("gh", "release") for call in run.call_args_list))
        self.assertIn(f"{self.sha}\trefs/tags/v2.7.9", self.refs())

    def test_remote_tag_verification_failure_stops_before_post(self):
        original = lc.remote_refs
        calls = 0

        def refs(*args):
            nonlocal calls
            calls += 1
            return original(*args) if calls == 1 else {f"refs/tags/v2.7.9": SHA}

        with patch.object(lc, "remote_refs", side_effect=refs), patch.object(lc, "api", return_value=[]) as api:
            with self.assertRaisesRegex(ValueError, "target does not equal"):
                self.publish()
            self.assertEqual(api.call_count, 1)
        self.assertIn(f"{self.sha}\trefs/tags/v2.7.9", self.refs())

    def test_malformed_post_response_retains_tag_without_upload(self):
        original = lc.run
        with patch.object(lc, "api", side_effect=[[], {}]), patch.object(lc, "run", wraps=original) as run:
            with self.assertRaisesRegex(ValueError, "Malformed"):
                self.publish()
            self.assertFalse(any(call.args[:2] == ("gh", "release") for call in run.call_args_list))
        self.assertIn(f"{self.sha}\trefs/tags/v2.7.9", self.refs())

    def test_upload_failure_retains_release_tag_and_partial_assets(self):
        original = lc.run
        state = []

        def api(method, endpoint, payload=None):
            if method == "GET":
                return []
            state.append("release")
            return record("v2.7.9", name="v2.7.9", target_commitish=self.sha)

        def run(*args, **kwargs):
            if args[:3] == ("gh", "release", "upload"):
                state.append("first asset")
                raise subprocess.CalledProcessError(1, args)
            return original(*args, **kwargs)

        with patch.object(lc, "api", side_effect=api) as api_mock, patch.object(lc, "run", side_effect=run):
            with self.assertRaises(subprocess.CalledProcessError):
                self.publish()
            self.assertEqual(api_mock.call_count, 2)
        self.assertEqual(state, ["release", "first asset"])
        self.assertIn(f"{self.sha}\trefs/tags/v2.7.9", self.refs())

    def test_late_draft_collision_stops_publication(self):
        # A preflight may have passed hours earlier; publication must check again.
        with patch.object(lc, "api", return_value=[]):
            lc.available("v2.7.9")
        with patch.object(lc, "api", return_value=[record("v2.7.9", draft=True)]) as api:
            with self.assertRaises(ValueError):
                self.publish()
            self.assertEqual(api.call_count, 1)
        self.assertNotIn("refs/tags/", self.refs())

    def test_invalid_sha_version_and_missing_asset_prevent_remote_changes(self):
        with patch.object(lc, "api") as api:
            for sha, tag, assets in (
                (SHA, "v2.7.9", self.assets()),
                (self.sha, "v2.7.8", self.assets()),
                (self.sha, "v2.7.9", ["absent.zip", "absent.dmg"]),
            ):
                with self.assertRaises(ValueError):
                    lc.publish(sha, tag, assets)
            api.assert_not_called()
        self.assertNotIn("refs/tags/", self.refs())


class WiringTests(unittest.TestCase):
    def test_live_version_contract_without_fixed_version_expectation(self):
        with directory(ROOT):
            version = lc.read_version()
            self.assertRegex(version, f"^{lc.VERSION}$")
            data = plistlib.loads(lc.PLIST.read_bytes())
            for key in ("CFBundleShortVersionString", "CFBundleVersion"):
                if key in data:
                    self.assertIn(data[key], {version, *lc.REFERENCES})

    def test_manual_zero_input_and_immutable_checkout_wiring(self):
        release = (ROOT / ".github/workflows/release.yml").read_text()
        bump = (ROOT / ".github/workflows/bump-version.yml").read_text()
        self.assertIn("\nname: Build\n", release)
        trigger = release.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertEqual(trigger.strip(), "workflow_dispatch:")
        self.assertNotIn("inputs.", release)
        self.assertNotIn("github.ref_name", release)
        for workflow in (release, bump):
            self.assertEqual(workflow.count("uses: actions/checkout@"), workflow.count("ref: ${{ github.sha }}"))
        self.assertIn("group: release-publication\n", release)
        self.assertNotIn("group: release-${{", release)
        self.assertIn("cancel-in-progress: false", release)
        self.assertIn("needs: [version]", release)
        self.assertIn('if [[ "$SELECTED_REF" != refs/heads/* ]]', bump)
        self.assertIn('--sha "$SELECTED_SHA" --mode "$MODE"', bump)
        self.assertNotIn("lifecycle.py publish", bump)
        self.assertNotIn("git tag", bump)
        self.assertIn("          - patch\n          - minor\n          - major", bump)

    def test_required_build_gates_and_original_asset_notes_policy(self):
        release = (ROOT / ".github/workflows/release.yml").read_text()
        gates = [
            "check prebuilt libraries", "import Developer ID certificate",
            "store notarytool credentials", "build macOS plugin (universal)",
            "verify universal binary", "locate bundle", "codesign",
            "package, notarize & staple", "publish new tag, release and assets",
        ]
        positions = [release.index(f"- name: {gate}") for gate in gates]
        self.assertEqual(positions, sorted(positions))
        for secret in (
            "APPLE_DEVELOPER_ID_CERTIFICATE", "APPLE_DEVELOPER_ID_CERTIFICATE_PASSWORD",
            "KEYCHAIN_PASSWORD", "NOTARYTOOL_APPLE_ID", "NOTARYTOOL_TEAM_ID", "NOTARYTOOL_PASSWORD",
        ):
            self.assertIn(f"secrets.{secret}", release)
        for gate in (
            'ARCHS="arm64 x86_64"', 'grep -q "i386"', 'notarize "$ZIP_PATH"',
            'notarize "$DMG_PATH"', 'xcrun stapler staple "$DMG_PATH"',
            'ZIP_PATH="${PRODUCT}-${VERSION}.zip"', 'DMG_PATH="${PRODUCT}-${VERSION}.dmg"',
            'python3 .github/scripts/lifecycle.py publish --sha "$SELECTED_SHA"',
            '--tag "$TAG" "$ZIP_PATH" "$DMG_PATH"',
        ):
            self.assertIn(gate, release)
        self.assertNotIn("softprops/", release)
        self.assertNotIn("if: always()", release)
        source = (ROOT / ".github/scripts/lifecycle.py").read_text()
        self.assertNotIn('"DELETE"', source)
        self.assertNotIn('"PATCH"', source)
        self.assertNotIn('"--force"', source)
        self.assertNotIn('"--clobber"', source)
        self.assertNotIn('"release", "create"', source)
        self.assertNotIn('"generate_release_notes"', source)


if __name__ == "__main__":
    unittest.main()
