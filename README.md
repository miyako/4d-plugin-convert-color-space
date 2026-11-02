# 4d-plugin-convert-color-space

Convert RGB obtained from picker to generic RGB.

A 4D plugin that converts a 4D RGB color value (Longint `0x00RRGGBB`) between the **Generic RGB** color space and **sRGB**, using AppKit's `NSColor` / `NSColorSpace` color-space conversion. Use it to turn the value returned by `Select RGB color` into the equivalent device RGB that you pass to `OBJECT SET RGB COLORS`, or the other way round. The result is a Longint in the same `0x00RRGGBB` form.

| Command | Returns | Purpose |
|---|---|---|
| [Convert color space](#convert-color-space) | `Longint` | Convert an RGB color between Generic RGB (picker) and sRGB (device) |

**Platforms:** macOS (universal: Apple silicon and Intel). Windows: see below.

Releases: [github.com/miyako/4d-plugin-convert-color-space/releases](https://github.com/miyako/4d-plugin-convert-color-space/releases)

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

Since the 2026 modernization, each converted channel is clamped to 0–255, and if macOS can't perform the conversion, the input color is returned unchanged. For ordinary inputs the results are the same as in earlier versions.

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
