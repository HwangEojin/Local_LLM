"""실행 간 비교(G6, UPGRADE_PLAN.md; H2 로 판정 규칙 수정) — 신규/지속/재발/미재현(조치 추정)/미확인/기각/비교 불가.

"관찰되지 않음"은 "고쳐졌음"의 증거가 아니다(CLAUDE.md §3/§15, _resolve_retest 의 원칙과 동일) — 그래서
미재현(조치 추정)과 미확인을 반드시 분리한다. 미재현(조치 추정)은 다음 둘 중 하나일 때만이다.

1. 그 finding 에 FIXED 재검증 이력이 있다.
2. head 실행이 **같은 분류의 같은 지점**(같은 v2 fingerprint — 자산·분류·메서드·경로·매개변수)을 실제로 다시
   보내 결정론적 신호 판정까지 했는데(`exploration_signal_checked`, matched=false) 신호가 안 떴다.

H2(2026-11-17) 이전에는 분류가 빠진 locator_hash 로 "다시 때림"을 판정해서, 같은 경로에 다른 분류의 페이로드를
하나 보낸 것만으로 조치 추정이 됐다. 또 finding 상태를 보지 않아 기각(REJECTED)된 오탐이 신규·지속으로 셈해졌다.

비교 대상은 `duplicate_of IS NULL AND status <> 'REJECTED'` 이고 현재 fingerprint 버전(v2)인 행뿐이다. 기각된
행은 `rejected` 묶음(base·head 각각)으로 따로 보여 준다. fingerprint 가 없거나 옛 버전(v1)인 행은 비교 불가.

fingerprint 는 asset_id 를 포함해 계산되고(fingerprint.py) 자산은 실행을 가로질러 공유되는 전역 행이라, 서로 다른
실행의 finding 을 fingerprint 로 직접 비교할 수 있다(G1 의 중복 제거 자체는 D3b 로 같은 실행 안에서만 합친다).
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


def _rechecked_without_signal(conn, run_id) -> set[str]:
    """이 실행에서 결정론적 신호 판정을 했는데 신호가 안 뜬 지점들의 v2 fingerprint 집합.

    판정 기록(`exploration_signal_checked`)은 fact 노드를 가리키고, fact 는 `exploration_anchors` 로 그 증거에
    이어진다. 판정한 분류(details.vuln_class)와 그 증거의 원 인자로 fingerprint 를 다시 계산한다 — 분류가 다르거나
    매개변수가 다르면 다른 지점이다. 한 번 훑어 집합으로 만든다(finding 마다 증거 전체를 다시 계산하지 않음)."""
    rows = conn.execute(
        "SELECT e.asset_id, e.args, ae.details->>'vuln_class' AS vuln_class "
        "FROM audit_events ae "
        "JOIN exploration_anchors ea ON ea.node_id = (ae.details->>'fact')::uuid AND ea.evidence_id IS NOT NULL "
        "JOIN evidence e ON e.id = ea.evidence_id AND e.success "
        "WHERE ae.run_id=%s AND ae.event='exploration_signal_checked' "
        "AND (ae.details->>'matched')::boolean = false", (run_id,)).fetchall()
    out = set()
    for r in rows:
        fp, _, _ = fingerprint.compute(r["asset_id"], r["vuln_class"], r["args"] or {})
        if fp is not None:
            out.add(fp)
    return out


def _comparable(f) -> bool:
    return bool(f["fingerprint"]) and f["fingerprint_v"] == fingerprint.FINGERPRINT_VERSION


def compare_runs(conn, base_run_id, head_run_id) -> dict:
    base_findings = conn.execute(
        "SELECT * FROM findings WHERE run_id=%s AND duplicate_of IS NULL ORDER BY created_at", (base_run_id,)).fetchall()
    head_findings = conn.execute(
        "SELECT * FROM findings WHERE run_id=%s AND duplicate_of IS NULL ORDER BY created_at", (head_run_id,)).fetchall()

    rejected = {"base": [f for f in base_findings if f["status"] == "REJECTED"],
                "head": [f for f in head_findings if f["status"] == "REJECTED"]}
    base_live = [f for f in base_findings if f["status"] != "REJECTED"]
    head_live = [f for f in head_findings if f["status"] != "REJECTED"]
    incomparable = [f for f in base_live + head_live if not _comparable(f)]
    base_by_fp = {f["fingerprint"]: f for f in base_live if _comparable(f)}
    head_by_fp = {f["fingerprint"]: f for f in head_live if _comparable(f)}
    head_rejected_fps = {f["fingerprint"] for f in rejected["head"] if _comparable(f)}

    new_, persistent, recurred, mitigated_presumed, unconfirmed = [], [], [], [], []

    for fp, hf in head_by_fp.items():
        bf = base_by_fp.get(fp)
        if bf is None:
            new_.append(hf)
        elif _has_fixed_retest(conn, bf["id"]):
            recurred.append({"base": bf, "head": hf})
        else:
            persistent.append({"base": bf, "head": hf})

    rechecked = _rechecked_without_signal(conn, head_run_id) if base_by_fp else set()
    for fp, bf in base_by_fp.items():
        if fp in head_by_fp:
            continue
        # head 에서 같은 지점이 다시 관찰됐지만 사람이 기각했다 — 고쳐졌다는 증거가 아니므로 조치 추정으로 보지 않는다.
        if fp not in head_rejected_fps and (_has_fixed_retest(conn, bf["id"]) or fp in rechecked):
            mitigated_presumed.append(bf)
        else:
            unconfirmed.append(bf)

    return {
        "new": new_,
        "persistent": persistent,
        "recurred": recurred,
        "mitigated_presumed": mitigated_presumed,
        "unconfirmed": unconfirmed,
        "rejected": rejected,
        "incomparable": incomparable,
    }
