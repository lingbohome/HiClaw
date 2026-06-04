# Finite Task Workflow

## Choosing task type

- **Finite** — clear end state. Worker delivers result, it's done. Examples: "implement login page", "fix bug #123", "write a report".
- **Infinite** — repeats on schedule, no natural end. See `references/infinite-tasks.md`.

**Rule**: if the request contains a recurring schedule or implies ongoing monitoring, use infinite. Everything else is finite.

## Assigning a finite task

1. Generate task ID: `task-YYYYMMDD-HHMMSS`
2. Create task directory and files:
   ```bash
   mkdir -p /root/hiclaw-fs/shared/tasks/{task-id}
   ```

   **Write `meta.json`** — use this EXACT template (replace `{...}` placeholders):
   ```json
   {
     "task_id": "{task-id}",
     "title": "{task title}",
     "type": "finite",
     "status": "assigned",
     "assigned_to": "{worker-name}",
     "room_id": "{room-id-from-step-4a}",
     "created_at": "{ISO-8601-now}"
   }
   ```
   **CRITICAL**: Every field is mandatory. `room_id` is required by Workers for
   identity verification and by Solforge for Matrix room message access. If the
   task originates from Solforge, also include `"solforge_ref": "{solforge-id}"`.

   **Write `spec.md`** (requirements, acceptance criteria, context).

3. Push to MinIO **immediately** — Worker cannot file-sync until files are in MinIO:
   ```bash
   mc cp /root/hiclaw-fs/shared/tasks/{task-id}/meta.json ${HICLAW_STORAGE_PREFIX}/shared/tasks/{task-id}/meta.json
   mc cp /root/hiclaw-fs/shared/tasks/{task-id}/spec.md ${HICLAW_STORAGE_PREFIX}/shared/tasks/{task-id}/spec.md
   ```
   **Verify the push succeeded** (non-zero exit = retry). Do NOT proceed to step 4 until files are confirmed in MinIO.

4. Notify Worker in their Room (never in admin DM):

   **HARD RULE:** Do **not** put @worker task-assignment text in your admin DM reply. Workers cannot read the admin DM. The admin DM reply must only confirm to the admin (for example: assigned `{task-id}` to `{worker}`). The full dispatch with @mention MUST go to the Worker's Matrix room using the protocol below.

   a) Get the Worker's `room_id` (and Matrix ID if needed):
   ```bash
   hiclaw get workers -o json
   ```

   b) Get your Manager runtime from the controller (source of truth):
   ```bash
   hiclaw get managers -o json | jq -r '.managers[0].runtime'
   ```

   c) Compose the body the Worker must receive (full Matrix @mention so they wake):
   ```
   @{worker}:{domain} New task [{task-id}]: {title}. Use your file-sync skill to pull the spec: shared/tasks/{task-id}/spec.md. @mention me when complete.
   ```

   d) Send that body to the Worker's room, branching on runtime from step (b):

   - **`openclaw`** — use the **message** tool with `channel=matrix` and `target=room:<ROOM_ID>` (the literal `room_id` value from step (a), prefixed with `room:`). Do not rely on the implicit current room when you are in an admin DM.

   - **`copaw`** — use the shell tool:
   ```bash
   copaw channels send \
     --agent-id default \
     --channel matrix \
     --target-session "<ROOM_ID>" \
     --target-user "@{worker}:${HICLAW_MATRIX_DOMAIN}" \
     --text '@{worker}:{domain} New task [{task-id}]: {title}. Use your file-sync skill to pull the spec: shared/tasks/{task-id}/spec.md. @mention me when complete.'
   ```
   (Quote `--text` so the shell preserves spaces and @mentions.)

5. **MANDATORY — Add to state.json** (this step is NOT optional, even for coordination, research, or management tasks):
   ```bash
   bash /opt/hiclaw/agent/skills/task-management/scripts/manage-state.sh \
     --action add-finite --task-id {task-id} --title "{title}" \
     --assigned-to {worker} --room-id {room-id}
   ```
   If task belongs to a project, append `--project-room-id {project-room-id}`.
   **WARNING**: Skipping this step causes the Worker to be auto-stopped by idle timeout. Every task assigned to a Worker MUST be registered here.

## On completion

When a Worker @mentions you with task completion:

1. Pull task directory from MinIO (Worker has pushed results):
   ```bash
   mc mirror ${HICLAW_STORAGE_PREFIX}/shared/tasks/{task-id}/ /root/hiclaw-fs/shared/tasks/{task-id}/ --overwrite
   ```

2. Notify admin — the task is ready for review. Read `SOUL.md` first for persona/language, then resolve channel:
   ```bash
   bash /opt/hiclaw/agent/skills/task-management/scripts/resolve-notify-channel.sh
   ```
   Send: `Task [{task-id}]: {title} is ready for your review. Worker {assigned_to} has submitted deliverables.`
   If `revision_round` is > 0: `Task [{task-id}]: {title} (revision round {N}) is ready for re-review.`

3. **DONE.** Do nothing else. Do NOT update meta.json. Do NOT touch state.json. The admin will send an ACCEPTED or REJECTED DM — handle those per the sections below.

There is no step 4. Applies every time, including revision/re-submission.

## On human accept

When you receive a DM containing `[NOTICE] Task [...] has been ACCEPTED`:

1. Pull task directory from MinIO:
   ```bash
   mc mirror ${HICLAW_STORAGE_PREFIX}/shared/tasks/{task-id}/ /root/hiclaw-fs/shared/tasks/{task-id}/ --overwrite
   ```

2. Verify deliverables exist — check `result.md` and any referenced deliverables.

3. Update `meta.json`:
   - `status` → `completed`
   - `human_accepted` → `true`  **(required — gates state.json removal)**
   - Fill `completed_at` with current ISO-8601 timestamp
   - Push back to MinIO

4. Remove from state.json:
   ```bash
   bash /opt/hiclaw/agent/skills/task-management/scripts/manage-state.sh \
     --action complete --task-id {task-id}
   ```

5. Log to `memory/YYYY-MM-DD.md`.

6. Notify admin: `[Task Completed] {task-id}: {title} — assigned to {worker}. Human review accepted, task finalized.`

## On human reject

When you receive a DM containing `[ACTION REQUIRED] Task [...] has been REJECTED`:

1. **Task is STILL in state.json** — no add-finite needed. (It was never removed — see "On completion" above.)

2. Extract the rejection reason from the DM message (after `Reason:` line).

3. Pull and update `meta.json`:
   ```bash
   mc mirror ${HICLAW_STORAGE_PREFIX}/shared/tasks/{task-id}/meta.json /root/hiclaw-fs/shared/tasks/{task-id}/meta.json --overwrite
   ```
   Update:
   - `status` → `in_progress`
   - `revision_round` → (existing value || 0) + 1
   - `rejection_reason` → the reason text
   - Append to `revision_history` array: `{ "revision_round": <N>, "rejected_at": "<ISO>", "reason": "<reason>" }`
   - Push back to MinIO

4. Notify the Worker in their task room (use `assigned_to` from meta.json to get the Worker name, and `room_id` for the room):
   ```
   @{worker}:{domain} Task [{task-id}] needs revision.

   Rejection reason: {reason}

   Please pull the latest meta.json, revise the deliverables based on the feedback above, and re-submit.
   ```

5. Do NOT remove from state.json. Wait for Worker to re-submit. Then return to "On completion" above.

## Task directory layout

```
shared/tasks/{task-id}/
├── meta.json     # Manager-maintained
├── spec.md       # Manager-written
├── base/         # Manager-maintained reference files (Workers must not overwrite)
├── plan.md       # Worker-written execution plan
├── result.md     # Worker-written final result
└── *             # Intermediate artifacts
```

## meta.json field reference

Every field this file may contain across the full task lifecycle.
**Only use fields listed here — do not invent new ones.**

### Required on creation (always write these)

| Field | Type | Example | Who writes | Notes |
|-------|------|---------|------------|-------|
| `task_id` | string | `"task-20260603-160100"` | Manager | Format: `task-YYYYMMDD-HHMMSS` |
| `title` | string | `"Implement login page"` | Manager | Task title |
| `type` | string | `"finite"` | Manager | Always `"finite"` for this flow |
| `status` | string | `"assigned"` | Manager → Worker → Manager | See status lifecycle below |
| `assigned_to` | string | `"code-worker"` | Manager | Worker CR name (no `@` prefix) |
| `room_id` | string | `"!abc123:matrix.org"` | Manager | Worker's Matrix room ID (from `hiclaw get workers -o json`) |
| `created_at` | string | `"2026-06-03T16:01:00Z"` | Manager | ISO-8601 UTC. Solforge uses this for chronological ordering |

### Solforge tasks only

| Field | Type | Example | Who writes | Notes |
|-------|------|---------|------------|-------|
| `solforge_ref` | string | `"sol-20260603-000108"` | Manager | Present only for Solforge-originated tasks |

### Worker lifecycle (written by Worker via hiclaw-taskflow / taskflow)

| Field | Type | Example | Who writes | Notes |
|-------|------|---------|------------|-------|
| `acknowledged_at` | string | `"2026-06-03T16:02:00Z"` | Worker (ack) | Set when Worker calls `ack` |
| `submitted_at` | string | `"2026-06-03T16:05:00Z"` | Worker (submit) | Set when Worker calls `submit` |

### Manager completion (written ONLY in "On human accept" or for non-Solforge tasks)

| Field | Type | Example | Who writes | Notes |
|-------|------|---------|------------|-------|
| `completed_at` | string | `"2026-06-03T16:10:00Z"` | Manager | ISO-8601 UTC. Only after human accept for Solforge tasks |
| `human_accepted` | boolean | `true` | Manager | **Required for Solforge tasks** before `manage-state.sh complete` will succeed |

### Revision tracking (written by Manager on human reject)

| Field | Type | Example | Who writes | Notes |
|-------|------|---------|------------|-------|
| `revision_round` | number | `1` | Manager | Increments on each rejection. 0 or absent = never rejected |
| `rejection_reason` | string | `"Add speed control"` | Manager | Last rejection reason. Cleared on next submit? |
| `revision_history` | array | `[{...}]` | Manager | Audit log, append-only. See sub-fields below |

**`revision_history[]` entry:**
| Sub-field | Type | Example |
|-----------|------|---------|
| `revision_round` | number | `1` |
| `rejected_at` | string | `"2026-06-03T16:08:00Z"` |
| `reason` | string | `"Add speed control"` |
| `revised_at` | string or null | `"2026-06-03T16:12:00Z"` or `null` |

### Project tasks only (NOT used by finite/standalone tasks)

| Field | Type | When | Notes |
|-------|------|------|-------|
| `project_id` | string | DAG/Team tasks only | Set by `delegate_task()`. Absent for standalone tasks |
| `depends_on` | string[] | DAG/Team tasks only | Task dependencies |

### Status lifecycle

```
assigned ──→ in_progress ──→ submitted ──→ completed
  (Manager)    (Worker ack)   (Worker submit)  (Manager, after human accept for Solforge)
```

**Never skip status values.** Each transition must be written explicitly.

### Optional enrichment (free-form, additive only)

You may add extra fields for display/classification purposes. These are never
required and must not affect the task state machine. Examples:

| Field | Type | Example | Purpose |
|-------|------|---------|---------|
| `tags` | string[] | `["frontend", "react", "urgent"]` | Category labels for Console filtering/display |
| `description` | string | `"Brief one-liner"` | Short summary for list views (spec.md is the full version) |
| `priority` | string | `"high"` | Display hint |
| `notes` | string[] | `["Depends on API v2"]` | Manager's internal notes |

**Rule**: Any field not listed in Required/Creation is fine as long as it:
1. Doesn't duplicate or conflict with a Required field name
2. Doesn't change the state machine behavior
3. Is additive (removing it wouldn't break anything)

### Forbidden (will break the state machine)

- ~~`project`~~ — conflicts with `project_id`. Use `project_id` if this is a DAG task
- ~~`state`~~ — conflicts with `status`
