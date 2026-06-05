---
name: task-management
description: Use before any Worker taskflow call or assigned-task workflow, including reading task state, acknowledging a task, executing a task, tracking progress, handling blockers/questions, submitting structured results, or reporting completion. Always use this skill when the message mentions assigned task, task ID, shared/tasks, spec.md, meta.json, result.md, deliverables, BLOCKED, REVISION_NEEDED, SUCCESS, submit_task, or ack_task.
---

# Task Management

You are a Worker. Execute only your assigned task.

## Task Directory

All work for a task stays under:

```text
shared/tasks/{task-id}/
```

Your coordinator creates:

```text
shared/tasks/{task-id}/spec.md
shared/tasks/{task-id}/meta.json
shared/tasks/{task-id}/base/
```

You own:

```text
shared/tasks/{task-id}/workspace/
shared/tasks/{task-id}/progress/
shared/tasks/{task-id}/<deliverables>
```

`taskflow` owns `shared/tasks/{task-id}/result.md` and `meta.json`. Do not hand-edit either file. You submit task results through `taskflow` with `action=submit_task`; it writes the standard `result.md` protocol for you.

`ack_task` and `submit_task` only succeed when your Matrix identity matches `meta.json.assigned_to`. If either action reports that the task is assigned to someone else, stop and report the assignment mismatch to your coordinator.

Create `plan.md` in the task directory with checkbox steps before starting work. Update each step as you progress with `taskflow` `action=mark_step`:

```json
{
  "action": "mark_step",
  "payload": {
    "taskId": "{task-id}",
    "stepIndex": 0,
    "marker": "x"
  }
}
```

Task-level `shared/tasks/{task-id}/plan.md` is YOUR execution plan for this task's sub-steps. Project-level `shared/projects/{project-id}/plan.md` is the DAG graph managed by the Team Leader. These are separate files at different paths — they do not conflict.

If you need private working notes, write them under `shared/tasks/{task-id}/workspace/`.

Do not edit project-level `shared/projects/{project-id}/plan.md` or `meta.json` unless the task spec explicitly tells you to.

## Execution Flow

1. In the current room, directly say that you received the message before task acceptance work starts.
2. Accept the task with `taskflow`. This single call pulls the task directory from storage, reads `spec.md` and `meta.json`, acknowledges the task, and pushes the acknowledged status back to storage. The response contains the spec content:

   ```json
   {
     "action": "ack_task",
     "payload": {
       "taskId": "{task-id}"
     }
   }
   ```

   The response `spec` field contains the full task spec. Read it from the response instead of calling `read_file` separately.

   `meta.json.room_id` is the task's assignment and delivery room. Use it only when cross-room delivery is truly needed. If it is missing, stop and report a blocker in the current session instead of guessing another room.

3. Create `plan.md` with checkbox steps, then execute the task. After each step, mark it complete via `taskflow` `action=mark_step`. Keep deliverables inside `shared/tasks/{task-id}/`. When you submit, `submit_task` auto-completes any remaining plan.md checkboxes.

   **Preview check (must complete before submit):**

   If you built a web application, API service, or HTTP server:
   1. Start it bound to `0.0.0.0` (not localhost / 127.0.0.1 — the preview URL
      reaches your container from outside, so loopback-only won't work).
      Common frameworks: `vite --host 0.0.0.0`, `next dev -H 0.0.0.0`,
      `uvicorn main:app --host 0.0.0.0`, `flask run --host=0.0.0.0`.
   2. Wait for its startup log line, then verify:
      `curl -s http://0.0.0.0:<port>` must respond.
   3. Include a `preview` object in the submit payload with `port` and
      optional `description`.

   If your deliverable is static files only (reports, documents, images,
   single HTML), or there is no HTTP server to start, skip `preview`.

4. Submit the task result with `taskflow`. This writes `shared/tasks/{task-id}/result.md`, marks local task state submitted, pushes the task directory to storage, and verifies `result.md` exists on storage:

   ```json
   {
     "action": "submit_task",
     "payload": {
       "taskId": "{task-id}",
       "status": "SUCCESS",
       "summary": "<one paragraph summary>",
       "deliverables": [
         "shared/tasks/{task-id}/workspace/<file>"
       ],
       "preview": {
         "port": 3000,
         "description": "React admin dashboard"
       }
     }
   }
   ```

   Use `SUCCESS`, `SUCCESS_WITH_NOTES`, `REVISION_NEEDED`, or `BLOCKED` for normal task execution status.

   Submitting a result ends this Worker task. If more work is needed after `REVISION_NEEDED` or `BLOCKED`, wait for your coordinator to assign a new task; do not resume or rewrite the submitted task on your own.

5. In the current room, directly @mention your coordinator with completion:

   ```text
   @coordinator:domain TASK_COMPLETED: {task-id} - <short outcome>. Result: shared/tasks/{task-id}/result.md
   ```

   Do not look up your Worker profile room or private room as a fallback. The task directory is the source of truth if you ever need to verify the assignment room.

## Revision

When your coordinator asks you to revise a previously submitted task (same task-id):

1. Read `rejection_reason` from `meta.json` — this tells you what needs to be changed. The task status is already `in_progress`, so you do not need to call `ack_task` again.

2. Revise your deliverables in the existing `workspace/` directory. Do NOT create a new plan.md — append revision steps at the bottom:

   ```markdown
   - [ ] Revision: {describe what you changed based on rejection_reason}
   ```

   **Always add at least one revision step.** The only exception is a trivial
   one-line fix (typo, format, phrasing). If the change involves any new logic,
   behavior, or content → add a step. When in doubt, add it — marking it done
   is cheap; a missing step can't be recovered.

3. Mark revision steps as you complete them:
   ```json
   {
     "action": "mark_step",
     "payload": {
       "taskId": "{task-id}",
       "stepIndex": 0,
       "marker": "x"
     }
   }
   ```

4. Re-submit when revisions are complete. This overwrites `result.md` and pushes to MinIO:
   ```json
   {
     "action": "submit_task",
     "payload": {
       "taskId": "{task-id}",
       "status": "SUCCESS",
       "summary": "<one paragraph summary of revisions>",
       "deliverables": [
         "shared/tasks/{task-id}/workspace/<file>"
       ]
     }
   }
   ```

5. @mention your coordinator:
   ```text
   @coordinator:domain TASK_COMPLETED: {task-id} - revisions applied
   ```

This is still the same task (same task-id, same workspace). Treat it as a revision, not a new task.

## Blocked

If blocked, submit a `BLOCKED` result. `submit_task` automatically pushes and verifies:

```json
{
  "action": "submit_task",
  "payload": {
    "taskId": "{task-id}",
    "status": "BLOCKED",
    "summary": "<what is blocking you>",
    "deliverables": []
  }
}
```

Then @mention your coordinator:

```text
@coordinator:domain BLOCKED: {task-id} - <what is blocking you>
```

Do not invent missing task files, project plans, or shared directories.

## Progress

Write a progress log after each meaningful action (completing a step, hitting a problem, making a decision). Append to:

```text
shared/tasks/{task-id}/progress/YYYY-MM-DD.md
```

Use this format:
```markdown
## HH:MM — {brief action title}

- What was done: ...
- Current state: ...
- Next step: ...
```

Push to MinIO after each update via `filesync push`. This feeds the Console Activity Feed.
Progress updates that require no decision should not @mention anyone.
