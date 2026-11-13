"""대시보드: 실행 하나가 아니라 전체 진단 현황을 한 번에 본다.

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
    totals_and_recent = conn.execute(
        f"SELECT (SELECT count(*) FROM runs r WHERE {_SCOPE}) AS runs, "
        f"(SELECT count(*) FROM evidence e JOIN runs r ON r.id=e.run_id WHERE {_SCOPE} AND e.success) AS evidence, "
        f"(SELECT count(*) FROM findings f JOIN runs r ON r.id=f.run_id WHERE {_SCOPE}) AS findings, "
        f"(SELECT count(DISTINCT a.id) FROM assets a JOIN run_assets ra ON ra.asset_id=a.id "
        f" JOIN runs r ON r.id=ra.run_id WHERE {_SCOPE}) AS assets, "
        f"(SELECT jsonb_agg(row_to_json(x)) FROM (SELECT r.id, r.run_code, r.status, r.operator, r.created_at, "
        f"   (SELECT count(*) FROM findings f WHERE f.run_id=r.id) AS findings_count "
        f"  FROM runs r WHERE {_SCOPE} ORDER BY r.created_at DESC LIMIT 10) x) AS recent_runs"
    ).fetchone()
    recent_runs = totals_and_recent.pop("recent_runs") or []
    # LLM 토큰 사용량(B3, UPGRADE_PLAN.md) — model_calls 는 explore.py 가 호출마다 남기는 추가 전용 기록
    # (토큰 수를 모델이 보고 안 하면 token_source='estimated'/'unavailable', 합계는 그 행을 단순히 더함).
    token_daily = conn.execute(
        f"SELECT date(mc.ts) AS day, sum(coalesce(mc.prompt_tokens,0) + coalesce(mc.completion_tokens,0)) AS tokens, "
        f"count(*) AS calls FROM model_calls mc JOIN runs r ON r.id=mc.run_id WHERE {_SCOPE} "
        "AND mc.ts > now() - interval '14 days' GROUP BY 1 ORDER BY 1").fetchall()
    token_totals = conn.execute(
        f"SELECT count(*) AS calls, sum(coalesce(mc.prompt_tokens,0) + coalesce(mc.completion_tokens,0)) AS tokens, "
        f"count(*) FILTER (WHERE NOT mc.ok) AS failed FROM model_calls mc JOIN runs r ON r.id=mc.run_id "
        f"WHERE {_SCOPE}").fetchone()
    return {"by_status": by_status, "totals": totals_and_recent,
            "recent_runs": recent_runs, "token_daily": token_daily, "token_totals": token_totals}
