-- 0025: 역할별 "이름 있는 프로파일"(F3a 절충안, UPGRADE_PLAN.md). artex 처럼 완전 자유로운 에이전트 생성은
-- CLAUDE.md §6(Planner=제안, Python=검증을 역할 단위로 서술)을 재설계해야 해서, 고정 5역할은 그대로 두고
-- 역할마다 이름 붙은 변형(예: worker-aggressive/worker-cautious)을 여러 개 만들 수 있게만 한다.
-- prompt_overrides 는 그 역할이 쓰는 프롬프트 이름(예: worker_agent_system) -> 문구. 지정 안 한 프롬프트는
-- 전역 agent_prompts 오버라이드, 그것도 없으면 코드 기본값으로 그대로 떨어진다(기존 동작 그대로, 순수 추가).
CREATE TABLE agent_profiles (
    id               bigserial PRIMARY KEY,
    role             text NOT NULL,
    name             text NOT NULL,
    prompt_overrides jsonb NOT NULL DEFAULT '{}',
    llm_profile      text,  -- 선택: agent_models 에 이 역할 지정이 없을 때만 기본값으로 쓰임
    created_by       text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (role, name)
);

-- 실행이 역할별로 고른 프로파일 이름. {"worker": "worker-aggressive", ...}. 지정 안 한 역할은 기존처럼
-- 전역 agent_prompts/llm_agent_models 만 쓴다(기존 실행 전부 빈 객체 = 동작 변경 없음).
ALTER TABLE runs ADD COLUMN agent_profile_names jsonb NOT NULL DEFAULT '{}'::jsonb;
