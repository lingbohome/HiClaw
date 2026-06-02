"""Shared taskflow utilities for HiClaw workers.

Full task lifecycle + plan.md management + result handling for all worker runtimes.
"""

from hiclaw_taskflow.plan import (
    ack_task,
    auto_complete_markers,
    canonical_worker_id,
    find_workspace,
    get_task_dir,
    get_task_summary,
    is_effective_result,
    mark_step,
    parse_plan_steps,
    parse_task_result,
    plan_exists,
    read_meta,
    read_plan,
    read_task_result,
    render_task_result,
    resolve_actor,
    safe_task_id,
    validate_task_result,
    verify_worker_identity,
    write_meta_status,
    write_plan,
    write_task_result,
    RESULT_STATUSES,
    EFFECTIVE_RESULT_STATUSES,
)

from hiclaw_taskflow.sync import (
    pull_task,
    push_task,
)

__all__ = [
    # plan.md
    "auto_complete_markers",
    "mark_step",
    "parse_plan_steps",
    "plan_exists",
    "read_plan",
    "write_plan",
    # task lifecycle
    "ack_task",
    "get_task_summary",
    "read_meta",
    "write_meta_status",
    # result.md
    "parse_task_result",
    "validate_task_result",
    "render_task_result",
    "write_task_result",
    "read_task_result",
    "is_effective_result",
    "RESULT_STATUSES",
    "EFFECTIVE_RESULT_STATUSES",
    # identity
    "resolve_actor",
    "canonical_worker_id",
    "verify_worker_identity",
    # workspace
    "find_workspace",
    "get_task_dir",
    "safe_task_id",
    # sync
    "pull_task",
    "push_task",
]
