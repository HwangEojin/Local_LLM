-- 0014: 자산에 회사(company) 태그를 선택적으로 붙인다. 이 시점에는 assets 의 UNIQUE(kind, value) 가
-- 그대로 전역이라 보고·필터링용 태그일 뿐이다 — 실제 격리(같은 호스트명을 회사별로 다른 자산으로
-- 분리)는 0015 에서 UNIQUE(company, kind, value) 로 바뀌며 적용된다.
ALTER TABLE assets ADD COLUMN company text;
