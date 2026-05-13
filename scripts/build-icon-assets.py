#!/usr/bin/env python3
"""Build the locked A3-gapped-T sigil into every platform's icon asset set.

Geometry is sourced from ``docs/design/icon-direction.md`` §4.2:

- Crossbar — solid rectangle, ~16% icon-width thick, ~58% icon-width
  long, sitting at the upper third with crisp 90° corners.
- Stem — solid rectangle, ~9% icon-width thick, descending from a
  point ~4% icon-height below the crossbar's bottom edge to ~75%
  icon-height, with a clean flat squared end.
- Inner Tahoe layered-glass plate — a subtle inner squircle outline at
  ~88% icon-edge radius.
- Background — deep dark indigo near-black squircle.
- Mark tint — calm blue-violet ``oklch(70% 0.15 250)`` (§11.4).

The script writes one canonical SVG at
``docs/design/icon-concepts/A3-vector.svg`` (the source of truth) and
rasterises it to the PNG sizes Tauri, the macOS ``.icns``, the Windows
``.ico``, and the Linux freedesktop hicolor set require. The ``.icon``
file for macOS Tahoe is **not** generated here — that build step ships
through Apple's Icon Composer and is tracked in
``docs/going-public-checklist.md``.

Run from the repo root:

    python3 scripts/build-icon-assets.py
"""

from __future__ import annotations

import math
import shutil
import struct
import subprocess
import sys
import tempfile
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[1]
ICONS_DIR = REPO_ROOT / "src-tauri" / "icons"
LINUX_ICONS_DIR = REPO_ROOT / "src-tauri" / "icons" / "linux"
DESIGN_DIR = REPO_ROOT / "docs" / "design" / "icon-concepts"

REFERENCE_SIZE = 1024
SQUIRCLE_EXPONENT = 4.0  # n in |x|^n + |y|^n = 1; n≈4 matches Apple's curve
SQUIRCLE_PADDING_RATIO = 0.0  # the icon edge is the squircle edge

# --- color ------------------------------------------------------------------

# Background: deep dark indigo near-black per icon-direction.md.
BG_INDIGO = (0x0D, 0x0A, 0x1A, 0xFF)

# Inner plate: a subtle ~88%-radius squircle outline on the background.
INNER_PLATE_INSET_RATIO = 0.06  # plate edge is 6% inset from icon edge
INNER_PLATE_BORDER_RATIO = 0.0025  # ~2 px at 1024 — a hint, not a feature
INNER_PLATE_BORDER_COLOR = (0x6B, 0x70, 0xA8, 0x28)  # muted violet, low alpha


def oklch_to_srgb(lightness: float, chroma: float, hue_deg: float) -> tuple[int, int, int]:
    """Convert OKLCH (L 0..1, C 0..0.4, h degrees) to 8-bit sRGB."""
    h_rad = math.radians(hue_deg)
    a = chroma * math.cos(h_rad)
    b = chroma * math.sin(h_rad)
    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    l3, m3, s3 = l_ ** 3, m_ ** 3, s_ ** 3
    r_lin = +4.0767416621 * l3 - 3.3077115913 * m3 + 0.2309699292 * s3
    g_lin = -1.2684380046 * l3 + 2.6097574011 * m3 - 0.3413193965 * s3
    b_lin = -0.0041960863 * l3 - 0.7034186147 * m3 + 1.7076147010 * s3

    def lin_to_srgb(x: float) -> float:
        x = max(0.0, min(1.0, x))
        return 1.055 * (x ** (1 / 2.4)) - 0.055 if x > 0.0031308 else 12.92 * x

    return tuple(round(255 * lin_to_srgb(c)) for c in (r_lin, g_lin, b_lin))


# Mark tint: calm blue-violet at OKLCH(70% 0.15 250) — the §11.9 spec.
# The hue is honoured literally; Icon Composer's layered-glass pass will
# darken the appearance for the .icon format. The flat raster ships
# closest to the underlying tint.
MARK_FILL = (*oklch_to_srgb(0.70, 0.15, 250), 0xFF)
# Sheen + shadow: very gentle vertical gradient so the limb reads as a
# glass slab at 256+ px without intruding on the silhouette at 16 px.
MARK_HIGHLIGHT = (*oklch_to_srgb(0.86, 0.06, 250), 0x66)
MARK_SHADOW = (*oklch_to_srgb(0.42, 0.10, 250), 0x40)

# --- geometry ---------------------------------------------------------------

# Percentages from icon-direction.md §4.2. All are fractions of the icon edge.
CROSSBAR_WIDTH_RATIO = 0.58
CROSSBAR_THICKNESS_RATIO = 0.16
CROSSBAR_CENTER_Y_RATIO = 1 / 3  # "upper third"
STEM_THICKNESS_RATIO = 0.09
STEM_GAP_RATIO = 0.04
STEM_BOTTOM_Y_RATIO = 0.75


def squircle_mask(size: int, radius_ratio: float = 1.0) -> Image.Image:
    """Return a single-channel mask (L) for a superellipse-shaped squircle.

    Subpixel anti-aliasing is achieved by rendering at 4× and downsampling.
    """
    upscale = 4
    canvas = size * upscale
    mask = Image.new("L", (canvas, canvas), 0)
    pixels = mask.load()
    assert pixels is not None
    cx = cy = (canvas - 1) / 2.0
    half = (canvas - 1) / 2.0 * radius_ratio
    n = SQUIRCLE_EXPONENT
    inv_n = 1.0 / n
    for y in range(canvas):
        ny = (y - cy) / half
        ny_abs_n = abs(ny) ** n
        if ny_abs_n >= 1.0:
            continue
        x_limit = ((1.0 - ny_abs_n) ** inv_n) * half
        x_start = int(math.floor(cx - x_limit))
        x_end = int(math.ceil(cx + x_limit))
        for x in range(max(0, x_start), min(canvas, x_end + 1)):
            nx = (x - cx) / half
            if abs(nx) ** n + ny_abs_n <= 1.0:
                pixels[x, y] = 255
    return mask.resize((size, size), Image.Resampling.LANCZOS)


def draw_glass_rect(
    overlay: Image.Image,
    box: tuple[int, int, int, int],
) -> None:
    """Draw the T limb as a solid filled rectangle with a subtle glass sheen.

    The geometry stays "crisp 90° corners" per the spec; the sheen is a
    1-pixel-thin gradient strip on the top edge plus a hint of shadow on
    the bottom, just enough to keep the limb from reading as flat
    monochrome at large sizes. At small sizes (≤32 px) the sheen vanishes
    into the underlying fill.
    """
    x0, y0, x1, y1 = box
    draw = ImageDraw.Draw(overlay)
    draw.rectangle(box, fill=MARK_FILL)

    height = y1 - y0
    if height < 24:
        # At small sizes the sheen muddles the silhouette; the flat
        # fill reads cleaner.
        return

    sheen_height = max(2, height // 10)
    shadow_height = max(2, height // 16)
    sheen_layer = Image.new("RGBA", overlay.size, (0, 0, 0, 0))
    sheen_pixels = sheen_layer.load()
    assert sheen_pixels is not None
    # Vertical alpha falloff for the top highlight — a smooth band, not
    # a hard strip.
    hr, hg, hb, ha = MARK_HIGHLIGHT
    for dy in range(sheen_height):
        falloff = 1.0 - (dy / sheen_height) ** 1.5
        alpha = max(0, min(255, int(round(ha * falloff))))
        if alpha == 0:
            continue
        for x in range(x0, x1):
            sheen_pixels[x, y0 + dy] = (hr, hg, hb, alpha)
    sr, sg, sb, sa = MARK_SHADOW
    for dy in range(shadow_height):
        falloff = (dy / max(1, shadow_height - 1)) ** 1.2
        alpha = max(0, min(255, int(round(sa * falloff))))
        if alpha == 0:
            continue
        y = y1 - shadow_height + dy
        for x in range(x0, x1):
            sheen_pixels[x, y] = (sr, sg, sb, alpha)
    overlay.alpha_composite(sheen_layer)


def render_master(size: int = REFERENCE_SIZE) -> Image.Image:
    """Render the canonical icon at the given pixel size."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # 1. background squircle: full-bleed, near-black indigo.
    bg = Image.new("RGBA", (size, size), BG_INDIGO)
    sq_mask = squircle_mask(size, radius_ratio=1.0)
    image.paste(bg, (0, 0), sq_mask)

    # 2. inner plate ring: outer-squircle AND NOT inner-squircle gives a
    # subtle line that traces the inner squircle outline at ~88% radius.
    plate_size = int(size * (1 - INNER_PLATE_INSET_RATIO * 2))
    plate_offset = (size - plate_size) // 2
    plate_outer = squircle_mask(plate_size, radius_ratio=1.0)
    plate_thickness = max(1, int(round(size * INNER_PLATE_BORDER_RATIO)))
    plate_inner_size = plate_size - plate_thickness * 2
    plate_inner = squircle_mask(plate_inner_size, radius_ratio=1.0)
    plate_inner_full = Image.new("L", (plate_size, plate_size), 0)
    plate_inner_full.paste(plate_inner, (plate_thickness, plate_thickness))
    inverse_inner = plate_inner_full.point(lambda v: 255 - v)
    plate_ring = ImageChops.multiply(plate_outer, inverse_inner)
    plate_layer = Image.new("RGBA", (plate_size, plate_size), INNER_PLATE_BORDER_COLOR)
    image.paste(plate_layer, (plate_offset, plate_offset), plate_ring)

    # 3. the T mark: crossbar and stem on an overlay clipped to the inner
    # plate so glass elements never spill past the squircle.
    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    crossbar_w = round(size * CROSSBAR_WIDTH_RATIO)
    crossbar_h = round(size * CROSSBAR_THICKNESS_RATIO)
    crossbar_cx = size / 2
    crossbar_cy = size * CROSSBAR_CENTER_Y_RATIO
    crossbar_box = (
        round(crossbar_cx - crossbar_w / 2),
        round(crossbar_cy - crossbar_h / 2),
        round(crossbar_cx - crossbar_w / 2) + crossbar_w,
        round(crossbar_cy - crossbar_h / 2) + crossbar_h,
    )
    crossbar_bottom = crossbar_box[3]

    stem_w = round(size * STEM_THICKNESS_RATIO)
    stem_top = crossbar_bottom + round(size * STEM_GAP_RATIO)
    stem_bottom = round(size * STEM_BOTTOM_Y_RATIO)
    stem_cx = size / 2
    stem_box = (
        round(stem_cx - stem_w / 2),
        stem_top,
        round(stem_cx - stem_w / 2) + stem_w,
        stem_bottom,
    )

    draw_glass_rect(overlay, crossbar_box)
    draw_glass_rect(overlay, stem_box)

    # Clip the T to the squircle so the mark never escapes the plate.
    image.alpha_composite(overlay)
    final = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    final.paste(image, (0, 0), sq_mask)
    return final


# --- raster export ----------------------------------------------------------

def downsample(master: Image.Image, target: int) -> Image.Image:
    if master.size == (target, target):
        return master.copy()
    return master.resize((target, target), Image.Resampling.LANCZOS)


def write_png(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG", optimize=True)


def build_icns(master: Image.Image, dest: Path) -> None:
    """Build a Tauri-compatible .icns via macOS's iconutil if available.

    Falls back to a hand-rolled ICNS writer when iconutil isn't on PATH
    (e.g. CI Linux). The hand-rolled writer packs the icns "icp4"-"ic10"
    blocks that bundlers actually need.
    """
    sizes = {
        "icon_16x16.png": 16,
        "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32,
        "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128,
        "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256,
        "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512,
        "icon_512x512@2x.png": 1024,
    }
    if shutil.which("iconutil"):
        with tempfile.TemporaryDirectory() as tmp:
            iconset = Path(tmp) / "icon.iconset"
            iconset.mkdir()
            for name, size in sizes.items():
                write_png(downsample(master, size), iconset / name)
            subprocess.run(
                ["iconutil", "--convert", "icns", str(iconset), "--output", str(dest)],
                check=True,
            )
        return
    # Fallback: hand-pack an icns file with the modern PNG types.
    blocks: list[tuple[bytes, bytes]] = []
    type_map = {
        16: b"icp4",
        32: b"icp5",
        64: b"icp6",
        128: b"ic07",
        256: b"ic08",
        512: b"ic09",
        1024: b"ic10",
    }
    for size, fourcc in type_map.items():
        buf = BytesIO()
        downsample(master, size).save(buf, format="PNG")
        png = buf.getvalue()
        blocks.append((fourcc, png))
    body = b"".join(
        fourcc + struct.pack(">I", 8 + len(payload)) + payload
        for fourcc, payload in blocks
    )
    header = b"icns" + struct.pack(">I", 8 + len(body))
    dest.write_bytes(header + body)


def build_ico(master: Image.Image, dest: Path) -> None:
    """Write a Windows ICO with embedded 16/24/32/48/64/128/256 entries.

    PIL packs the multi-resolution set when the master is supplied at the
    largest target size and ``sizes=[(N, N), ...]`` enumerates each entry.
    """
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    base = downsample(master, 256)
    base.save(dest, format="ICO", sizes=sizes)


# --- SVG source-of-truth ----------------------------------------------------

def _squircle_path(size: int, radius_ratio: float = 1.0) -> str:
    """Return an SVG path 'd' approximating the squircle as cubic beziers.

    The path traces 32 segments of the superellipse for smooth output at
    any rasteriser. Reasonable for 4 corners; we still write a bezier
    chain since SVG ``rx``/``ry`` rounds with a quarter-ellipse, not a
    proper superellipse.
    """
    half = (size / 2) * radius_ratio
    cx = cy = size / 2
    n = SQUIRCLE_EXPONENT
    samples = 256
    points = []
    for i in range(samples):
        theta = (i / samples) * 2 * math.pi
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)
        x = half * math.copysign(abs(cos_t) ** (2 / n), cos_t)
        y = half * math.copysign(abs(sin_t) ** (2 / n), sin_t)
        points.append((cx + x, cy + y))
    parts = [f"M {points[0][0]:.3f} {points[0][1]:.3f}"]
    for x, y in points[1:]:
        parts.append(f"L {x:.3f} {y:.3f}")
    parts.append("Z")
    return " ".join(parts)


def write_svg_source(dest: Path, size: int = 1024) -> None:
    bg = f"#{BG_INDIGO[0]:02X}{BG_INDIGO[1]:02X}{BG_INDIGO[2]:02X}"
    fg = f"#{MARK_FILL[0]:02X}{MARK_FILL[1]:02X}{MARK_FILL[2]:02X}"
    plate = (
        f"#{INNER_PLATE_BORDER_COLOR[0]:02X}"
        f"{INNER_PLATE_BORDER_COLOR[1]:02X}"
        f"{INNER_PLATE_BORDER_COLOR[2]:02X}"
    )
    plate_alpha = INNER_PLATE_BORDER_COLOR[3] / 255.0
    sq_path = _squircle_path(size, radius_ratio=1.0)
    plate_size = size * (1 - INNER_PLATE_INSET_RATIO * 2)
    plate_offset = (size - plate_size) / 2
    plate_path = _squircle_path(int(round(plate_size)), radius_ratio=1.0)

    crossbar_w = size * CROSSBAR_WIDTH_RATIO
    crossbar_h = size * CROSSBAR_THICKNESS_RATIO
    crossbar_x = (size - crossbar_w) / 2
    crossbar_y = size * CROSSBAR_CENTER_Y_RATIO - crossbar_h / 2

    stem_w = size * STEM_THICKNESS_RATIO
    stem_x = (size - stem_w) / 2
    stem_top = crossbar_y + crossbar_h + size * STEM_GAP_RATIO
    stem_bottom = size * STEM_BOTTOM_Y_RATIO

    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}">
  <title>Thalyn — A3 gapped-T sigil</title>
  <desc>
    Geometry sourced from docs/design/icon-direction.md §4.2.
    Crossbar 16%×58% at upper third; stem 9% × (4% gap → 75% Y);
    background squircle in dark indigo; mark tint
    oklch(70% 0.15 250). Source of truth for the v0.38 icon ship.
  </desc>
  <defs>
    <clipPath id="squircle">
      <path d="{sq_path}" />
    </clipPath>
  </defs>
  <g clip-path="url(#squircle)">
    <path d="{sq_path}" fill="{bg}" />
    <g transform="translate({plate_offset:.3f} {plate_offset:.3f})">
      <path d="{plate_path}" fill="none" stroke="{plate}" stroke-opacity="{plate_alpha:.3f}" stroke-width="4" />
    </g>
    <rect x="{crossbar_x:.3f}" y="{crossbar_y:.3f}" width="{crossbar_w:.3f}" height="{crossbar_h:.3f}" fill="{fg}" />
    <rect x="{stem_x:.3f}" y="{stem_top:.3f}" width="{stem_w:.3f}" height="{stem_bottom - stem_top:.3f}" fill="{fg}" />
  </g>
</svg>
"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(svg, encoding="utf-8")


# --- driver -----------------------------------------------------------------

def main() -> int:
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    LINUX_ICONS_DIR.mkdir(parents=True, exist_ok=True)

    master = render_master(REFERENCE_SIZE)

    # Tauri's bundle config (src-tauri/tauri.conf.json) lists:
    #   icons/32x32.png, icons/128x128.png, icons/128x128@2x.png,
    #   icons/icon.icns, icons/icon.ico
    # plus a master icon.png and the Square*Logo Windows-store set.
    tauri_targets = {
        "32x32.png": 32,
        "128x128.png": 128,
        "128x128@2x.png": 256,
        "icon.png": 1024,
    }
    for filename, size in tauri_targets.items():
        write_png(downsample(master, size), ICONS_DIR / filename)

    # Windows-store Square*Logo set, used by Tauri's MSIX manifest.
    square_targets = {
        "Square30x30Logo.png": 30,
        "Square44x44Logo.png": 44,
        "Square71x71Logo.png": 71,
        "Square89x89Logo.png": 89,
        "Square107x107Logo.png": 107,
        "Square142x142Logo.png": 142,
        "Square150x150Logo.png": 150,
        "Square284x284Logo.png": 284,
        "Square310x310Logo.png": 310,
        "StoreLogo.png": 50,
    }
    for filename, size in square_targets.items():
        write_png(downsample(master, size), ICONS_DIR / filename)

    build_icns(master, ICONS_DIR / "icon.icns")
    build_ico(master, ICONS_DIR / "icon.ico")

    # Linux freedesktop hicolor set.
    linux_sizes = [16, 22, 24, 32, 48, 64, 128, 256, 512]
    for size in linux_sizes:
        write_png(downsample(master, size), LINUX_ICONS_DIR / f"{size}x{size}.png")

    write_svg_source(DESIGN_DIR / "A3-vector.svg", size=1024)
    # Also place a Linux-conformant scalable SVG next to the PNG set.
    write_svg_source(LINUX_ICONS_DIR / "thalyn.svg", size=512)

    # Validation aid: a small 16×16 dock-size proof.
    write_png(downsample(master, 16), DESIGN_DIR / "A3-vector-16.png")
    write_png(downsample(master, 32), DESIGN_DIR / "A3-vector-32.png")
    write_png(downsample(master, 64), DESIGN_DIR / "A3-vector-64.png")
    write_png(downsample(master, 128), DESIGN_DIR / "A3-vector-128.png")

    print(f"Wrote icon assets to {ICONS_DIR.relative_to(REPO_ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
