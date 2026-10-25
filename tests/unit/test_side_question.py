"""사이드 질문(ARTEX 대응, exploration_nodes.kind='question')을 확인한다.

- explore.propose 가 LLM 의 선택적 question 필드를 받으면 kind='question' 노드를 만든다(상태는 기본
  OPEN). 이미 OPEN 인 질문이 있으면 새 질문은 조용히 버려진다(한 번에 하나, 스팸 방지).
- _context() 가 QUESTIONS 블록에 OPEN/ANSWERED 질문을 담아 다음 라운드 프롬프트에 넣는다.
- 답변 기록(cmd_question answer 가 실제로 하는 DB 갱신)은 state 를 ANSWERED 로 바꾸고 이력을 남긴다.
"""
import json
import unittest

from audit.models import ConfigError
from diagnostic_workbench import config, db, explore, runs


class _FakeLLM:
    """explore 가 기대하는 최소 인터페이스. bootstrap_probe 호출은 즉시 중단시켜(tool="") 최초 라운드의
    프로브 단계를 건너뛰고, explore 호출에만 미리 정한 응답을 순서대로 돌려준다."""
    model = "fake-question"

    def __init__(self, explore_replies):
        self._replies = list(explore_replies)

    def structured_chat(self, messages, schema, purpose):
        if purpose == "bootstrap_probe":
            return {"tool": "", "target_id": "", "args": {}, "rationale": ""}
        return self._replies.pop(0)


class SideQuestionDB(unittest.TestCase):
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

    def _run_and_ctx(self):
        run = runs.create_run(self.conn, self.cfg, ["baseline-demo"], "pytest-side-question",
                              checklist_id=None, use_llm=True)
        self.addCleanup(self.conn.commit)
        ctx = runs.build_context(self.cfg, self.conn, run)
        return run, ctx

    def _questions(self, run_id):
        return self.conn.execute(
            "SELECT * FROM exploration_nodes WHERE run_id=%s AND kind='question' ORDER BY created_at",
            (run_id,)).fetchall()

    def test_question_field_creates_open_node(self):
        run, ctx = self._run_and_ctx()
        ctx.llm = _FakeLLM([{"proposals": [], "question": {"text": "어떤 로그인 계정으로 테스트해야 하나요?"}}])
        explore.propose(ctx)
        qs = self._questions(run["id"])
        self.assertEqual(len(qs), 1)
        self.assertEqual(qs[0]["state"], "OPEN")
        self.assertIn("로그인 계정", qs[0]["payload"]["text"])

    def test_second_question_rejected_while_one_open(self):
        run, ctx = self._run_and_ctx()
        ctx.llm = _FakeLLM([
            {"proposals": [], "question": {"text": "첫 번째 질문"}},
            {"proposals": [], "question": {"text": "두 번째 질문"}},
        ])
        explore.propose(ctx)
        explore.propose(ctx)
        qs = self._questions(run["id"])
        self.assertEqual(len(qs), 1)  # 두 번째는 버려짐
        self.assertEqual(qs[0]["payload"]["text"], "첫 번째 질문")

    def test_answered_question_appears_in_next_context(self):
        run, ctx = self._run_and_ctx()
        ctx.llm = _FakeLLM([{"proposals": [], "question": {"text": "스테이징 DB 비밀번호가 뭔가요?"}}])
        explore.propose(ctx)
        qid = self._questions(run["id"])[0]["id"]
        # cmd_question answer 가 실제로 하는 갱신과 같은 내용
        from psycopg.types.json import Jsonb
        payload = {"text": "스테이징 DB 비밀번호가 뭔가요?", "answer": "담당자에게 별도 전달함", "answered_by": "ops"}
        self.conn.execute("UPDATE exploration_nodes SET state='ANSWERED', payload=%s WHERE id=%s",
                          (Jsonb(payload), qid))
        text, _known, _latest = explore._context(ctx)
        self.assertIn("ANSWERED", text)
        self.assertIn("담당자에게 별도 전달함", text)

    def test_no_question_field_does_nothing(self):
        run, ctx = self._run_and_ctx()
        ctx.llm = _FakeLLM([{"proposals": []}])
        explore.propose(ctx)
        self.assertEqual(self._questions(run["id"]), [])


if __name__ == "__main__":
    unittest.main()
