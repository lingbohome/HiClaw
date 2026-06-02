"""plan.md / meta.json / result.md management for HiClaw taskflow.

Handles the full task lifecycle file format:

    plan.md   — checkbox-based step tracking
    meta.json — task state machine (status, timestamps, assignment)
    result.md — structured task deliverables

All functions are pure file-I/O + string manipulation — no external dependencies.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

# ── Marker constants (aligned with copaw_worker.task.MARKER_TO_STATUS) ──────

STATUS_MARKERS = {" ", "x", "X", "~", "!", "→"}  # → is U+2192
CHECKBOX_RE = re.compile(r"^([-*]\s*\[)([ xX~!→])(\]\s+)(.+)$")

# ── Result statuses (aligned with copaw_worker.task.RESULT_STATUSES) ─────────

RESULT_STATUSES = {
    "SUCCESS",
    "SUCCESS_WITH_NOTES",
    "REVISION_NEEDED",
    "BLOCKED",
    "INTERRUPTED",
}
EFFECTIVE_RESULT_STATUSES = {"SUCCESS", "SUCCESS_WITH_NOTES"}
RESULT_STATUS_RE = re.compile(
    r"^\*\*status\*\*:\s*(.+)$", re.MULTILINE | re.IGNORECASE
)
RESULT_SUMMARY_RE = re.compile(
    r"^\*\*summary\*\*:\s*(.+)$", re.MULTILINE | re.IGNORECASE
)
RESULT_DELIVERABLES_HEADER_RE = re.compile(
    r"^\*\*deliverables\*\*:\s*$", re.MULTILINE | re.IGNORECASE
)
RESULT_NOTES_HEADER_RE = re.compile(
    r"^\*\*notes\*\*:\s*$", re.MULTILINE | re.IGNORECASE
)

# ── Actor / identity ────────────────────────────────────────────────────────

_ACTOR_ENV_VARS = [
    "HICLAW_WORKER_NAME",
    "HICLAW_MATRIX_USER_ID",
    "COPAW_MATRIX_USER_ID",
]


def resolve_actor(explicit: str | None = None) -> str | None:
    """Resolve the current worker identity.

    1. Explicit ``--actor`` argument
    2. ``HICLAW_WORKER_NAME`` env var
    3. ``HICLAW_MATRIX_USER_ID`` or ``COPAW_MATRIX_USER_ID`` env var
       (normalised via ``canonical_worker_id``)

    Returns None if no identity can be resolved.
    """
    if explicit:
        return canonical_worker_id(explicit)

    for var in _ACTOR_ENV_VARS:
        val = os.environ.get(var)
        if val:
            return canonical_worker_id(val)

    return None


def canonical_worker_id(value: str) -> str:
    """Normalize a worker identity string.

    Strips ``@`` prefix, ``:domain`` suffix on Matrix IDs, display-name
    prefixes, and surrounding whitespace/backticks/quotes.
    """
    text = (value or "").strip()
    if not text:
        return ""

    # Take first token (before any space)
    token = text.split()[0].strip()
    token = token.strip("`'\"")
    token = token.removeprefix("@")
    # Strip Matrix domain (e.g. "@worker:matrix.org" → "worker")
    if ":" in token:
        token = token.split(":", 1)[0]
    return token.strip(":,;")


def verify_worker_identity(
    task_dir: Path, actor: str | None = None
) -> str:
    """Verify that *actor* matches the task's ``assigned_to`` field.

    Returns the resolved actor name on success.
    Raises ``ValueError`` if identity cannot be resolved or doesn't match.
    """
    resolved = resolve_actor(actor)
    if not resolved:
        raise ValueError(
            "Cannot resolve worker identity. "
            "Set --actor, HICLAW_WORKER_NAME, or HICLAW_MATRIX_USER_ID."
        )

    meta = read_meta(task_dir)
    if meta is None:
        raise ValueError(
            f"meta.json not found — cannot verify worker identity for ack"
        )

    assigned = meta.get("assigned_to", "")
    assigned_canonical = canonical_worker_id(assigned)
    if resolved != assigned_canonical:
        raise ValueError(
            f"Worker identity mismatch: you are '{resolved}' but "
            f"task is assigned to '{assigned_canonical}'"
        )

    return resolved


# ── Workspace discovery ────────────────────────────────────────────────────

_WORKSPACE_ENV_VARS = [
    "HICLAW_HOME",
    "HICLAW_ROOT",
    "COPAW_WORKING_DIR",
]


def find_workspace() -> Path:
    """Discover the HiClaw workspace (filesystem root with shared/)."""
    for var in _WORKSPACE_ENV_VARS:
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


def safe_task_id(task_id: str) -> str:
    """Validate and return a safe task ID (alphanumeric + _- only)."""
    text = str(task_id or "").strip()
    if not text:
        raise ValueError("task_id is required")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", text):
        raise ValueError(f"invalid task_id: {task_id}")
    return text


def get_task_dir(task_id: str, workspace: Path | None = None) -> Path:
    """Return the task directory path."""
    ws = workspace or find_workspace()
    return ws / "shared" / "tasks" / safe_task_id(task_id)


# ── plan.md I/O ────────────────────────────────────────────────────────────


def plan_exists(task_dir: Path) -> bool:
    return (task_dir / "plan.md").exists()


def read_plan(task_dir: Path) -> str | None:
    path = task_dir / "plan.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def write_plan(task_dir: Path, plan: str) -> None:
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "plan.md").write_text(plan, encoding="utf-8")


# ── plan.md parsing ────────────────────────────────────────────────────────


def parse_plan_steps(plan_text: str) -> list[dict]:
    """Parse checkbox lines into structured steps.

    Returns list of {index, marker, description, line}.
    """
    steps = []
    idx = 0
    for line in plan_text.splitlines():
        m = CHECKBOX_RE.match(line)
        if m:
            steps.append({
                "index": idx,
                "marker": m.group(2),
                "description": m.group(4).strip(),
                "line": line,
            })
            idx += 1
    return steps


# ── plan.md manipulation ───────────────────────────────────────────────────


def auto_complete_markers(plan_text: str) -> str:
    """Replace all non-completed checkboxes with [x]."""
    result_lines = []
    for line in plan_text.splitlines():
        m = CHECKBOX_RE.match(line)
        if m:
            marker = m.group(2)
            if marker not in ("x", "X"):
                line = f"{m.group(1)}x{m.group(3)}{m.group(4)}"
        result_lines.append(line)
    return "\n".join(result_lines) + (
        "\n" if plan_text.endswith("\n") else ""
    )


def mark_step(plan_text: str, step_index: int, marker: str) -> str:
    """Update a specific step's checkbox marker."""
    if marker not in STATUS_MARKERS:
        raise ValueError(
            f"invalid marker {marker!r}, must be one of: "
            f"{', '.join(repr(m) for m in sorted(STATUS_MARKERS))}"
        )

    result_lines = []
    current_idx = 0
    found = False

    for line in plan_text.splitlines():
        m = CHECKBOX_RE.match(line)
        if m:
            if current_idx == step_index:
                line = f"{m.group(1)}{marker}{m.group(3)}{m.group(4)}"
                found = True
            current_idx += 1
        result_lines.append(line)

    if not found:
        raise ValueError(
            f"step_index {step_index} out of range "
            f"(found {current_idx} checkbox lines)"
        )

    return "\n".join(result_lines) + (
        "\n" if plan_text.endswith("\n") else ""
    )


# ── meta.json helpers ──────────────────────────────────────────────────────


def read_meta(task_dir: Path) -> dict | None:
    meta_path = task_dir / "meta.json"
    if not meta_path.exists():
        return None
    return json.loads(meta_path.read_text(encoding="utf-8-sig"))


def write_meta_status(task_dir: Path, status: str) -> dict:
    """Update status field in meta.json. Returns the updated dict."""
    meta_path = task_dir / "meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"meta.json not found at {meta_path}")

    raw = meta_path.read_text(encoding="utf-8-sig")
    meta = json.loads(raw)
    meta["status"] = status
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if status == "submitted":
        meta["submitted_at"] = now_str
    elif status == "in_progress":
        meta["acknowledged_at"] = meta.get("acknowledged_at") or now_str
    meta_path.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return meta


def ack_task(
    task_dir: Path, actor: str | None = None, verify: bool = True
) -> dict:
    """Acknowledge a task — mark it as in_progress.

    If *verify* is True (default), validates that the calling worker
    matches the task's ``assigned_to`` field.
    """
    if verify:
        verify_worker_identity(task_dir, actor)

    meta_path = task_dir / "meta.json"
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
    else:
        task_dir.mkdir(parents=True, exist_ok=True)
        meta = {"status": "assigned"}

    meta["status"] = "in_progress"
    meta["acknowledged_at"] = meta.get("acknowledged_at") or now_str
    meta_path.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return meta


# ── result.md — parsing / validation / rendering / writing ──────────────────


def parse_task_result(result_text: str) -> dict:
    """Parse result.md into structured fields.

    Returns dict with: status, summary, deliverables (list), notes (list).
    """
    result: dict = {
        "status": None,
        "summary": None,
        "deliverables": [],
        "notes": [],
    }

    m = RESULT_STATUS_RE.search(result_text)
    if m:
        result["status"] = m.group(1).strip()

    m = RESULT_SUMMARY_RE.search(result_text)
    if m:
        result["summary"] = m.group(1).strip()

    # deliverables list items
    lines = result_text.splitlines()
    in_deliverables = False
    in_notes = False
    for line in lines:
        if RESULT_DELIVERABLES_HEADER_RE.match(line):
            in_deliverables = True
            in_notes = False
            continue
        if RESULT_NOTES_HEADER_RE.match(line):
            in_deliverables = False
            in_notes = True
            continue
        if in_deliverables:
            item = line.strip().lstrip("-* ").strip()
            if item:
                result["deliverables"].append(item)
        elif in_notes:
            item = line.strip().lstrip("-* ").strip()
            if item:
                result["notes"].append(item)

    return result


def validate_task_result(task_id: str, result: dict) -> list[str]:
    """Validate a parsed task result. Returns list of error messages (empty = valid).

    Checks:
    - status is a known value
    - summary is non-empty
    - deliverables paths are under shared/tasks/{task_id}/
    - no path traversal (..)
    """
    errors: list[str] = []

    status = result.get("status")
    if not status:
        errors.append("status is required")
    elif status not in RESULT_STATUSES:
        errors.append(
            f"invalid status '{status}', must be one of: "
            f"{', '.join(sorted(RESULT_STATUSES))}"
        )

    summary = result.get("summary")
    if not summary:
        errors.append("summary is required")

    for path in result.get("deliverables", []):
        if ".." in path:
            errors.append(f"path traversal detected in deliverable: {path}")
        elif not path.startswith(f"shared/tasks/{task_id}/"):
            errors.append(
                f"deliverable path must be under shared/tasks/{task_id}/: {path}"
            )

    return errors


def is_effective_result(result: dict) -> bool:
    """Return whether a task result qualifies as 'done'."""
    return result.get("status") in EFFECTIVE_RESULT_STATUSES


def render_task_result(result: dict) -> str:
    """Render a structured result dict to result.md markdown."""
    lines = [f"**status**: {result.get('status', '')}"]

    summary = result.get("summary")
    if summary:
        lines.append(f"**summary**: {summary}")
    else:
        lines.append("**summary**: ")

    deliverables = result.get("deliverables", [])
    lines.append("**deliverables**:")
    if deliverables:
        for d in deliverables:
            lines.append(f"- {d}")
    else:
        lines.append("(none)")

    notes = result.get("notes", [])
    lines.append("**notes**:")
    if notes:
        for n in notes:
            lines.append(f"- {n}")
    else:
        lines.append("(none)")

    return "\n".join(lines) + "\n"


def write_task_result(
    task_dir: Path, result: dict, task_id: str
) -> dict:
    """Validate and write result.md. Returns the parsed result dict.

    Raises ValueError if validation fails.
    """
    errors = validate_task_result(task_id, result)
    if errors:
        raise ValueError("result validation failed:\n- " + "\n- ".join(errors))

    content = render_task_result(result)
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "result.md").write_text(content, encoding="utf-8")
    return result


def read_task_result(task_dir: Path) -> dict | None:
    """Read and parse result.md; returns None if not found."""
    path = task_dir / "result.md"
    if not path.exists():
        return None
    return parse_task_result(path.read_text(encoding="utf-8-sig"))


# ── Task summary (full check) ──────────────────────────────────────────────


def get_task_summary(task_dir: Path) -> dict:
    """Return a complete task summary for the ``check`` command."""
    summary: dict = {
        "task_id": task_dir.name,
        "meta": None,
        "meta_exists": False,
        "plan_exists": False,
        "steps": [],
        "result": None,
    }

    meta = read_meta(task_dir)
    if meta is not None:
        summary["meta"] = meta
        summary["meta_exists"] = True

    plan = read_plan(task_dir)
    if plan is not None:
        summary["plan_exists"] = True
        summary["steps"] = parse_plan_steps(plan)

    result = read_task_result(task_dir)
    if result is not None:
        summary["result"] = result
        summary["result_effective"] = is_effective_result(result)

    return summary
