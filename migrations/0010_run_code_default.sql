-- 0010: runs 에 run_code 없이 직접 INSERT 해도(테스트, 수동 SQL) 진단 ID 가 채워지게 한다.
-- 앱의 create_run 은 대상 약어를 넣은 값을 직접 지정하므로 이 트리거는 그 값을 덮어쓰지 않는다.
-- 직접 INSERT 시점에는 run_assets 가 아직 없어 약어를 알 수 없으므로 'RUN-<YYYYMMDD>-<순번>' 으로 채운다.

CREATE FUNCTION runs_fill_run_code() RETURNS trigger AS $$
DECLARE
    d text := to_char(now() AT TIME ZONE 'Asia/Seoul', 'YYYYMMDD');
    n int;
BEGIN
    IF NEW.run_code IS NULL THEN
        PERFORM pg_advisory_xact_lock(hashtext('run_code'));
        SELECT coalesce(max(substring(run_code from '-(\d+)$')::int), 0) + 1 INTO n
          FROM runs WHERE run_code LIKE 'RUN-' || d || '-%';
        NEW.run_code := 'RUN-' || d || '-' || lpad(n::text, 2, '0');
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER runs_fill_run_code BEFORE INSERT ON runs
    FOR EACH ROW EXECUTE FUNCTION runs_fill_run_code();
