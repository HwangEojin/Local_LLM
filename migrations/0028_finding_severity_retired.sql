-- D7: finding 심각도 개념 폐지. 값은 보존하고 새 행에는 쓰지 않는다(체크리스트 항목 원문 위험도는 별개, 유지).
ALTER TABLE findings ALTER COLUMN severity DROP NOT NULL;
COMMENT ON COLUMN findings.severity IS 'retired 2026-11 (D7): 읽기 전용 과거 값. 새 finding 은 NULL';
