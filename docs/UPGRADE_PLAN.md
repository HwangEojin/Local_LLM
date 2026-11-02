# 업그레이드 작업 계획서 (2026-10-30)

**A1~A3 구현 완료(2026-11-02)**. 실제 구현 시 A1 의 "가정" 표시였던 autonomy 재정의는 **채택하지 않음**으로
확정했다 — 기존 `test_intercept_rules.py`가 "인터셉트 규칙은 자율 모드(full 포함)와 무관하게 항상 사람 승인을
강제한다"를 이미 검증하고 있었고, 그 불변식을 깨는 쪽(autonomy='full'이 인터셉트도 건너뛰게 하는 것)은 운영자가
등록한 통제를 자율 모드로 무력화할 수 있게 만들어 안전성 후퇴라고 판단했다. 결과: `autonomy`는 DB/CLI/UI에
그대로 남지만 `policy.decide()`의 승인 요구 여부에는 더 이상 영향을 주지 않는다(인터셉트 규칙 매치가 유일한
게이트). 상세는 아래 각 항목의 실제 변경 내역 참고.

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

1. **사이드바 재편 — 구현 완료(2026-11-02)**: `Header.tsx` 상단 탭 → 좌측 사이드바(`src/components/Sidebar.tsx`), `(main)` 라우트 그룹(`src/app/(main)/`, URL 불변) 도입, "작업"(대시보드/실행/명령이력) · "시스템"(설정/감사로그) 2분할. `AuthGate`/`KillSwitchBanner` 도 레이아웃으로 올려 각 page.tsx 중복 제거. **자산/발견사항/트래픽·증거는 B2(작업 화면 분리)에서 독립 페이지가 생길 때, LLM/MCP/에이전트/인터셉트/알림/스킬/도구는 B4(라우터 재편)에서 설정 내부 섹션이 독립 URL을 받을 때 사이드바에 추가** — 지금은 실제로 존재하는 라우트만 링크한다. `Header.tsx`는 삭제, 사용처 없음 확인(grep). 브라우저로 7개 라우트(대시보드/실행/실행 상세/새 진단/명령이력/감사로그/설정) 전부 직접 접속해 사이드바·active 상태·콘솔 에러 없음 확인.
2. **작업 화면 분리 — 구현 완료(2026-11-02)**: `GET /api/findings`/`GET /api/traffic`(신규 `src/webapi/routers/global_views.py`, findings/evidence 를 runs 와 조인해 조회만 함, 새 진단 로직 없음) + 프론트 `/findings`·`/traffic` 페이지(사이드바 "작업" 그룹에 추가). 상세·검토·재검증 쓰기 동작은 각 행의 `run_id`로 기존 run-scope 엔드포인트(`FindingPanel`)를 그대로 재사용 — 새 쓰기 경로 없음. `runs/[id]`의 기존 탭은 그대로 둠(실행 범위로 보고 싶을 때 여전히 유용). 백엔드 테스트 3건 추가, 브라우저로 두 페이지 모두 실데이터 렌더링·상세 패널 열림 확인.
3. **대시보드 강화 — 구현 완료(2026-11-02)**: 기존 `model_calls` 테이블(이미 explore.py 가 호출마다 기록)을 `/api/dashboard`에서 집계(`token_daily`/`token_totals`)해 막대 차트로, `/api/admin/audit`를 재사용해 최근 활동 피드, `/api/findings?status=NEEDS_REVIEW`(B2)를 재사용해 검토 대기 패널 추가. 새 테이블·새 쓰기 경로 없음. 백엔드 테스트 1건 추가, 브라우저로 실데이터(토큰 18,186/27건, 활동 8건) 렌더링 확인.
4. **백엔드 라우터 재편 — 구현 완료(2026-11-02)**: `admin.py`/`admin_extra.py`를 `admin_agents.py`/`admin_mcp.py`(기존 파일에 토글 엔드포인트 합침)/`admin_notify.py`/`admin_skills.py`/`admin_tools.py`/`admin_intercept.py`로 분리(프론트 설정 탭과 1:1). `admin.py`에는 scope/workbench/settings/kill-switch/checklists/audit/approvals(5개 카테고리에 안 들어가는 공통 시스템 설정)만 남음. 엔드포인트 URL·동작은 전부 그대로(새 로직 없음) — 전체 테스트 244 passed로 무회귀 확인, 브라우저로 6개 설정 탭(에이전트/도구/MCP/인터셉트/알림/스킬) 전부 재확인.
5. **디자인 톤 — 구현 완료(2026-11-02)**: 다크테마는 유지. artex 참고해 목록 테이블(실행/발견 사항/트래픽/감사 로그) 셀 패딩을 `px-4 py-2` → `px-3 py-1.5`로 좁혀 한 화면에 더 많은 행이 보이게 함. `runs/[id]` 상세 페이지는 테이블과 카드가 같은 패딩 클래스를 섞어 써서 범위에서 제외(건드리면 카드 레이아웃까지 바뀜 — 더 다듬으려면 상세 페이지를 테이블/카드용 클래스로 먼저 분리해야 함).

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

## D. 리스크 / 확인 필요 — 전부 해소됨(2026-11-02)

- **위험도 게이트 삭제의 실질 효과 — 해결**: 점검 결과 `config/intercept_rules.json`(공유, Git 추적)이 비어 있었다(예시 파일만 있고 실제 파일은 없었음) — write/dangerous 도구 39개가 전부 승인 없이 바로 실행되는 상태였다. 등록된 레지스트리를 전수 조사해 write/dangerous 도구를 전부 커버하는 규칙 7개를 추가(`config/intercept_rules.json`), 회귀 테스트(`test_intercept_rules_cover_nonread_tools.py`)로 "새 write/dangerous 도구가 등록됐는데 규칙이 없으면" 테스트가 실패하도록 고정.
- `runs.autonomy` 의미 재정의 — **채택 안 함으로 확정**(위 상단 메모 참고).
- A2 실패 귀속 저장 위치 — **해결**: `tool_invocations.outcome_class` 신규 컬럼(마이그레이션 0019)으로 결정, 구현 완료.
- **Planner 자동 전략 전환 — 추가 구현(2026-11-02)**: PAST_FAILURES로 LLM에 신호만 주는 것만으로는 프롬프트를 무시하고 같은 조합을 또 제안할 수 있어, `worker.blocked_streak()`(같은 실행·자산·도구 조합의 최근 연속 blocked 횟수)를 추가하고 `explore.propose()`의 제안 승인 체인에 연결 — 연속 2회 이상 차단된 조합은 Python이 직접 거부(기존 REJECTED 탐색 노드 기록 경로 재사용, 새 로직 최소화). 테스트 4건 추가(`test_blocked_streak.py`).

## E. MCP/스킬 artex 동일 적용 + 미확인 모듈 재조사(2026-11-02)

재점검(별도 fork 조사)으로 artex `web/src/app/(main)/system/mcp/`·`system/skills/`의 실제 레이아웃·로직과, 이전에 "미확인"으로 남겼던 `llmpool/`·`llmrec/`·`selfupdate/`·`mcphttp/`를 확인했다.

- **MCP stdio 지원 — artex와 동일하게 적용(사용자 확정)**: artex의 stdio(웹에서 받은 command를 이 PC에서 실행) 로직은 애초 CLAUDE.md §8과 충돌한다고 보고했으나, 사용자가 "그대로 옮기기"를 선택해 적용했다. `admin_mcp.py`에 `transport=stdio` 지원 추가, 단 **artex엔 없는 안전장치**로 `confirm_stdio` 별도 확인을 항상 요구하고 env는 raw 값이 아니라 호스트 환경변수 이름(`env_from`)으로만 받는다. 등록 이후의 실제 도구 '호출'은 stdio든 sse든 기존과 동일하게 `policy.decide()`를 통과한다 — 이번 변경은 '누가 서버를 등록할 수 있는지'만 바꿨다. 프론트(`McpAddCard.tsx`)에 전송 방식 선택 UI 추가. 테스트 2건 추가.
- **스킬 ZIP 업로드 — artex와 동일하게 적용**: `admin_skills.py`에 `POST /api/admin/skills/import-zip` 추가. ZIP 안에서 **SKILL.md 하나만** 읽고 scripts/ 등 다른 파일은 열어보지도 않는다(우리 스킬은 LLM 프롬프트 지침일 뿐 실행 대상이 아니므로 애초에 실행 경로가 없음 — 이 설계는 유지). 프론트(`SkillsSection.tsx`)에 파일 업로드 입력 추가. 테스트 1건(scripts/run.sh가 무시되는지 포함) 추가.
- **화면 디자인 — 레이아웃만 참고, 색상은 다크 유지(사용자 확정)**: artex는 카드그리드+Sheet 드로어(MCP), 파일트리+상세(스킬)에 라이트 테마다. 이번엔 기능(stdio, ZIP)만 이식하고 전체 레이아웃 재구성은 하지 않았다 — 시간·비용 대비 가치가 낮다고 판단해 범위에서 뺌(필요하면 별도 작업).
- **`llmpool/`(클라우드 LLM 장애조치 체인)** — 미채택. 기존 세션 판단("크레딧 소진 대응용이라 제외")이 코드로 재확인됨, 로컬 Ollama 중심 구조라 불필요.
- **`llmrec/`(LLM 호출 원문 기록) — 채택, 구현 완료**: `model_calls.request_text`/`response_text`(마이그레이션 0020) 추가, 저장 전 `mask_secrets`로 마스킹(explore.py의 `_ModelLog.record_model_call`, `audit/local_model.py`, `audit/claude_cli_model.py` 세 곳 모두 연결). `GET /api/llm-records` + `/llm-records` 전역 페이지(사이드바 "작업" 그룹에 추가). 이전 조사에서 찾은 "LLM 호출 기록 전역 화면 없음" 갭을 메움. 테스트 1건 추가.
- **`selfupdate/`(바이너리 자가 업데이트)** — 미채택. Python 소스 체크아웃 배포라 전제 자체가 안 맞음.
- **`mcphttp/`(SSE/HTTP MCP 전송 자체 구현)** — 미채택, 이미 더 나은 대안 보유. 우리는 공식 Python MCP SDK(`audit/mcp_client.py`)가 stdio+sse 전송을 이미 지원.
