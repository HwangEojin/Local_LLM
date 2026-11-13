"""웹 '알림' 편집(B4, UPGRADE_PLAN.md — admin_extra.py 에서 분리). 웹훅 주소는 데이터가 밖으로 나가는
통로다. 주소는 응답에 마스킹해서 내보내고(웹훅 주소 자체가 비밀), 루프백/사설 주소가 아닌 곳을 켜거나
새로 등록하려면 별도 확인(confirm_external)이 있어야 한다. 결과는 config/notify.local.json(Git 제외).
공유 파일(notify.json)의 채널은 끄고 켜기만 덮어쓴다."""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from audit import config as audit_config
from audit import schemas
from diagnostic_workbench import notify

from ..deps import current_user, get_cfg, get_conn
from .admin import _audit, _file, _need_confirm, _write_json

router = APIRouter(prefix="/api/admin", tags=["admin"])


class ChannelBody(BaseModel):
    url: str | None = None
    enabled: bool | None = None
    confirm: bool = False
    confirm_external: bool = False


class ConfirmBody(BaseModel):
    confirm: bool = False


def _is_internal(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    if host in ("localhost", "::1"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def _mask(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}/…(숨김)" if p.path.strip("/") or p.query else f"{p.scheme}://{p.netloc}"


def _check_url(url: str) -> str:
    url = url.strip()
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname or len(url) > 2000:
        raise HTTPException(422, "웹훅 주소는 http(s)://호스트/… 형식이어야 합니다(최대 2000자)")
    return url


def _channel_rows(cfg) -> list[dict]:
    return [{"name": c["name"], "kind": c["kind"], "enabled": c.get("enabled", True), "source": c["source"],
             "url_masked": _mask(c["url"]), "external": not _is_internal(c["url"])}
            for c in notify.load_all_channels(_file(cfg, "notify.json"))]


def _local_doc(cfg) -> dict:
    p = _file(cfg, "notify.local.json")
    return audit_config.load_json(p, schemas.NOTIFY_CHANNELS) if p.exists() else {"channels": []}


@router.get("/notifications")
def get_notifications(cfg=Depends(get_cfg), user: str = Depends(current_user)):
    return _channel_rows(cfg)


@router.put("/notifications/{name}")
def put_notification(name: str, body: ChannelBody, cfg=Depends(get_cfg), conn=Depends(get_conn),
                     user: str = Depends(current_user)):
    _need_confirm(body.confirm)
    if schemas.validate(name, schemas._ID):
        raise HTTPException(422, "채널 이름은 영문·숫자·._:- 만 쓸 수 있습니다(최대 80자)")
    all_ch = {c["name"]: c for c in notify.load_all_channels(_file(cfg, "notify.json"))}
    cur = all_ch.get(name)
    url = _check_url(body.url) if body.url is not None else (cur["url"] if cur else None)
    if url is None:
        raise HTTPException(422, "새 채널에는 url 이 필요합니다")
    if cur and cur["source"] == "shared" and body.url is not None and body.url.strip() != cur["url"]:
        raise HTTPException(400, "공유 파일(notify.json)의 채널 주소는 웹에서 바꿀 수 없습니다. 끄고 켜기만 가능합니다")
    enabled = body.enabled if body.enabled is not None else (cur.get("enabled", True) if cur else True)
    changes_target = (cur is None) or (body.url is not None and url != cur["url"]) or (enabled and not cur.get("enabled", True))
    if enabled and changes_target and not _is_internal(url) and not body.confirm_external:
        raise HTTPException(400, "외부 주소로 진단 결과 알림이 전송됩니다. 별도 확인(confirm_external)이 필요합니다")
    entry = {"name": name, "kind": "webhook", "url": url, "enabled": enabled}  # min_severity 폐지(D7) — 저장 시 지움
    doc = _local_doc(cfg)
    doc["channels"] = [c for c in doc["channels"] if c["name"] != name] + [entry]
    errs = schemas.validate(doc, schemas.NOTIFY_CHANNELS)
    if errs:
        raise HTTPException(422, "검증 실패: " + "; ".join(errs[:5]))
    _write_json(cfg, _file(cfg, "notify.local.json"), doc)
    # 주소 자체는 비밀이라 감사 로그에는 마스킹한 값만 남긴다
    _audit(conn, user, "admin_notification_changed", {"channel": name, "enabled": enabled, "url": _mask(url),
                                                       "created": cur is None})
    return _channel_rows(get_cfg())


@router.get("/notifications/history")
def notification_history(limit: int = 100, conn=Depends(get_conn), user: str = Depends(current_user)):
    """최근 전송 이력(audit_events 의 notification_sent/notification_failed). 새 테이블 없음 — 이미 §8 감사
    로그에 남는 기록을 전송 이력 형태로만 보여준다."""
    limit = max(1, min(limit, 500))
    return conn.execute(
        "SELECT a.id, a.ts, a.event, a.details, r.run_code FROM audit_events a LEFT JOIN runs r ON r.id = a.run_id "
        "WHERE a.event IN ('notification_sent','notification_failed') ORDER BY a.id DESC LIMIT %s",
        (limit,)).fetchall()


@router.post("/notifications/{name}/delete")
def delete_notification(name: str, body: ConfirmBody, cfg=Depends(get_cfg), conn=Depends(get_conn),
                        user: str = Depends(current_user)):
    _need_confirm(body.confirm)
    doc = _local_doc(cfg)
    if name not in {c["name"] for c in doc["channels"]}:
        raise HTTPException(404, "이 PC 전용 채널만 삭제할 수 있습니다(공유 파일의 채널은 끄기만 가능)")
    doc["channels"] = [c for c in doc["channels"] if c["name"] != name]
    _write_json(cfg, _file(cfg, "notify.local.json"), doc)
    _audit(conn, user, "admin_notification_deleted", {"channel": name})
    return _channel_rows(get_cfg())


@router.post("/notifications/{name}/test")
def test_notification(name: str, body: ConfirmBody, cfg=Depends(get_cfg), conn=Depends(get_conn),
                      user: str = Depends(current_user)):
    """저장된 주소로 테스트 이벤트 한 번 전송(진단 데이터 없음). 성공·실패 모두 200 으로 사유를 돌려준다."""
    _need_confirm(body.confirm)
    ch = {c["name"]: c for c in notify.load_all_channels(_file(cfg, "notify.json"))}.get(name)
    if ch is None:
        raise HTTPException(404, f"채널 '{name}' 이(가) 없음")
    detail, ok = notify.send_test(ch["url"])
    _audit(conn, user, "admin_notification_tested", {"channel": name, "ok": ok, "detail": detail})
    return {"ok": ok, "detail": detail}
