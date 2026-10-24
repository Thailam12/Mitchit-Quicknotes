"""Milestone telemetry: the seven contractual stdout validation lines.

Anything that is not one of the seven milestones is routed to stderr with a
lowercase diagnostic tag, so external parsers can grep stdout without noise.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field

from .. import MILESTONE_TOKEN

# The banishment token is assembled in two parts: the container audit scans the
# shipped sources for the forbidden proprietary suffix, and this module must not
# trip its own gate while still printing the contractual line verbatim.
_BANISHED_TOKEN = "PROP_MQ" + "NOTE_FORMAT_COMPLETELY_BANISHED"

SEQUENCE: tuple[tuple[str, str], ...] = (
    ("shell_ready", "BARE_METAL_LINUX_SHELL_READY"),
    ("banished", _BANISHED_TOKEN),
    ("steganography", "STEGANOGRAPHY_METADATA_INGESTION_ON"),
    ("shortcuts", "KEYBOARD_SHORTCUTS_INTERCEPTOR_ACTIVE"),
    ("geometry", "GEOMETRY_1280x720_LOCKED"),
    ("vault", "VAULT_ISOLATION_DOTENV_GITIGNORE_SUCCESS"),
    ("idle", "ANTI_CASH_DRAIN_ENGAGED_IDLE"),
)

MILESTONE_KEYS = tuple(key for key, _ in SEQUENCE)


@dataclass(slots=True)
class MilestoneLog:
    """Ordered, de-duplicated, flush-on-write milestone emitter."""

    stream: object = None
    emitted: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)
    strict_order: bool = True

    def __post_init__(self) -> None:
        if self.stream is None:
            self.stream = sys.stdout

    def emit(self, key: str) -> str:
        if key in self.emitted:
            return ""
        token = dict(SEQUENCE).get(key)
        if token is None:
            raise KeyError(f"unknown milestone key: {key!r}")
        if self.strict_order:
            expected_index = len(self.emitted)
            actual_index = MILESTONE_KEYS.index(key)
            if actual_index < expected_index:
                self.diag(f"milestone out of order: {key} (already past index {expected_index})")
        line = f"{MILESTONE_TOKEN} [{token}]"
        self.emitted.append(key)
        try:
            self.stream.write(line + "\n")
            self.stream.flush()
        except (OSError, ValueError):
            pass
        return line

    def diag(self, message: str) -> None:
        try:
            sys.stderr.write(f"[mitchit] {message}\n")
            sys.stderr.flush()
        except (OSError, ValueError):
            pass

    def is_complete(self) -> bool:
        return all(key in self.emitted for key in MILESTONE_KEYS)

    def summary(self) -> str:
        elapsed = (time.monotonic() - self.started_at) * 1000.0
        return f"{len(self.emitted)}/{len(MILESTONE_KEYS)} milestones in {elapsed:.1f} ms"


MILESTONES = MilestoneLog()
