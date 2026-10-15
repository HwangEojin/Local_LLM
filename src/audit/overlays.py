"""자동 점검 (A) 점검 규칙 오버레이: 원문과 분리된 (checklist_id, check_id) -> 규칙 등록.

원본 체크리스트 항목의 pass_criteria/fail_criteria 는 건드리지 않는다. 오버레이는 항목 제목이 가리키는
표준 취약점 클래스에 대한 업계 공인 기법일 뿐이며, 그 사실을 판정 사유에 항상 남긴다(CLAUDE.md §7).
"""
from __future__ import annotations

import re
from pathlib import Path

from . import schemas
from .config import load_json
from .models import ConfigError

NOTE = "오버레이(업계 표준 기법) 기준, 원문 판단기준 아님"


def load_overlays(path: Path) -> dict[tuple[str, str], dict]:
    """path 가 없으면 빈 딕셔너리(오버레이 없음)."""
    if not Path(path).exists():
        return {}
    data = load_json(path, schemas.RULE_OVERLAYS)
    by_key: dict[tuple[str, str], dict] = {}
    for ov in data["overlays"]:
        key = (ov["checklist_id"], ov["check_id"])
        if key in by_key:
            raise ConfigError(f"{path}: {key} 오버레이가 중복됨")
        req_ids = [r["id"] for r in ov["evidence_requirements"]]
        if len(set(req_ids)) != len(req_ids):
            raise ConfigError(f"{path}: {key} 증거 요구사항 id 중복")
        if ov["rule"]["evidence"] not in req_ids:
            raise ConfigError(f"{path}: {key} 규칙이 알 수 없는 증거 '{ov['rule']['evidence']}' 를 참조함")
        try:
            re.compile(ov["rule"]["pattern"])
        except re.error as e:
            raise ConfigError(f"{path}: {key} 잘못된 정규식: {e}") from None
        by_key[key] = ov
    return by_key


def apply(item: dict, overlay: dict) -> dict:
    """오버레이가 적용된 실행용 항목 사본을 반환한다. 원본 item 과 원본 파일은 바뀌지 않는다.

    pass_criteria/fail_criteria/procedure 등 원문 필드는 그대로 둔다 — 판정은 verification_method.rule 과
    evidence_requirements 만 오버레이 것으로 바꿔서 내린다.
    """
    merged = dict(item)
    merged["verification_method"] = {"type": "rule", "rule": overlay["rule"]}
    merged["evidence_requirements"] = overlay["evidence_requirements"]
    # item_status 는 '원문 기준이 완성됐는가'를 뜻하지 않게 된다: 오버레이가 독립적인 판정 근거를 제공하므로
    # 실행 엔진(기존 calls()/decide())이 이 항목을 계속 사람 검토로 미루지 않도록 complete 로 둔다.
    merged["item_status"] = "complete"
    merged["overlay"] = {"basis": overlay["basis"], "rule_version": overlay["rule_version"], "note": NOTE}
    return merged


def automation_level(overlay: dict) -> str:
    return overlay.get("automation_level", "AUTOMATABLE")
