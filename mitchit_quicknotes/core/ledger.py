"""Audit ledger, IO write gate and bare-metal hardware probe.

The ledger is the single accounting surface for the anti-cash-drain contract:
disk serialization is only released on a sanctioned channel (an explicit user
command), and every suppressed idle write is counted so the HUD can prove that
the engine performed no background work.
"""

from __future__ import annotations

import os
import platform
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Deque, Iterable

SANCTIONED_CHANNELS = frozenset(
    {"KHAI_HOA", "SAVE", "SAVE_AS", "ZIP", "SEED", "BOOTSTRAP"}
)


class BlockedWrite(RuntimeError):
    """Raised when a write is attempted outside a sanctioned channel."""


@dataclass(frozen=True, slots=True)
class Event:
    stamp: float
    level: str
    source: str
    message: str

    def render(self) -> str:
        clock = time.strftime("%H:%M:%S", time.localtime(self.stamp))
        return f"{clock} {self.level:<5} {self.source:<9} {self.message}"


@dataclass(frozen=True, slots=True)
class HardwareSnapshot:
    cpu_model: str
    cores: int
    mem_total_mb: int
    mem_available_mb: int
    temp_c: float | None
    temp_source: str
    kernel: str
    session: str
    python: str
    machine: str

    @property
    def temp_label(self) -> str:
        if self.temp_c is None:
            return "n/a"
        return f"{self.temp_c:.1f}°C"

    def temp_delta(self, target_c: float = 20.0) -> str:
        if self.temp_c is None:
            return "sensor offline"
        delta = self.temp_c - target_c
        return f"{delta:+.1f}°C vs {target_c:.0f}°C target"


def _read_first_existing(paths: Iterable[Path]) -> str | None:
    for path in paths:
        try:
            if path.exists():
                text = path.read_text(encoding="utf-8", errors="ignore").strip()
                if text:
                    return text
        except OSError:
            continue
    return None


def _scan_cpu_temp() -> tuple[float | None, str]:
    thermal = Path("/sys/class/thermal")
    candidates: list[tuple[float, str]] = []
    try:
        for zone in sorted(thermal.glob("thermal_zone*")):
            raw = _read_first_existing((zone / "temp",))
            if raw is None:
                continue
            try:
                value = float(raw)
            except ValueError:
                continue
            if value > 1000.0:
                value /= 1000.0
            if -20.0 < value < 130.0:
                label = _read_first_existing((zone / "type",)) or zone.name
                candidates.append((value, label))
    except OSError:
        pass

    hwmon = Path("/sys/class/hwmon")
    try:
        for chip in sorted(hwmon.glob("hwmon*")):
            name = _read_first_existing((chip / "name",)) or chip.name
            for entry in sorted(chip.glob("temp*_input")):
                raw = _read_first_existing((entry,))
                if raw is None:
                    continue
                try:
                    value = float(raw) / 1000.0
                except ValueError:
                    continue
                if -20.0 < value < 130.0:
                    candidates.append((value, f"{name}:{entry.stem}"))
    except OSError:
        pass

    if not candidates:
        return None, "no sensor"
    value, label = max(candidates, key=lambda item: item[0])
    return value, label


def _read_meminfo() -> tuple[int, int]:
    total = 0
    available = 0
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) // 1024
                elif line.startswith("MemAvailable:"):
                    available = int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return total, available
    return total, available


def _cpu_model() -> str:
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except (OSError, IndexError):
        pass
    return platform.processor() or "unknown CPU"


def probe_hardware() -> HardwareSnapshot:
    temp, source = _scan_cpu_temp()
    total, available = _read_meminfo()
    session = os.environ.get("XDG_SESSION_TYPE", "unknown")
    if os.environ.get("WAYLAND_DISPLAY"):
        session = f"{session}/wayland"
    elif os.environ.get("DISPLAY"):
        session = f"{session}/x11"
    return HardwareSnapshot(
        cpu_model=_cpu_model(),
        cores=os.cpu_count() or 1,
        mem_total_mb=total,
        mem_available_mb=available,
        temp_c=temp,
        temp_source=source,
        kernel=platform.release(),
        session=session,
        python=platform.python_version(),
        machine=platform.machine(),
    )


def process_cpu_seconds() -> float:
    """CPU time consumed by this process (user + system), no timers involved."""
    try:
        fields = Path("/proc/self/stat").read_text(encoding="utf-8").split()
        ticks = os.sysconf("SC_CLK_TCK")
        return (float(fields[13]) + float(fields[14])) / float(ticks)
    except (OSError, ValueError, IndexError):
        return 0.0


def process_rss_mb() -> float:
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except (OSError, ValueError, IndexError):
        return 0.0
    return 0.0


@dataclass(slots=True)
class Ledger:
    events: Deque[Event] = field(default_factory=lambda: deque(maxlen=400))
    counters: Counter[str] = field(default_factory=Counter)
    io_log: Deque[tuple[float, str, Path, int]] = field(default_factory=lambda: deque(maxlen=64))
    last_action_at: float = field(default_factory=time.monotonic)
    boot_cpu_seconds: float = field(default_factory=process_cpu_seconds)

    def log(self, source: str, message: str, level: str = "INFO") -> Event:
        event = Event(stamp=time.time(), level=level, source=source, message=message)
        self.events.append(event)
        self.counters[f"log.{source}"] += 1
        if level in ("WARN", "ERROR"):
            self.counters[f"log.{level}"] += 1
        return event

    def bump(self, name: str, delta: int = 1) -> None:
        self.counters[name] += delta

    def mark_action(self) -> None:
        self.last_action_at = time.monotonic()

    def idle_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.last_action_at)

    def cpu_seconds_since_boot(self) -> float:
        return max(0.0, process_cpu_seconds() - self.boot_cpu_seconds)

    def write_bytes(self, path: Path, payload: bytes, channel: str) -> int:
        """Sanctioned disk release. Raises ``BlockedWrite`` for idle channels."""
        if channel not in SANCTIONED_CHANNELS:
            self.counters["io.blocked"] += 1
            self.log("io-gate", f"BLOCKED write on idle channel '{channel}' → {path.name}", "WARN")
            raise BlockedWrite(f"write channel not sanctioned: {channel!r}")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        except OSError as exc:
            self.counters["io.errors"] += 1
            self.log("io-gate", f"write failed {path.name}: {exc}", "ERROR")
            raise
        size = len(payload)
        self.counters["io.writes"] += 1
        self.counters["io.bytes"] += size
        self.io_log.append((time.time(), channel, path, size))
        self.log("io-gate", f"{channel} · {path.name} · {size / 1024.0:.1f} KiB", "WRITE")
        return size

    def write_text(self, path: Path, text: str, channel: str) -> int:
        return self.write_bytes(path, text.encode("utf-8"), channel)

    def read_text(self, path: Path, limit_bytes: int = 8_000_000) -> str:
        try:
            stat = path.stat()
            if stat.st_size > limit_bytes:
                self.log("io", f"refused oversized read {path.name} ({stat.st_size} B)", "WARN")
                return ""
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            self.log("io", f"read failed {path.name}: {exc}", "WARN")
            return ""

    def tail(self, count: int = 12) -> list[str]:
        items = list(self.events)[-count:]
        return [event.render() for event in items]
