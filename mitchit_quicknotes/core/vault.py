"""AUDIT_2 — shield repository vault.

Guarantees a local ``.env`` parameters database exists and that ``.env`` is
permanently absent from version control by appending it to ``.gitignore``.
No credential value is ever generated or logged here: the file is a clean
template of local runtime parameters only.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from .constants import ENV_FILE, EXPORT_DIR, GITIGNORE_FILE, NOTES_DIR, PROJECT_ROOT

ENV_TEMPLATE: tuple[tuple[str, str], ...] = (
    ("MQ_STUDIO_HOME", str(PROJECT_ROOT)),
    ("MQ_NOTES_DIR", str(NOTES_DIR)),
    ("MQ_EXPORT_DIR", str(EXPORT_DIR)),
    ("MQ_PILLOW_BACKEND", "native"),
    ("MQ_OCR_BUDGET_MS", "1.0"),
    ("MQ_OCR_ENGINE", "local_vector_then_raster"),
    ("MQ_EGRESS_POLICY", "deny"),
    ("MQ_EGRESS_ALLOW_LOOPBACK", "1"),
    ("MQ_GLASS_BLUR_RADIUS", "26"),
    ("MQ_TELEMETRY", "off"),
    ("MQ_CLOUD_RUNTIME_COST_VND", "0"),
)

GITIGNORE_GUARDS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "!.env.example",
    "__pycache__/",
    "*.py[cod]",
    ".venv/",
    "venv/",
    "exports/",
    "notes/",
    "*.png~",
    "*.tmp.png",
)

DEFAULT_ENV_BODY = """# Mitchit Quicknotes Studio — local environment vault
# Every parameter is local. No secret, key or token belongs in this file.
# The .env path is hard-locked inside .gitignore: tiền không cánh mà bay.
""" + "".join(f"{key}={value}\n" for key, value in ENV_TEMPLATE)


@dataclass(slots=True)
class VaultReport:
    env_path: Path
    env_created: bool
    gitignore_path: Path
    gitignore_created: bool
    gitignore_appended: list[str] = field(default_factory=list)
    permissions_hardened: bool = False
    parameters: dict[str, str] = field(default_factory=dict)

    @property
    def isolated(self) -> bool:
        lines = self._gitignore_lines()
        return ".env" in lines and ".env.*" in lines

    def _gitignore_lines(self) -> set[str]:
        try:
            text = self.gitignore_path.read_text(encoding="utf-8")
        except OSError:
            return set()
        return {line.strip() for line in text.splitlines()}

    def describe(self) -> str:
        created = "created" if self.env_created else "verified"
        return (
            f".env {created} · .gitignore "
            f"{'appended' if self.gitignore_appended else 'clean'} · isolation "
            f"{'ON' if self.isolated else 'OFF'}"
        )


def _ensure_gitignore_line(path: Path, lines: list[str], wanted: str) -> bool:
    existing = set()
    if path.exists():
        try:
            existing = {ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()}
        except OSError:
            existing = set()
    else:
        existing = set()
    if wanted in existing:
        return False
    lines.append(wanted)
    return True


def ensure_vault(env_path: Path = ENV_FILE, gitignore_path: Path = GITIGNORE_FILE) -> VaultReport:
    """Create/verify the vault and its gitignore lock. Never raises OSError."""
    env_created = False
    gi_created = False
    appended: list[str] = []
    hardened = False

    try:
        env_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    if not env_path.exists():
        try:
            env_path.write_text(DEFAULT_ENV_BODY, encoding="utf-8")
            env_created = True
        except OSError:
            env_created = False
    try:
        mode = env_path.stat().st_mode
        os.chmod(env_path, stat.S_IRUSR | stat.S_IWUSR)
        hardened = bool(stat.S_IMODE(mode) != (stat.S_IRUSR | stat.S_IWUSR))
    except OSError:
        hardened = False

    if not gitignore_path.exists():
        try:
            gitignore_path.write_text("", encoding="utf-8")
            gi_created = True
        except OSError:
            gi_created = False

    pending: list[str] = []
    for guard in GITIGNORE_GUARDS:
        if _ensure_gitignore_line(gitignore_path, pending, guard):
            appended.append(guard)

    if appended:
        try:
            prefix = ""
            try:
                existing_text = gitignore_path.read_text(encoding="utf-8")
                if existing_text and not existing_text.endswith("\n"):
                    prefix = "\n"
            except OSError:
                prefix = ""
            header = "" if gi_created else "\n# Mitchit Quicknotes Studio — vault isolation\n"
            with gitignore_path.open("a", encoding="utf-8") as handle:
                handle.write(prefix + header + "\n".join(appended) + "\n")
        except OSError:
            appended = []

    return VaultReport(
        env_path=env_path,
        env_created=env_created,
        gitignore_path=gitignore_path,
        gitignore_created=gi_created,
        gitignore_appended=appended,
        permissions_hardened=hardened,
        parameters=load_env(env_path),
    )


def load_env(path: Path = ENV_FILE) -> dict[str, str]:
    """Minimal, dependency-free ``.env`` reader. Malformed lines are skipped."""
    data: dict[str, str] = {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return data
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            data[key] = value
    return data


def env_float(params: dict[str, str], key: str, fallback: float) -> float:
    try:
        return float(params.get(key, fallback))
    except (TypeError, ValueError):
        return fallback
