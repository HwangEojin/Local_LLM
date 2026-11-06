-- 0022: 역할별 기본 LLM 프로파일(F3a, UPGRADE_PLAN.md). 새 진단 화면(/runs/new)의 "에이전트별 모델"
-- 칸을 채워 주는 기본값일 뿐이다 — 이미 시작된 진단은 runs.llm_agent_models 에 박힌 값을 그대로 쓰므로
-- (진단 시작 후에는 바꿀 수 없음, cli.py 의 run create 설명과 동일한 원칙) 여기 값을 바꿔도 실행 중 진단에는
-- 영향이 없다.
CREATE TABLE agent_role_defaults (
    role        text PRIMARY KEY,
    llm_profile text NOT NULL,
    updated_by  text NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
