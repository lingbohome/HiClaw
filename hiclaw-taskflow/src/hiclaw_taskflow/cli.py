"""CLI entry point for hiclaw-taskflow.

Full task lifecycle::

    hiclaw-taskflow ack <task_id> [--actor X] [--no-verify] [--sync] [--dry-run]
    hiclaw-taskflow submit <task_id> [--status ... --summary ...] [--sync] [--dry-run]
    hiclaw-taskflow mark-step <task_id> <idx> <marker> [--sync] [--dry-run]
    hiclaw-taskflow check <task_id> [--sync]
    hiclaw-taskflow result parse <task_id>
    hiclaw-taskflow result validate <task_id> [--json]
    hiclaw-taskflow result show <task_id>        # render from structured input
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from hiclaw_taskflow.plan import (
    RESULT_STATUSES,
    ack_task,
    auto_complete_markers,
    find_workspace,
    get_task_dir,
    get_task_summary,
    is_effective_result,
    mark_step,
    parse_task_result,
    plan_exists,
    read_plan,
    render_task_result,
    resolve_actor,
    safe_task_id,
    validate_task_result,
    write_meta_status,
    write_plan,
    write_task_result,
)
from hiclaw_taskflow.sync import pull_task, push_task


# ── helpers ─────────────────────────────────────────────────────────────────


def _resolve_task_dir(
    task_id: str, workspace: Path | None, sync: bool
) -> Path:
    td = get_task_dir(task_id, workspace=workspace)
    if sync:
        result = pull_task(task_id, workspace=workspace)
        if not result["ok"]:
            print(
                f"hiclaw-taskflow: sync pull warning — {result.get('error')}",
                file=sys.stderr,
            )
    return td


def _maybe_sync(task_id: str, workspace: Path | None, sync: bool) -> None:
    if not sync:
        return
    result = push_task(task_id, workspace=workspace)
    if result["ok"]:
        print("hiclaw-taskflow: synced to MinIO")
    else:
        print(
            f"hiclaw-taskflow: sync push warning — {result.get('error')}",
            file=sys.stderr,
        )


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--sync", action="store_true", default=False)
    parser.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Preview what would happen without making changes",
    )
    parser.add_argument(
        "--actor",
        default=None,
        help="Worker identity (default: auto-detect from env)",
    )


def _dry_run_msg() -> None:
    print("hiclaw-taskflow: [DRY-RUN] no changes made")


# ── ack ─────────────────────────────────────────────────────────────────────


def _cmd_ack(args: argparse.Namespace) -> int:
    if args.dry_run:
        actor = resolve_actor(args.actor)
        print(f"[DRY-RUN] Would ack task {args.task_id}")
        print(f"  Actor: {actor or '(unresolved)'}")
        print(f"  Verify identity: {not args.no_verify}")
        print(f"  Sync: {args.sync}")
        return 0

    task_dir = _resolve_task_dir(args.task_id, args.workspace, args.sync)
    try:
        meta = ack_task(task_dir, actor=args.actor, verify=not args.no_verify)
    except ValueError as exc:
        print(f"hiclaw-taskflow: ERROR — {exc}", file=sys.stderr)
        return 1

    _maybe_sync(args.task_id, args.workspace, args.sync)
    print(
        f"hiclaw-taskflow: task {args.task_id} acknowledged "
        f"(status: {meta['status']}, "
        f"acknowledged_at: {meta.get('acknowledged_at', 'N/A')})"
    )
    return 0


# ── submit ──────────────────────────────────────────────────────────────────


def _cmd_submit(args: argparse.Namespace) -> int:
    if args.dry_run:
        print(f"[DRY-RUN] Would submit task {args.task_id}")
        print(f"  Auto-complete plan.md: {plan_exists(get_task_dir(args.task_id, args.workspace))}")
        if args.status:
            print(f"  Result status: {args.status}")
            print(f"  Result summary: {args.summary or '(none)'}")
            print(f"  Deliverables: {args.deliverables or '[]'}")
            print(f"  Notes: {args.notes or '[]'}")
        print(f"  Sync: {args.sync}")
        return 0

    task_dir = _resolve_task_dir(args.task_id, args.workspace, args.sync)

    # auto-complete plan.md
    if plan_exists(task_dir):
        plan = read_plan(task_dir)
        updated = auto_complete_markers(plan)
        write_plan(task_dir, updated)
        print(
            f"hiclaw-taskflow: auto-completed plan.md checkboxes "
            f"for task {args.task_id}"
        )
    else:
        print(
            f"hiclaw-taskflow: no plan.md found for task {args.task_id}, "
            f"skipping plan auto-complete"
        )

    # write result.md if structured result provided
    if args.status:
        try:
            deliv_raw = args.deliverables or "[]"
            deliverables = json.loads(deliv_raw) if isinstance(deliv_raw, str) else deliv_raw
            if not isinstance(deliverables, list):
                deliverables = [deliverables]
        except json.JSONDecodeError:
            deliverables = [d.strip() for d in deliv_raw.split(",") if d.strip()]

        try:
            notes_raw = args.notes or "[]"
            notes = json.loads(notes_raw) if isinstance(notes_raw, str) else notes_raw
            if not isinstance(notes, list):
                notes = [notes]
        except json.JSONDecodeError:
            notes = [n.strip() for n in notes_raw.split(",") if n.strip()]

        result = {
            "status": args.status,
            "summary": args.summary or "",
            "deliverables": deliverables,
            "notes": notes,
        }
        try:
            write_task_result(task_dir, result, safe_task_id(args.task_id))
            print(
                f"hiclaw-taskflow: result.md written "
                f"(status: {args.status}, "
                f"effective: {is_effective_result(result)})"
            )
        except ValueError as exc:
            print(f"hiclaw-taskflow: result validation ERROR — {exc}", file=sys.stderr)
            return 1

    # update meta.json
    try:
        meta = write_meta_status(task_dir, "submitted")
        print(
            f"hiclaw-taskflow: meta.json status → submitted "
            f"(submitted_at: {meta.get('submitted_at', 'N/A')})"
        )
    except FileNotFoundError:
        print(
            f"hiclaw-taskflow: WARNING — meta.json not found for task "
            f"{args.task_id}, skipping status update"
        )

    _maybe_sync(args.task_id, args.workspace, args.sync)
    return 0


# ── mark-step ───────────────────────────────────────────────────────────────


def _cmd_mark_step(args: argparse.Namespace) -> int:
    if args.dry_run:
        print(
            f"[DRY-RUN] Would mark task {args.task_id} "
            f"step {args.step_index} → [{args.marker}]"
        )
        print(f"  Sync: {args.sync}")
        return 0

    task_dir = _resolve_task_dir(args.task_id, args.workspace, args.sync)
    if not plan_exists(task_dir):
        print(
            f"hiclaw-taskflow: ERROR — no plan.md found for task {args.task_id}",
            file=sys.stderr,
        )
        return 1

    plan = read_plan(task_dir)
    try:
        updated = mark_step(plan, args.step_index, args.marker)
    except ValueError as exc:
        print(f"hiclaw-taskflow: ERROR — {exc}", file=sys.stderr)
        return 1

    write_plan(task_dir, updated)
    marker_label = " " if args.marker == " " else args.marker
    print(
        f"hiclaw-taskflow: step {args.step_index} → [{marker_label}] "
        f"in task {args.task_id}"
    )
    _maybe_sync(args.task_id, args.workspace, args.sync)
    return 0


# ── check ───────────────────────────────────────────────────────────────────


def _cmd_check(args: argparse.Namespace) -> int:
    task_dir = _resolve_task_dir(args.task_id, args.workspace, args.sync)

    if args.json:
        summary = get_task_summary(task_dir)
        # Remove large fields for clean JSON
        for s in summary.get("steps", []):
            s.pop("line", None)
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        return 0

    summary = get_task_summary(task_dir)

    # meta.json
    meta = summary["meta"]
    if meta:
        print(f"Task: {summary['task_id']}")
        print(f"  Status:     {meta.get('status', 'unknown')}")
        print(f"  Project:    {meta.get('project_id', 'N/A')}")
        print(f"  Title:      {meta.get('task_title', 'N/A')}")
        print(f"  Assigned:   {meta.get('assigned_to', 'N/A')}")
        assigned_at = meta.get("assigned_at")
        if assigned_at:
            print(f"  Created:    {assigned_at}")
        ack = meta.get("acknowledged_at")
        if ack:
            print(f"  Ack'd:      {ack}")
        sub = meta.get("submitted_at")
        if sub:
            print(f"  Submitted:  {sub}")
    else:
        print(f"Task: {summary['task_id']}")
        print("  (no meta.json)")

    # plan.md
    print()
    if summary["plan_exists"]:
        steps = summary["steps"]
        if steps:
            STATUS_LABELS = {
                " ": "pending", "x": "completed", "X": "completed",
                "~": "delegated", "!": "blocked", "→": "revision",
            }
            print(f"Plan steps ({len(steps)}):")
            for s in steps:
                marker_display = " " if s["marker"] == " " else s["marker"]
                label = STATUS_LABELS.get(s["marker"], "unknown")
                print(f"  [{marker_display}] ({label}) {s['description']}")
        else:
            print("Plan: (no checkbox steps)")
    else:
        print("Plan: (no plan.md)")

    # result.md
    print()
    result = summary["result"]
    if result:
        effective = summary.get("result_effective", False)
        effective_str = "[OK] effective" if effective else "[!!] not effective"
        print(f"Result ({effective_str}):")
        print(f"  status:       {result.get('status', 'N/A')}")
        print(f"  summary:      {result.get('summary', 'N/A')}")
        deliverables = result.get("deliverables", [])
        if deliverables:
            print(f"  deliverables: ({len(deliverables)} files)")
            for d in deliverables:
                print(f"    - {d}")
        else:
            print("  deliverables: (none)")
        notes = result.get("notes", [])
        if notes:
            print(f"  notes: ({len(notes)} items)")
    else:
        print("Result: (no result.md)")

    return 0


# ── result subcommands ──────────────────────────────────────────────────────


def _cmd_result_parse(args: argparse.Namespace) -> int:
    task_dir = _resolve_task_dir(args.task_id, args.workspace, args.sync)
    result_path = task_dir / "result.md"
    if not result_path.exists():
        print(f"hiclaw-taskflow: no result.md found for task {args.task_id}", file=sys.stderr)
        return 1
    result = parse_task_result(result_path.read_text(encoding="utf-8-sig"))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _cmd_result_validate(args: argparse.Namespace) -> int:
    task_dir = _resolve_task_dir(args.task_id, args.workspace, args.sync)
    result_path = task_dir / "result.md"
    if not result_path.exists():
        print(f"hiclaw-taskflow: no result.md found for task {args.task_id}", file=sys.stderr)
        return 1
    result = parse_task_result(result_path.read_text(encoding="utf-8-sig"))
    errors = validate_task_result(safe_task_id(args.task_id), result)
    if errors:
        if args.json:
            print(json.dumps({"valid": False, "errors": errors}, indent=2))
        else:
            print("Validation FAILED:")
            for e in errors:
                print(f"  - {e}")
        return 1
    else:
        effective = is_effective_result(result)
        if args.json:
            print(json.dumps({"valid": True, "effective": effective}, indent=2))
        else:
            print(f"Validation PASSED (effective: {effective})")
        return 0


def _cmd_result_show(args: argparse.Namespace) -> int:
    """Render a result from CLI arguments (for testing / manual creation)."""
    result = {
        "status": args.status,
        "summary": args.summary or "",
        "deliverables": args.deliverables or [],
        "notes": args.notes or [],
    }
    print(render_task_result(result))
    return 0


# ── main ────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="hiclaw-taskflow",
        description="HiClaw shared taskflow CLI — full task lifecycle management",
    )
    parser.add_argument(
        "--workspace", type=Path, default=None,
        help="Override workspace directory (default: auto-detect)",
    )

    sub = parser.add_subparsers(dest="action", required=True)

    # ---- ack ----
    p_ack = sub.add_parser("ack", help="Acknowledge task (mark in_progress)")
    p_ack.add_argument("task_id", help="Task ID")
    _add_common_args(p_ack)
    p_ack.add_argument(
        "--no-verify", action="store_true", default=False,
        help="Skip worker identity verification",
    )
    p_ack.set_defaults(func=_cmd_ack)

    # ---- submit ----
    p_submit = sub.add_parser(
        "submit", help="Auto-complete plan.md + write result + mark submitted"
    )
    p_submit.add_argument("task_id", help="Task ID")
    _add_common_args(p_submit)
    p_submit.add_argument(
        "--status",
        choices=sorted(RESULT_STATUSES),
        help="Result status (writes result.md)",
    )
    p_submit.add_argument("--summary", help="Result summary (required if --status)")
    p_submit.add_argument(
        "--deliverables", help="JSON array or comma-separated deliverable paths"
    )
    p_submit.add_argument(
        "--notes", help="JSON array or comma-separated notes"
    )
    p_submit.set_defaults(func=_cmd_submit)

    # ---- mark-step ----
    p_mark = sub.add_parser("mark-step", help="Update one plan.md step checkbox")
    p_mark.add_argument("task_id", help="Task ID")
    p_mark.add_argument("step_index", type=int, help="0-based step index")
    p_mark.add_argument(
        "marker",
        help="Checkbox marker: ' '(pending), x, ~ (delegated), ! (blocked)",
    )
    _add_common_args(p_mark)
    p_mark.set_defaults(func=_cmd_mark_step)

    # ---- check ----
    p_check = sub.add_parser(
        "check", help="Full task status: meta + plan + result"
    )
    p_check.add_argument("task_id", help="Task ID")
    p_check.add_argument("--sync", action="store_true", default=False)
    p_check.add_argument(
        "--json", action="store_true", default=False,
        help="Output as JSON",
    )
    p_check.set_defaults(func=_cmd_check)

    # ---- result subcommands ----
    p_result = sub.add_parser("result", help="Result.md operations")
    r_sub = p_result.add_subparsers(dest="result_action", required=True)

    p_rp = r_sub.add_parser("parse", help="Parse result.md to JSON")
    p_rp.add_argument("task_id", help="Task ID")
    p_rp.add_argument("--sync", action="store_true", default=False)
    p_rp.set_defaults(func=_cmd_result_parse)

    p_rv = r_sub.add_parser("validate", help="Validate result.md")
    p_rv.add_argument("task_id", help="Task ID")
    p_rv.add_argument("--sync", action="store_true", default=False)
    p_rv.add_argument("--json", action="store_true", default=False)
    p_rv.set_defaults(func=_cmd_result_validate)

    p_rs = r_sub.add_parser("show", help="Render result.md from arguments")
    p_rs.add_argument(
        "--status", required=True, choices=sorted(RESULT_STATUSES),
        help="Result status",
    )
    p_rs.add_argument("--summary", help="Result summary")
    p_rs.add_argument(
        "--deliverables", nargs="*", default=[],
        help="Deliverable paths",
    )
    p_rs.add_argument("--notes", nargs="*", default=[], help="Notes")
    p_rs.set_defaults(func=_cmd_result_show)

    args = parser.parse_args(argv)
    sys.exit(args.func(args))
