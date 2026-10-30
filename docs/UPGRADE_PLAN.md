# 업그레이드 작업 계획서 (2026-10-30)

레퍼런스 프로젝트(artex) 대비 구조/정책 격차를 메우는 전체 작업 계획. CLAUDE.md §14 절차(조사 → 최소 수정 → 점진적 구현)를 따른다.

관련 논의: 구조 비교(IA/대시보드), 정책 로직 비교(policy.py vs guard.go/intercept.go). 이번 계획서에서 사용자가 확정한 정책 변경 3건을 포함한다.

---

## A. 정책 엔진 변경 (이번에 확정)

### A1. 도구 호출 위험도 체계 삭제

**삭제 대상 (강제/게이팅 로직만)**
- [src/audit/policy.py](../src/audit/policy.py)
  - `Policy.__init__`의 `self.allowed_risks = set(scope["allowed_risks"])`
  - `authorize()`의 `if spec.risk not in self.allowed_risks: raise PolicyDenied(...)`
  - `authorize()`의 `if spec.risk != "read" and not dry_run: approver(...)` 승인 강제 분기 (approver 콜백 자체는 artex 식 ask로 대체되므로 함께 제거)
- [src/diagnostic_workbench/policy.py](../src/diagnostic_workbench/policy.py) `decide()`
  - `if spec.risk == "read": return ALLOW` 분기 제거
  - `autonomy = ctx.run.get("autonomy", ...)` ~ `ALLOW_WITH_APPROVAL(...위험도...)` 블록 전체 삭제
  - 대체: 스코프/제외/킬스위치 통과 + 인터셉트 규칙 미매치 → **기본 ALLOW** (단, deny 규칙 매치 시 DENY, ask 규칙 매치 시 ALLOW_WITH_APPROVAL — 기존 ask 전용 구조 유지, 3-way 확장은 범위 밖이면 보류)
- [src/audit/schemas.py](../src/audit/schemas.py) `scope_schema`의 `"allowed_risks"` 필수 필드 제거, `tools_schema`의 `"risk": {"enum": [...]}` 요구 제거(또는 선택 필드로 완화)
- [config/scope.json](../config/scope.json) 및 example 파일에서 `allowed_risks` 제거
- [src/webapi/routers/admin.py:103-104](../src/webapi/routers/admin.py) `allowed_risks` 수락/검증 로직 제거
- [src/webapi/routers/admin_extra.py](../src/webapi/routers/admin_extra.py) 도구별 위험도 override API(`risk`, `confirm_lower_risk`, `RISK_ORDER` 비교) 전체 제거 → `disabled` 토글만 남김
- [src/diagnostic_workbench/runs.py:62-89](../src/diagnostic_workbench/runs.py) `RISK_ORDER`, override 병합 시 `risk` 키 제거 → `disabled`만 유지
- `web/src/app/settings/ToolsSection.tsx` 위험도 편집 UI(드롭다운/확인 체크박스) 제거, "사용 안 함" 토글만 남김
- migrations: 스키마 변경 없음(approvals/backlog_items 테이블은 risk를 직접 저장하지 않음) — **DB 마이그레이션 불필요**

**유지 (삭제하지 않음 — 다른 목적으로 쓰이는 read/write 레이블)**
- `ToolSpec.risk` 필드 자체는 존치. 다만 "승인 게이트" 용도가 아니라 **읽기/쓰기 구분 메타데이터**로만 쓰인다:
  - [worker.py:264](../src/diagnostic_workbench/worker.py) 재시도 가능 여부(`spec.risk == "read"`)
  - [worker.py:559,587](../src/diagnostic_workbench/worker.py) 사이드 질문은 읽기 도구만 사용
  - [explore.py:230](../src/diagnostic_workbench/explore.py) bootstrap 1차 탐색이 제안할 수 있는 티어 — `ctx.policy.allowed_risks` 참조를 **하드코딩 `{"read","write"}`** 로 교체(dangerous는 항상 배제, 단 이제는 스코프 승인과 무관하게 고정값)
- `runs.autonomy` 컬럼/필드 유지, 의미 재정의: **"full"일 때 인터셉트 `ask` 매치도 자동 승인**(지금까지 위험도 승인을 건너뛰던 자리를 대신함), "approval"(기본)은 ask 매치 시 그대로 사람 승인 요구. *(가정 — 사용자가 autonomy 토글을 없애라고 하면 쉽게 제거 가능하도록 이 분기 하나로 격리해서 구현)*
- `audit/importer.py`, `audit/schemas.py`의 체크리스트 "위험도(원문)"/`risk-1..5` severity 값은 **완전히 별개 개념**(원본 가이드의 finding 심각도) — 건드리지 않음

### A2. 실패 귀속(Observer/G5) 패턴 적용 — artex 구조 반영

레퍼런스: [artex guard.go `postToolUse`](artex 참고, `guard/guard.go:154-183`) — Bash 결과를 정규식(`403|forbidden|waf|blocked|rate.?limit|429|captcha|denied`)으로 `blocked|error|ok` 분류해 누적.

적용 위치: [src/diagnostic_workbench/worker.py](../src/diagnostic_workbench/worker.py) 도구 실행 결과 처리부(현재 성공/실패만 구분하는 지점).
- 도구 실행 후 출력(stdout/HTTP 응답 본문 등)에 정규식 매칭 → `ok | blocked | error` 분류를 작업 결과 메타데이터에 기록 (새 컬럼 불필요하면 `backlog_items`/`tool_calls` 기존 JSON 메타 컬럼에 저장 — 스키마 조사 후 결정)
- Planner([planner.py](../src/diagnostic_workbench/planner.py))가 동일 작업이 `blocked`로 연속 실패하면 "동일 실패 무한반복 금지"(CLAUDE.md §6) 규칙과 연동해 대체 전략(다른 도구/다른 인자)을 제안하도록 후속작업 생성 로직에 신호 전달
- 분류 패턴은 한국어 사이트 대응을 위해 `차단됨|거부됨|limit` 등 한글 키워드도 추가 검토
- 감사 로그에 분류 결과 기록(CLAUDE.md §8 감사로그 요구사항과 연결)

### A3. 차단 프레이밍 적용 — artex 구조 반영

레퍼런스: [artex guard.go `systemBlockMessage`](artex 참고, `guard/guard.go:135-150`) — "이 차단은 플랫폼 통제이며 대상의 방어가 아니다"를 명시해 에이전트가 우회를 시도하지 않게 유도.

적용 위치: [src/diagnostic_workbench/policy.py](../src/diagnostic_workbench/policy.py) `Decision`이 `DENY`/`ALLOW_WITH_APPROVAL`(미승인 상태)로 LLM에 되돌아가는 지점, 그리고 worker.py가 그 reason을 LLM 프롬프트/후속 계획 입력에 넣는 지점.
- 새 헬퍼 `frame_block_message(reason: str) -> str` 추가 (예: `"[워크벤치 정책 통제 — 대상 방어 아님] {reason}. 이 작업은 허용되지 않음. 다른 방법으로 우회하지 말고 다른 가설/도구를 시도할 것."`)
- **원본 reason은 그대로 감사 로그/DB에 남기고**, 프레이밍 문구는 LLM에게 보내는 메시지에만 씌운다 (artex도 `g.record`에는 raw reason, `hook.Result.Message`에만 프레이밍 — 동일 원칙 유지)
- 적용 지점: `policy.decide()`가 반환하는 `Decision.reason`을 LLM 쪽 입력으로 쓰는 모든 경로(explore.py 부트스트랩 제안 거부 피드백, worker.py 실행 거부 피드백)에서 `frame_block_message()`를 통과시켜 전달

---

## B. 프론트/IA 업그레이드 (이전 계획 유지)

1. **사이드바 재편**: `Header.tsx` 상단 탭 → 좌측 사이드바, `(main)` 레이아웃 그룹, "작업"(대시보드/실행/자산/발견사항/트래픽·증거/명령이력) · "시스템"(LLM/MCP/에이전트/인터셉트/알림/스킬/도구/감사로그) 2분할
2. **작업 화면 분리**: `runs/[id]`에 묻힌 Findings/Traffic을 전역 페이지로 승격
3. **대시보드 강화**: 토큰 사용량 차트 + 활동 피드 + 검토대기(NEEDS_REVIEW) 패널 추가
4. **백엔드 라우터 재편**: `admin.py`/`admin_extra.py`를 agents/mcp/notify/skills/tools로 분리해 프론트 IA와 매칭
5. **디자인 톤**: 다크테마 유지, artex의 밀도 높은 테이블 패턴만 부분 채용

---

## C. 작업 순서 (권장)

| 순서 | 작업 | 이유 |
|---|---|---|
| 1 | A1 위험도 체계 삭제 (백엔드 전용, UI 변경 최소) | 다른 모든 정책 변경의 전제 — 먼저 안정화해야 A2/A3가 깨끗하게 올라감 |
| 2 | 관련 테스트 수정/추가: `test_tools_kill_switch.py`, `test_autonomy_binary.py`, 신규 `test_policy_no_risk_gate.py` | 회귀 방지 |
| 3 | A2 실패 귀속 패턴 | Worker 실행 흐름에 비파괴적으로 추가 가능 |
| 4 | A3 차단 프레이밍 | A1 정리 후 reason 문자열 흐름이 명확해진 상태에서 적용 |
| 5 | B1~B2 (사이드바, 작업화면 분리) | 정책 변경과 독립적, 병행 가능 |
| 6 | B3~B5 (대시보드/라우터/디자인) | 마지막 — 상대적으로 저위험 |

A군(정책)과 B군(IA)은 서로 의존성이 없어 **병렬 진행 가능**.

## D. 리스크 / 확인 필요

- **위험도 게이트 삭제의 실질 효과**: 지금까지 "write/dangerous 도구는 매번 사람 승인"이 기본값이었는데, 삭제 후에는 **인터셉트 규칙에 안 걸리면 바로 실행**된다. 운영 중인 `config/intercept_rules.local.json`에 write성 도구를 충분히 커버하는 규칙이 없다면 사실상 완전 자율 실행이 됨 — 적용 전에 intercept 규칙셋 점검 필요.
- `runs.autonomy` 의미 재정의(위 A1의 "유지" 항목, *가정* 표시)는 사용자 확인 전 임시 설계. 자율모드 토글 자체를 없앨지 여부는 미확정.
- A2 실패 귀속 저장 위치(기존 컬럼 재사용 vs 신규 컬럼)는 `tool_calls`/`backlog_items` 스키마를 실제로 열어본 뒤 결정 — 이번 계획서에는 포함 안 함(구현 착수 시 1차 조사 필요).
