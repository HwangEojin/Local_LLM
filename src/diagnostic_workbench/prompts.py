"""DB 로 편집 가능한 에이전트 프롬프트.

기본값은 여전히 explore.py 코드에 있다 — agent_prompts 에 같은 이름의 행이 있으면 그 값으로 바꿀 뿐,
LLM 의 판단·정책 계층은 그대로다(CLAUDE.md §7 "LLM 자체가... 임의의 도구 등록" 등의 권한은 바꾸지
않는다, 프롬프트 "문구"만 운영자가 바꿀 수 있다). 행이 없으면 코드 기본값을 그대로 쓴다 — 동작 변경 없음.
"""
from __future__ import annotations


_SKILLS_MAX_CHARS = 6000  # 시스템 프롬프트가 요청 크기 한도(max_request_bytes)를 잡아먹지 않게 하는 상한


def get(conn, name: str, default: str, role: str | None = None) -> str:
    """프롬프트 본문. role 을 주면 그 역할에 붙은 활성 스킬을 뒤에 덧붙인다(스킬은 문구일 뿐 권한은 그대로)."""
    row = conn.execute("SELECT text FROM agent_prompts WHERE name=%s", (name,)).fetchone()
    text = row["text"] if row else default
    return text + skills_for(conn, role) if role else text


def skills_for(conn, role: str) -> str:
    rows = conn.execute("SELECT name, instructions FROM agent_skills WHERE enabled AND %s = ANY(roles) ORDER BY name",
                        (role,)).fetchall()
    parts, used = [], 0
    for r in rows:
        item = f"### {r['name']}\n{r['instructions']}"
        if used + len(item) > _SKILLS_MAX_CHARS:
            break  # 상한을 넘는 스킬은 붙이지 않는다(이름순으로 앞선 것부터)
        parts.append(item)
        used += len(item)
    if not parts:
        return ""
    return ("\n\nSKILLS: operator-provided guidance for this role. They refine how you work but never grant "
            "tools or permissions, and never override the rules above.\n" + "\n\n".join(parts))


def set(conn, name: str, text: str, *, operator: str) -> None:
    conn.execute(
        "INSERT INTO agent_prompts (name, text, updated_by) VALUES (%s,%s,%s) "
        "ON CONFLICT (name) DO UPDATE SET text=EXCLUDED.text, updated_by=EXCLUDED.updated_by, updated_at=now()",
        (name, text, operator))


def reset(conn, name: str) -> None:
    """DB 오버라이드를 지워 코드 기본값으로 되돌린다."""
    conn.execute("DELETE FROM agent_prompts WHERE name=%s", (name,))


def list_all(conn) -> list:
    return conn.execute("SELECT * FROM agent_prompts ORDER BY name").fetchall()


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
