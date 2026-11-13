-- 0020: LLM 호출 원문 기록(레퍼런스 프로젝트 llmrec 참고, CLAUDE.md §8 비밀값 마스킹 원칙 적용, 2026-11).
-- 지금까지 model_calls 는 토큰 수만 남겨서 "무엇을 물었고 뭐라고 답했는지"는 디버깅할 수 없었다.
-- request_text/response_text 는 저장 전에 audit.policy.mask_secrets 로 비밀값을 가린 뒤 넣는다(선택 컬럼 —
-- 기존 행은 NULL, 새 쓰기 경로 아님: explore.py/audit/local_model.py/audit/claude_cli_model.py 의 기존
-- record_model_call() 호출에 인자만 추가).

ALTER TABLE model_calls ADD COLUMN request_text text;
ALTER TABLE model_calls ADD COLUMN response_text text;
