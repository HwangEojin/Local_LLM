"""도구 호출 위험도 체계 삭제(CLAUDE.md §8, 2026-11) 이후, 인터셉트 규칙이 유일한 승인 게이트가 됐다
(UPGRADE_PLAN.md D. 리스크). 모든 비읽기(read_only=False) 도구가 config/intercept_rules.json 의 규칙에
걸리는지 확인한다 — 새 MCP 도구를 등록하고 규칙을 깜빡하면 그 도구는 승인 없이 바로 실행되므로, 이 테스트가
깜빡임을 잡아 준다. 새 진단 로직 없음(기존 레지스트리·규칙 조회만)."""
import unittest

from audit.models import ConfigError
from diagnostic_workbench import config, policy, runs


# 사용자가 '승인 없이 자동'으로 결정한 비읽기 도구. read_only=False 는 재시도 안전성(POST 를 다시 보내지 않음) 때문에
# 유지한다. 이 목록 밖의 도구가 규칙 없이 빠지면 여전히 이 테스트가 잡는다(깜빡임 방지라는 원래 목적은 그대로).
_AUTO_BY_DECISION = {"http.request": "UPGRADE_PLAN.md K-결정 KD1(2026-11-24): 모든 메서드 자동"}


class InterceptRulesCoverNonReadToolsTest(unittest.TestCase):
    def test_every_non_read_only_tool_matches_an_intercept_rule(self):
        try:
            cfg = config.load(None)
        except ConfigError as e:
            raise unittest.SkipTest(f"설정 불가: {e}") from None
        rules = cfg.intercept_rules
        reg = runs.registry(cfg)
        missing = sorted(name for name, spec in reg.items()
                         if not spec.read_only and name not in _AUTO_BY_DECISION
                         and policy._intercepted(rules, name, {}) is None)
        self.assertEqual(missing, [],
                         f"승인 없이 바로 실행되는 비읽기 도구: {missing} — "
                         "config/intercept_rules.json 에 규칙을 추가하세요")


if __name__ == "__main__":
    unittest.main()
