"""Anti-public-network interception shield.

Evaluates the live link layer (interface class, SSID, VPN tunnels) and, when an
unencrypted public LAN is detected without an encrypted tunnel, installs an
in-process egress veto that prevents any socket from connecting off-host.

The veto is implemented with a CPython audit hook, so it covers every library
that ultimately calls ``socket.connect`` — including urllib, requests and Qt's
own network stack — without patches and without touching the kernel firewall.
"""

from __future__ import annotations

import ipaddress
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .constants import PUBLIC_SSID_MARKERS, VPN_INTERFACE_PREFIXES, VPN_PROCESS_MARKERS

LOOPBACK_NAMES = frozenset({"localhost"})
_AUDIT_HOOK_INSTALLED = False
_ACTIVE_SHIELD: "NetworkShield | None" = None


@dataclass(slots=True)
class ShieldReport:
    interface: str
    link_kind: str
    ssid: str
    vpn_active: bool
    vpn_interface: str
    public_profile: bool
    armed: bool
    reason: str
    blocked_attempts: int = 0

    @property
    def headline(self) -> str:
        if self.armed:
            return "EGRESS VETO ARMED"
        if self.vpn_active:
            return f"VPN TUNNEL · {self.vpn_interface or 'encrypted'}"
        return "EGRESS OPEN · TRUSTED LINK"

    @property
    def detail(self) -> str:
        link = self.link_kind.upper()
        if self.ssid:
            link = f"{link} · {self.ssid}"
        return f"{link} · {self.interface or 'no-link'}"


def _run(argv: list[str], timeout: float = 1.4) -> str:
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (completed.stdout or "").strip()


def _default_interface() -> str:
    route = _run(["ip", "-o", "route", "show", "default"])
    for line in route.splitlines():
        parts = line.split()
        if "dev" in parts:
            index = parts.index("dev")
            if index + 1 < len(parts):
                return parts[index + 1]
    return ""


def _link_kind(interface: str) -> str:
    if not interface:
        return "offline"
    sysfs = Path("/sys/class/net") / interface
    try:
        if (sysfs / "wireless").exists():
            return "wifi"
        if (sysfs / "phy80211").exists():
            return "wifi"
    except OSError:
        pass
    try:
        if (sysfs / "carrier").exists() and (sysfs / "device").exists():
            return "ethernet"
    except OSError:
        pass
    return "unknown"


def _current_ssid(interface: str) -> str:
    if not interface:
        return ""
    ssid = _run(["iwgetid", "-r", interface])
    if ssid:
        return ssid
    ssid = _run(["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"])
    for line in ssid.splitlines():
        if line.startswith("yes:"):
            return line.split(":", 1)[1]
    return ""


def _vpn_tunnels() -> tuple[bool, str]:
    net = Path("/sys/class/net")
    try:
        for entry in sorted(net.iterdir()):
            name = entry.name
            if any(name.startswith(prefix) for prefix in VPN_INTERFACE_PREFIXES):
                return True, name
    except OSError:
        pass
    ps = _run(["ps", "-eo", "comm"])
    haystack = ps.lower()
    for marker in VPN_PROCESS_MARKERS:
        if marker in haystack:
            return True, f"proc:{marker}"
    return False, ""


def evaluate_network() -> ShieldReport:
    interface = _default_interface()
    kind = _link_kind(interface)
    ssid = _current_ssid(interface) if kind == "wifi" else ""
    vpn, vpn_iface = _vpn_tunnels()

    lowered = ssid.lower()
    public_profile = False
    if kind == "wifi":
        public_profile = any(marker in lowered for marker in PUBLIC_SSID_MARKERS)
        if "eduroam" in lowered or "corporate" in lowered or "wpa3" in lowered:
            public_profile = False

    needs_veto = bool(public_profile and not vpn)
    if needs_veto:
        reason = f"public LAN '{ssid or interface}' without encrypted tunnel"
    elif vpn:
        reason = f"encrypted tunnel via {vpn_iface}"
    elif kind == "offline":
        reason = "no active default route"
    else:
        reason = f"{kind} link accepted"

    return ShieldReport(
        interface=interface,
        link_kind=kind,
        ssid=ssid,
        vpn_active=vpn,
        vpn_interface=vpn_iface,
        public_profile=public_profile,
        armed=needs_veto,
        reason=reason,
    )


def _is_local_target(address: object) -> bool:
    if address is None:
        return True
    host = ""
    if isinstance(address, tuple) and address:
        host = str(address[0])
    elif isinstance(address, (str, bytes)):
        host = address.decode() if isinstance(address, bytes) else address
    if not host:
        return True
    if host in LOOPBACK_NAMES or host.startswith("/") or host.startswith("@abstract"):
        return True
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
        return ip.is_loopback or ip.is_unspecified
    except ValueError:
        return False


def _install_audit_hook() -> bool:
    global _AUDIT_HOOK_INSTALLED
    if _AUDIT_HOOK_INSTALLED:
        return True

    def _hook(event: str, args: tuple) -> None:
        if event != "socket.connect":
            return
        shield = _ACTIVE_SHIELD
        if shield is None or not shield.armed:
            return
        address = args[1] if len(args) > 1 else None
        if _is_local_target(address):
            return
        shield.blocked_attempts += 1
        raise PermissionError(
            "MITCHIT EGRESS VETO: " + (shield.report.reason if shield.report else "public link")
        )

    try:
        sys.addaudithook(_hook)
    except (AttributeError, RuntimeError):
        return False
    _AUDIT_HOOK_INSTALLED = True
    return True


@dataclass(slots=True)
class NetworkShield:
    report: ShieldReport | None = None
    blocked_attempts: int = 0
    audit_available: bool = False
    on_change: list = field(default_factory=list)

    def __post_init__(self) -> None:
        self.audit_available = _install_audit_hook()

    @property
    def armed(self) -> bool:
        return bool(self.report and self.report.armed and self.audit_available)

    def evaluate(self) -> ShieldReport:
        global _ACTIVE_SHIELD
        report = evaluate_network()
        report.blocked_attempts = self.blocked_attempts
        self.report = report
        _ACTIVE_SHIELD = self
        for callback in self.on_change:
            try:
                callback(report)
            except Exception:
                continue
        return report

    def summary(self) -> str:
        if self.report is None:
            return "SHIELD · not evaluated"
        return f"{self.report.headline} · {self.report.detail}"

    def allow_non_local(self) -> bool:
        return not self.armed
