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
   Write `meta.json` (type: "finite", status: "assigned") and `spec.md` (requirements, acceptance criteria, context).

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

2. **Check for Solforge tasks** — read `meta.json`:
   ```bash
   cat /root/hiclaw-fs/shared/tasks/{task-id}/meta.json
   ```

   **If meta.json has a `solforge_ref` field:**
   - **HARD RULE — applies every time, including revision/re-submission cycles.**
     Even if `revision_round` is > 0 and admin has rejected before, you MUST
     stop and wait. Each new submission (including revisions) requires a fresh
     human review. Never auto-complete a Solforge task without an explicit
     ACCEPTED DM from admin.
   - Do NOT update meta.json to completed yet — human review comes first.
   - Do NOT remove from state.json.
   - If `revision_round` is present and > 0, include it in the notification:
     `Task [{task-id}]: {title} (revision round {N}) is ready for re-review.`
   - Notify admin: the task is ready for review. Read `SOUL.md` first for persona/language, then resolve channel:
     ```bash
     bash /opt/hiclaw/agent/skills/task-management/scripts/resolve-notify-channel.sh
     ```
     Send: `Task [{task-id}]: {title} is ready for your review. Worker {assigned_to} has submitted deliverables.`
   - **STOP here** — wait for admin to Accept or Reject. Do not proceed to steps 3-5.
     (You will receive an ACCEPTED or REJECTED DM; see "On human accept" and "On human reject" below.)

   **If meta.json does NOT have `solforge_ref`:**
   - This is a hiClaw-internal task. Proceed with normal completion (steps 3-5 below).

3. Update `meta.json`: status=completed, fill completed_at. Push back to MinIO.
4. Remove from state.json:
   ```bash
   bash /opt/hiclaw/agent/skills/task-management/scripts/manage-state.sh \
     --action complete --task-id {task-id}
   ```
5. Log to `memory/YYYY-MM-DD.md`.
6. Notify admin — read SOUL.md first for persona/language, then resolve channel:
   ```bash
   bash /opt/hiclaw/agent/skills/task-management/scripts/resolve-notify-channel.sh
   ```
   Re-read runtime if needed: `hiclaw get managers -o json | jq -r '.managers[0].runtime'`.

   - **`openclaw`:** If `channel` is not `"none"`, use the **message** tool with the resolved `channel` and `target` (same mapping as channel-management / primary-channel docs). Send `[Task Completed] {task-id}: {title} — assigned to {worker}. {summary}`.

   - **`copaw`:** If `channel` is not `"none"`, use **`copaw channels send`** with the resolved channel and target (Matrix: `--channel matrix`, `--target-session` = room id without `room:` prefix when the script returns `room:!...`, `--target-user` = admin Matrix ID). If you are **in an admin DM session** for this turn, do **not** CLI-send to the admin DM — put `[Task Completed] ...` in your **final reply only** (avoids duplicate messages; see copaw-manager-agent AGENTS.md). If you are in a Worker or project room session, use `copaw channels send` per the resolved JSON.

   - If `channel` is `"none"`: the admin DM room is not yet cached. Discover it now — list joined rooms, find the DM room with exactly 2 members (you and admin), then persist:
     ```bash
     bash /opt/hiclaw/agent/skills/task-management/scripts/manage-state.sh \
       --action set-admin-dm --room-id "<discovered-room-id>"
     ```
     After persisting, retry `resolve-notify-channel.sh` and send the notification. If discovery fails, log a warning and move on — heartbeat will catch up.

## On human accept

When you receive a DM containing `[NOTICE] Task [...] has been ACCEPTED`:

1. Pull task directory from MinIO:
   ```bash
   mc mirror ${HICLAW_STORAGE_PREFIX}/shared/tasks/{task-id}/ /root/hiclaw-fs/shared/tasks/{task-id}/ --overwrite
   ```

2. Verify deliverables exist — check `result.md` and any referenced deliverables.

3. Update `meta.json`:
   - `status` → `completed`
   - Fill `completed_at` with current ISO-8601 timestamp
   - Push back to MinIO

4. Remove from state.json:
   ```bash
   bash /opt/hiclaw/agent/skills/task-management/scripts/manage-state.sh \
     --action complete --task-id {task-id}
   ```

5. Log to `memory/YYYY-MM-DD.md`.

6. Notify admin (same channel resolution as normal completion): `[Task Completed] {task-id}: {title} — assigned to {worker}. Human review accepted, task finalized.`

## On human reject

When you receive a DM containing `[ACTION REQUIRED] Task [...] has been REJECTED`:

1. **Task is STILL in state.json** — no add-finite needed. The task was never removed (see "On completion" step 2 for Solforge tasks).

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

5. Do NOT remove from state.json. Wait for Worker to re-submit. When they do, the task will go back through "On completion" flow (step 2 will detect `solforge_ref` again and wait for human review).

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
