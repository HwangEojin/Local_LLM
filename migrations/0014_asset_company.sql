-- 0014: 자산에 회사(company) 태그를 선택적으로 붙인다(ARTEX companies/company_scope 대응, 축소판).
-- 전체 격리(같은 호스트명을 회사별로 다른 자산으로 분리)는 아니다 — assets 의 UNIQUE(kind, value) 는
-- 그대로 전역이라, 같은 값의 자산은 여전히 하나로 공유된다. 보고·필터링을 위한 태그일 뿐이다.
ALTER TABLE assets ADD COLUMN company text;
