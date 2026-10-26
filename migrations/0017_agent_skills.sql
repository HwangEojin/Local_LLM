-- 0017: 에이전트 스킬. 운영자가 쓰는 재사용 지침 묶음을 역할별로 붙인다. 스킬은 해당 역할 시스템 프롬프트 뒤에
-- 덧붙는 '문구'일 뿐이라 도구 실행 권한·정책(policy.decide)은 바꾸지 않는다. 행이 없으면 동작 변경 없음.

CREATE TABLE agent_skills (
    name         text PRIMARY KEY CHECK (name ~ '^[A-Za-z0-9_.:-]{1,80}$'),
    description  text NOT NULL DEFAULT '',
    instructions text NOT NULL CHECK (length(instructions) BETWEEN 1 AND 4000),
    roles        text[] NOT NULL DEFAULT '{}',
    enabled      boolean NOT NULL DEFAULT true,
    updated_by   text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
