from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path


def system_notify_enabled() -> bool:
    """
    Native OS notifications when price drops fire.

    - KALIMATI_SYSTEM_NOTIFY=1 / true / on  → force enable (if the OS supports it).
    - KALIMATI_SYSTEM_NOTIFY=0 / false / off → force disable.
    - Unset → enabled by default on macOS only (osascript). On Linux, enable only if
      ``notify-send`` exists and you set KALIMATI_SYSTEM_NOTIFY=1 (avoids headless errors).
    """
    v = os.environ.get("KALIMATI_SYSTEM_NOTIFY", "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    return platform.system() == "Darwin"


def _truncate(s: str, max_len: int) -> str:
    s = s.strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _escape_applescript_string(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def send_system_notification(title: str, body: str, *, force: bool = False) -> None:
    """
    Best-effort native notification; never raises.

    ``force=True`` skips ``KALIMATI_SYSTEM_NOTIFY`` (used for scheduled 7:30 AM/PM digests).
    """
    if not force and not system_notify_enabled():
        return

    title = _truncate(title, 120)
    body = _truncate(body, 450)
    system = platform.system()

    try:
        if system == "Darwin":
            t = _escape_applescript_string(title)
            b = _escape_applescript_string(body)
            script = f'display notification "{b}" with title "{t}"'
            subprocess.run(
                ["osascript", "-e", script],
                check=False,
                capture_output=True,
                timeout=15,
                text=True,
            )
        elif system == "Linux" and shutil.which("notify-send"):
            subprocess.run(
                ["notify-send", "--app-name", "Kalimati", title, body],
                check=False,
                capture_output=True,
                timeout=15,
                text=True,
            )
        elif system == "Windows":
            ps1 = Path(__file__).resolve().parent.parent / "install" / "windows" / "toast.ps1"
            if ps1.is_file():
                subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(ps1),
                        "-Title",
                        title,
                        "-Body",
                        body,
                    ],
                    check=False,
                    capture_output=True,
                    timeout=30,
                    text=True,
                )
    except (OSError, subprocess.TimeoutExpired):
        pass
