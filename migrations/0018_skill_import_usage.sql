-- 0018: 스킬 가져오기(SKILL.md/ZIP) 메타데이터와 사용 기록.
-- license/source 는 가져온 SKILL.md 의 frontmatter 와 출처를 남기는 표시용 컬럼이다(동작에는 영향 없음).
-- skill_usage 는 어떤 스킬이 어느 진단·역할의 프롬프트에 실제로 덧붙었는지 센다(진단·스킬·역할당 한 행, 호출 수 누적).
-- artex 의 skill_usage 처럼 외래 키를 걸지 않는다 — 진단이나 스킬이 지워져도 기록은 남는다.

ALTER TABLE agent_skills ADD COLUMN license text NOT NULL DEFAULT '';
ALTER TABLE agent_skills ADD COLUMN source  text NOT NULL DEFAULT 'manual';

CREATE TABLE skill_usage (
    run_id    uuid        NOT NULL,
    skill     text        NOT NULL,
    role      text        NOT NULL,
    calls     integer     NOT NULL DEFAULT 1,
    first_at  timestamptz NOT NULL DEFAULT now(),
    last_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, skill, role)
);
CREATE INDEX skill_usage_skill_idx ON skill_usage (skill);
