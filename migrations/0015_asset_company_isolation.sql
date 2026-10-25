-- 0015: 자산 멀티테넌시를 태그에서 실제 격리로 바꾼다(ARTEX companies/company_scope 대응, 전체판).
-- 0014 에서는 company 가 보고용 태그일 뿐이라 UNIQUE(kind, value) 가 전역이라 같은 호스트명은
-- 회사가 달라도 한 자산으로 공유됐다. 이제 company 를 식별자의 일부로 만든다 — 같은 kind/value 라도
-- company 가 다르면 서로 다른 자산 행이다. company 가 없던 기존 자산은 ''(전역/미지정)으로 채운다.
UPDATE assets SET company = '' WHERE company IS NULL;
ALTER TABLE assets ALTER COLUMN company SET NOT NULL;
ALTER TABLE assets ALTER COLUMN company SET DEFAULT '';
ALTER TABLE assets DROP CONSTRAINT assets_kind_value_key;
ALTER TABLE assets ADD CONSTRAINT assets_company_kind_value_key UNIQUE (company, kind, value);
