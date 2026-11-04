from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from typing import Any

from timonel.config import Notifications


def message(record: dict[str, Any]) -> str:
    return f"Timonel run {record['id']}: {record['status']}"


def desktop(text: str) -> None:
    if sys.platform == "darwin" and shutil.which("osascript"):
        command = [
            "osascript",
            "-e",
            f'display notification {json.dumps(text)} with title "Timonel"',
        ]
    elif os.name == "nt" and shutil.which("powershell"):
        script = (
            "[void][System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms');"
            "$n=New-Object System.Windows.Forms.NotifyIcon;$n.Icon=[System.Drawing.SystemIcons]::Information;"
            "$n.Visible=$true;$n.ShowBalloonTip(5000,'Timonel',$env:TIMONEL_NOTICE,'Info');Start-Sleep 6;$n.Dispose()"
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", script],
            env={**os.environ, "TIMONEL_NOTICE": text},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    elif shutil.which("notify-send"):
        command = ["notify-send", "Timonel", text]
    else:
        return
    subprocess.run(
        command, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10
    )


def webhook(url: str, record: dict[str, Any]) -> None:
    body = json.dumps(
        {"id": record["id"], "status": record["status"], "diff_stat": record.get("diff_stat") or ""}
    ).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        urllib.request.urlopen(request, timeout=10).close()
    except OSError:
        return


def notify(settings: Notifications, record: dict[str, Any]) -> None:
    if record.get("status") not in settings.events:
        return
    text = message(record)
    if settings.sound:
        sys.stderr.write("\a")
        sys.stderr.flush()
    if settings.desktop:
        desktop(text)
    if settings.webhook_url:
        webhook(settings.webhook_url, record)
