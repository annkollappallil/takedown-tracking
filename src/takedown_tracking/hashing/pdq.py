"""PDQ perceptual hashing in pure Python.

PDQ is the open perceptual hash Meta released for matching known harmful images
(ThreatExchange, BSD licence). Visually similar images get hashes that differ in
only a few of their 256 bits, so a re-uploaded copy that was resized, recompressed
or lightly edited still matches.

This module follows the reference algorithm step by step:
  1. RGB -> luminance (0.299 R + 0.587 G + 0.114 B)
  2. two passes of a Jarosz box filter, then decimate to 64x64
  3. quality score from the 64x64 image's gradients (0-100)
  4. 2D DCT, keeping the 16x16 low-frequency block (DC excluded)
  5. bit = 1 where the coefficient is above the median

Tests check the output against vectors produced by the official implementation.
No third-party packages are needed; Pillow is only used elsewhere to read image files.

Privacy: hashes of reported content must stay inside the detector. Never write them
to observations, logs or any public output.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Sequence, Tuple

Matrix = List[List[float]]

DOWNSAMPLE_DIM = 64
DCT_DIM = 16
HASH_BITS = DCT_DIM * DCT_DIM  # 256
NUM_JAROSZ_PASSES = 2
MIN_HASHABLE_DIM = 5

# Recommended by the PDQ authors: distance <= 31 of 256 bits is a match,
# and hashes with quality < 50 are too featureless to trust.
DEFAULT_MATCH_THRESHOLD = 31
MIN_USEFUL_QUALITY = 50

LUMA_R, LUMA_G, LUMA_B = 0.299, 0.587, 0.114


def _dct_matrix() -> Matrix:
    scale = math.sqrt(2.0 / DOWNSAMPLE_DIM)
    return [
        [
            scale * math.cos((math.pi / 2.0 / DOWNSAMPLE_DIM) * (i + 1) * (2 * j + 1))
            for j in range(DOWNSAMPLE_DIM)
        ]
        for i in range(DCT_DIM)
    ]


_DCT = _dct_matrix()


@dataclass(frozen=True)
class PDQHash:
    """256-bit PDQ hash. Bit i*16+j is set when DCT coefficient [i][j] is above the median."""

    bits: int
    quality: int

    def to_hex(self) -> str:
        """Same 64-character hex form as the reference implementation."""
        return f"{self.bits:064x}"

    @classmethod
    def from_hex(cls, value: str, quality: int = 100) -> "PDQHash":
        if len(value) != 64:
            raise ValueError("PDQ hex hashes are 64 characters")
        return cls(int(value, 16), quality)

    def distance(self, other: "PDQHash") -> int:
        """Hamming distance: number of differing bits (0-256)."""
        return (self.bits ^ other.bits).bit_count()

    @property
    def is_useful(self) -> bool:
        return self.quality >= MIN_USEFUL_QUALITY


# ----------------------------------------------------------------------------- luminance


def luma_from_rgb(width: int, height: int, pixels: Sequence[Tuple[int, int, int]]) -> Matrix:
    """Row-major RGB pixels (0-255) -> luminance matrix [row][col]."""
    if len(pixels) != width * height:
        raise ValueError("pixel count does not match width x height")
    rows: Matrix = []
    for r in range(height):
        base = r * width
        rows.append(
            [LUMA_R * p[0] + LUMA_G * p[1] + LUMA_B * p[2] for p in pixels[base : base + width]]
        )
    return rows


# ----------------------------------------------------------------------------- downsampling


def _jarosz_window(old_dim: int, new_dim: int) -> int:
    return (old_dim + 2 * new_dim - 1) // (2 * new_dim)


def _box_1d(vec: List[float], window: int) -> List[float]:
    """Reference box1DFloat: centred moving average with shrinking windows at the edges."""
    n = len(vec)
    out = [0.0] * n
    half = (window + 2) // 2
    phase1 = half - 1
    phase2 = window - half + 1
    phase3 = n - window
    phase4 = half - 1

    li = ri = oi = 0
    total = 0.0
    size = 0
    for _ in range(phase1):
        total += vec[ri]
        size += 1
        ri += 1
    for _ in range(phase2):
        total += vec[ri]
        size += 1
        out[oi] = total / size
        ri += 1
        oi += 1
    for _ in range(phase3):
        total += vec[ri]
        total -= vec[li]
        out[oi] = total / size
        li += 1
        ri += 1
        oi += 1
    for _ in range(phase4):
        total -= vec[li]
        size -= 1
        out[oi] = total / size
        li += 1
        oi += 1
    return out


def _jarosz_filter(luma: Matrix, window_along_rows: int, window_along_cols: int) -> Matrix:
    rows = luma
    for _ in range(NUM_JAROSZ_PASSES):
        rows = [_box_1d(row, window_along_rows) for row in rows]
        cols = [_box_1d(list(col), window_along_cols) for col in zip(*rows)]
        rows = [list(r) for r in zip(*cols)]
    return rows


def _decimate(buf: Matrix) -> Matrix:
    n_rows, n_cols = len(buf), len(buf[0])
    row_idx = [int(((i + 0.5) * n_rows) / DOWNSAMPLE_DIM) for i in range(DOWNSAMPLE_DIM)]
    col_idx = [int(((j + 0.5) * n_cols) / DOWNSAMPLE_DIM) for j in range(DOWNSAMPLE_DIM)]
    return [[buf[ri][cj] for cj in col_idx] for ri in row_idx]


def _quality(buf64: Matrix) -> int:
    gradient_sum = 0
    for i in range(DOWNSAMPLE_DIM - 1):
        a, b = buf64[i], buf64[i + 1]
        for j in range(DOWNSAMPLE_DIM):
            gradient_sum += int(abs(((a[j] - b[j]) * 100) / 255))
    for row in buf64:
        for j in range(DOWNSAMPLE_DIM - 1):
            gradient_sum += int(abs(((row[j] - row[j + 1]) * 100) / 255))
    return min(gradient_sum // 90, 100)


# ----------------------------------------------------------------------------- DCT and bits


def _dct_16(buf64: Matrix) -> Matrix:
    # T = D * A  (16x64)
    t = [
        [sum(d_row[k] * buf64[k][j] for k in range(DOWNSAMPLE_DIM)) for j in range(DOWNSAMPLE_DIM)]
        for d_row in _DCT
    ]
    # B = T * D^T  (16x16)
    return [[sum(t_row[k] * d_row[k] for k in range(DOWNSAMPLE_DIM)) for d_row in _DCT] for t_row in t]


def _bits_from_dct(dct: Matrix) -> int:
    values = [v for row in dct for v in row]
    median = sorted(values)[(HASH_BITS + 1) // 2 - 1]  # same element Torben's method returns
    bits = 0
    for k, v in enumerate(values):
        if v > median:
            bits |= 1 << k
    return bits


def _dihedral_dcts(dct: Matrix) -> List[Matrix]:
    """The DCT block of all 8 rotations/reflections of the 64x64 image.

    Reversing the image along an axis flips the sign of every odd-frequency
    coefficient on that axis; transposing the image transposes the block.
    """
    def flip_rows(m: Matrix) -> Matrix:
        return [[v if (i % 2) else -v for v in row] for i, row in enumerate(m)]

    def flip_cols(m: Matrix) -> Matrix:
        return [[v if (j % 2) else -v for j, v in enumerate(row)] for row in m]

    def transpose(m: Matrix) -> Matrix:
        return [list(r) for r in zip(*m)]

    out = []
    for base in (dct, transpose(dct)):
        out.extend([base, flip_rows(base), flip_cols(base), flip_rows(flip_cols(base))])
    return out


# ----------------------------------------------------------------------------- public API


def _prepare(luma: Matrix) -> Tuple[Matrix, int]:
    n_rows = len(luma)
    n_cols = len(luma[0]) if n_rows else 0
    if n_rows < MIN_HASHABLE_DIM or n_cols < MIN_HASHABLE_DIM:
        raise ValueError(f"image must be at least {MIN_HASHABLE_DIM}x{MIN_HASHABLE_DIM}")
    filtered = _jarosz_filter(
        luma,
        _jarosz_window(n_cols, DOWNSAMPLE_DIM),
        _jarosz_window(n_rows, DOWNSAMPLE_DIM),
    )
    buf64 = _decimate(filtered)
    return buf64, _quality(buf64)


def pdq_from_luma(luma: Matrix) -> PDQHash:
    buf64, quality = _prepare(luma)
    return PDQHash(_bits_from_dct(_dct_16(buf64)), quality)


def pdq_dihedral_from_luma(luma: Matrix) -> List[PDQHash]:
    """Hashes of all 8 rotations/reflections; the first is the plain hash."""
    buf64, quality = _prepare(luma)
    return [PDQHash(_bits_from_dct(d), quality) for d in _dihedral_dcts(_dct_16(buf64))]


def pdq_from_rgb(width: int, height: int, pixels: Sequence[Tuple[int, int, int]]) -> PDQHash:
    return pdq_from_luma(luma_from_rgb(width, height, pixels))


def pdq_dihedral_from_rgb(width: int, height: int, pixels: Sequence[Tuple[int, int, int]]) -> List[PDQHash]:
    return pdq_dihedral_from_luma(luma_from_rgb(width, height, pixels))
