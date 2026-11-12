-- 0026: 커스텀 에이전트(ARTEX 스타일 자유 에이전트 생성, 2026-11-13). 5개 고정 역할(planner/worker/
-- analyst/goals/reporter)과 별개로, 운영자가 이름·프롬프트·모델·허용 도구를 자유롭게 정의해 무제한
-- 만들고 지울 수 있는 에이전트. 자기 실행 경로는 없다 — run_triggers(0027)에 연결됐을 때만 쓰인다.
CREATE TABLE custom_agents (
    id            bigserial PRIMARY KEY,
    name          text NOT NULL UNIQUE,
    system_prompt text NOT NULL,
    llm_profile   text,              -- null=이 실행의 기본 모델(ctx.llm) 사용
    allowed_tools text[] NOT NULL,   -- 이 에이전트가 고를 수 있는 도구만(최소 1개, 웹 API 레벨에서 검증)
    enabled       boolean NOT NULL DEFAULT true,
    created_by    text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
