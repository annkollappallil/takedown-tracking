"""Re-upload detection with PDQ perceptual hashes.

How it works:
- When a takedown is sent, the police register PDQ hashes of the reported content
  in a HashRegistry inside their own environment.
- A CandidateFeed supplies posts seen on the platform (in a real deployment, for
  example, items surfaced by platform hash-sharing programmes or monitoring).
- PDQReuploadDetector.scan() says whether any candidate live at a given time
  matches the removed content: Hamming distance <= threshold (default 31 of 256
  bits) in any of the 8 rotations/reflections, with both hashes of usable quality.

Hashes never leave the registry, the feed or the detector. The runner only learns
a yes/no and turns it into reupload_detected_at.

SimulatedCandidateFeed builds a feed from the simulator's hidden ground truth using
harmless generated images: real re-uploads appear as edited copies (resized, recoloured,
mirrored, cropped, watermarked) and unrelated decoy images are mixed in, so the demo
measures both missed copies and false matches.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Dict, Iterable, List, Optional, Protocol, Sequence, Tuple

from takedown_tracking.hashing.images import Image, seeded_rng, reupload_variant, synthetic_image
from takedown_tracking.hashing.pdq import (
    DEFAULT_MATCH_THRESHOLD,
    MIN_USEFUL_QUALITY,
    PDQHash,
    pdq_dihedral_from_rgb,
    pdq_from_rgb,
)
from takedown_tracking.models.request import RequestRecord
from takedown_tracking.verifier.simulator import SimulatedChecker


@dataclass(frozen=True)
class Candidate:
    """A post seen on a platform. `hashes` holds all 8 orientations; the first is the plain hash."""

    platform: str
    seen_at: datetime
    hashes: Tuple[PDQHash, ...]
    taken_down_at: Optional[datetime] = None

    def live_at(self, at: datetime) -> bool:
        return self.seen_at <= at and (self.taken_down_at is None or self.taken_down_at > at)


class CandidateFeed(Protocol):
    def candidates(self, platform: str, until: datetime) -> Iterable[Candidate]:
        """Posts seen on `platform` up to `until`. Must not include the reported post itself."""
        ...


class HashRegistry:
    """PDQ hashes of reported content, keyed by request ID. Stays inside the police environment."""

    def __init__(self, loader: Optional[Callable[[RequestRecord], Optional[PDQHash]]] = None) -> None:
        self._hashes: Dict[str, PDQHash] = {}
        self._loader = loader

    def register(self, request_id: str, pdq: PDQHash) -> None:
        self._hashes[request_id] = pdq

    def get(self, record: RequestRecord) -> Optional[PDQHash]:
        known = self._hashes.get(record.request_id)
        if known is None and self._loader is not None:
            known = self._loader(record)
            if known is not None:
                self._hashes[record.request_id] = known
        return known


def hash_image(img: Image) -> PDQHash:
    return pdq_from_rgb(img.width, img.height, img.pixels)


def candidate_from_image(platform: str, seen_at: datetime, img: Image, taken_down_at: Optional[datetime] = None) -> Candidate:
    return Candidate(platform, seen_at, tuple(pdq_dihedral_from_rgb(img.width, img.height, img.pixels)), taken_down_at)


def best_distance(known: PDQHash, candidate: Candidate) -> int:
    return min(known.distance(h) for h in candidate.hashes)


class PDQReuploadDetector:
    """ReuploadDetector backed by PDQ hash matching."""

    def __init__(
        self,
        registry: HashRegistry,
        feed: CandidateFeed,
        threshold: int = DEFAULT_MATCH_THRESHOLD,
        min_quality: int = MIN_USEFUL_QUALITY,
    ) -> None:
        self.registry = registry
        self.feed = feed
        self.threshold = threshold
        self.min_quality = min_quality

    def scan(self, record: RequestRecord, at: datetime) -> bool:
        known = self.registry.get(record)
        if known is None or known.quality < self.min_quality:
            return False  # too featureless to match reliably
        for candidate in self.feed.candidates(record.platform.value, at):
            if not candidate.live_at(at) or candidate.hashes[0].quality < self.min_quality:
                continue
            if best_distance(known, candidate) <= self.threshold:
                return True
        return False


# ----------------------------------------------------------------------------- simulation


def simulated_content(record: RequestRecord) -> Image:
    """The harmless stand-in image for a synthetic request's reported content."""
    return synthetic_image(record.request_id)


def simulated_registry() -> HashRegistry:
    return HashRegistry(loader=lambda record: hash_image(simulated_content(record)))


class SimulatedCandidateFeed:
    """Candidate posts built from the simulator's ground truth, plus unrelated decoys."""

    def __init__(
        self,
        checker: SimulatedChecker,
        records: Sequence[RequestRecord],
        decoys_per_platform: int = 5,
        seed: int = 42,
    ) -> None:
        self.checker = checker
        self.decoys_per_platform = decoys_per_platform
        self.seed = seed
        self._by_platform: Dict[str, List[RequestRecord]] = {}
        for r in records:
            self._by_platform.setdefault(r.platform.value, []).append(r)
        self._cache: Dict[str, List[Candidate]] = {}
        self.variant_kinds: Dict[str, str] = {}  # request_id -> edit applied (for evaluation)

    def _build(self, platform: str) -> List[Candidate]:
        records = self._by_platform.get(platform, [])
        out: List[Candidate] = []
        for r in records:
            truth = self.checker.ground_truth(r)
            if truth.reupload_at is None:
                continue
            copy, kind = reupload_variant(simulated_content(r), f"{self.seed}:{r.request_id}")
            self.variant_kinds[r.request_id] = kind
            out.append(candidate_from_image(platform, truth.reupload_at, copy))

        if records:
            start = min(r.sent_at for r in records)
            end = max(r.sent_at for r in records) + timedelta(days=30)
            span = (end - start).total_seconds()
            rng = seeded_rng(f"decoys:{self.seed}:{platform}")
            for i in range(self.decoys_per_platform):
                seen = start + timedelta(seconds=rng.uniform(0, span))
                decoy = synthetic_image(f"decoy:{self.seed}:{platform}:{i}")
                out.append(candidate_from_image(platform, seen, decoy))
        return out

    def candidates(self, platform: str, until: datetime) -> Iterable[Candidate]:
        if platform not in self._cache:
            self._cache[platform] = self._build(platform)
        return [c for c in self._cache[platform] if c.seen_at <= until]


def simulated_pdq_detector(
    checker: SimulatedChecker, records: Sequence[RequestRecord], decoys_per_platform: int = 5
) -> Tuple[PDQReuploadDetector, SimulatedCandidateFeed]:
    feed = SimulatedCandidateFeed(checker, records, decoys_per_platform, seed=checker.seed)
    return PDQReuploadDetector(simulated_registry(), feed), feed
