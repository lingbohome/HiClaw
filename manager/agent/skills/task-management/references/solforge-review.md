# Solforge Human Review

This file handles the human-in-the-loop review flow for tasks originating from
the Solforge Console. These tasks have a `solforge_ref` field in meta.json.

**Only load this file when the task context already shows it is a Solforge task.**

## On worker completion (Solforge task)

When a Worker @mentions you with TASK_COMPLETED for a Solforge task:

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

3. **DONE.** Do nothing else. The task stays in state.json. The meta.json stays as the Worker left it. Wait for admin to Accept or Reject.

**There is no step 4.** Do NOT update meta.json. Do NOT touch state.json. The admin will send an ACCEPTED or REJECTED DM — handle those per the sections below.

Applies every time, including after revision/re-submission. A revision_round > 0 does NOT mean the task is pre-approved — every submission requires a fresh human review.

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

6. Notify admin (same channel resolution as normal completion): `[Task Completed] {task-id}: {title} — assigned to {worker}. Human review accepted, task finalized.`

## On human reject

When you receive a DM containing `[ACTION REQUIRED] Task [...] has been REJECTED`:

1. **Task is STILL in state.json** — no add-finite needed. (It was never removed — see "On worker completion" above.)

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

5. Do NOT remove from state.json. Wait for Worker to re-submit. When they do, return to "On worker completion" above.
