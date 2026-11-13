"""Finding 확정(CONFIRMED) 알림: 등록된 웹훅으로 POST 한다(운영 편의 기능).

알림은 안전 통제가 아니라 부수 효과다 — 전송이 실패해도 판정이나 실행을 막지 않고 audit_events 에만
기록한다. 채널은 운영자가 config/notify.json 에 직접 적는 값이고(LLM 이나 대상 응답이 못 바꿈) 그 값
자체의 유효성은 다른 config 파일(mcp_servers.json)과 같은 수준으로 운영자 책임이다 — 별도 SSRF 검사는
하지 않는다.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from audit import schemas
from audit.config import load_json

from . import db

_TIMEOUT_S = 5


def _read(path: Path) -> list[dict]:
    return load_json(path, schemas.NOTIFY_CHANNELS)["channels"] if Path(path).exists() else []


def load_all_channels(path: Path) -> list[dict]:
    """공유 파일(notify.json)과 이 PC 전용 파일(notify.local.json, 웹이 씀, Git 제외)의 채널을 합친다.
    같은 이름이면 이 PC 전용 항목이 이긴다. 각 항목에 source("shared"|"local")를 붙인다. 꺼진 채널도 포함."""
    path = Path(path)  # 공유 파일 경로. 이 PC 전용 파일은 같은 폴더의 notify.local.json
    merged = {c["name"]: {**c, "source": "shared"} for c in _read(path)}
    merged.update({c["name"]: {**c, "source": "local"} for c in _read(path.with_name("notify.local.json"))})
    return list(merged.values())


def load_channels(path: Path) -> list[dict]:
    """켜진 채널만. 파일이 없으면 빈 목록(알림 없음)."""
    return [c for c in load_all_channels(path) if c.get("enabled", True)]


def send_test(url: str) -> tuple[str, bool]:
    """채널 연결 테스트용 한 번 전송. 판정·증적 같은 진단 데이터는 담지 않는다."""
    return _post(url, json.dumps({"event": "test", "message": "진단 워크벤치 알림 연결 테스트"},
                                 ensure_ascii=False).encode("utf-8"))


def _notify_event(conn, cfg, run_id, finding_id, event: str, **extra) -> None:
    """등록된 모든 채널에 finding 이벤트를 알린다(D7: 심각도 필터 폐지). 채널이 없으면 아무 일도 하지 않는다."""
    channels = load_channels(cfg.home / "config" / "notify.json")
    if not channels:
        return
    payload = json.dumps({"event": event, "run_id": str(run_id), "finding_id": str(finding_id), **extra},
                         ensure_ascii=False).encode("utf-8")
    for ch in channels:
        detail, ok = _post(ch["url"], payload)
        db.audit(conn, run_id, "notification_sent" if ok else "notification_failed",
                 {"channel": ch["name"], "finding_id": str(finding_id), "detail": detail})


def notify_finding_confirmed(conn, cfg, run_id, finding_id, *, title, actor) -> None:
    _notify_event(conn, cfg, run_id, finding_id, "finding_confirmed", title=title, actor=actor)


def notify_finding_reopened(conn, cfg, run_id, finding_id, *, title, actor) -> None:
    """G1 D3: 기각됐던 finding 이 새 종류 근거로 재관찰돼 NEEDS_REVIEW 로 재오픈됐을 때 알린다."""
    _notify_event(conn, cfg, run_id, finding_id, "finding_reopened", title=title, actor=actor)


def _post(url: str, body: bytes) -> tuple[str, bool]:
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            return f"HTTP {resp.status}", resp.status < 400
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}", False
    except (urllib.error.URLError, OSError) as e:
        return str(e), False
