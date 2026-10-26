-- 0016: 에이전트 역할별 모델 지정. {"worker": "ollama:qwen2.5:3b", "reporter": "내-프로파일", ...}
-- 값은 config/llm_profiles.json 의 프로파일 이름 또는 'ollama:<설치된 모델명>'. 지정 안 한 역할은
-- 기존처럼 runs.llm_profile_name / llm_provider 의 실행 기본 모델을 쓴다(기존 행은 빈 객체 = 동작 변경 없음).

ALTER TABLE runs ADD COLUMN llm_agent_models jsonb NOT NULL DEFAULT '{}'::jsonb;
