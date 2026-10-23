-- 0011: 진단 소프트 삭제. 행은 지우지 않고 '삭제됨' 표시만 한다.
-- 이유: audit_events/evidence/finding_review_history/verdict_history 는 수정·삭제가 금지된 추가 전용 테이블이고
-- (0001, 0006) 14개 테이블이 runs(id)를 외래 키로 참조한다. 하드 삭제는 이 보호를 끄지 않고는 불가능하고,
-- CLAUDE.md §8/§11 의 감사 로그·증적 보존 원칙에도 어긋난다. 삭제된 진단은 기본 목록에서 숨기고 복원할 수 있다.

ALTER TABLE runs ADD COLUMN deleted_at    timestamptz;
ALTER TABLE runs ADD COLUMN deleted_by    text;
ALTER TABLE runs ADD COLUMN delete_reason text;

-- 삭제되지 않은 진단 목록이 주 조회 경로라 부분 인덱스로 둔다.
CREATE INDEX runs_active_created_idx ON runs (created_at DESC) WHERE deleted_at IS NULL;
