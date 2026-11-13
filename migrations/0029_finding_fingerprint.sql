-- G1: finding fingerprint·중복 제거(UPGRADE_PLAN.md). 기존 행 삭제 없음 — 컬럼 추가 + 백필만.
ALTER TABLE findings ADD COLUMN vuln_class       text;          -- SQLI/XSS/.../OTHER, 체크리스트 finding 은 NULL
ALTER TABLE findings ADD COLUMN fingerprint      text;
ALTER TABLE findings ADD COLUMN locator          jsonb NOT NULL DEFAULT '{}';  -- {method, path, params} 표시용
ALTER TABLE findings ADD COLUMN occurrence_count integer NOT NULL DEFAULT 1;
ALTER TABLE findings ADD COLUMN last_seen_at     timestamptz NOT NULL DEFAULT now();
ALTER TABLE findings ADD COLUMN locator_hash     text;          -- D2 "중복 의심" 묶음
-- D3: 이 finding 을 뒷받침한 근거 종류 집합. 'RULE'(결정론적 신호) | 'LLM'(LLM 주장). 사람 확정은 상태 이력에 이미 남음.
ALTER TABLE findings ADD COLUMN evidence_kinds   text[] NOT NULL DEFAULT '{}';
UPDATE findings SET evidence_kinds = CASE verification
    WHEN 'RULE_CONFIRMED' THEN ARRAY['RULE'] WHEN 'LLM_CONFIRMED' THEN ARRAY['LLM'] ELSE '{}' END;

-- 백필: _create_finding 이 남긴 제목 형식("탐색 기반 발견: <CLASS>")에서만 vuln_class 를 복원한다.
-- fingerprint 는 백필하지 않는다 — 과거 행의 주입 지점을 확실히 복원할 수 없음(모르면 합치지 않음).
UPDATE findings SET vuln_class = substring(title FROM '^탐색 기반 발견: (\w+)$')
 WHERE run_checklist_item_id IS NULL AND title ~ '^탐색 기반 발견: \w+$';

CREATE UNIQUE INDEX findings_run_fingerprint_live
    ON findings (run_id, fingerprint) WHERE fingerprint IS NOT NULL AND duplicate_of IS NULL;
CREATE INDEX ON findings (fingerprint) WHERE fingerprint IS NOT NULL;   -- G6 실행 간 조회용
CREATE INDEX ON findings (run_id, locator_hash) WHERE locator_hash IS NOT NULL AND duplicate_of IS NULL;
