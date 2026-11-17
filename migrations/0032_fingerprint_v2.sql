-- 0032: H1(UPGRADE_PLAN.md) — fingerprint 계산 규칙 v2(실제 HTTP 매개변수 이름 기준).
-- 기존 행은 v1 규칙(도구 인자 키 이름 기준)으로 계산돼 있어 v2 와 섞어 비교·병합하면 안 된다. 값은 지우거나
-- 다시 계산하지 않고 버전만 표시한다(HD2: 자동 재계산 안 함 — `finding refingerprint` 로 사람이 정리).
ALTER TABLE findings ADD COLUMN fingerprint_v smallint NOT NULL DEFAULT 1;

-- 자동 병합의 유일성 기준에 버전을 넣는다(같은 실행 안에서도 v1 행과 v2 행은 서로 병합 대상이 아님).
DROP INDEX findings_run_fingerprint_live;
CREATE UNIQUE INDEX findings_run_fingerprint_live
    ON findings (run_id, fingerprint_v, fingerprint) WHERE fingerprint IS NOT NULL AND duplicate_of IS NULL;
