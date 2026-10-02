"""최소 JSON-Schema 부분집합 검증기(표준 라이브러리만 사용)와 프로젝트 스키마.

지원하지 않는 구조 키워드($ref, not, if/then/else 등)는 SchemaError를 발생시킨다:
조용히 통과시키지 않고 fail-closed 로 처리한다.
"""
from __future__ import annotations

import re

_ANNOTATIONS = {"title", "description", "default", "examples", "format", "$schema",
                "$id", "$comment", "readOnly", "writeOnly", "deprecated"}
_SUPPORTED = {"type", "properties", "required", "additionalProperties", "enum", "const",
              "pattern", "minLength", "maxLength", "minimum", "maximum", "items",
              "minItems", "maxItems", "anyOf", "oneOf", "allOf"} | _ANNOTATIONS

_TYPES = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "null": lambda v: v is None,
}


class SchemaError(Exception):
    pass


def validate(value, schema: dict, path: str = "$") -> list[str]:
    """오류 문자열 목록을 반환한다(빈 목록이면 유효)."""
    unknown = set(schema) - _SUPPORTED
    if unknown:
        raise SchemaError(f"{path}: 지원하지 않는 스키마 키워드 {sorted(unknown)}")
    errs: list[str] = []
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_TYPES[t](value) for t in types):
            return [f"{path}: {'/'.join(types)} 타입이어야 하나 {type(value).__name__} 임"]
    if "const" in schema and value != schema["const"]:
        errs.append(f"{path}: {schema['const']!r} 와 같아야 함")
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {value!r} 은(는) {schema['enum']} 에 없음")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errs.append(f"{path}: 길이가 {schema['minLength']} 미만임")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errs.append(f"{path}: 길이가 {schema['maxLength']} 초과임")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errs.append(f"{path}: 패턴 {schema['pattern']!r} 과 일치하지 않음")
    if _TYPES["number"](value):
        if "minimum" in schema and value < schema["minimum"]:
            errs.append(f"{path}: 최솟값 {schema['minimum']} 미만임")
        if "maximum" in schema and value > schema["maximum"]:
            errs.append(f"{path}: 최댓값 {schema['maximum']} 초과임")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errs.append(f"{path}: 필수 항목 '{key}' 이(가) 없음")
        extra = schema.get("additionalProperties", True)
        for key, v in value.items():
            if key in props:
                errs += validate(v, props[key], f"{path}.{key}")
            elif extra is False:
                errs.append(f"{path}: 허용되지 않는 속성 '{key}'")
            elif isinstance(extra, dict):
                errs += validate(v, extra, f"{path}.{key}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errs.append(f"{path}: 항목 수가 {schema['minItems']}개 미만임")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errs.append(f"{path}: 항목 수가 {schema['maxItems']}개 초과임")
        if "items" in schema:
            for i, v in enumerate(value):
                errs += validate(v, schema["items"], f"{path}[{i}]")
    if "allOf" in schema:
        for sub in schema["allOf"]:
            errs += validate(value, sub, path)
    if "anyOf" in schema and not any(not validate(value, s, path) for s in schema["anyOf"]):
        errs.append(f"{path}: anyOf 중 어느 것과도 일치하지 않음")
    if "oneOf" in schema and sum(not validate(value, s, path) for s in schema["oneOf"]) != 1:
        errs.append(f"{path}: oneOf 중 정확히 하나와 일치해야 함")
    return errs


# ---------------------------------------------------------------- 스키마
_STR = {"type": "string", "minLength": 1}
_ID = {"type": "string", "pattern": r"^[A-Za-z0-9_.:-]{1,80}$"}
_POS_INT = {"type": "integer", "minimum": 1}

SETTINGS = {
    "type": "object", "additionalProperties": False,
    "required": ["ollama", "external_api", "paths", "limits"],
    "properties": {
        "ollama": {
            "type": "object", "additionalProperties": False,
            "required": ["base_url", "model", "timeout_s", "max_request_bytes",
                         "max_response_bytes", "max_calls_per_run", "max_retries"],
            "properties": {
                "base_url": {"type": "string", "pattern": r"^https?://"},
                "model": _STR,
                "timeout_s": _POS_INT,
                "max_request_bytes": _POS_INT,
                "max_response_bytes": _POS_INT,
                "max_calls_per_run": _POS_INT,
                "max_retries": {"type": "integer", "minimum": 0, "maximum": 5},
            },
        },
        "external_api": {
            "type": "object", "additionalProperties": False, "required": ["enabled"],
            "properties": {"enabled": {"type": "boolean"}},
        },
        "paths": {
            "type": "object", "additionalProperties": False,
            "required": ["db", "evidence_dir", "reports_dir", "checklist_dir", "templates_dir"],
            "properties": {k: _STR for k in
                           ("db", "evidence_dir", "reports_dir", "checklist_dir", "templates_dir")},
        },
        "limits": {
            "type": "object", "additionalProperties": False,
            "required": ["max_tool_calls_per_run", "run_timeout_s", "max_evidence_bytes"],
            "properties": {"max_tool_calls_per_run": _POS_INT, "run_timeout_s": _POS_INT,
                           "max_evidence_bytes": _POS_INT},
        },
    },
}

SCOPE = {
    "type": "object", "additionalProperties": False,
    "required": ["engagement_id", "approved_by", "valid_until", "allowed_risks", "targets"],
    "properties": {
        "engagement_id": _ID,
        "approved_by": _STR,
        "valid_until": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
        "allowed_risks": {"type": "array", "minItems": 1,
                          "items": {"enum": ["read", "write", "dangerous"]}},
        "targets": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["target_id", "type", "value"],
                "properties": {"target_id": _ID, "type": {"enum": ["path", "host", "url"]},
                               "value": _STR, "description": {"type": "string"},
                               "platform": _STR},
            },
        },
    },
}

_TOOL_POLICY = {
    "type": "object", "additionalProperties": False,
    "required": ["risk"],
    "properties": {
        "risk": {"enum": ["read", "write", "dangerous"]},
        "scope_args": {"type": "object", "additionalProperties": {"enum": ["path", "host", "url"]}},
        "args_schema": {"type": "object"},
        "scope_filter": {"enum": ["burp"]},  # 결과를 증거로 저장하기 전에 진단 범위 밖 항목을 제거
    },
}

MCP_SERVERS = {
    "type": "object", "additionalProperties": False, "required": ["servers"],
    "properties": {"servers": {"type": "object", "additionalProperties": {
        "type": "object", "additionalProperties": False,
        "required": ["enabled", "transport", "timeout_s", "allowed_tools"],
        "properties": {
            "enabled": {"type": "boolean"},
            "persistent": {"type": "boolean"},
            # 정상 응답(isError=false) 본문이 이 정규식 중 하나와 맞으면 오류로 보고 증거로 저장하지 않는다
            # (Ghidra/JADX 브리지는 연결 실패를 오류 플래그 없이 본문 문구로 돌려준다)
            "error_patterns": {"type": "array", "items": {"type": "string"}},  # true: 한 프로세스 안에서 연결을 유지(상태가 있는 서버: Frida, Playwright)
            "description": {"type": "string"},
            "transport": {"enum": ["stdio", "sse"]},
            "command": _STR,
            "args": {"type": "array", "items": {"type": "string"}},
            "env_from": {"type": "object", "additionalProperties": {"type": "string"}},
            "url": {"type": "string", "pattern": r"^https?://"},
            "timeout_s": _POS_INT,
            "allowed_tools": {"type": "object", "additionalProperties": _TOOL_POLICY},
        },
    }}},
}

_RULE = {
    "type": "object", "additionalProperties": False,
    "required": ["kind", "evidence", "pattern", "fail_if"],
    "properties": {"kind": {"const": "regex"}, "evidence": _ID, "pattern": _STR,
                   "fail_if": {"enum": ["match", "no_match"]}},
}

CHECK_ITEM = {
    "type": "object", "additionalProperties": False,
    "required": ["check_id", "title", "item_status", "source_reference", "target_types",
                 "applicability", "procedure", "pass_criteria", "fail_criteria",
                 "evidence_requirements", "verification_method", "remediation",
                 "severity", "rule_version"],
    "properties": {
        "check_id": _ID,
        "title": _STR,
        "item_status": {"enum": ["complete", "incomplete"]},
        "source_reference": {"type": "string"},
        "target_types": {"type": "array", "minItems": 1, "items": {"enum": ["path", "host", "url"]}},
        "applicability": {"type": "string"},
        "procedure": {"type": "string"},
        "pass_criteria": {"type": "string"},
        "fail_criteria": {"type": "string"},
        "evidence_requirements": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "tool", "args"],
            "properties": {"id": _ID, "description": {"type": "string"}, "tool": _STR,
                           "args": {"type": "object"}},
        }},
        "verification_method": {
            "type": "object", "additionalProperties": False, "required": ["type"],
            "properties": {"type": {"enum": ["rule", "manual", "llm_assisted"]}, "rule": _RULE},
        },
        "remediation": {"type": "string"},
        # risk-1..5: 원본 가이드의 위험도 숫자를 해석 없이 그대로 옮긴 값
        "severity": {"enum": ["critical", "high", "medium", "low", "info",
                              "risk-1", "risk-2", "risk-3", "risk-4", "risk-5"]},
        # 이 항목이 적용되는 대상 platform 목록(없으면 모든 대상). 대상의 platform 이 목록에 있어야 한다.
        "platforms": {"type": "array", "minItems": 1, "items": _STR},
        # 원본 문서의 나머지 열(상세설명, 확인 자료, 법령 근거 등)을 원문 그대로 보존
        "source_data": {"type": "object", "additionalProperties": {"type": "string"}},
        "rule_version": _STR,
    },
}

CHECKLIST = {
    "type": "object", "additionalProperties": False,
    "required": ["checklist_id", "guide_name", "guide_version", "official", "notice", "items"],
    "properties": {
        "checklist_id": _ID, "guide_name": _STR, "guide_version": _STR,
        "official": {"type": "boolean"}, "notice": {"type": "string"},
        "sources": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["name", "sha256"],
            "properties": {"name": _STR, "sha256": {"type": "string", "pattern": r"^[0-9a-f]{64}$"},
                           "role": {"type": "string"}}}},
        "items": {"type": "array", "minItems": 1, "items": CHECK_ITEM},
    },
}

MANIFEST = {
    "type": "object", "additionalProperties": False, "required": ["checklists"],
    "properties": {"checklists": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["checklist_id", "versions"],
        "properties": {
            "checklist_id": _ID,
            "versions": {"type": "array", "minItems": 1, "items": {
                "type": "object", "additionalProperties": False,
                "required": ["version", "path", "status"],
                "properties": {
                    "version": _STR, "path": _STR,
                    "status": {"enum": ["draft", "active", "superseded", "retired"]},
                    "sha256": {"type": "string", "pattern": r"^[0-9a-f]{64}$"},
                    "activated_at": {"type": "string"},
                },
            }},
        },
    }}},
}

PLAN = {
    "type": "object", "additionalProperties": False,
    "required": ["plan_id", "checklist_id", "checklist_version", "targets"],
    "properties": {
        "plan_id": _ID,
        "description": {"type": "string"},
        "checklist_id": _ID,
        "checklist_version": _STR,
        "targets": {"type": "array", "minItems": 1, "items": _ID},
        "check_ids": {"type": "array", "items": _ID},
        "use_llm": {"type": "boolean"},
        # 정보용일 뿐 권한을 부여하지 않는다(권한은 정책 엔진이 결정한다).
        "requires_approval": {"type": "boolean"},
    },
}

_VERDICTS = ["PASS", "FAIL", "MANUAL_REVIEW", "NOT_APPLICABLE", "INCONCLUSIVE"]

LLM_ANALYSIS = {
    "type": "object", "additionalProperties": False,
    "required": ["verdict", "rationale", "evidence_ids"],
    "properties": {
        "verdict": {"enum": _VERDICTS},
        "rationale": {"type": "string", "minLength": 1, "maxLength": 2000},
        "evidence_ids": {"type": "array", "maxItems": 20, "items": {"type": "string"}},
    },
}


# ---------------------------------------------------------------- 모의해킹 결과보고서 입력
_ISO_DATE = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}

REPORT_META = {
    "type": "object", "additionalProperties": False, "required": ["customer", "project", "targets"],
    "properties": {
        "customer": _STR, "project": _STR,
        "business_name": {"type": "string"},  # 사업명(생략하면 '고객사 프로젝트')
        "purpose": {"type": "string"},  # 사업 목적 문단(생략하면 템플릿 문구)
        "scope": {"type": "array", "items": _STR},  # 사업 범위 항목(생략하면 템플릿 문구)
        "diagnostic_items": {"type": "array", "items": {  # 수행 방법의 '진단 항목' 표(생략하면 템플릿 표)
            "type": "object", "additionalProperties": False, "required": ["code", "name"],
            "properties": {"code": _STR, "name": _STR, "description": {"type": "string"}}}},
        "revisions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["version", "date"],
            "properties": {"version": _STR, "date": _ISO_DATE, "reason": {"type": "string"},
                           "content": {"type": "string"}, "author": {"type": "string"}}}},
        "targets": {"type": "array", "minItems": 1, "items": {
            "type": "object", "additionalProperties": False, "required": ["target_id", "name"],
            "properties": {"target_id": _ID, "name": _STR, "url": {"type": "string"},
                           "kind": {"enum": ["WEB", "Mobile", "HTS"]}}}},
        "schedule": {"type": "object", "additionalProperties": False, "properties": {
            "overall": {"type": "object", "additionalProperties": False, "required": ["start", "end"],
                        "properties": {"start": _ISO_DATE, "end": _ISO_DATE, "note": {"type": "string"}}},
            "phases": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["name", "start", "end"],
                "properties": {"name": _STR, "start": _ISO_DATE, "end": _ISO_DATE,
                               "note": {"type": "string"}}}}}},
        "staff": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["company", "person"],
            "properties": {"company": _STR, "person": _STR, "place": {"type": "string"}}}},
    },
}

FINDINGS = {
    "type": "object", "additionalProperties": False, "required": ["findings"],
    "properties": {
        "improvements": {"type": "array", "items": {  # 3.2 개선 방안(주제별로 직접 작성). 생략하면 취약점별로 구성
            "type": "object", "additionalProperties": False, "required": ["title", "text"],
            "properties": {"title": _STR, "text": _STR}}},
        "findings": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["check_id", "target_id"],
        "properties": {
            "check_id": _ID, "target_id": _ID,
            "name": _STR,  # 보고서에 표시할 취약점명(생략하면 체크리스트 항목명). 권고안 연결의 기준이 된다
            "result": {"type": "string"}, "path": {"type": "string"}, "notes": {"type": "string"},
            "recommendation": {"type": "string"},
            "recommendation_titles": {"type": "array", "items": _STR},  # 권고안 제목이 다를 때 직접 지정
            "cases": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["title"],
                "properties": {"title": _STR, "steps": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "anyOf": [{"required": ["text"]}, {"required": ["title"]}],
                    "properties": {
                        "text": _STR,  # [Step N] 뒤에 쓰는 설명 문장
                        "title": _STR,  # text 의 옛 이름(호환)
                        "code": {"type": "array", "items": {"type": "string"}},  # 회색 음영 코드 문단(줄 단위)
                        "image": {"type": ["string", "null"]},
                        "caption": {"type": "string"}}}}}}},
        }}}},
}
