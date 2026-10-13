"""Private access codes for the victim status page.

When police register a takedown request they give the victim the complaint
reference plus a one-off access code. The status page needs both, so knowing
(or guessing) a reference number is not enough to see anything.

- Codes have 60 random bits (12 characters from a 32-letter alphabet without
  look-alike characters), shown as XXXX-XXXX-XXXX.
- Only a salted PBKDF2-SHA256 hash of each code is stored. The plain codes are
  printed once for handing to victims and are never written to the access file.
- Checking an unknown reference costs the same as checking a known one, and both
  failures look identical to the caller.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Union

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L
CODE_LENGTH = 12
DEFAULT_ITERATIONS = 100_000

assert len(set(ALPHABET)) == len(ALPHABET)


def generate_access_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
    return "-".join(raw[i : i + 4] for i in range(0, CODE_LENGTH, 4))


def normalise_code(code: str) -> str:
    """Accept lower case, spaces and missing dashes."""
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def normalise_reference(reference: str) -> str:
    return (reference or "").strip().upper()


def _hash(code: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", normalise_code(code).encode("utf-8"), salt, iterations)


@dataclass(frozen=True)
class AccessEntry:
    reference: str
    salt_hex: str
    hash_hex: str
    iterations: int = DEFAULT_ITERATIONS

    def matches(self, code: str) -> bool:
        candidate = _hash(code, bytes.fromhex(self.salt_hex), self.iterations)
        return hmac.compare_digest(candidate, bytes.fromhex(self.hash_hex))

    @classmethod
    def create(cls, reference: str, code: str, iterations: int = DEFAULT_ITERATIONS) -> "AccessEntry":
        salt = secrets.token_bytes(16)
        return cls(normalise_reference(reference), salt.hex(), _hash(code, salt, iterations).hex(), iterations)


class AccessStore:
    """Reference -> hashed access code. Police-held; never committed or published."""

    def __init__(self, entries: Iterable[AccessEntry] = (), iterations: int = DEFAULT_ITERATIONS) -> None:
        self.iterations = iterations
        self._entries: Dict[str, AccessEntry] = {e.reference: e for e in entries}
        # Used to make failed lookups for unknown references cost the same as real ones.
        self._decoy = AccessEntry.create("DECOY", generate_access_code(), iterations)

    def __contains__(self, reference: str) -> bool:
        return normalise_reference(reference) in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def issue(self, reference: str) -> str:
        """Create (or replace) the code for a reference and return the plain code once."""
        code = generate_access_code()
        entry = AccessEntry.create(reference, code, self.iterations)
        self._entries[entry.reference] = entry
        return code

    def verify(self, reference: str, code: str) -> bool:
        entry = self._entries.get(normalise_reference(reference))
        if entry is None:
            self._decoy.matches(code)  # same work, same answer shape
            return False
        return entry.matches(code)

    # ----------------------------------------------------------------- storage

    def save(self, path: Union[str, Path]) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "format": "victim-access-v1",
            "entries": [
                {"reference": e.reference, "salt": e.salt_hex, "hash": e.hash_hex, "iterations": e.iterations}
                for e in sorted(self._entries.values(), key=lambda e: e.reference)
            ],
        }
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(target)

    @classmethod
    def load(cls, path: Union[str, Path]) -> "AccessStore":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("format") != "victim-access-v1":
            raise ValueError("not a victim access file")
        entries = [
            AccessEntry(e["reference"], e["salt"], e["hash"], int(e.get("iterations", DEFAULT_ITERATIONS)))
            for e in data["entries"]
        ]
        iterations = entries[0].iterations if entries else DEFAULT_ITERATIONS
        return cls(entries, iterations)


def issue_codes(store: AccessStore, references: Iterable[str], replace: bool = False) -> List[Tuple[str, str]]:
    """Issue codes for references that don't have one yet (or all, with replace=True)."""
    issued = []
    for ref in references:
        if replace or ref not in store:
            issued.append((normalise_reference(ref), store.issue(ref)))
    return issued


def optional_store(path: Optional[str]) -> Optional[AccessStore]:
    return AccessStore.load(path) if path else None
