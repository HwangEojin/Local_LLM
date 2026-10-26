"""진단 환경 설정 화면이 쓰는 조회 전용 API: 대상/체크리스트/도구 목록 + 상태 점검.

새 진단 로직은 없다 — scope.json/체크리스트/도구 레지스트리를 읽고 cli.cmd_doctor 와 같은 점검을
그대로 다시 호출할 뿐이다(출력만 JSON으로 바꿈).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from audit import config as audit_config
from audit.checklist_engine import Checklists
from diagnostic_workbench import explore, runs as wb_runs

from ..deps import current_user, get_cfg

router = APIRouter(prefix="/api/environment", tags=["environment"])


def _doctor_status(cfg) -> list[dict]:
    rows = []

    def check(name, fn, warn=False):
        try:
            rows.append({"status": "OK", "name": name, "detail": fn()})
        except Exception as e:  # 점검은 하나가 실패해도 나머지를 계속 본다(cli.cmd_doctor 와 동일)
            rows.append({"status": "WARN" if warn else "FAIL", "name": name, "detail": f"{type(e).__name__}: {e}"})

    check("진단 범위(scope)", lambda: f"대상 {len(audit_config.load_scope(cfg.home)['targets'])}개, "
                                    f"제외 {len(cfg.exclude)}개, 중지 스위치 {'켜짐' if cfg.stop_file.exists() else '꺼짐'}")

    def ollama():
        from audit.local_model import OllamaClient
        info = OllamaClient(cfg.settings.ollama, cfg.settings.external_api_enabled).check_ready()
        return f"v{info['version']} 모델={info['model']}"
    check("로컬 LLM(Ollama)", ollama, warn=True)

    for name, srv in audit_config.load_mcp_servers(cfg.home).items():
        rows.append({"status": "INFO", "name": f"MCP {name}", "detail": "활성" if srv["enabled"] else "비활성"})
    return rows


def _llm_profiles(cfg) -> list[dict]:
    return [{"name": name, "kind": p["kind"], "model": p.get("model")}
            for name, p in sorted(audit_config.load_llm_profiles(cfg.home).items())]


@router.get("")
def get_environment(cfg=Depends(get_cfg), user: str = Depends(current_user)):
    scope = audit_config.load_scope(cfg.home)
    cl = Checklists(cfg.settings.path("checklist_dir"))
    checklists = [{"checklist_id": cid, "version": v["version"], "sha256": v.get("sha256")}
                  for cid, v in cl.entries() if v["status"] == "active"]
    tools = [{"name": name, "risk": spec.risk} for name, spec in sorted(wb_runs.registry(cfg).items())]
    llm_profiles = _llm_profiles(cfg)
    return {
        "targets": scope["targets"],
        "checklists": checklists,
        "tools": tools,
        "autonomy_levels": list(wb_runs.AUTONOMY_LEVELS),
        "llm_providers": list(wb_runs.LLM_PROVIDERS),
        "llm_profiles": llm_profiles,
        "ollama_models": explore.installed_ollama_models(cfg),  # None = Ollama 연결 불가
        "agent_roles": list(explore.AGENT_ROLES),
        "doctor": _doctor_status(cfg),
    }


class LlmTestBody(BaseModel):
    spec: str | None = None  # 'ollama:<모델>' 또는 프로파일 이름. 비우면 provider 의 기본 모델
    provider: str = "ollama"


@router.get("/ollama-models")
def get_ollama_models(cfg=Depends(get_cfg), user: str = Depends(current_user)):
    """설치된 Ollama 모델 목록 새로고침(연결할 수 없으면 models=null)과 등록된 LLM 프로파일 목록."""
    return {"models": explore.installed_ollama_models(cfg), "profiles": _llm_profiles(cfg)}


@router.post("/llm-test")
def test_llm(body: LlmTestBody, cfg=Depends(get_cfg), user: str = Depends(current_user)):
    """모델 연결 테스트. 성공/실패 모두 200 으로 ok 와 사유를 돌려준다(화면에 그대로 보여 주려고)."""
    if body.provider not in wb_runs.LLM_PROVIDERS:
        raise HTTPException(422, f"provider 는 {list(wb_runs.LLM_PROVIDERS)} 중 하나여야 함")
    try:
        return {"ok": True, **explore.check_model(cfg, body.spec, body.provider)}
    except Exception as e:  # LLMError/ConfigError 외 예기치 못한 오류도 화면에 사유로 보여 줌
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
