"""J: 탐색 작업 1건 안의 다단계 루프(worker._continue_steps, UPGRADE_PLAN.md J)를 실제 PostgreSQL 로 확인한다.

- 단계 2..N 은 같은 의도·같은 자산 안에서 Worker LLM 이 고르고, 단계마다 run_tool()(단일 정책 경로)·fact 노드·
  신호 판정이 따로 남는다. 단계 수 상한은 없다(JD1).
- 종료: 빈 tool / 같은 tool+args 반복 / 등록·가시 도구 아님 / 정책 비허용(JD4) / 연속 실패 2회 / 벽시계 마감 /
  LLM 오류. 재검증 작업·설정 꺼짐·LLM 없음이면 1단계만(지금 동작).
- read 전용 제한 없음(JD3) — read_only=False 도구도 정책이 허용하면 실행된다.
- 작업 안 단계는 Planner 의 ALREADY_TRIED 에 들어가 다음 라운드에 재제안되지 않는다.
"""
import json
import time
import unittest
from unittest import mock

from psycopg.types.json import Jsonb

from audit.local_model import LLMError
from audit.models import ConfigError, ToolError, ToolOutput, ToolSpec
from diagnostic_workbench import config, db, explore, policy, runs, worker


class _SeqLLM:
    """worker_decide 는 제안 그대로 확인, worker_step 은 준비된 응답을 차례로 돌려주고 다 쓰면 빈 tool(종료)."""
    model = "fake-steps"

    def __init__(self, steps=(), fail_step=False):
        self.steps = list(steps)
        self.fail_step = fail_step
        self.step_calls = 0

    def structured_chat(self, messages, schema, purpose):
        if purpose == "worker_decide":
            return {"tool": "test.step1", "args": {}, "rationale": "제안 그대로"}
        assert purpose == "worker_step", purpose
        self.step_calls += 1
        if self.fail_step:
            raise LLMError("모델 응답 없음(테스트)")
        if self.steps:
            return self.steps.pop(0)
        return {"tool": "", "args": {}, "rationale": "의도 달성"}


def _ok(name):
    return lambda args: ToolOutput(json.dumps({"from": name, "args": args}).encode(), f"{name}/1")


def _boom(args):
    raise ToolError("대상 응답 없음(테스트)")


class TaskStepsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.cfg = config.load(None)
            cls.conn = db.connect(cls.cfg.dsn)
            db.require_migrated(cls.conn, cls.cfg.migrations_dir)
        except ConfigError as e:
            raise unittest.SkipTest(f"DB 연결/마이그레이션 불가: {e}") from None
        if "baseline-demo" not in {t["target_id"] for t in json.loads(
                (cls.cfg.home / "config" / "scope.json").read_text(encoding="utf-8"))["targets"]}:
            raise unittest.SkipTest("scope.json 에 baseline-demo 대상이 없음")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "conn"):
            cls.conn.close()

    def _setup(self, llm, intent_payload=None):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-task-steps",
                              checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        ctx = runs.build_context(self.cfg, self.conn, run)
        ctx.registry["test.step1"] = ToolSpec("test.step1", True, {}, {}, _ok("test.step1"))
        ctx.registry["test.read"] = ToolSpec("test.read", True, {}, {}, _ok("test.read"))
        ctx.registry["test.write"] = ToolSpec("test.write", False, {}, {}, _ok("test.write"))
        ctx.registry["test.boom"] = ToolSpec("test.boom", True, {}, {}, _boom)
        ctx.llm = llm
        asset_id = next(iter(ctx.targets))
        node = self.conn.execute(
            "INSERT INTO exploration_nodes (run_id, kind, origin, payload) VALUES (%s,'intent','llm',%s) RETURNING id",
            (run["id"], Jsonb(intent_payload or {"rationale": "로그인 폼 확인", "expected_evidence": "폼 응답",
                                                  "stop_condition": "응답 비교 완료"}))).fetchone()["id"]
        item_id = self.conn.execute(
            "INSERT INTO backlog_items (run_id, kind, exploration_node_id, asset_id, priority, idempotency_key, "
            "reason, payload, max_attempts, status, lease_until) VALUES (%s,'exploration',%s,%s,80,%s,'테스트',%s,1,"
            "'RUNNING', now() - interval '1 hour') RETURNING id",
            (run["id"], node, asset_id, f"test-steps-{node}", Jsonb({"tool": "test.step1", "args": {}}))
        ).fetchone()["id"]
        item = dict(self.conn.execute("SELECT * FROM backlog_items WHERE id=%s", (item_id,)).fetchone())
        return ctx, worker.load_item(ctx, item), run

    def _steps(self, it):
        return [(r["step_no"], r["tool"], r["status"]) for r in self.conn.execute(
            "SELECT step_no, tool, status FROM tool_invocations WHERE backlog_item_id=%s ORDER BY step_no, started_at",
            (it["id"],)).fetchall()]

    def _finish_reason(self, run, it):
        row = self.conn.execute(
            "SELECT details FROM audit_events WHERE run_id=%s AND event='task_steps_finished' "
            "AND details->>'backlog_item'=%s ORDER BY id DESC LIMIT 1", (run["id"], str(it["id"]))).fetchone()
        return row["details"]["reason"] if row else None

    def test_continues_steps_then_stops_on_empty_tool(self):
        llm = _SeqLLM([{"tool": "test.read", "args": {"q": "a"}, "rationale": "두 번째"},
                       {"tool": "test.read", "args": {"q": "b"}, "rationale": "세 번째"}])
        ctx, it, run = self._setup(llm)
        status, _ = worker._run_exploration(ctx, it, None)
        self.assertEqual(status, "SUCCEEDED")
        self.assertEqual(self._steps(it), [(1, "test.step1", "SUCCEEDED"), (2, "test.read", "SUCCEEDED"),
                                           (3, "test.read", "SUCCEEDED")])
        facts = self.conn.execute(
            "SELECT n.payload FROM exploration_edges e JOIN exploration_nodes n ON n.id = e.dst_id "
            "WHERE e.src_id=%s AND e.rel='yields' ORDER BY n.created_at", (it["exploration_node_id"],)).fetchall()
        self.assertEqual([f["payload"]["step"] for f in facts], [1, 2, 3])  # 단계마다 fact 노드
        self.assertEqual(self._finish_reason(run, it), "done")
        lease = self.conn.execute("SELECT lease_until > now() AS fresh FROM backlog_items WHERE id=%s",
                                  (it["id"],)).fetchone()
        self.assertTrue(lease["fresh"])  # 단계마다 임대 갱신(실행 중 회수 방지)

    def test_signal_in_later_step_becomes_finding(self):
        payload = "<script>alert(1)</script>"  # test.read 는 인자를 그대로 응답에 담는다 → 반사형 XSS 신호
        llm = _SeqLLM([{"tool": "test.read", "args": {"q": payload}, "rationale": "반사 확인"}])
        ctx, it, run = self._setup(llm)
        worker._run_exploration(ctx, it, None)
        step2_eid = self.conn.execute(
            "SELECT evidence_id FROM tool_invocations WHERE backlog_item_id=%s AND step_no=2", (it["id"],)).fetchone()
        linked = self.conn.execute(
            "SELECT f.verification FROM finding_evidence fe JOIN findings f ON f.id = fe.finding_id "
            "WHERE fe.evidence_id=%s", (step2_eid["evidence_id"],)).fetchall()
        self.assertEqual([r["verification"] for r in linked], ["RULE_CONFIRMED"])  # 2단계 증거로 규칙 확정

    def test_no_step_cap(self):
        llm = _SeqLLM([{"tool": "test.read", "args": {"n": i}, "rationale": "계속"} for i in range(8)])
        ctx, it, run = self._setup(llm)
        worker._run_exploration(ctx, it, None)
        self.assertEqual(len(self._steps(it)), 9)  # 1단계 + 8단계, 상한으로 잘리지 않음(JD1)

    def test_repeat_stops_loop(self):
        llm = _SeqLLM([{"tool": "test.step1", "args": {}, "rationale": "같은 것 또"}])
        ctx, it, run = self._setup(llm)
        worker._run_exploration(ctx, it, None)
        self.assertEqual(self._steps(it), [(1, "test.step1", "SUCCEEDED")])
        self.assertEqual(self._finish_reason(run, it), "repeat")

    def test_unknown_tool_stops_loop(self):
        llm = _SeqLLM([{"tool": "test.nope", "args": {}, "rationale": "없는 도구"}])
        ctx, it, run = self._setup(llm)
        worker._run_exploration(ctx, it, None)
        self.assertEqual(len(self._steps(it)), 1)
        self.assertEqual(self._finish_reason(run, it), "invalid_tool")

    def test_tool_outside_run_allowed_list_is_not_offered(self):
        llm = _SeqLLM([{"tool": "test.read", "args": {}, "rationale": "이 실행에서 허용 안 된 도구"}])
        ctx, it, run = self._setup(llm)
        ctx.run = {**ctx.run, "allowed_tools": ["test.step1"]}
        worker._run_exploration(ctx, it, None)
        self.assertEqual(len(self._steps(it)), 1)
        self.assertEqual(self._finish_reason(run, it), "invalid_tool")

    def test_non_read_tool_is_allowed_when_policy_allows(self):
        llm = _SeqLLM([{"tool": "test.write", "args": {}, "rationale": "쓰기 도구도 정책이 허용하면"}])
        ctx, it, run = self._setup(llm)
        worker._run_exploration(ctx, it, None)
        self.assertIn((2, "test.write", "SUCCEEDED"), self._steps(it))  # JD3: read 전용 제한 없음

    def test_policy_denial_stops_loop(self):
        real = policy.decide

        def deny_write(ctx, tool, args, asset_id):
            if tool == "test.write":
                return policy.Decision(policy.ALLOW_WITH_APPROVAL, "가로채기 규칙(테스트)", args)
            return real(ctx, tool, args, asset_id)

        llm = _SeqLLM([{"tool": "test.write", "args": {}, "rationale": "승인 필요한 단계"},
                       {"tool": "test.read", "args": {}, "rationale": "여기까지 오면 안 됨"}])
        ctx, it, run = self._setup(llm)
        with mock.patch.object(policy, "decide", deny_write):
            worker._run_exploration(ctx, it, None)
        self.assertEqual(self._steps(it), [(1, "test.step1", "SUCCEEDED"), (2, "test.write", "SKIPPED")])
        self.assertEqual(self._finish_reason(run, it), "policy")  # JD4: 승인 대기로 바꾸지 않고 종료

    def test_two_consecutive_failures_stop_loop(self):
        llm = _SeqLLM([{"tool": "test.boom", "args": {"i": 1}, "rationale": "실패1"},
                       {"tool": "test.boom", "args": {"i": 2}, "rationale": "실패2"},
                       {"tool": "test.read", "args": {}, "rationale": "여기까지 오면 안 됨"}])
        ctx, it, run = self._setup(llm)
        status, _ = worker._run_exploration(ctx, it, None)
        self.assertEqual(status, "SUCCEEDED")  # 1단계가 증거를 냈다
        self.assertEqual(self._finish_reason(run, it), "failures")
        self.assertNotIn("test.read", [t for _, t, _ in self._steps(it)])

    def test_deadline_stops_before_llm_call(self):
        llm = _SeqLLM([{"tool": "test.read", "args": {}, "rationale": "마감 지남"}])
        ctx, it, run = self._setup(llm)
        ctx.deadline = time.monotonic() - 1
        worker._run_exploration(ctx, it, None)
        self.assertEqual(llm.step_calls, 0)
        self.assertEqual(self._finish_reason(run, it), "deadline")

    def test_kill_switch_stops_before_llm_call(self):
        llm = _SeqLLM([{"tool": "test.read", "args": {}, "rationale": "중지 스위치 켜짐"}])
        ctx, it, run = self._setup(llm)
        # 1단계(정책 검사 1회)는 통과한 뒤 중지 스위치가 켜진 상황 — 실제 STOP 파일은 만들지 않는다.
        with mock.patch.object(config.Config, "stop_file", new_callable=mock.PropertyMock) as stop:
            stop.return_value = mock.Mock(exists=mock.Mock(side_effect=[False, True, True, True]))
            worker._run_exploration(ctx, it, None)
        self.assertEqual(llm.step_calls, 0)
        self.assertEqual(self._finish_reason(run, it), "kill_switch")

    def test_cancelled_run_stops_before_llm_call(self):
        llm = _SeqLLM([{"tool": "test.read", "args": {}, "rationale": "취소됨"}])
        ctx, it, run = self._setup(llm)
        self.conn.execute("UPDATE runs SET status='CANCELLED' WHERE id=%s", (run["id"],))
        worker._run_exploration(ctx, it, None)
        self.assertEqual(llm.step_calls, 0)
        self.assertEqual(self._finish_reason(run, it), "cancelled")

    def test_llm_error_falls_back_to_first_step(self):
        ctx, it, run = self._setup(_SeqLLM(fail_step=True))
        status, _ = worker._run_exploration(ctx, it, None)
        self.assertEqual(status, "SUCCEEDED")
        self.assertEqual(self._finish_reason(run, it), "llm_error")

    def test_retest_task_runs_single_step(self):
        llm = _SeqLLM([{"tool": "test.read", "args": {}, "rationale": "재검증엔 안 붙어야 함"}])
        ctx, it, run = self._setup(llm, {"tool": "test.step1", "args": {},
                                         "retest_of_finding": "00000000-0000-0000-0000-000000000000"})
        worker._run_exploration(ctx, it, None)
        self.assertEqual(llm.step_calls, 0)
        self.assertEqual(len(self._steps(it)), 1)

    def test_disabled_runs_single_step(self):
        llm = _SeqLLM([{"tool": "test.read", "args": {}, "rationale": "꺼져 있음"}])
        ctx, it, run = self._setup(llm)
        ctx.cfg = config.Config(**{**ctx.cfg.__dict__, "multi_step_tasks": False})
        worker._run_exploration(ctx, it, None)
        self.assertEqual(llm.step_calls, 0)

    def test_in_task_steps_appear_in_planner_already_tried(self):
        llm = _SeqLLM([{"tool": "test.read", "args": {"marker": "step-two-marker"}, "rationale": "두 번째"}])
        ctx, it, run = self._setup(llm)
        worker._run_exploration(ctx, it, None)
        text, _, _ = explore._context(ctx)
        tried_line = next(line for line in text.splitlines() if line.startswith("ALREADY_TRIED"))
        self.assertIn("step-two-marker", tried_line)


if __name__ == "__main__":
    unittest.main()
