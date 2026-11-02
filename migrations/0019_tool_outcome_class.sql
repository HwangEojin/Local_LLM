-- 0019: 실패 귀속(Observer/G5 패턴, artex guard.go 의 postToolUse 참고, CLAUDE.md §6/§8, 2026-11).
-- 도구 호출 결과를 ok/blocked/error 로 분류해 저장한다 — 성공/실패 2분법으로는 구분 못 하던 "막힘"(WAF/레이트리밋/
-- 캡차 등 대상측 차단)과 "일반 오류"를 나눠, Planner 가 같은 도구를 그대로 반복하지 않고 다른 전략을 택할 근거로 쓴다.
-- NULL 허용: 기존 행과 분류 대상이 아닌 SKIPPED(정책 거부) 행은 분류하지 않는다.

ALTER TABLE tool_invocations ADD COLUMN outcome_class text
    CHECK (outcome_class IS NULL OR outcome_class IN ('ok', 'blocked', 'error'));
