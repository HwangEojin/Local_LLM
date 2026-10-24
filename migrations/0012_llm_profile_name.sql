-- 0012: 다중 LLM 프로파일(ARTEX llm_profiles 대응). runs.llm_provider(ollama/claude_cli, 0008) 는 그대로
-- 두고, 이름 있는 프로파일을 config/llm_profiles.json 에서 고를 수 있는 선택적 컬럼을 추가한다.
-- NULL 이면(기존 실행 전부, 그리고 프로파일을 안 고른 새 실행) 지금처럼 llm_provider + settings.json 의
-- 단일 ollama/claude_cli 블록을 그대로 쓴다 — 동작 변경 없음.
ALTER TABLE runs ADD COLUMN llm_profile_name text;
