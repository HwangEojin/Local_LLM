-- 0013: DB 로 편집 가능한 에이전트 프롬프트. 기본값은 여전히 코드에 있다 —
-- 이 테이블에 같은 이름의 행이 생기면 그 값이 기본값을 덮어쓴다(prompts.get). 행이 없으면(기본 상태)
-- 기존 동작과 완전히 같다.
CREATE TABLE agent_prompts (
    name       text PRIMARY KEY,
    text       text NOT NULL,
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
