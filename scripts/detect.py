"""Hardware and OS detection for model-tracker.

Attempts to auto-detect OS, agent, CPU, RAM, and VRAM. Returns a dict that
can be merged into a system_info row. Any field that fails returns None, so
the caller (setup wizard or auto-record) knows to prompt the user.

Never raises — returns None for every un-detectable field.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
from typing import Any, Dict, Optional

# User-supplied overrides (set by setup wizard or config loader).
_STATIC_OVERRIDES: Dict[str, str] = {}


def set_static_overrides(overrides: Dict[str, str]) -> None:
    """Apply static hardware overrides from config or /command."""
    _STATIC_OVERRIDES.clear()
    _STATIC_OVERRIDES.update(overrides)


def clear_static_overrides() -> None:
    _STATIC_OVERRIDES.clear()


def detect() -> Dict[str, Optional[str]]:
    """Run all detection heuristics. Returns {field: value | None}."""
    return {
        "os_make_version": _detect_os(),
        "agent_make_version": _detect_agent(),
        "hardware_details": _detect_hardware(),
    }


def detect_os() -> Optional[str]:
    return _detect_os()


def detect_agent() -> Optional[str]:
    return _detect_agent()


def detect_hardware() -> Optional[str]:
    return _detect_hardware()


# ---------------------------------------------------------------------------
# OS detection
# ---------------------------------------------------------------------------

def _detect_os() -> Optional[str]:
    # Try freedesktop os-release first (systemd-based distros).
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            return _read_os_release(path)
        except (FileNotFoundError, OSError):
            continue

    # Fallback: platform module.
    name = platform.system()  # Linux, Darwin, Windows, etc.
    release = platform.release()
    version = platform.version()
    machine = platform.machine()

    if name == "Linux":
        # Try lsb_release.
        try:
            r = subprocess.run(
                ["lsb_release", "-ds"], capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Try hostnamectl.
        try:
            r = subprocess.run(
                ["hostnamectl", "--static"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                hostname = r.stdout.strip()
                version_id = ""
                for p in ("/etc/os-release", "/usr/lib/os-release"):
                    try:
                        version_id = _read_field(p, "VERSION_ID")
                        break
                    except (FileNotFoundError, OSError):
                        continue
                parts = [hostname]
                if version_id:
                    parts.append(version_id)
                return " ".join(parts)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        return f"Linux {release} {machine}"

    if name == "Darwin":
        try:
            r = subprocess.run(
                ["sw_vers", "-productVersion"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                return f"macOS {r.stdout.strip()}"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return f"macOS {release}"

    if name == "Windows":
        try:
            edition = platform.win32_edition()
            if edition:
                return f"Windows {edition} (build {platform.win32_ver()[1] or release})"
        except Exception:
            pass
        return f"Windows {platform.win32_ver()[1] or release} {machine}"

    return f"{name} {release}"


def _read_os_release(path: str) -> str:
    id_name = _read_field(path, "NAME")
    version_id = _read_field(path, "VERSION_ID")
    version = _read_field(path, "VERSION")

    parts = []
    if id_name:
        parts.append(id_name)
    if version_id:
        parts.append(version_id)
    elif version:
        parts.append(version.strip('"'))

    if not parts:
        return ""
    return " ".join(parts)


def _read_field(path: str, key: str) -> str:
    """Read a single key from a key=value file, stripping quotes."""
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith(key + "="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                return val
    return ""


# ---------------------------------------------------------------------------
# Agent version detection
# ---------------------------------------------------------------------------

def _detect_agent() -> Optional[str]:
    # Environment variable (set by Hermes gateway or launcher).
    for var in ("HERMES_VERSION", "AGENT_VERSION", "AGENT_MAKE_VERSION"):
        val = os.environ.get(var)
        if val:
            return val

    # Hermes desktop app might signal via a file.
    hermes_info = os.path.expanduser("~/.hermes/info.json")
    if os.path.isfile(hermes_info):
        try:
            import json as _json
            info = _json.load(open(hermes_info))
            version = info.get("version", "")
            if version:
                return f"Hermes {version}"
        except (json.JSONDecodeError, OSError):
            pass

    return None


# ---------------------------------------------------------------------------
# Hardware detection
# ---------------------------------------------------------------------------

def _detect_hardware() -> Optional[str]:
    cpu = _detect_cpu()
    ram = _detect_ram()
    vram = _detect_vram()

    parts = []
    if cpu:
        parts.append(cpu)
    if ram:
        parts.append(ram)
    if vram:
        parts.append(vram)

    return "; ".join(parts) if parts else None


def _detect_cpu() -> Optional[str]:
    # Try /proc/cpuinfo first.
    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if line.startswith("model name"):
                    model = line.split(":", 1)[1].strip()
                    break
            else:
                model = None

        # Count cores from /proc/cpuinfo or nproc.
        nproc = None
        try:
            r = subprocess.run(
                ["nproc"], capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0 and r.stdout.strip():
                nproc = r.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        if not nproc:
            # Count processor lines.
            with open("/proc/cpuinfo", "r") as f:
                nproc = sum(1 for l in f if l.startswith("processor"))

        if model and nproc:
            return f"CPU: {model} ({nproc} cores)"
        if model:
            return f"CPU: {model}"
    except (FileNotFoundError, OSError):
        pass

    # Fallback: platform.machine().
    machine = platform.machine()
    try:
        nproc = subprocess.run(
            ["nproc"], capture_output=True, text=True, timeout=5
        )
        if nproc.returncode == 0:
            return f"CPU: {machine} ({nproc.stdout.strip()} cores)"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return f"CPU: {machine}" if machine else None


def _detect_ram() -> Optional[str]:
    # Try /proc/meminfo.
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    kb = int(line.split(":")[1].strip().split()[0])
                    gb = round(kb / 1024 / 1024, 1)
                    return f"{gb} GB RAM"
    except (FileNotFoundError, OSError, ValueError):
        pass

    # Fallback: platform.
    try:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        gb = round(total / 1024 / 1024 / 1024, 1)
        return f"{gb} GB RAM"
    except (ValueError, OSError):
        pass

    return None


def _detect_vram() -> Optional[str]:
    # Try nvidia-smi.
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            lines = [l.strip() for l in r.stdout.strip().split("\n") if l.strip()]
            if lines:
                parts = []
                for line in lines:
                    # Format: "GPU 0, NVIDIA RTX 4090, 24576 MiB"
                    # or "NVIDIA RTX 4090\t24576 MiB" depending on CSV format.
                    parts.append(line)
                return f"GPU: {'; '.join(parts)}"
    except (FileNotFoundError, subprocess.TimeoutExpired, IndexError):
        pass

    # Try Apple GPU (macOS).
    if platform.system() == "Darwin":
        try:
            r = subprocess.run(
                ["system_profiler", "SPDisplaysDataType"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                for line in r.stdout.split("\n"):
                    if "Chipset Model" in line or "Display Chipset" in line:
                        chip = line.split(":")[1].strip()
                        return f"GPU: {chip}"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    return None
