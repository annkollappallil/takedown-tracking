"""PDQ hashing: exact agreement with the official implementation, and match behaviour.

Reference vectors below were produced with the official PDQ library (the `pdqhash`
binding of Meta's ThreatExchange C++ code) on the same synthetic images, so these
tests prove the pure-Python version is bit-for-bit compatible without needing it.
"""

import itertools

import pytest

from takedown_tracking.hashing.images import (
    Image,
    adjust_brightness,
    mirror,
    rescale,
    reupload_variant,
    synthetic_image,
)
from takedown_tracking.hashing.pdq import (
    DEFAULT_MATCH_THRESHOLD,
    PDQHash,
    pdq_dihedral_from_rgb,
    pdq_from_luma,
    pdq_from_rgb,
)

REFERENCE = [
    (("plain", "SYN-REQ-2026-00001", 128), "cd235a176cf090e9b3cd37497a9c7b1cc523c9b46ad4e52b338c33ccb0c932c9", 100),
    (("plain", "SYN-REQ-2026-00002", 128), "cd870fe607c9078787c1c7f8633e703179837dc23dc11f831b80f878c83cc63f", 100),
    (("plain", "pdq-ref-a", 64), "d0f848760db61fcc86f1d318df0cefa3f1c5f0db401e38612db007be66dbca01", 100),
    (("plain", "pdq-ref-b", 97), "f6b3984de433c84d918c66b20b4b25b71acfca5bb5b4a9ece7f292c4084b6433", 100),
    (("plain", "pdq-ref-c", 200), "aec9a6c928b98e97b2e9ae89aec9a2c91a50c5af38e9a2c9aac9a2d95d365136", 49),
    (("rescaled", "pdq-ref-d", 128), "1c5671f05dfcf7f0ae78b389dc6e5a2973f995deaa0184070901480755d2f0f9", 100),
    (("wide", "pdq-ref-e", 160), "0038fe0007ffe0072fc3c0ff7c0007f8f8180f80c1ffff83c1f73c1f1cf03c3e", 55),
]

# All 8 rotations/reflections of the first reference image, from the official library.
DIHEDRAL_0 = [
    "90d23e0f273fa0f0e0605b6f4f0d73233a6fc7d8b0d09c9f1f1fb493c090336c",
    "98760f4279a5c5bd4698625c67c9274990769ce13d81b07e66d96699e59c679c",
    "9876b0bd79a53a42e6989da367c9d0b69076631e3d814f8166d99966e59c9863",
    "98d2e3f02f3f5f0fe360a4904f2d8ddc3a6f3c27b0d063601f1f4b6cc090dc93",
    "cd235a176cf090e9b3cd37497a9c7b1cc523c9b46ad4e52b338c33ccb0c932c9",
    "cd23e5e82cf06f17b3cdc8f6329c85e3c523364b68d41ad4338ccc33b0c9cd36",
    "cd872b5a7a6af5a5b4350e3a1a5826766f3a928de585c9ca4a4ae1c695c56639",
    "cd87d4a57a6a0a5ab435f1c51a58d9896f3a6d72e58536354a4a1e3995c599c6",
]


def build(case):
    kind, seed, size = case
    img = synthetic_image(seed, size)
    if kind == "rescaled":
        img = rescale(img, 0.73)
    if kind == "wide":
        img = Image(img.width, 110, img.pixels[: img.width * 110])
    return img


def h(img):
    return pdq_from_rgb(img.width, img.height, img.pixels)


class TestReferenceCompatibility:
    @pytest.mark.parametrize("case, expected_hex, expected_quality", REFERENCE)
    def test_matches_official_pdq(self, case, expected_hex, expected_quality):
        result = h(build(case))
        assert result.to_hex() == expected_hex
        assert result.quality == expected_quality

    def test_dihedral_matches_official_pdq(self):
        img = build(REFERENCE[0][0])
        hashes = pdq_dihedral_from_rgb(img.width, img.height, img.pixels)
        assert sorted(x.to_hex() for x in hashes) == DIHEDRAL_0
        assert hashes[0] == h(img)  # first is the plain hash


class TestHashBasics:
    def test_hex_round_trip_and_distance(self):
        a = PDQHash.from_hex(REFERENCE[0][1])
        b = PDQHash.from_hex(REFERENCE[1][1])
        assert a.to_hex() == REFERENCE[0][1]
        assert a.distance(a) == 0
        assert a.distance(b) == b.distance(a) > DEFAULT_MATCH_THRESHOLD

    def test_flat_image_has_no_useful_quality(self):
        flat = [[128.0] * 64 for _ in range(64)]
        assert pdq_from_luma(flat).quality == 0
        assert not pdq_from_luma(flat).is_useful

    def test_too_small_image_rejected(self):
        with pytest.raises(ValueError):
            pdq_from_luma([[1.0] * 4 for _ in range(4)])


class TestMatchBehaviour:
    @pytest.fixture(scope="class")
    def originals(self):
        return {f"SYN-REQ-2026-{i:05d}": synthetic_image(f"SYN-REQ-2026-{i:05d}") for i in range(20)}

    def test_common_reupload_edits_still_match(self, originals):
        for seed, img in originals.items():
            base = h(img)
            for edited in (rescale(img, 0.6), rescale(img, 0.85), adjust_brightness(img, 1.15, -10)):
                assert base.distance(h(edited)) <= DEFAULT_MATCH_THRESHOLD, seed

    def test_mirrored_copy_matches_via_rotations_and_reflections(self, originals):
        for seed, img in originals.items():
            flipped = mirror(img)
            best = min(h(img).distance(x) for x in pdq_dihedral_from_rgb(flipped.width, flipped.height, flipped.pixels))
            assert best <= DEFAULT_MATCH_THRESHOLD, seed

    def test_unrelated_images_never_match(self, originals):
        hashes = {s: h(img) for s, img in originals.items()}
        dihedral = {s: pdq_dihedral_from_rgb(img.width, img.height, img.pixels) for s, img in originals.items()}
        for a, b in itertools.combinations(originals, 2):
            best = min(hashes[a].distance(x) for x in dihedral[b])
            assert best > 2 * DEFAULT_MATCH_THRESHOLD, (a, b)

    def test_variants_are_deterministic(self, originals):
        img = originals["SYN-REQ-2026-00003"]
        first, kind1 = reupload_variant(img, "x")
        second, kind2 = reupload_variant(img, "x")
        assert first == second and kind1 == kind2

    def test_jpeg_recompression_matches(self, originals):
        pytest.importorskip("PIL")
        from takedown_tracking.hashing.images import jpeg_recompress

        for seed, img in list(originals.items())[:8]:
            assert h(img).distance(h(jpeg_recompress(img, quality=60))) <= DEFAULT_MATCH_THRESHOLD, seed
