# 4d-plugin-convert-color-space

Convert RGB obtained from picker to generic RGB.

A 4D plugin that converts a 4D RGB color value (Longint `0x00RRGGBB`) between the **Generic RGB** color space and **sRGB**, using AppKit's `NSColor` / `NSColorSpace` color-space conversion. Use it to turn the value returned by `Select RGB color` into the equivalent device RGB that you pass to `OBJECT SET RGB COLORS`, or the other way round. The result is a Longint in the same `0x00RRGGBB` form.

| Command | Returns | Purpose |
|---|---|---|
| [Convert color space](#convert-color-space) | `Longint` | Convert an RGB color between Generic RGB (picker) and sRGB (device) |

**Platforms:** macOS (universal: Apple silicon and Intel). Windows: see below.

Releases: [github.com/miyako/4d-plugin-convert-color-space/releases](https://github.com/miyako/4d-plugin-convert-color-space/releases)

## Maintainer release workflow

1. Run **Bump version** on the desired branch and select `patch`, `minor`, or `major`. It validates the coordinated source version, commits only `Convert Color Space.xcodeproj/project.pbxproj` and `Info.plist`, and pushes that branch without force or follow-tags. It rejects a branch that moved since selection. It creates no tag/release and does not start Build.
2. Run **Build** separately on the desired branch using the Actions branch selector (no inputs), or `gh workflow run release.yml --ref <branch>`. Every checkout uses the immutable selected `github.sha`, not the moving branch tip. The strict `X.Y.Z` source version determines the release tag `vX.Y.Z`; no version is incremented by Build.
3. Build preserves the universal macOS build and binary/resource checks, Developer ID signing, ZIP/DMG notarization, and DMG stapling. There is no Windows build: the committed Windows64 payload is shipped unchanged. Assets remain `Convert Color Space-X.Y.Z.zip` and `Convert Color Space-X.Y.Z.dmg`. Release notes remain empty (no generated notes).

`MARKETING_VERSION` and `CURRENT_PROJECT_VERSION` in **all** Xcode configurations and source `Info.plist`'s `CFBundleShortVersionString` (plus `CFBundleVersion` if present) share one version. This coordination was introduced together in the modernization commit; these are not independent build counters. Recognized `$(MARKETING_VERSION)` / `$(CURRENT_PROJECT_VERSION)` plist references are preserved, not replaced. Unknown references, malformed versions, and drift fail before bumping or publishing. The committed bundle's old generated `Contents/Info.plist`, prebuilt Windows payload, dependency/API versions, plist format versions, and 4D database metadata are not release-version sources and are not rewritten.

Build runs are serialized repository-wide across branches with cancellation disabled. Both preflight and the final publication step reject an existing remote tag or any same-tag release, including drafts, using authenticated paginated release-list requests. An API/authentication/network error (including 404), malformed response, or failed later page stops publication; absence is accepted only after the entire listing succeeds.

After every required build/signing/notarization gate succeeds, publication rechecks availability, non-force pushes a new lightweight tag at exactly the selected SHA, rejects even a same-SHA tag creation race, and verifies the remote target. It then uses a create-only REST `POST` with explicit tag, SHA, name, `draft=false`, `prerelease=false`, and empty notes, followed by asset upload without `--clobber`. It never updates an existing release or deletes/rolls back/retries a failed publication.

**Recovery:** A failed release creation leaves its tag; a failed upload leaves the published release and any uploaded assets. Read the failure log and inspect the remote tag target, all releases (including drafts), and assets before deciding on manual recovery. Rerunning the same version intentionally fails; do not expect an automatic overwrite or cleanup. Reconcile partial state explicitly, or prepare a new source version with Bump version and run Build separately. Neither release workflow is triggered by a push or pull request. The separate **Native ownership** CI runs unsigned tests only; it does not build a plugin bundle, sign, package, notarize, or publish anything.

Local lifecycle regressions (no native build, signing, notarization, dispatch, or publication):

```sh
python3 -m unittest discover -s .github/scripts -p 'test_*.py'
actionlint .github/workflows/bump-version.yml .github/workflows/release.yml
```

## Native wrapper ownership regression

Both project files compile the shared native wrappers even though the only
dispatched command takes and returns Longints. CPicture's file-path argument
owns its assigned string and is cleared once; the wrapper retains the borrowed
picture before clearing the output argument. Newly created picture temporaries
are released after the wrapper retains them. Arguments immediately assigned a
string or picture start Undefined, avoiding overwritten allocated empties.
Charset conversion returns bytes, which `createCopy` converts to UTF-16 units.
The public color conversion command and bundle metadata are unchanged.

On macOS with Xcode command-line tools, run:

```sh
python3 tests/run_native_ownership.py
# The same harness must fail on the immutable original sources:
python3 tests/run_native_ownership.py --baseline 5bd06fbf41198ca0031502b7ffa94a5136c5823a
```

The harness compiles the actual dispatcher, all 16 wrapper implementations and
the entire unchanged bundled `4DPluginAPI.c`; it replaces only the host engine
callback. It uses ASan/UBSan and explicit string, handle and picture-reference
balances (macOS LeakSanitizer is disabled). It exercises allocated/null empty
strings, exact ASCII/BMP/supplementary text, picture handoffs/replacement,
unchanged-on-error synchronous commands, scalar content setters and array
transfer/rejection/replacement/shrink/clear. Test outputs live in a temporary
directory, not the tracked bundle.

The SDK implementation originated in repository commit
`9fd4821c277564da7b75efd16df5224bc5e0116a`; its SHA-256 is
`355ee208ad6c61dcc68e8432546fc42484888211dd07ed8fa3fdb4440f3edbd8`.
The v13 header/v14 source comments and initialization's `0x00001400` are not
proof of an upstream SDK release identity. The array setter at lines 4444-4458
disposes the old element and shallow-transfers only for Unicode arrays with
data, an inclusive index from zero to the element count, and a non-null
descriptor. Rejections retain caller ownership; getters borrow. Scalar
string/picture setters shallow-assign without clearing an old value;
`PA_SetUnistring`/`PA_ReturnString` instead use the engine content setter.

This is host-shim evidence, not execution in 4D or Windows certification.
`C_POINTER::getVariable` interprets pointer results as reference metadata;
ownership cannot safely be inferred from its generic getter cleanup comment,
so no speculative clear was added. Picture-data/application-path returned
string ownership is also unspecified by this SDK and left unchanged. Object/
collection getters and JSON callbacks are absent from this SDK/wrapper set.
All five CPicture command IDs are procedures; no owning function result was
identified. SDK resize allocation-failure count corruption is outside scope;
normal-array tests do not certify out-of-memory behavior.

---

## Requirements & platform notes

- **macOS 11.5 or later** (the deployment target of the current build).
- **Windows is not implemented in the source.** The plugin code uses AppKit only and has no Windows branch. The bundle contains a `Windows64` `.4DX` committed in 2015 that was not built from this source; what [Convert color space](#convert-color-space) does on Windows is unverified. Don't rely on it there.
- Both parameters are **mandatory**. There is no optional form.
- The command never raises a 4D error. Unexpected input produces a value, not an error (see [Error handling & troubleshooting](#error-handling--troubleshooting)).
- The conversion is **lossy and asymmetrical**: converting a color one way and back does not always give the original value, because each channel is rounded to 8 bits after each conversion.

The plugin installs two constants in the `Convert Color Space` theme:

| Constant | Value | Meaning |
|---|---|---|
| `From picker color` | `0` | Generic RGB → sRGB (picker value to device value) |
| `To picker color` | `1` | sRGB → Generic RGB (device value to picker value) |

---

## Convert color space

### Syntax

```4d
result := Convert color space ( color ; direction )
```

| Parameter | Type | Description |
|---|---|---|
| `color` | Longint | RGB color as `0x00RRGGBB`. The high byte is ignored. |
| `direction` | Longint | `From picker color` (0) or `To picker color` (1). Any value other than `0` is treated as `To picker color`. |
| Result | Longint | Converted RGB color as `0x00RRGGBB`. The high byte is always `0`. |

### Description

`Convert color space` splits `color` into its red, green and blue bytes, builds an opaque color from them in the source color space, converts it to the target color space, and rounds each channel back to 0–255.

- With `From picker color`, the source is **Generic RGB** and the target is **sRGB**. Use this on a value you got from the color picker (`Select RGB color`) before you pass it to `OBJECT SET RGB COLORS`.
- With `To picker color`, the source is **sRGB** and the target is **Generic RGB**.

The exact numbers come from macOS's color management (ColorSync), so they can differ slightly between macOS versions. For example, on macOS 26 pure red `0x00FF0000` becomes `0x00FF2600` with `From picker color`, and `0x00FB0007` with `To picker color`.

From 14.3.0, each converted channel is clamped to 0–255, and if macOS can't perform the conversion, the input color is returned unchanged. For ordinary inputs the results are the same as in earlier versions.

### Example

Lines recovered from the plugin's own test database (`Convert Color Space.4dbase`, binary format): a color from the picker is converted and applied to a form object.

```4d
$color:=Convert color space ($color;From picker color)
OBJECT SET RGB COLORS(*;"Rectangle";$color;$color)
```

From the original README:

```4d
$color:=Convert color space ($color;To picker color)
$color:=Convert color space ($color;From picker color)
```

Pick a color and apply the device equivalent to an object:

```4d
C_LONGINT($picked;$device)

$picked:=Select RGB color
If (OK=1)
	$device:=Convert color space ($picked;From picker color)
	OBJECT SET RGB COLORS(*;"Rectangle";$device;$device)
End if
```

---

## Error handling & troubleshooting

- **No 4D error is ever raised.** The command always returns a Longint; check the result yourself if it matters.
- **Any `direction` other than `0` means `To picker color`.** A typo such as `2` silently converts sRGB → Generic RGB.
- **The high byte of `color` is dropped.** Only the low 24 bits (`0x00RRGGBB`) are read, and the result always has a high byte of `0`.
- **Round trips are not exact.** Converting with `To picker color` and then `From picker color` can be off by one or more per channel. Keep the original value if you need it back.
- **Results can vary slightly by macOS version**, because the conversion is done by the system's color management.
- **Windows behavior is unverified.** The source has no Windows implementation (see [Requirements & platform notes](#requirements--platform-notes)).

---

## Quick reference

```4d
$device:=Convert color space ($picked;From picker color)  // Generic RGB -> sRGB
$picked:=Convert color space ($device;To picker color)    // sRGB -> Generic RGB
```
