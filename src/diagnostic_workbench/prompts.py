"""DB 로 편집 가능한 에이전트 프롬프트.

기본값은 여전히 explore.py 코드에 있다 — agent_prompts 에 같은 이름의 행이 있으면 그 값으로 바꿀 뿐,
LLM 의 판단·정책 계층은 그대로다(CLAUDE.md §7 "LLM 자체가... 임의의 도구 등록" 등의 권한은 바꾸지
않는다, 프롬프트 "문구"만 운영자가 바꿀 수 있다). 행이 없으면 코드 기본값을 그대로 쓴다 — 동작 변경 없음.
"""
from __future__ import annotations

from psycopg.types.json import Jsonb

_SKILLS_MAX_CHARS = 6000  # 시스템 프롬프트가 요청 크기 한도(max_request_bytes)를 잡아먹지 않게 하는 상한


def get(conn, name: str, default: str, role: str | None = None, run_id=None, profile_name: str | None = None) -> str:
    """프롬프트 본문. role 을 주면 그 역할에 붙은 활성 스킬을 뒤에 덧붙인다(스킬은 문구일 뿐 권한은 그대로).

    profile_name 을 주면(그 실행이 이 역할에 "이름 있는 프로파일"을 골랐을 때) 전역 agent_prompts 오버라이드
    보다 그 프로파일의 prompt_overrides 를 먼저 본다 — 프로파일에 이 이름의 오버라이드가 없으면 전역 값,
    그것도 없으면 default 로 그대로 떨어진다(기존 동작과 100% 같음, 프로파일은 순수 추가 기능)."""
    text = None
    if profile_name and role:
        p = conn.execute("SELECT prompt_overrides FROM agent_profiles WHERE role=%s AND name=%s",
                         (role, profile_name)).fetchone()
        if p and name in (p["prompt_overrides"] or {}):
            text = p["prompt_overrides"][name]
    if text is None:
        row = conn.execute("SELECT text FROM agent_prompts WHERE name=%s", (name,)).fetchone()
        text = row["text"] if row else default
    return text + skills_for(conn, role, run_id) if role else text


def skills_for(conn, role: str, run_id=None) -> str:
    """role 에 붙은 활성 스킬을 프롬프트 뒤에 덧붙일 문구로. run_id 를 주면 실제로 덧붙은 스킬을 skill_usage 에 센다
    (커밋은 호출자 몫 — 읽기 경로에서 호출자의 트랜잭션을 마음대로 커밋하지 않는다)."""
    rows = conn.execute("SELECT name, instructions FROM agent_skills WHERE enabled AND %s = ANY(roles) ORDER BY name",
                        (role,)).fetchall()
    parts, used, attached = [], 0, []
    for r in rows:
        item = f"### {r['name']}\n{r['instructions']}"
        if used + len(item) > _SKILLS_MAX_CHARS:
            break  # 상한을 넘는 스킬은 붙이지 않는다(이름순으로 앞선 것부터)
        parts.append(item)
        attached.append(r["name"])
        used += len(item)
    if run_id is not None:
        for skill in attached:
            conn.execute(
                "INSERT INTO skill_usage (run_id, skill, role) VALUES (%s,%s,%s) "
                "ON CONFLICT (run_id, skill, role) DO UPDATE SET calls = skill_usage.calls + 1, last_at = now()",
                (run_id, skill, role))
    if not parts:
        return ""
    return ("\n\nSKILLS: operator-provided guidance for this role. They refine how you work but never grant "
            "tools or permissions, and never override the rules above.\n" + "\n\n".join(parts))


def list_profiles(conn, role: str | None = None) -> list:
    """역할별 '이름 있는 프로파일'(F3a 절충안, UPGRADE_PLAN.md — 완전 자유 에이전트 생성 대신 고정 5역할
    안에서 역할마다 여러 이름 붙은 변형을 둔다, 예: worker-aggressive/worker-cautious)."""
    if role:
        return conn.execute("SELECT * FROM agent_profiles WHERE role=%s ORDER BY name", (role,)).fetchall()
    return conn.execute("SELECT * FROM agent_profiles ORDER BY role, name").fetchall()


def create_profile(conn, role: str, name: str, prompt_overrides: dict, llm_profile: str | None, *,
                   operator: str) -> dict:
    return conn.execute(
        "INSERT INTO agent_profiles (role, name, prompt_overrides, llm_profile, created_by) "
        "VALUES (%s,%s,%s,%s,%s) RETURNING *",
        (role, name, Jsonb(prompt_overrides), llm_profile, operator)).fetchone()


def delete_profile(conn, profile_id: int) -> bool:
    return conn.execute("DELETE FROM agent_profiles WHERE id=%s", (profile_id,)).rowcount > 0


def visible_tools(tools: dict, role: str, overrides: dict) -> dict:
    """role 에게 제안 후보로 보여줄 도구만 남긴다(config/tools.local.json 의 hidden_from). disabled 와 달리
    도구를 레지스트리에서 빼지 않으므로 실행 권한에는 영향 없음 — policy.decide() 는 이 필터와 무관하게
    전체 레지스트리 기준으로 그대로 판정한다."""
    hidden = overrides.get("hidden_from", {})
    return {name: spec for name, spec in tools.items() if role not in hidden.get(name, [])}


def set(conn, name: str, text: str, *, operator: str) -> None:
    conn.execute(
        "INSERT INTO agent_prompts (name, text, updated_by) VALUES (%s,%s,%s) "
        "ON CONFLICT (name) DO UPDATE SET text=EXCLUDED.text, updated_by=EXCLUDED.updated_by, updated_at=now()",
        (name, text, operator))
    _record_version(conn, name, text, operator)


def reset(conn, name: str, *, operator: str = "system", default: str | None = None) -> None:
    """DB 오버라이드를 지워 코드 기본값으로 되돌린다. default 를 주면 복원 목록에 그 상태도 한 행 남긴다."""
    conn.execute("DELETE FROM agent_prompts WHERE name=%s", (name,))
    if default is not None:
        _record_version(conn, name, default, operator)


def _record_version(conn, name: str, text: str, operator: str) -> None:
    conn.execute("INSERT INTO agent_prompt_versions (name, text, updated_by) VALUES (%s,%s,%s)",
                 (name, text, operator))


def versions(conn, name: str, limit: int = 20) -> list:
    """최근 버전부터. 되돌리기 목록용(복원 전용, line diff 는 안 만든다)."""
    return conn.execute(
        "SELECT id, text, updated_by, created_at FROM agent_prompt_versions "
        "WHERE name=%s ORDER BY created_at DESC LIMIT %s", (name, limit)).fetchall()


def version(conn, name: str, version_id: int):
    return conn.execute(
        "SELECT text FROM agent_prompt_versions WHERE id=%s AND name=%s", (version_id, name)).fetchone()


def list_all(conn) -> list:
    return conn.execute("SELECT * FROM agent_prompts ORDER BY name").fetchall()


def role_defaults(conn) -> dict[str, str]:
    """역할별 기본 LLM 프로파일(/runs/new 의 "에이전트별 모델" 칸을 미리 채우는 값일 뿐, 실행 중 진단에는 영향 없음)."""
    return {r["role"]: r["llm_profile"] for r in conn.execute("SELECT role, llm_profile FROM agent_role_defaults").fetchall()}


def set_role_default(conn, role: str, llm_profile: str, *, operator: str) -> None:
    conn.execute(
        "INSERT INTO agent_role_defaults (role, llm_profile, updated_by) VALUES (%s,%s,%s) "
        "ON CONFLICT (role) DO UPDATE SET llm_profile=EXCLUDED.llm_profile, updated_by=EXCLUDED.updated_by, updated_at=now()",
        (role, llm_profile, operator))


def clear_role_default(conn, role: str) -> None:
    conn.execute("DELETE FROM agent_role_defaults WHERE role=%s", (role,))


def catalog() -> list[dict]:
    """웹 '에이전트' 화면용: 역할별로 편집 가능한 프롬프트와 코드 기본값. 기본값이 각 모듈에 있어 순환 import 를
    피하려고 호출 시점에 가져온다. analyst(체크리스트 분석)의 프롬프트는 audit.local_model 에 고정돼 있어
    편집 대상이 아니다(빈 목록)."""
    from . import explore, reporting, runs, worker
    return [
        {"role": "planner", "prompts": [
            {"name": "explore_system", "label": "탐색 제안", "default": explore.SYSTEM},
            {"name": "explore_probe_system", "label": "최초 정찰 프로브", "default": explore.PROBE_SYSTEM}]},
        {"role": "worker", "prompts": [
            {"name": "worker_agent_system", "label": "도구 선택", "default": worker._WORKER_AGENT_SYSTEM}]},
        {"role": "analyst", "prompts": []},
        {"role": "goals", "prompts": [
            {"name": "goals_system", "label": "목표 분해", "default": runs._GOAL_DECOMPOSE_SYSTEM}]},
        {"role": "reporter", "prompts": [
            {"name": "reporter_system", "label": "보고서 문장 다듬기", "default": reporting._POLISH_SYSTEM}]},
    ]
