-- G2: LLM 판정 교차 검증 게이트(UPGRADE_PLAN.md).
-- 실행 생성 시 고정(autonomy·llm_profile_name 과 같은 방식). NULL = 교차 검증 안 함(D4: 현행 동작).
ALTER TABLE runs ADD COLUMN verifier_model text;

-- 단일 모델 제안 상태(사람 검토 대기)를 구분하는 판정 주체 값 추가.
ALTER TABLE findings DROP CONSTRAINT findings_verification_check;
ALTER TABLE findings ADD CONSTRAINT findings_verification_check CHECK (
    verification IN ('RULE_CONFIRMED', 'REVIEWER_CONFIRMED', 'LLM_CONFIRMED', 'LLM_PROPOSED'));

-- 모델별 판정 기록(추가 전용). LLM 성능 비교(판정 일치율)의 원천 데이터도 된다.
CREATE TABLE finding_llm_votes (
    id           bigserial PRIMARY KEY,
    finding_id   uuid NOT NULL REFERENCES findings(id),
    model        text NOT NULL,
    stage        text NOT NULL CHECK (stage IN ('proposer', 'verifier')),
    verdict      text NOT NULL CHECK (verdict IN ('CONFIRMS', 'REFUTES', 'INSUFFICIENT', 'ERROR')),
    rationale    text NOT NULL DEFAULT '',
    evidence_ids uuid[] NOT NULL DEFAULT '{}',
    model_call_id bigint REFERENCES model_calls(id),
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER finding_llm_votes_append_only BEFORE UPDATE OR DELETE ON finding_llm_votes
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
