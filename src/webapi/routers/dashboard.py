"""대시보드(ARTEX 대시보드 대응): 실행 하나가 아니라 전체 진단 현황을 한 번에 본다.

새 진단 로직 없음 — runs/findings/evidence/assets 를 여러 실행에 걸쳐 집계만 한다(테스트 진단은
환경 화면과 같은 기준으로 제외: operator 가 'pytest' 로 시작).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import current_user, get_conn

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

_SCOPE = "r.deleted_at IS NULL AND r.operator NOT ILIKE 'pytest%%'"


@router.get("")
def get_dashboard(conn=Depends(get_conn), user: str = Depends(current_user)):
    by_status = conn.execute(
        f"SELECT status, count(*) AS n FROM runs r WHERE {_SCOPE} GROUP BY status").fetchall()
    by_finding_status = conn.execute(
        f"SELECT f.status, count(*) AS n FROM findings f JOIN runs r ON r.id=f.run_id "
        f"WHERE {_SCOPE} GROUP BY f.status").fetchall()
    by_severity = conn.execute(
        f"SELECT f.severity, count(*) AS n FROM findings f JOIN runs r ON r.id=f.run_id "
        f"WHERE {_SCOPE} AND f.status='CONFIRMED' GROUP BY f.severity").fetchall()
    totals = conn.execute(
        f"SELECT (SELECT count(*) FROM runs r WHERE {_SCOPE}) AS runs, "
        f"(SELECT count(*) FROM evidence e JOIN runs r ON r.id=e.run_id WHERE {_SCOPE} AND e.success) AS evidence, "
        f"(SELECT count(*) FROM findings f JOIN runs r ON r.id=f.run_id WHERE {_SCOPE}) AS findings, "
        f"(SELECT count(DISTINCT a.id) FROM assets a JOIN run_assets ra ON ra.asset_id=a.id "
        f" JOIN runs r ON r.id=ra.run_id WHERE {_SCOPE}) AS assets").fetchone()
    recent = conn.execute(
        f"SELECT r.id, r.run_code, r.status, r.operator, r.created_at, "
        f"(SELECT count(*) FROM findings f WHERE f.run_id=r.id) AS findings_count "
        f"FROM runs r WHERE {_SCOPE} ORDER BY r.created_at DESC LIMIT 10").fetchall()
    return {"by_status": by_status, "by_finding_status": by_finding_status, "by_severity": by_severity,
            "totals": totals, "recent_runs": recent}
