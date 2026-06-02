"""MinIO sync wrapper for hiclaw-taskflow.

Uses the ``mc`` CLI (pre-configured in all HiClaw workers) to push
task directory changes to MinIO.  Sync is optional — the CLI operates
on local files by default; pass ``--sync`` to push after each operation.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _mc_available() -> bool:
    """Check whether the ``mc`` CLI is on PATH."""
    try:
        subprocess.run(
            ["mc", "--version"],
            capture_output=True,
            timeout=10,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _storage_prefix() -> str | None:
    """Return ``HICLAW_STORAGE_PREFIX`` (e.g. ``hiclaw/hiclaw``)."""
    return os.environ.get("HICLAW_STORAGE_PREFIX")


def push_task(task_id: str, workspace: Path | None = None) -> dict:
    """Push a task directory to MinIO via ``mc mirror``.

    Mirrors ``<workspace>/shared/tasks/<task_id>/`` to
    ``<HICLAW_STORAGE_PREFIX>/shared/tasks/<task_id>/``,
    excluding ``spec.md`` and ``base/`` (read-only coordinator files).

    Returns:
        ``{ok: bool, synced: bool, error?: str}``
    """
    if not _mc_available():
        return {"ok": False, "synced": False, "error": "mc CLI not found on PATH"}

    prefix = _storage_prefix()
    if not prefix:
        return {
            "ok": False,
            "synced": False,
            "error": "HICLAW_STORAGE_PREFIX not set",
        }

    from hiclaw_taskflow.plan import find_workspace, get_task_dir, safe_task_id

    ws = workspace or find_workspace()
    tid = safe_task_id(task_id)
    local_dir = get_task_dir(tid, workspace=ws)
    remote_path = f"{prefix}/shared/tasks/{tid}/"

    # Ensure trailing slash for directory sync
    local_arg = str(local_dir).rstrip("/\\") + "/"

    cmd = [
        "mc",
        "mirror",
        local_arg,
        remote_path,
        "--overwrite",
        "--exclude",
        "spec.md",
        "--exclude",
        "base/",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            return {"ok": True, "synced": True}
        return {
            "ok": False,
            "synced": False,
            "error": result.stderr.strip() or f"mc exit code {result.returncode}",
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "synced": False, "error": "mc mirror timed out"}
    except Exception as exc:
        return {"ok": False, "synced": False, "error": str(exc)}


def pull_task(task_id: str, workspace: Path | None = None) -> dict:
    """Pull a task directory FROM MinIO (reverse of push_task).

    Useful for ``check`` / ``ack`` to get the latest state before operating.
    """
    if not _mc_available():
        return {"ok": False, "synced": False, "error": "mc CLI not found on PATH"}

    prefix = _storage_prefix()
    if not prefix:
        return {"ok": False, "synced": False, "error": "HICLAW_STORAGE_PREFIX not set"}

    from hiclaw_taskflow.plan import find_workspace, get_task_dir, safe_task_id

    ws = workspace or find_workspace()
    tid = safe_task_id(task_id)
    local_dir = get_task_dir(tid, workspace=ws)
    local_dir.mkdir(parents=True, exist_ok=True)
    remote_path = f"{prefix}/shared/tasks/{tid}/"

    local_arg = str(local_dir).rstrip("/\\") + "/"

    cmd = ["mc", "mirror", remote_path, local_arg, "--overwrite"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            return {"ok": True, "synced": True}
        return {
            "ok": False,
            "synced": False,
            "error": result.stderr.strip() or f"mc exit code {result.returncode}",
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "synced": False, "error": "mc mirror timed out"}
    except Exception as exc:
        return {"ok": False, "synced": False, "error": str(exc)}
