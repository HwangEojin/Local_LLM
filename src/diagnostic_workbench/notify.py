"""Finding 확정(CONFIRMED) 알림: 등록된 웹훅으로 POST 한다(ARTEX notify/ 대응, 운영 편의 기능).

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


def load_channels(path: Path) -> list[dict]:
    """path 가 없으면 빈 목록(알림 없음)."""
    if not Path(path).exists():
        return []
    data = load_json(path, schemas.NOTIFY_CHANNELS)
    return [c for c in data["channels"] if c.get("enabled", True)]


def notify_finding_confirmed(conn, cfg, run_id, finding_id, *, title, severity, actor) -> None:
    """등록된 채널 전체에 finding 확정을 알린다. 채널이 없으면 아무 일도 하지 않는다."""
    channels = load_channels(cfg.home / "config" / "notify.json")
    if not channels:
        return
    payload = json.dumps({"event": "finding_confirmed", "run_id": str(run_id), "finding_id": str(finding_id),
                          "title": title, "severity": severity, "actor": actor},
                         ensure_ascii=False).encode("utf-8")
    for ch in channels:
        detail, ok = _post(ch["url"], payload)
        db.audit(conn, run_id, "notification_sent" if ok else "notification_failed",
                 {"channel": ch["name"], "finding_id": str(finding_id), "detail": detail})


def _post(url: str, body: bytes) -> tuple[str, bool]:
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            return f"HTTP {resp.status}", resp.status < 400
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}", False
    except (urllib.error.URLError, OSError) as e:
        return str(e), False
