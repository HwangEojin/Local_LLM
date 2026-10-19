-- 0005: 체크리스트를 실행의 필수 조건에서 분리한다(CLAUDE.md §3.2/§13 — 체크리스트 없이도 자율 진단을
-- 시작할 수 있어야 한다). checklist_version_id 가 NULL 인 실행은 체크리스트 항목 없이 자율 탐색만 한다.
ALTER TABLE runs ALTER COLUMN checklist_version_id DROP NOT NULL;
