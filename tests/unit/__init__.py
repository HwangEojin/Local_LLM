"""단위 테스트 격리: 테스트가 실제 진단 DB·증적 폴더에 흔적을 남기지 않게 한다.

이 패키지가 import 될 때(unittest discover, 개별 모듈 실행 모두) 한 번 실행된다.
- 같은 PostgreSQL 안에 전용 스키마 `workbench_test` 를 새로 만들고 마이그레이션을 적용한 뒤, 모든 테스트가 쓰는
  환경 변수 WORKBENCH_PG_DSN 을 그 스키마로 돌린다(search_path). 실제 `public` 스키마의 진단·감사 로그·증적은
  테스트가 아무리 돌아도 늘지 않는다.
- 증적 파일과 설정 사본은 임시 홈(AUDIT_HOME)에 둬서 data/evidence 도 건드리지 않는다.
- 끝나면(atexit) 접속을 정리하고 `workbench_test` 스키마와 임시 홈을 지운다. 이 이름의 스키마는 이 파일이 만든 것뿐이라 지워도
  실제 데이터와 무관하다. 비정상 종료로 남아 있으면 다음 실행 시작 때 다시 지우고 만든다.
- DB 에 연결할 수 없으면 아무것도 바꾸지 않는다(각 테스트가 기존처럼 스스로 건너뜀).
- 실제 DB 로 돌리고 싶을 때만 WORKBENCH_TEST_USE_REAL_DB=1 (진단이 쌓이는 것을 감수해야 한다).
"""
import atexit
import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "workbench_test"  # 이 이름의 스키마만 만들고 지운다
APP_NAME = "workbench_test"  # 남은 접속을 찾아 끊기 위한 표식


def _with_params(dsn: str) -> str:
    """접속 문자열에 search_path(전용 스키마)와 application_name 을 붙인다."""
    if dsn.startswith(("postgresql://", "postgres://")):
        sep = "&" if "?" in dsn else "?"
        return f"{dsn}{sep}options=-csearch_path%3D{SCHEMA}&application_name={APP_NAME}"
    return f"{dsn} options='-c search_path={SCHEMA}' application_name={APP_NAME}"


def _isolate() -> None:
    if os.environ.get("WORKBENCH_TEST_USE_REAL_DB") == "1":
        return
    try:
        from diagnostic_workbench import config, db
        config._load_dotenv(ROOT / ".env")  # DSN·PGPASSWORD 를 .env 에서 읽는다(임시 홈에는 .env 가 없으므로 먼저)
        real_dsn = os.environ.get(config.DSN_ENV, "").strip()
        if not real_dsn:
            return
        admin = db.connect(real_dsn)
        admin.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        admin.execute(f"CREATE SCHEMA {SCHEMA}")
        admin.commit()
        admin.close()
        test_dsn = _with_params(real_dsn)
        conn = db.connect(test_dsn)
        db.migrate(conn, ROOT / "migrations")
        conn.close()
        # 일부 테스트는 이미 가져와 둔 체크리스트(참고 데이터)를 전제로 한다. 실제 DB 에서 읽어 와 복사만 한다(진단 데이터는 복사 안 함).
        seed = db.connect(real_dsn)
        for table in ("checklist_versions", "checklist_items"):  # 외래 키 순서
            seed.execute(f"INSERT INTO {SCHEMA}.{table} SELECT * FROM public.{table}")
        seed.commit()
        seed.close()
    except Exception:  # DB/설정 문제: 격리하지 않고 두면 각 테스트가 기존 방식대로 건너뛴다
        return

    home = Path(tempfile.mkdtemp(prefix="workbench-test-home-"))
    for name in ("config", "checklist", "templates"):
        if (ROOT / name).is_dir():
            shutil.copytree(ROOT / name, home / name)
    (home / "data").mkdir()
    os.environ[config.DSN_ENV] = test_dsn
    os.environ["AUDIT_HOME"] = str(home)

    def cleanup() -> None:
        try:
            admin = db.connect(real_dsn)
            admin.autocommit = True
            admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                          "WHERE application_name = %s AND pid <> pg_backend_pid()", (APP_NAME,))
            admin.execute("SET lock_timeout = '5s'")
            admin.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
            admin.close()
        except Exception:
            pass  # 남아도 다음 실행 시작 때 지워진다
        shutil.rmtree(home, ignore_errors=True)

    atexit.register(cleanup)


_isolate()
