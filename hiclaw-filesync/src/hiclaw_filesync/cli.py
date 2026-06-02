"""CLI entry point for hiclaw-filesync.

Deterministic file-sync tool for HiClaw workers::

    hiclaw-filesync pull <path>            # Pull a shared path from MinIO
    hiclaw-filesync push <path> [--exclude ...]  # Push a shared path to MinIO
    hiclaw-filesync stat <path>            # Check path existence in MinIO
    hiclaw-filesync list <path>            # List entries under a path

Paths are relative to the shared directory::

    tasks/task-001/          → shared/tasks/task-001/
    projects/proj-001/       → shared/projects/proj-001/
    shared/tasks/task-001/   → explicit shared/ prefix also accepted
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


# ── helpers ─────────────────────────────────────────────────────────────────


def _find_workspace() -> Path:
    for var in ("HICLAW_HOME", "HICLAW_ROOT", "COPAW_WORKING_DIR"):
        val = os.environ.get(var)
        if val:
            ws = Path(val)
            if var == "COPAW_WORKING_DIR":
                ws = ws / "workspaces" / "default"
            return ws
    cwd = Path.cwd()
    if (cwd / "shared").exists():
        return cwd
    if (cwd.parent / "shared").exists():
        return cwd.parent
    return cwd


def _shared_dir(workspace: Path | None = None) -> Path:
    ws = workspace or _find_workspace()
    return ws / "shared"


def _storage_prefix() -> str | None:
    return os.environ.get("HICLAW_STORAGE_PREFIX")


def _normalize_path(path: str, for_directory: bool = False) -> tuple[str, Path]:
    """Normalize a user-facing path to (subpath, local_full_path).

    Handles:
        tasks/task-001/         → subpath = "tasks/task-001/"
        shared/tasks/task-001/  → subpath = "tasks/task-001/"
        tasks/task-001/result.md → subpath = "tasks/task-001/result.md"

    Auto-appends trailing ``/`` for common directory patterns when
    *for_directory* is True.
    """
    stripped = path.strip().lstrip("/")
    if stripped.startswith("shared/"):
        stripped = stripped[len("shared/"):]

    if for_directory and not stripped.endswith("/"):
        parts = stripped.split("/")
        # Known directory patterns: tasks/<id>, projects/<id>
        if (
            len(parts) >= 2
            and parts[0] in {"tasks", "projects"}
            and parts[1]
        ):
            stripped = stripped + "/"

    local = _shared_dir() / stripped
    return stripped, local


def _mc(*args: str, timeout: int = 60) -> tuple[int, str, str]:
    """Run mc CLI. Returns (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            ["mc"] + list(args),
            capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError:
        return -1, "", "mc CLI not found on PATH"
    except subprocess.TimeoutExpired:
        return -1, "", "mc command timed out"


def _check_mc() -> bool:
    code, _, _ = _mc("--version", timeout=10)
    return code == 0


def _check_prefix() -> str:
    prefix = _storage_prefix()
    if not prefix:
        raise RuntimeError("HICLAW_STORAGE_PREFIX not set")
    return prefix


def _result(ok: bool, **kwargs) -> dict:
    return {"ok": ok, **kwargs}


# ── actions ─────────────────────────────────────────────────────────────────


def do_pull(path: str) -> dict:
    """Pull a shared path from MinIO to local workspace."""
    if not _check_mc():
        return _result(False, error="mc CLI not found on PATH")

    prefix = _check_prefix()
    subpath, local = _normalize_path(path, for_directory=True)
    remote = f"{prefix}/shared/{subpath}"

    # For directories, use mirror; for files, use cp
    if subpath.endswith("/"):
        local.parent.mkdir(parents=True, exist_ok=True)
        local_str = str(local).rstrip("/\\") + "/"
        code, stdout, stderr = _mc("mirror", remote, local_str, "--overwrite")
    else:
        local.parent.mkdir(parents=True, exist_ok=True)
        code, stdout, stderr = _mc("cp", remote, str(local))

    if code == 0:
        return _result(True, pulled=True, local=str(local), remote=remote)
    return _result(False, pulled=False, error=stderr or f"mc exit {code}")


def do_push(path: str, exclude: list[str] | None = None) -> dict:
    """Push a local shared path to MinIO."""
    if not _check_mc():
        return _result(False, error="mc CLI not found on PATH")

    prefix = _check_prefix()
    subpath, local = _normalize_path(path, for_directory=True)
    remote = f"{prefix}/shared/{subpath}"

    if not local.exists():
        return _result(False, pushed=False, error=f"local path not found: {local}")

    cmd = ["mc", "mirror"]
    local_str = str(local).rstrip("/\\") + "/"
    cmd.extend([local_str, remote, "--overwrite"])
    for ex in (exclude or []):
        cmd.extend(["--exclude", ex])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return _result(False, pushed=False, error="mc mirror timed out")

    if result.returncode == 0:
        return _result(True, pushed=True, local=str(local), remote=remote)
    return _result(False, pushed=False, error=result.stderr.strip() or f"mc exit {result.returncode}")


def do_stat(path: str) -> dict:
    """Check whether a shared path exists in MinIO."""
    if not _check_mc():
        return _result(False, error="mc CLI not found on PATH")

    prefix = _check_prefix()
    subpath, local = _normalize_path(path)
    remote = f"{prefix}/shared/{subpath}"

    code, stdout, stderr = _mc("stat", remote)
    if code == 0:
        return _result(True, exists=True, remote=remote, info=stdout)
    return _result(True, exists=False, remote=remote)


def do_list(path: str) -> dict:
    """List entries under a shared path in MinIO."""
    if not _check_mc():
        return _result(False, error="mc CLI not found on PATH")

    prefix = _check_prefix()
    subpath, local = _normalize_path(path, for_directory=True)
    remote = f"{prefix}/shared/{subpath}"

    code, stdout, stderr = _mc("ls", "--recursive", remote)
    if code == 0:
        entries = [line.strip() for line in stdout.splitlines() if line.strip()]
        return _result(True, entries=entries, remote=remote, count=len(entries))
    return _result(False, entries=[], error=stderr or f"mc exit {code}")


# ── CLI ─────────────────────────────────────────────────────────────────────


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", help="Shared path (e.g. tasks/task-001/)")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="hiclaw-filesync",
        description="Deterministic file-sync tool for HiClaw workers",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    # pull
    p_pull = sub.add_parser("pull", help="Pull a shared path from MinIO")
    _add_common(p_pull)
    p_pull.set_defaults(func=lambda a: _print_result(do_pull(a.path)))

    # push
    p_push = sub.add_parser("push", help="Push a local shared path to MinIO")
    _add_common(p_push)
    p_push.add_argument(
        "--exclude", nargs="*", default=[],
        help="Patterns to exclude (e.g. spec.md base/)"
    )
    p_push.set_defaults(func=lambda a: _print_result(do_push(a.path, a.exclude)))

    # stat
    p_stat = sub.add_parser("stat", help="Check path existence in MinIO")
    _add_common(p_stat)
    p_stat.set_defaults(func=lambda a: _print_result(do_stat(a.path)))

    # list
    p_list = sub.add_parser("list", help="List entries under a path")
    _add_common(p_list)
    p_list.set_defaults(func=lambda a: _print_result(do_list(a.path)))

    args = parser.parse_args(argv)
    result = args.func(args)
    if isinstance(result, int):
        sys.exit(result)
    elif isinstance(result, dict) and not result.get("ok"):
        sys.exit(1)


def _print_result(result: dict) -> int:
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1
