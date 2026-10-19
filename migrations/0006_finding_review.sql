-- 0006: Finding(발견 사항) 검토 상태 체계와 사람의 검토 이력(CLAUDE.md §9/§10).
--
-- findings.status 는 지금까지 'OPEN' 고정값으로만 쓰였고(검증 없음), 사람이 확정/기각할 방법이 없었다.
-- CLAUDE.md §9 의 권장 상태(CANDIDATE/NEEDS_REVIEW/CONFIRMED/REJECTED/INCONCLUSIVE/NOT_TESTED)로 전환한다.
--
-- 기존 값 전환: verification='REVIEWER_CONFIRMED' 는 cmd_review 에서 이미 사람이 확인한 FAIL 판정이므로
-- CONFIRMED로, 'RULE_CONFIRMED'(결정론적 규칙만으로 확정, 아직 사람이 보지 않음)는 보고서에 이미 적던
-- "사람 최종 검토 전" 그대로 CANDIDATE 로 옮긴다. 둘 다 기존 findings 행과 finding_evidence 는 그대로 둔다.
UPDATE findings SET status = CASE WHEN verification = 'REVIEWER_CONFIRMED' THEN 'CONFIRMED' ELSE 'CANDIDATE' END
WHERE status = 'OPEN';

ALTER TABLE findings ALTER COLUMN status SET DEFAULT 'CANDIDATE';
ALTER TABLE findings ADD CONSTRAINT findings_status_check CHECK (
    status IN ('CANDIDATE', 'NEEDS_REVIEW', 'CONFIRMED', 'REJECTED', 'INCONCLUSIVE', 'NOT_TESTED'));

ALTER TABLE findings ADD COLUMN reviewed_by    text;
ALTER TABLE findings ADD COLUMN reviewed_at    timestamptz;
ALTER TABLE findings ADD COLUMN review_reason  text;

-- verdict_history 와 같은 패턴: 사람의 판정 변경으로 증거·이력이 사라지지 않게 추가 전용으로 보존한다.
CREATE TABLE finding_review_history (
    id          bigserial PRIMARY KEY,
    finding_id  uuid NOT NULL REFERENCES findings(id),
    old_status  text,
    new_status  text NOT NULL,
    reason      text NOT NULL,
    actor       text NOT NULL,
    evidence_ids uuid[] NOT NULL DEFAULT '{}',
    changed_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON finding_review_history (finding_id);
CREATE TRIGGER finding_review_history_append_only BEFORE UPDATE OR DELETE ON finding_review_history
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
