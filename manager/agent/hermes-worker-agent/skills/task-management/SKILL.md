---
name: task-management
description: Use for all task lifecycle operations — acknowledging, tracking progress, submitting results, handling blockers, or checking task status. Always use when working with assigned tasks, task IDs, spec.md, plan.md, result.md, or deliverables.
---

# Task Management

You are a Worker. Execute only your assigned task.

## Task Directory

All work for a task stays under:

```
~/shared/tasks/{task-id}/
├── spec.md       # Written by coordinator (read-only)
├── base/         # Reference files from coordinator (read-only)
├── plan.md       # Your execution plan (create before starting)
├── result.md     # Written by `hiclaw-taskflow submit` — do not hand-edit
├── meta.json     # Task state — do not hand-edit
├── workspace/    # Intermediate working files (drafts, code, research)
├── progress/     # Daily progress logs
└── <deliverables>  # Final output files
```

Your coordinator creates `spec.md`, `base/`, and `meta.json`. You own everything else.

- **plan.md**: Create with checkbox steps before starting work. Updated via `hiclaw-taskflow mark-step`.
- **workspace/**: Intermediate files (code drafts, research notes, build artifacts). Use this for work-in-progress that isn't a deliverable yet.
- **progress/**: Daily logs (`progress/YYYY-MM-DD.md`). Optional unless the task spec asks for them.
- **Deliverables**: Final output files. Place them under `shared/tasks/{task-id}/` and list them in `hiclaw-taskflow submit --deliverables`. Paths outside this directory are rejected.

## Execution Flow

### 1. Acknowledge

Accept the task and mark it in_progress:

```bash
hiclaw-taskflow ack {task-id} --sync
```

This reads meta.json, verifies your identity matches `assigned_to`, marks the task `in_progress`, and pushes to MinIO. If it reports an assignment mismatch, stop and notify your coordinator.

### 2. Execute

Create `plan.md` with checkbox steps, then work through them. After each step:

```bash
hiclaw-taskflow mark-step {task-id} <step-index> x --sync
```

Write progress notes under `progress/YYYY-MM-DD.md`. Progress updates that require no decision should not @mention anyone.

### 3. Submit

Write your result and finalize:

```bash
hiclaw-taskflow submit {task-id} \
  --status SUCCESS \
  --summary "<one paragraph summary>" \
  --deliverables "shared/tasks/{task-id}/output/file1, shared/tasks/{task-id}/output/file2" \
  --sync
```

This auto-completes any remaining plan.md checkboxes, writes `result.md` in standard format, marks the task `submitted`, and pushes everything to MinIO.

**Status values:**
- `SUCCESS` — task completed successfully
- `SUCCESS_WITH_NOTES` — completed with caveats or follow-ups
- `REVISION_NEEDED` — needs rework (coordinator will re-open)
- `BLOCKED` — cannot proceed due to external factor
- `INTERRUPTED` — stopped before completion

Submitting a result ends this Worker task. If more work is needed after `REVISION_NEEDED` or `BLOCKED`, wait for your coordinator to assign a new task; do not resume or rewrite the submitted task on your own.

### 4. Report

@mention your coordinator in the task room:

```
@coordinator:domain TASK_COMPLETED: {task-id} - <short outcome>
```

## Blocked

If blocked, submit a `BLOCKED` result immediately:

```bash
hiclaw-taskflow submit {task-id} --status BLOCKED --summary "<what is blocking you>" --sync
```

Then @mention your coordinator:

```
@coordinator:domain BLOCKED: {task-id} - <what is blocking you>
```

Do not invent missing task files, project plans, or shared directories.

## Checking Status

To see current task state at any time:

```bash
hiclaw-taskflow check {task-id}
```

This shows meta.json status, plan.md step progress, and result.md summary.

## Important

- `hiclaw-taskflow submit` writes `result.md` and updates `meta.json`. **Do not hand-edit either file.**
- Deliverables must be under `shared/tasks/{task-id}/`. Paths outside this directory are rejected.
- `ack` verifies your identity against `meta.json.assigned_to`. Only the assigned worker can ack.
- If plan.md doesn't exist yet, create it with checkbox steps before using `mark-step`.
