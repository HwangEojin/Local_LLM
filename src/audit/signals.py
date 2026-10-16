"""자동 점검 (B) 검증 신호: 탐색이 남긴 fact 가 특정 취약점 클래스를 입증했다고 볼 신호와 일치하는지 판정한다.

LLM 의 "취약하다"는 의견이나 confidence 숫자만으로 확정하지 않는다(CLAUDE.md §7B). 각 취약점 클래스는
의미 있게 따로 정의하며, 범용 정규식 하나로 모든 클래스를 확정하지 않는다. classify_payload() 가 실행에
쓰인 인자에서 알려진 공격 페이로드 시그니처를 찾아 어떤 클래스를 시도했는지 추정하고, SIGNALS 의 해당
checker 가 그 응답(evidence)에서 클래스별 증거를 따로 확인한다. 둘 다 일치해야 매치다 — 반사 하나,
오류 문구 하나만으로는 안 된다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_INJECTION_PATTERNS = {
    # 공격 시도를 가리키는 시그니처일 뿐, 그 자체가 입증은 아니다 — checker 가 응답에서 별도로 확인한다.
    "XSS": re.compile(r"<script\b|on(?:error|load|mouseover|focus)\s*=|javascript:", re.I),
    "SQLI": re.compile(r"'\s*(or|and)\s*'?1'?\s*=\s*'?1|\bunion\b\s+\bselect\b|'\s*--|xp_cmdshell", re.I),
}


def _flatten_strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _flatten_strings(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _flatten_strings(v)]
    return []


def classify_payload(args: dict) -> str | None:
    """도구 호출 인자에서 알려진 공격 페이로드 시그니처를 찾는다. 모르면 None(이 메커니즘을 쓰지 않는다)."""
    for value in _flatten_strings(args):
        for vclass, pattern in _INJECTION_PATTERNS.items():
            if pattern.search(value):
                return vclass
    return None


@dataclass(frozen=True)
class SignalResult:
    matched: bool
    reason: str


_SQL_ERROR_SIGNATURES = re.compile(
    r"SQL syntax.*MySQL|Warning.*mysqli?_|ORA-\d{5}|Microsoft SQL Server|"
    r"Unclosed quotation mark|SQLite3?::\w*Exception|PostgreSQL.*ERROR|pg_query\(\)|SQLSTATE\[", re.I)
_ENCODED = re.compile(r"&lt;|&gt;|%3C|%3E|&#x3c;|&#60;", re.I)


def verify_sqli(args: dict, body: bytes) -> SignalResult:
    """오류 기반 신호: 공격 시그니처가 포함된 요청에 특정 DB 엔진 오류 문구가 뒤따랐는지만 본다.

    일반 500 오류와 구분하기 위해 엔진을 특정하는 문구를 요구한다. 차등(diff)/행동 기반 검증은 아니다 —
    요청·응답 쌍을 비교하는 재현 가능한 검증은 별도 구현이 필요하며, 그 전까지는 이 신호만으로 FAIL 을
    자동 확정하지 않고 사람 검토로 보낸다(worker.py).
    """
    text = body.decode("utf-8", errors="replace")
    m = _SQL_ERROR_SIGNATURES.search(text)
    if not m:
        return SignalResult(False, "DB 엔진을 특정하는 오류 문구 없음(일반 500과 구분 불가)")
    return SignalResult(True, f"SQLi 의심 페이로드를 보낸 요청에서 DB 엔진 오류 문구 발견: '{m.group(0)[:60]}' "
                              "(오류 기반 신호 — 차등/행동 검증 아님)")


def verify_xss(args: dict, body: bytes) -> SignalResult:
    """입력이 그대로(인코딩 없이) 응답에 반사됐는지와 그 반사 지점의 문맥을 함께 본다.

    반사 하나만으로는 부족하다(CLAUDE.md §7B) — 보낸 페이로드 문자열이 응답에 바이트 그대로 있어야 하고
    (인과관계), 그 지점이 인코딩돼 있지 않아야 한다(실행 가능한 문맥으로 추정).
    """
    probes = sorted({s for s in _flatten_strings(args) if _INJECTION_PATTERNS["XSS"].search(s)}, key=len, reverse=True)
    if not probes:
        return SignalResult(False, "요청 인자에서 XSS 페이로드를 찾지 못함")
    text = body.decode("utf-8", errors="replace")
    probe = probes[0]
    idx = text.find(probe)
    if idx < 0:
        return SignalResult(False, "보낸 페이로드가 응답에 반사되지 않음(인과관계 없음)")
    window = text[max(0, idx - 20):idx + len(probe) + 20]
    if _ENCODED.search(window):
        return SignalResult(False, "페이로드가 반사됐으나 반사 지점이 인코딩되어 있어 실행 불가능으로 판단")
    return SignalResult(True, f"보낸 페이로드 '{probe[:60]}' 가 인코딩 없이 응답에 그대로 반사됨(실행 가능한 문맥으로 추정)")


SIGNALS = {"SQLI": verify_sqli, "XSS": verify_xss}
SEVERITY = {"SQLI": "high", "XSS": "medium"}
REMEDIATION = {
    "SQLI": "매개변수화된 쿼리(prepared statement)를 쓰고 사용자 입력을 SQL 문자열에 직접 연결하지 않는다.",
    "XSS": "출력 문맥에 맞는 인코딩(HTML/속성/URL/JS)을 적용하고 사용자 입력을 신뢰하지 않는다.",
}


def check(args: dict, body: bytes) -> tuple[str, SignalResult] | None:
    """(취약점 클래스, 신호 결과). 알려진 페이로드가 없으면 None(신호 확인 대상이 아님)."""
    vclass = classify_payload(args)
    if vclass is None or vclass not in SIGNALS:
        return None
    return vclass, SIGNALS[vclass](args, body)
