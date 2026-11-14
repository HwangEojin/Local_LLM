-- 0031: G3(검토자 편집, UPGRADE_PLAN.md) — CLAUDE.md §10 미구현 요구사항. 편집 대상은 title/rationale/
-- remediation 세 필드뿐(심각도는 G-S로 개념 자체가 없어졌고, 영향도는 D5로 만들지 않음). 원문은 지우지
-- 않고 바뀐 필드마다 한 행씩 추가 전용으로 남긴다.
CREATE TABLE finding_revisions (
    id          bigserial PRIMARY KEY,
    finding_id  uuid NOT NULL REFERENCES findings(id),
    field       text NOT NULL CHECK (field IN ('title', 'rationale', 'remediation')),
    old_value   text NOT NULL,
    new_value   text NOT NULL,
    reason      text NOT NULL,
    actor       text NOT NULL,
    changed_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON finding_revisions (finding_id);
CREATE TRIGGER finding_revisions_append_only BEFORE UPDATE OR DELETE ON finding_revisions
    FOR EACH ROW EXECUTE FUNCTION forbid_change();

-- 사람이 고친 finding 은 reporting._polish_finding_text() 가 다시 다듬지 않는다(사람이 확정한 문장 보존).
ALTER TABLE findings ADD COLUMN human_edited boolean NOT NULL DEFAULT false;
