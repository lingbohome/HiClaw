"""Smoke tests for hiclaw_taskflow full lifecycle."""
import json
import os
import tempfile
from pathlib import Path

from hiclaw_taskflow.plan import (
    ack_task,
    auto_complete_markers,
    canonical_worker_id,
    get_task_summary,
    is_effective_result,
    mark_step,
    parse_plan_steps,
    parse_task_result,
    read_meta,
    read_task_result,
    render_task_result,
    resolve_actor,
    validate_task_result,
    verify_worker_identity,
    write_meta_status,
    write_plan,
    write_task_result,
)

PLAN = """# Plan
## Steps
- [ ] Step 1: pending
- [x] Step 2: done
- [~] Step 3: in progress
- [!] Step 4: blocked
- [→] Step 5: revision
## Notes
"""

# ── auto_complete_markers ───────────────────────────────────────────────

result = auto_complete_markers(PLAN)
assert "[x] Step 1" in result
assert "[x] Step 2" in result
assert "[x] Step 3" in result
assert "[x] Step 4" in result
assert "[x] Step 5" in result
print("auto_complete_markers: PASS")

# ── mark_step ───────────────────────────────────────────────────────────

plan2 = "- [ ] Step 1\n- [ ] Step 2\n- [ ] Step 3\n"
r = mark_step(plan2, 1, "x")
assert "[ ] Step 1" in r
assert "[x] Step 2" in r
assert "[ ] Step 3" in r
print("mark_step: PASS")

r2 = mark_step(plan2, 0, "→")
assert "[→] Step 1" in r2
print("mark_step(unicode): PASS")

try:
    mark_step(plan2, 99, "x")
    assert False, "Should have raised"
except ValueError:
    print("mark_step OOB: PASS")

# ── parse_plan_steps ────────────────────────────────────────────────────

steps = parse_plan_steps(PLAN)
assert len(steps) == 5
assert steps[0]["marker"] == " "
assert steps[1]["marker"] == "x"
print("parse_plan_steps: PASS")

# ── canonical_worker_id / resolve_actor ─────────────────────────────────

assert canonical_worker_id("  @worker:matrix.org  ") == "worker"
assert canonical_worker_id("`@test-worker`") == "test-worker"
assert canonical_worker_id("Worker Display @real-id:domain") == "Worker"
assert canonical_worker_id("plain-name") == "plain-name"
print("canonical_worker_id: PASS")

# resolve_actor with env
os.environ["HICLAW_WORKER_NAME"] = "test-worker-1"
assert resolve_actor() == "test-worker-1"
assert resolve_actor("explicit-actor") == "explicit-actor"
del os.environ["HICLAW_WORKER_NAME"]
print("resolve_actor: PASS")

# ── ack_task / write_meta_status / read_meta ────────────────────────────

with tempfile.TemporaryDirectory() as tmp:
    td = Path(tmp)
    meta_path = td / "meta.json"
    meta_path.write_text(json.dumps({
        "status": "assigned",
        "task_id": "t1",
        "assigned_to": "test-worker-1",
    }))

    # ack with identity verification
    os.environ["HICLAW_WORKER_NAME"] = "test-worker-1"
    meta = ack_task(td)
    assert meta["status"] == "in_progress"
    assert "acknowledged_at" in meta
    del os.environ["HICLAW_WORKER_NAME"]
    print("ack_task (with verify): PASS")

    # ack with verify=False
    meta = ack_task(td, verify=False)
    assert meta["status"] == "in_progress"
    print("ack_task (no verify): PASS")

    # identity mismatch
    os.environ["HICLAW_WORKER_NAME"] = "wrong-worker"
    try:
        ack_task(td)  # should fail — wrong worker
        assert False, "Should have raised"
    except ValueError as e:
        assert "mismatch" in str(e)
    del os.environ["HICLAW_WORKER_NAME"]
    print("verify_worker_identity: PASS")

    # read_meta
    m = read_meta(td)
    assert m["status"] == "in_progress"
    print("read_meta: PASS")

    # write_meta_status (submit)
    m2 = write_meta_status(td, "submitted")
    assert m2["status"] == "submitted"
    assert "submitted_at" in m2
    print("write_meta_status: PASS")

# ── result.md — parse / validate / render / write / read ────────────────

RESULT_MD = """**status**: SUCCESS
**summary**: All tasks completed successfully
**deliverables**:
- shared/tasks/test-001/output/report.md
- shared/tasks/test-001/output/code.py
**notes**:
- Minor edge case in parser
- Docs updated
"""

parsed = parse_task_result(RESULT_MD)
assert parsed["status"] == "SUCCESS"
assert parsed["summary"] == "All tasks completed successfully"
assert len(parsed["deliverables"]) == 2
assert len(parsed["notes"]) == 2
print("parse_task_result: PASS")

errors = validate_task_result("test-001", parsed)
assert len(errors) == 0
print("validate_task_result (valid): PASS")

bad = parse_task_result("**status**: INVALID")
errors = validate_task_result("test-001", bad)
assert len(errors) > 0
print("validate_task_result (invalid): PASS")

bad_paths = {
    "status": "SUCCESS",
    "summary": "ok",
    "deliverables": ["../escape/path"],
    "notes": [],
}
errors = validate_task_result("test-001", bad_paths)
assert any("path traversal" in e for e in errors)
print("validate_task_result (path traversal): PASS")

assert is_effective_result(parsed)
assert not is_effective_result({"status": "BLOCKED"})
print("is_effective_result: PASS")

rendered = render_task_result(parsed)
assert "**status**: SUCCESS" in rendered
assert "**summary**:" in rendered
print("render_task_result: PASS")

with tempfile.TemporaryDirectory() as tmp:
    td = Path(tmp)
    r = write_task_result(td, parsed, "test-001")
    assert r["status"] == "SUCCESS"
    assert (td / "result.md").exists()

    r2 = read_task_result(td)
    assert r2["status"] == "SUCCESS"
    print("write/read_task_result: PASS")

# ── get_task_summary ────────────────────────────────────────────────────

with tempfile.TemporaryDirectory() as tmp:
    td = Path(tmp)
    td.joinpath("meta.json").write_text(json.dumps({
        "task_id": "task-1", "project_id": "p1",
        "task_title": "Test", "assigned_to": "w1",
        "status": "in_progress",
    }))
    write_plan(td, "- [x] Step 1\n- [ ] Step 2\n")
    td.joinpath("result.md").write_text(
        "**status**: SUCCESS\n**summary**: All done\n"
    )

    s = get_task_summary(td)
    assert s["meta_exists"]
    assert s["plan_exists"]
    assert len(s["steps"]) == 2
    assert s["result"]["status"] == "SUCCESS"
    assert s["result_effective"]
    print("get_task_summary: PASS")

print("\nAll tests passed!")
