-- 0009: 사람이 읽고 외울 수 있는 진단 ID(run_code). 형식: <대상약어>-<YYYYMMDD>-<일련번호>, 예: BASE-20261023-01
-- 기본키(uuid)와 모든 외래 키는 그대로 두고 표시/조회용 컬럼만 추가한다(파괴적 변경 없음).
-- 기존 행은 run_assets 의 첫 대상 이름과 생성일(KST)로 채운다. 대상이 없으면 약어는 'RUN'.

ALTER TABLE runs ADD COLUMN run_code text;

UPDATE runs r SET run_code = x.code
FROM (
    SELECT id,
           -- lpad 폭 2 는 같은 abbr+날짜에 100건 이상 쌓이면 자릿수가 모자라 통째로 잘려서(lpad 는
           -- 길이 초과 시 truncate) 서로 다른 일련번호가 같은 코드로 겹친다(실제로 겪음: rn=100/101 이
           -- 둘 다 '10' 이 됨) — 4자리(최대 9999건/일)로 넉넉히 늘림.
           abbr || '-' || d || '-' || lpad(row_number() OVER (PARTITION BY abbr, d ORDER BY created_at, id)::text, 4, '0') AS code
    FROM (
        SELECT r2.id, r2.created_at,
               to_char(r2.created_at AT TIME ZONE 'Asia/Seoul', 'YYYYMMDD') AS d,
               coalesce(nullif(upper(left(split_part(regexp_replace(
                   (SELECT ra.target_id FROM run_assets ra WHERE ra.run_id = r2.id ORDER BY ra.target_id LIMIT 1),
                   '[^A-Za-z0-9]+', ' ', 'g'), ' ', 1), 4)), ''), 'RUN') AS abbr
        FROM runs r2
    ) s
) x
WHERE r.id = x.id;

ALTER TABLE runs ALTER COLUMN run_code SET NOT NULL;
ALTER TABLE runs ADD CONSTRAINT runs_run_code_key UNIQUE (run_code);
