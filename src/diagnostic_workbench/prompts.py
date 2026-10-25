"""DB 로 편집 가능한 에이전트 프롬프트(ARTEX agent_prompts 대응).

기본값은 여전히 explore.py 코드에 있다 — agent_prompts 에 같은 이름의 행이 있으면 그 값으로 바꿀 뿐,
LLM 의 판단·정책 계층은 그대로다(CLAUDE.md §7 "LLM 자체가... 임의의 도구 등록" 등의 권한은 바꾸지
않는다, 프롬프트 "문구"만 운영자가 바꿀 수 있다). 행이 없으면 코드 기본값을 그대로 쓴다 — 동작 변경 없음.
"""
from __future__ import annotations


def get(conn, name: str, default: str) -> str:
    row = conn.execute("SELECT text FROM agent_prompts WHERE name=%s", (name,)).fetchone()
    return row["text"] if row else default


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
