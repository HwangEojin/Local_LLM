"""실행 간 비교(G6, UPGRADE_PLAN.md) — 신규/지속/재발/미재현(조치 추정)/미확인/비교 불가로 분류한다.

"관찰되지 않음"은 "고쳐졌음"의 증거가 아니다(CLAUDE.md §15 "확인하지 않은 것을 확인했다고 하지 않음",
_resolve_retest 의 원칙과 동일) — 그래서 미재현(조치 추정)과 미확인을 반드시 분리한다. 전자는 head
실행이 실제로 그 지점(같은 자산·메서드·경로·매개변수, fingerprint.locator_hash)을 다시 때렸는데도
신호가 안 떴거나, 그 finding 에 FIXED 재검증 이력이 있는 경우만이다. 후자는 head 가 그 지점을 아예
건드리지 않은 경우다.

fingerprint 는 asset_id 를 포함해 계산되고(fingerprint.py) 자산은 실행을 가로질러 공유되는 전역 행이라,
서로 다른 실행의 finding 을 fingerprint 로 직접 비교할 수 있다(G1 의 중복 제거 자체는 D3b 로 같은 실행
안에서만 합치지만, 비교는 그 fingerprint 값을 그대로 재사용한다).
"""
from __future__ import annotations

from . import fingerprint


def run_companies(conn, run_id) -> set[str]:
    """이 실행이 관찰한 자산들의 company 집합(빈 문자열 = 미지정)."""
    rows = conn.execute(
        "SELECT DISTINCT a.company FROM run_assets ra JOIN assets a ON a.id = ra.asset_id WHERE ra.run_id=%s",
        (run_id,)).fetchall()
    return {r["company"] for r in rows}


def _has_fixed_retest(conn, finding_id) -> bool:
    return conn.execute(
        "SELECT 1 FROM finding_review_history WHERE finding_id=%s AND actor='system:retest' "
        "AND reason LIKE '재검증 결과: FIXED%%' LIMIT 1", (finding_id,)).fetchone() is not None


def _head_hit_same_locator(conn, head_run_id, base_finding) -> bool:
    """head 실행이 base finding 과 같은 지점을 실제로 다시 때렸는지 — evidence.args 에서 그때그때
    locator_hash 를 다시 계산해 맞춰 본다(evidence 에는 locator_hash 를 따로 저장하지 않음)."""
    if not base_finding["locator_hash"]:
        return False
    rows = conn.execute("SELECT args FROM evidence WHERE run_id=%s AND asset_id=%s AND success",
                        (head_run_id, base_finding["asset_id"])).fetchall()
    for r in rows:
        _, loc_hash, _ = fingerprint.compute(base_finding["asset_id"], None, r["args"])
        if loc_hash == base_finding["locator_hash"]:
            return True
    return False


def compare_runs(conn, base_run_id, head_run_id) -> dict:
    base_findings = conn.execute(
        "SELECT * FROM findings WHERE run_id=%s AND duplicate_of IS NULL", (base_run_id,)).fetchall()
    head_findings = conn.execute(
        "SELECT * FROM findings WHERE run_id=%s AND duplicate_of IS NULL", (head_run_id,)).fetchall()

    base_by_fp = {f["fingerprint"]: f for f in base_findings if f["fingerprint"]}
    head_by_fp = {f["fingerprint"]: f for f in head_findings if f["fingerprint"]}
    incomparable = [f for f in base_findings if not f["fingerprint"]] + \
                   [f for f in head_findings if not f["fingerprint"]]

    new_, persistent, recurred, mitigated_presumed, unconfirmed = [], [], [], [], []

    for fp, hf in head_by_fp.items():
        bf = base_by_fp.get(fp)
        if bf is None:
            new_.append(hf)
        elif bf["status"] != "REJECTED" and _has_fixed_retest(conn, bf["id"]):
            recurred.append({"base": bf, "head": hf})
        else:
            persistent.append({"base": bf, "head": hf})

    for fp, bf in base_by_fp.items():
        if fp in head_by_fp:
            continue
        if _has_fixed_retest(conn, bf["id"]) or _head_hit_same_locator(conn, head_run_id, bf):
            mitigated_presumed.append(bf)
        else:
            unconfirmed.append(bf)

    return {
        "new": new_,
        "persistent": persistent,
        "recurred": recurred,
        "mitigated_presumed": mitigated_presumed,
        "unconfirmed": unconfirmed,
        "incomparable": incomparable,
    }
