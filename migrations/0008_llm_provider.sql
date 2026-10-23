-- 0008: 실행별 LLM 제공자 선택. 기존에는 WORKBENCH_LLM_PROVIDER 환경 변수로 백엔드 프로세스
-- 전체에 적용됐는데(웹 UI에서는 런마다 고를 방법이 없었음), runs 테이블 컬럼으로 바꿔 생성 시점에
-- 정하고(scope/autonomy 와 같은 방식으로 고정) 이후 바꾸지 않는다.
--   ollama    : 기본값. 로컬 Ollama.
--   claude_cli: 임시 테스트용 — 이 PC 에 로그인된 claude CLI(Claude Code 구독) 호출.
ALTER TABLE runs ADD COLUMN llm_provider text NOT NULL DEFAULT 'ollama'
    CHECK (llm_provider IN ('ollama', 'claude_cli'));
