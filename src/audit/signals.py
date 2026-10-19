"""자동 점검 (B) 검증 신호: 탐색이 남긴 fact 가 특정 취약점 클래스를 입증했다고 볼 신호와 일치하는지 판정한다.

LLM 의 "취약하다"는 의견이나 confidence 숫자만으로 확정하지 않는다(CLAUDE.md §7B). 각 취약점 클래스는
의미 있게 따로 정의하며, 범용 정규식 하나로 모든 클래스를 확정하지 않는다. classify_payload() 가 실행에
쓰인 인자에서 알려진 공격 페이로드 시그니처를 찾아 어떤 클래스를 시도했는지 추정하고, SIGNALS 의 해당
checker 가 그 응답(evidence)에서 클래스별 증거를 따로 확인한다. 둘 다 일치해야 매치다 — 반사 하나,
오류 문구 하나만으로는 안 된다.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass

_INJECTION_PATTERNS = {
    # 순서가 중요하다: 한 문자열이 여러 패턴과 맞을 때(예: XXE 의 file:// URI 는 SSRF 의 스킴 패턴과도
    # 맞음) 먼저 나온 것이 이긴다. 더 구조적이고 특정적인 패턴(XXE의 <!ENTITY)을 더 일반적인 패턴(SSRF의
    # 스킴만 보는 것)보다 앞에 둔다.
    "XSS": re.compile(r"<script\b|on(?:error|load|mouseover|focus)\s*=|javascript:", re.I),
    "SQLI": re.compile(r"'\s*(or|and)\s*'?1'?\s*=\s*'?1|\bunion\b\s+\bselect\b|'\s*--|xp_cmdshell", re.I),
    "XXE": re.compile(r"<!ENTITY\s+\S+\s+SYSTEM|<!DOCTYPE[^>]*\bSYSTEM\b", re.I),
    # 흔한 주소(localhost/127.0.0.1)는 일부러 뺐다 — 이 프로젝트 자체의 진단 대상도 그 주소일 수 있어
    # 넣으면 정상 요청까지 전부 SSRF 시도로 오분류한다. 클라우드 메타데이터 주소·위험 스킴만 본다.
    "SSRF": re.compile(r"(file|gopher|dict)://|169\.254\.169\.254|100\.100\.100\.200|"
                       r"metadata\.google\.internal|metadata\.azure\.com", re.I),
    # 템플릿 엔진이 산술식을 평가하는지 보는 고전적 산술 프로브. 문자열 그대로 반사되는지(평가 안 됨)와
    # 계산된 값이 나오는지(평가됨)를 checker 가 구분한다.
    "SSTI": re.compile(r"\{\{\s*7\s*\*\s*7\s*\}\}|\$\{\s*7\s*\*\s*7\s*\}|<%=?\s*7\s*\*\s*7\s*%>|#\{\s*7\s*\*\s*7\s*\}"),
    "CMDI": re.compile(r"[;&|]\s*(cat\s+/etc/passwd|id|whoami|uname\s*-?a?)\b|"
                       r"\$\(\s*(id|whoami|uname)[^)]*\)|`\s*(id|whoami|uname)[^`]*`", re.I),
}


def _flatten_strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _flatten_strings(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _flatten_strings(v)]
    return []


_METHOD_LINE = re.compile(r"^(POST|PUT|DELETE|PATCH)\s+\S+\s+HTTP/\d", re.M)
_HOST_HEADER = re.compile(r"^Host:\s*([^\r\n]+)", re.M | re.I)
_ORIGIN_HEADER = re.compile(r"^(?:Origin|Referer):\s*([^\r\n]+)", re.M | re.I)
_COOKIE_HEADER = re.compile(r"^Cookie:\s*([^\r\n]+)", re.M | re.I)


def _raw_http_request(args: dict) -> str | None:
    for value in _flatten_strings(args):
        if _METHOD_LINE.search(value):
            return value
    return None


def classify_csrf(args: dict) -> bool:
    """CSRF 는 악성 문자열 시그니처가 없다 — '세션은 있지만 출처가 위조됐다'는 요청 구조 자체가 시도의
    증거다. 상태 변경 메서드 + 인증 쿠키(탈취한 세션 재사용의 전제) + 대상 호스트와 다른 Origin/Referer
    세 가지가 모두 있어야 CSRF 시도로 본다. 하나라도 없으면 그냥 평범한 요청일 뿐이다.
    """
    text = _raw_http_request(args)
    if text is None:
        return False
    host_m, cookie_m, origin_m = _HOST_HEADER.search(text), _COOKIE_HEADER.search(text), _ORIGIN_HEADER.search(text)
    if not (host_m and cookie_m and origin_m):
        return False
    origin_host = re.sub(r"^https?://", "", origin_m.group(1).strip(), flags=re.I).split("/")[0].split(":")[0]
    target_host = host_m.group(1).strip().split(":")[0]
    return origin_host.lower() != target_host.lower()


def classify_payload(args: dict) -> str | None:
    """도구 호출 인자에서 알려진 공격 페이로드 시그니처를 찾는다. 모르면 None(이 메커니즘을 쓰지 않는다)."""
    for value in _flatten_strings(args):
        for vclass, pattern in _INJECTION_PATTERNS.items():
            if pattern.search(value):
                return vclass
    if classify_csrf(args):
        return "CSRF"
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


_BOOL_TRUE = re.compile(r"'\s*(or|and)\s*'1'\s*=\s*'1", re.I)
_BOOL_FALSE = re.compile(r"'\s*(or|and)\s*'1'\s*=\s*'2", re.I)


def classify_sqli_boolean(args: dict) -> str | None:
    """'1'='1'(참) / '1'='2'(거짓) 같은 불린 기반 SQLi 프로브를 구분한다. 차등 검증의 짝을 찾는 데 쓴다.

    실제 GET 요청에서는 이 리터럴이 URL 인코딩된 채로(`%27%20OR%20...`) args 에 들어온다 — 원문과
    디코딩한 문자열을 둘 다 본다(실제 Burp MCP 트래픽으로 확인한 문제).
    """
    for value in _flatten_strings(args):
        for candidate in (value, urllib.parse.unquote(value)):
            if _BOOL_FALSE.search(candidate):
                return "FALSE"
            if _BOOL_TRUE.search(candidate):
                return "TRUE"
    return None


def _normalize_bool(value):
    if isinstance(value, str):
        decoded = urllib.parse.unquote(value)  # 인코딩된 요청도 같은 정규화 결과가 나오게 디코딩부터 한다
        return _BOOL_FALSE.sub("'1'='BOOL'", _BOOL_TRUE.sub("'1'='BOOL'", decoded))
    if isinstance(value, dict):
        return {k: _normalize_bool(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_bool(v) for v in value]
    return value


def pairing_key(args: dict) -> str | None:
    """참/거짓 리터럴만 같은 값으로 바꾼 뒤, 나머지 인자가 완전히 같은지 보기 위한 정규화 키.

    같은 주입 지점을 겨눈 참/거짓 짝인지 확인하는 용도다 — 엔드포인트·다른 매개변수가 다른 요청끼리
    비교하면 응답이 다른 게 당연해서 차등 신호가 아무 의미도 없다. 불린 프로브가 아니면 None.
    """
    if classify_sqli_boolean(args) is None:
        return None
    return json.dumps(_normalize_bool(args), sort_keys=True, ensure_ascii=False)


def verify_sqli_boolean_diff(true_body: bytes, false_body: bytes) -> SignalResult:
    """참/거짓 조건을 제외하면 완전히 같은 요청인데, 응답이 의미 있게 달라지는지만 본다(차등/행동 기반).

    SQL 오류 문구가 전혀 없어도 이 신호만으로 확정할 수 있다 — SQLi 중 유일한 비-오류-기반 신호. 길이
    차이가 너무 작으면(동적 타임스탬프 등 잡음) 우연으로 보고 매치로 치지 않는다.
    """
    t, f = true_body.decode("utf-8", errors="replace"), false_body.decode("utf-8", errors="replace")
    if t == f:
        return SignalResult(False, "참/거짓 조건 응답이 완전히 동일함(차이 없음)")
    delta = abs(len(t) - len(f))
    threshold = max(20, int(0.02 * max(len(t), len(f))))  # 아주 작은 길이 차이는 잡음으로 본다
    if delta < threshold:
        return SignalResult(False, f"참/거짓 응답 길이 차이({delta}자)가 잡음 수준이라 판단 보류(기준 {threshold}자)")
    return SignalResult(True, f"참({len(t)}자)/거짓({len(f)}자) 조건 응답 길이가 {delta}자 다름 "
                              "(불린 기반 차등 신호 — SQL 오류 문구 없이도 주입 지점이 응답을 바꿈을 시사)")


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


_STATUS_LINE = re.compile(r"HTTP/\d\.\d\s+(\d{3})")  # 줄 시작 고정 안 함: Burp MCP 는 응답을
# HttpRequestResponse{...httpResponse=HTTP/1.1 200 ...} 형태로 감싸서 줄바꿈 없이 돌려준다(실측). 요청
# 줄의 "HTTP/1.1" 뒤에는 숫자 3자리가 오지 않으므로 줄 위치를 안 봐도 요청/응답을 혼동하지 않는다.
_REJECTION_MARKERS = re.compile(r"\b(csrf|forbidden|invalid.{0,10}token|cross.site|same.origin|access denied)\b", re.I)
_SUCCESS_CODES = {200, 201, 202, 204, 301, 302, 303}


def verify_csrf(args: dict, body: bytes) -> SignalResult:
    """위조된 Origin/Referer + 쿠키로 보낸 상태 변경 요청이 거부 신호 없이 성공 코드를 받았는지만 본다.

    단일 요청 기반 근사치다 — 정상 토큰을 포함한 요청과 비교하는 차등(diff) 검증은 아니다. 성공 코드
    하나만으로는 다른 이유로 200 이 나왔을 가능성을 배제할 수 없어 흔한 거부 문구의 부재까지 함께
    보지만, 그래도 SQLi/XSS 보다 약한 신호다 — worker.py 는 이 신호만으로 체크리스트 FAIL 을 확정하지
    않는다(CLAUDE.md §7B).
    """
    text = body.decode("utf-8", errors="replace")
    m = _STATUS_LINE.search(text)
    if not m:
        return SignalResult(False, "응답에서 상태 코드를 찾지 못함")
    status = int(m.group(1))
    if status not in _SUCCESS_CODES:
        return SignalResult(False, f"요청이 성공 코드를 받지 못함(상태 {status})")
    rej = _REJECTION_MARKERS.search(text)
    if rej:
        return SignalResult(False, f"성공 코드({status})이지만 응답에 거부 관련 문구 '{rej.group(0)}' 가 있어 보류")
    return SignalResult(True, f"위조된 Origin/Referer + 쿠키로 보낸 상태 변경 요청이 거부 문구 없이 상태 {status} "
                              "받음(단일 요청 근사치 — 정상 토큰과의 차등 비교 아님)")


_SSRF_EVIDENCE = re.compile(
    r"ami-id|iam/security-credentials|computeMetadata|instance/service-accounts|"
    r"compute/metadata|root:[^\r\n]*:0:0:", re.I)


def verify_ssrf(args: dict, body: bytes) -> SignalResult:
    """주소가 요청에 있다는 사실만으로는 서버가 실제로 그걸 가져왔다는 증거가 안 된다(리다이렉트를
    막거나 SSRF 방화벽이 있을 수 있음) — 클라우드 메타데이터 응답 키나 /etc/passwd 같은, 내부 자원에만
    있는 고유한 내용이 응답에 그대로 있어야 한다.
    """
    text = body.decode("utf-8", errors="replace")
    m = _SSRF_EVIDENCE.search(text)
    if not m:
        return SignalResult(False, "내부/메타데이터 자원 고유의 내용이 응답에 없음(서버가 실제로 가져왔다는 증거 없음)")
    return SignalResult(True, f"SSRF 의심 주소로 보낸 요청의 응답에 내부 자원 고유 내용 발견: '{m.group(0)[:60]}'")


def verify_ssti(args: dict, body: bytes) -> SignalResult:
    """보낸 산술 표현식 문자열이 그대로 반사됐는지가 아니라, 평가된 결과값(7*7=49)이 응답에 있는지를 본다.

    '{{7*7}}' 문자열이 그대로 보이면 템플릿이 평가하지 않고 에코한 것뿐이다 — 반드시 계산된 값만 있어야
    인과관계가 성립한다. 다만 '49' 는 흔한 숫자라 페이지에 우연히 있을 수 있다는 한계는 그대로 남는다
    (오류 기반 신호인 SQLi 와 달리 결과가 숫자 하나뿐이라 SQLi 보다도 약한 신호).
    """
    probes = [s for s in _flatten_strings(args) if _INJECTION_PATTERNS["SSTI"].search(s)]
    if not probes:
        return SignalResult(False, "요청 인자에서 SSTI 산술 프로브를 찾지 못함")
    text = body.decode("utf-8", errors="replace")
    probe = probes[0]
    if probe in text:
        return SignalResult(False, "보낸 표현식이 그대로 반사됨(평가되지 않고 에코된 것으로 추정)")
    if "49" not in text:
        return SignalResult(False, "평가된 결과값(49)이 응답에 없음(인과관계 없음)")
    return SignalResult(True, f"템플릿 표현식 '{probe}' 가 그대로 반사되지 않고 평가된 결과(49)로 응답에 나타남 "
                              "(산술 평가 신호 — 49 가 우연히 있을 가능성은 배제하지 못함)")


_XXE_EVIDENCE = re.compile(r"root:[^\r\n]*:0:0:", re.I)


def verify_xxe(args: dict, body: bytes) -> SignalResult:
    """엔티티 선언을 보냈다는 사실만으로는 부족하다 — 그 엔티티가 실제로 해석(expand)돼 응답에 내부 파일
    내용(예: /etc/passwd)이 그대로 나타나야 한다.
    """
    text = body.decode("utf-8", errors="replace")
    m = _XXE_EVIDENCE.search(text)
    if not m:
        return SignalResult(False, "엔티티가 해석된 내부 파일 내용(예: /etc/passwd)이 응답에 없음")
    return SignalResult(True, f"XXE 의심 엔티티 선언을 보낸 요청의 응답에 해석된 내부 파일 내용 발견: '{m.group(0)[:60]}'")


_CMD_EVIDENCE = re.compile(r"uid=\d+\([^)]+\)\s+gid=\d+|root:[^\r\n]*:0:0:|\b(Linux|Darwin)\s+\S+\s+\d+\.\d+\.\d+", re.I)


def verify_cmdi(args: dict, body: bytes) -> SignalResult:
    """셸 메타문자 뒤에 명령 이름이 있다는 사실만으로는 부족하다 — 그 명령 고유의 출력 형태(id 의
    uid=.../gid=..., uname 의 커널 문자열, /etc/passwd 내용)가 응답에 그대로 있어야 한다.
    """
    text = body.decode("utf-8", errors="replace")
    m = _CMD_EVIDENCE.search(text)
    if not m:
        return SignalResult(False, "명령 실행 결과로 보이는 고유 출력(uid=.., 커널 문자열, /etc/passwd 등)이 응답에 없음")
    return SignalResult(True, f"명령 주입 의심 페이로드를 보낸 요청의 응답에 명령 실행 결과로 보이는 출력 발견: '{m.group(0)[:60]}'")


SIGNALS = {"SQLI": verify_sqli, "XSS": verify_xss, "CSRF": verify_csrf, "SSRF": verify_ssrf,
          "SSTI": verify_ssti, "XXE": verify_xxe, "CMDI": verify_cmdi}
SEVERITY = {"SQLI": "high", "XSS": "medium", "CSRF": "medium", "SSRF": "high",
           "SSTI": "critical", "XXE": "high", "CMDI": "critical"}
REMEDIATION = {
    "SQLI": "매개변수화된 쿼리(prepared statement)를 쓰고 사용자 입력을 SQL 문자열에 직접 연결하지 않는다.",
    "XSS": "출력 문맥에 맞는 인코딩(HTML/속성/URL/JS)을 적용하고 사용자 입력을 신뢰하지 않는다.",
    "CSRF": "상태 변경 요청에 예측 불가능한 CSRF 토큰을 요구하고 서버에서 Origin/Referer 를 검증하며, "
            "민감한 액션의 쿠키에는 SameSite=Strict/Lax 를 적용한다.",
    "SSRF": "사용자 입력 URL로 서버가 직접 요청하지 않도록 목적지 허용 목록(allowlist)을 적용하고, "
            "내부망·링크로컬·클라우드 메타데이터 주소로의 요청을 차단한다.",
    "SSTI": "사용자 입력을 템플릿 문자열로 직접 렌더링하지 말고, 템플릿과 데이터를 분리해 "
            "logic-less/sandboxed 템플릿 엔진만 사용한다.",
    "XXE": "XML 파서에서 외부 엔티티·DTD 처리를 비활성화한다(예: 파서의 external-general-entities, "
          "disallow-doctype-decl 옵션).",
    "CMDI": "사용자 입력으로 셸 명령을 구성하지 말고, 셸을 거치지 않는 API(예: execve 계열)에 인자를 "
           "배열로 전달한다.",
}


def check(args: dict, body: bytes) -> tuple[str, SignalResult] | None:
    """(취약점 클래스, 신호 결과). 알려진 페이로드가 없으면 None(신호 확인 대상이 아님)."""
    vclass = classify_payload(args)
    if vclass is None or vclass not in SIGNALS:
        return None
    return vclass, SIGNALS[vclass](args, body)
