"""Harmless synthetic test images and the edits re-uploads typically go through.

Images here are abstract patterns (gradients, rings, stripes, blobs) computed from
a seed with plain maths. No photographs or real content are used or stored, and
nothing is written to disk unless you ask for it.

An image is (width, height, pixels) with pixels as a row-major list of RGB tuples.
Pillow is optional and only needed to read/write image files or to apply JPEG
recompression.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from typing import List, Tuple

Pixel = Tuple[int, int, int]


@dataclass(frozen=True)
class Image:
    width: int
    height: int
    pixels: Tuple[Pixel, ...]

    def pixel(self, x: int, y: int) -> Pixel:
        return self.pixels[y * self.width + x]


def _clamp(v: float) -> int:
    return 0 if v < 0 else 255 if v > 255 else int(v)


def seeded_rng(seed: str) -> random.Random:
    return random.Random(int.from_bytes(hashlib.sha256(seed.encode("utf-8")).digest()[:8], "big"))


def synthetic_image(seed: str, size: int = 128) -> Image:
    """Deterministic abstract image for a seed (e.g. a synthetic request ID).

    Bold, large shapes over a gradient, like the coarse structure of a photo,
    so the perceptual hash has strong features to lock on to.
    """
    rng = seeded_rng(seed)

    def colour() -> Tuple[float, float, float]:
        return (rng.uniform(0, 255), rng.uniform(0, 255), rng.uniform(0, 255))

    top, bottom = colour(), colour()
    shapes = []
    for _ in range(rng.randint(4, 7)):
        kind = rng.choice(["rect", "ellipse"])
        w, h = rng.uniform(0.18, 0.55) * size, rng.uniform(0.18, 0.55) * size
        cx, cy = rng.uniform(0, size), rng.uniform(0, size)
        shapes.append((kind, cx, cy, w / 2, h / 2, colour()))
    texture = rng.uniform(4, 12)
    edge = max(2.0, size / 32)

    pixels: List[Pixel] = []
    for y in range(size):
        t = y / (size - 1)
        row_bg = [top[c] * (1 - t) + bottom[c] * t for c in range(3)]
        for x in range(size):
            ch = list(row_bg)
            for kind, cx, cy, rx, ry, col in shapes:
                dx, dy = (x - cx) / rx, (y - cy) / ry
                d = max(abs(dx), abs(dy)) if kind == "rect" else math.sqrt(dx * dx + dy * dy)
                # Soft edge a few pixels wide, like a photo rather than a vector drawing.
                a = (1.0 - d) * min(rx, ry) / edge
                if a > 0:
                    a = min(a, 1.0)
                    ch = [ch[c] * (1 - a) + col[c] * a for c in range(3)]
            n = texture * math.sin(x * 1.7 + y * 2.3) * math.cos(x * 0.9 - y * 1.3)
            pixels.append((_clamp(ch[0] + n), _clamp(ch[1] + n), _clamp(ch[2] + n)))
    return Image(size, size, tuple(pixels))


# ----------------------------------------------------------------------------- edits


def adjust_brightness(img: Image, factor: float, offset: float = 0.0) -> Image:
    return Image(
        img.width,
        img.height,
        tuple((_clamp(r * factor + offset), _clamp(g * factor + offset), _clamp(b * factor + offset)) for r, g, b in img.pixels),
    )


def crop(img: Image, fraction: float) -> Image:
    """Cut `fraction` of the width/height off each side."""
    dx, dy = int(img.width * fraction), int(img.height * fraction)
    w, h = img.width - 2 * dx, img.height - 2 * dy
    return Image(w, h, tuple(img.pixel(x + dx, y + dy) for y in range(h) for x in range(w)))


def rescale(img: Image, factor: float) -> Image:
    """Bilinear resize."""
    w, h = max(5, int(img.width * factor)), max(5, int(img.height * factor))
    out: List[Pixel] = []
    for y in range(h):
        sy = min(img.height - 1.0, (y + 0.5) * img.height / h - 0.5)
        sy = max(0.0, sy)
        y0, ty = int(sy), sy - int(sy)
        y1 = min(y0 + 1, img.height - 1)
        for x in range(w):
            sx = max(0.0, min(img.width - 1.0, (x + 0.5) * img.width / w - 0.5))
            x0, tx = int(sx), sx - int(sx)
            x1 = min(x0 + 1, img.width - 1)
            p00, p10, p01, p11 = img.pixel(x0, y0), img.pixel(x1, y0), img.pixel(x0, y1), img.pixel(x1, y1)
            out.append(
                tuple(
                    _clamp(
                        p00[c] * (1 - tx) * (1 - ty) + p10[c] * tx * (1 - ty) + p01[c] * (1 - tx) * ty + p11[c] * tx * ty
                    )
                    for c in range(3)
                )
            )
    return Image(w, h, tuple(out))


def overlay_box(img: Image, area_fraction: float = 0.02, colour: Pixel = (245, 245, 245), opacity: float = 0.5) -> Image:
    """Blend a semi-transparent box into the bottom-right corner, like a watermark or caption."""
    side_w = max(1, int(img.width * math.sqrt(area_fraction)))
    side_h = max(1, int(img.height * math.sqrt(area_fraction)))
    x0, y0 = img.width - side_w - 2, img.height - side_h - 2
    pixels = list(img.pixels)
    for y in range(y0, y0 + side_h):
        for x in range(x0, x0 + side_w):
            p = pixels[y * img.width + x]
            pixels[y * img.width + x] = tuple(_clamp(p[c] * (1 - opacity) + colour[c] * opacity) for c in range(3))
    return Image(img.width, img.height, tuple(pixels))


def mirror(img: Image) -> Image:
    return Image(
        img.width,
        img.height,
        tuple(img.pixel(img.width - 1 - x, y) for y in range(img.height) for x in range(img.width)),
    )


def reupload_variant(img: Image, seed: str) -> Tuple[Image, str]:
    """One plausible re-upload edit chosen by seed. Returns the image and a label.

    Most re-uploads are straight re-shares or resized copies; crops and
    watermarks are rarer and are also where perceptual hashes most often miss.
    """
    rng = seeded_rng("variant:" + seed)
    kinds = ["identical", "rescale", "brightness", "mirror", "crop", "overlay", "combo"]
    weights = [0.25, 0.25, 0.15, 0.10, 0.10, 0.10, 0.05]
    kind = rng.choices(kinds, weights=weights, k=1)[0]
    if kind == "identical":
        return img, kind
    if kind == "rescale":
        return rescale(img, rng.uniform(0.6, 0.9)), kind
    if kind == "brightness":
        return adjust_brightness(img, rng.uniform(0.85, 1.15), rng.uniform(-12, 12)), kind
    if kind == "crop":
        return crop(img, rng.uniform(0.01, 0.025)), kind
    if kind == "overlay":
        return overlay_box(img, rng.uniform(0.01, 0.03)), kind
    if kind == "mirror":
        return mirror(img), kind
    return overlay_box(adjust_brightness(rescale(img, 0.8), 1.08), 0.015), kind


# ----------------------------------------------------------------------------- Pillow helpers


def _require_pillow():
    try:
        from PIL import Image as PILImage  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised only without Pillow
        raise RuntimeError(
            "Pillow is needed for image files: pip install 'takedown-tracking[images]'"
        ) from exc
    return PILImage


def load_image(path: str) -> Image:
    PILImage = _require_pillow()
    with PILImage.open(path) as im:
        rgb = im.convert("RGB")
        return Image(rgb.width, rgb.height, tuple(rgb.getdata()))


def save_image(img: Image, path: str, **kwargs) -> None:
    PILImage = _require_pillow()
    im = PILImage.new("RGB", (img.width, img.height))
    im.putdata(list(img.pixels))
    im.save(path, **kwargs)


def jpeg_recompress(img: Image, quality: int = 60) -> Image:
    """Round-trip through JPEG, as most platforms do on upload. Needs Pillow."""
    import io

    PILImage = _require_pillow()
    im = PILImage.new("RGB", (img.width, img.height))
    im.putdata(list(img.pixels))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=quality)
    buf.seek(0)
    with PILImage.open(buf) as out:
        rgb = out.convert("RGB")
        return Image(rgb.width, rgb.height, tuple(rgb.getdata()))
