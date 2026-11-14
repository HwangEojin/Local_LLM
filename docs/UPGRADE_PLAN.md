# 업그레이드 작업 계획서 (2026-10-30)

**추가 후속 삭제(2026-11-13)**: A1 때 "유지(메타데이터로만)"로 남겨뒀던 `ToolSpec.risk`(read/write/dangerous)
필드 자체를 완전히 제거했다 — `read_only: bool` 하나로 축소. `explore.py` 의 `offered_risks`(dangerous 를
LLM 자율 제안에서 항상 빼던 필터)도 함께 삭제해, 이제 셸 도구(`shell.*`)도 LLM 이 자율적으로 제안할 수
있다(사용자 명시적 결정). 실행 시점의 안전판은 바뀌지 않음 — `config/intercept_rules.json` 의
`ask-dangerous-shell` 규칙이 모든 `shell.*` 호출에 여전히 사람 승인을 강제한다. bootstrap 라운드의
"read 전용만 즉시실행"(CLAUDE.md §1/§7)은 `spec.read_only` 로 그대로 보존됨.

**A1~A3 구현 완료(2026-11-02)**. 실제 구현 시 A1 의 "가정" 표시였던 autonomy 재정의는 **채택하지 않음**으로
확정했다 — 기존 `test_intercept_rules.py`가 "인터셉트 규칙은 자율 모드(full 포함)와 무관하게 항상 사람 승인을
강제한다"를 이미 검증하고 있었고, 그 불변식을 깨는 쪽(autonomy='full'이 인터셉트도 건너뛰게 하는 것)은 운영자가
등록한 통제를 자율 모드로 무력화할 수 있게 만들어 안전성 후퇴라고 판단했다. 결과: `autonomy`는 DB/CLI/UI에
그대로 남지만 `policy.decide()`의 승인 요구 여부에는 더 이상 영향을 주지 않는다(인터셉트 규칙 매치가 유일한
게이트). 상세는 아래 각 항목의 실제 변경 내역 참고.

레퍼런스 프로젝트(레퍼런스 프로젝트) 대비 구조/정책 격차를 메우는 전체 작업 계획. CLAUDE.md §14 절차(조사 → 최소 수정 → 점진적 구현)를 따른다.

관련 논의: 구조 비교(IA/대시보드), 정책 로직 비교(policy.py vs guard.go/intercept.go). 이번 계획서에서 사용자가 확정한 정책 변경 3건을 포함한다.

---

## A. 정책 엔진 변경 (이번에 확정)

### A1. 도구 호출 위험도 체계 삭제

**삭제 대상 (강제/게이팅 로직만)**
- [src/audit/policy.py](../src/audit/policy.py)
  - `Policy.__init__`의 `self.allowed_risks = set(scope["allowed_risks"])`
  - `authorize()`의 `if spec.risk not in self.allowed_risks: raise PolicyDenied(...)`
  - `authorize()`의 `if spec.risk != "read" and not dry_run: approver(...)` 승인 강제 분기 (approver 콜백 자체는 레퍼런스 프로젝트 식 ask로 대체되므로 함께 제거)
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

### A2. 실패 귀속(Observer/G5) 패턴 적용 — 레퍼런스 프로젝트 구조 반영

레퍼런스: [레퍼런스 프로젝트 guard.go `postToolUse`](레퍼런스 프로젝트 참고, `guard/guard.go:154-183`) — Bash 결과를 정규식(`403|forbidden|waf|blocked|rate.?limit|429|captcha|denied`)으로 `blocked|error|ok` 분류해 누적.

적용 위치: [src/diagnostic_workbench/worker.py](../src/diagnostic_workbench/worker.py) 도구 실행 결과 처리부(현재 성공/실패만 구분하는 지점).
- 도구 실행 후 출력(stdout/HTTP 응답 본문 등)에 정규식 매칭 → `ok | blocked | error` 분류를 작업 결과 메타데이터에 기록 (새 컬럼 불필요하면 `backlog_items`/`tool_calls` 기존 JSON 메타 컬럼에 저장 — 스키마 조사 후 결정)
- Planner([planner.py](../src/diagnostic_workbench/planner.py))가 동일 작업이 `blocked`로 연속 실패하면 "동일 실패 무한반복 금지"(CLAUDE.md §6) 규칙과 연동해 대체 전략(다른 도구/다른 인자)을 제안하도록 후속작업 생성 로직에 신호 전달
- 분류 패턴은 한국어 사이트 대응을 위해 `차단됨|거부됨|limit` 등 한글 키워드도 추가 검토
- 감사 로그에 분류 결과 기록(CLAUDE.md §8 감사로그 요구사항과 연결)

### A3. 차단 프레이밍 적용 — 레퍼런스 프로젝트 구조 반영

레퍼런스: [레퍼런스 프로젝트 guard.go `systemBlockMessage`](레퍼런스 프로젝트 참고, `guard/guard.go:135-150`) — "이 차단은 플랫폼 통제이며 대상의 방어가 아니다"를 명시해 에이전트가 우회를 시도하지 않게 유도.

적용 위치: [src/diagnostic_workbench/policy.py](../src/diagnostic_workbench/policy.py) `Decision`이 `DENY`/`ALLOW_WITH_APPROVAL`(미승인 상태)로 LLM에 되돌아가는 지점, 그리고 worker.py가 그 reason을 LLM 프롬프트/후속 계획 입력에 넣는 지점.
- 새 헬퍼 `frame_block_message(reason: str) -> str` 추가 (예: `"[워크벤치 정책 통제 — 대상 방어 아님] {reason}. 이 작업은 허용되지 않음. 다른 방법으로 우회하지 말고 다른 가설/도구를 시도할 것."`)
- **원본 reason은 그대로 감사 로그/DB에 남기고**, 프레이밍 문구는 LLM에게 보내는 메시지에만 씌운다 (레퍼런스 프로젝트도 `g.record`에는 raw reason, `hook.Result.Message`에만 프레이밍 — 동일 원칙 유지)
- 적용 지점: `policy.decide()`가 반환하는 `Decision.reason`을 LLM 쪽 입력으로 쓰는 모든 경로(explore.py 부트스트랩 제안 거부 피드백, worker.py 실행 거부 피드백)에서 `frame_block_message()`를 통과시켜 전달

---

## B. 프론트/IA 업그레이드 (이전 계획 유지)

1. **사이드바 재편 — 구현 완료(2026-11-02)**: `Header.tsx` 상단 탭 → 좌측 사이드바(`src/components/Sidebar.tsx`), `(main)` 라우트 그룹(`src/app/(main)/`, URL 불변) 도입, "작업"(대시보드/실행/명령이력) · "시스템"(설정/감사로그) 2분할. `AuthGate`/`KillSwitchBanner` 도 레이아웃으로 올려 각 page.tsx 중복 제거. **자산/발견사항/트래픽·증거는 B2(작업 화면 분리)에서 독립 페이지가 생길 때, LLM/MCP/에이전트/인터셉트/알림/스킬/도구는 B4(라우터 재편)에서 설정 내부 섹션이 독립 URL을 받을 때 사이드바에 추가** — 지금은 실제로 존재하는 라우트만 링크한다. `Header.tsx`는 삭제, 사용처 없음 확인(grep). 브라우저로 7개 라우트(대시보드/실행/실행 상세/새 진단/명령이력/감사로그/설정) 전부 직접 접속해 사이드바·active 상태·콘솔 에러 없음 확인.
2. **작업 화면 분리 — 구현 완료(2026-11-02)**: `GET /api/findings`/`GET /api/traffic`(신규 `src/webapi/routers/global_views.py`, findings/evidence 를 runs 와 조인해 조회만 함, 새 진단 로직 없음) + 프론트 `/findings`·`/traffic` 페이지(사이드바 "작업" 그룹에 추가). 상세·검토·재검증 쓰기 동작은 각 행의 `run_id`로 기존 run-scope 엔드포인트(`FindingPanel`)를 그대로 재사용 — 새 쓰기 경로 없음. `runs/[id]`의 기존 탭은 그대로 둠(실행 범위로 보고 싶을 때 여전히 유용). 백엔드 테스트 3건 추가, 브라우저로 두 페이지 모두 실데이터 렌더링·상세 패널 열림 확인.
3. **대시보드 강화 — 구현 완료(2026-11-02)**: 기존 `model_calls` 테이블(이미 explore.py 가 호출마다 기록)을 `/api/dashboard`에서 집계(`token_daily`/`token_totals`)해 막대 차트로, `/api/admin/audit`를 재사용해 최근 활동 피드, `/api/findings?status=NEEDS_REVIEW`(B2)를 재사용해 검토 대기 패널 추가. 새 테이블·새 쓰기 경로 없음. 백엔드 테스트 1건 추가, 브라우저로 실데이터(토큰 18,186/27건, 활동 8건) 렌더링 확인.
4. **백엔드 라우터 재편 — 구현 완료(2026-11-02)**: `admin.py`/`admin_extra.py`를 `admin_agents.py`/`admin_mcp.py`(기존 파일에 토글 엔드포인트 합침)/`admin_notify.py`/`admin_skills.py`/`admin_tools.py`/`admin_intercept.py`로 분리(프론트 설정 탭과 1:1). `admin.py`에는 scope/workbench/settings/kill-switch/checklists/audit/approvals(5개 카테고리에 안 들어가는 공통 시스템 설정)만 남음. 엔드포인트 URL·동작은 전부 그대로(새 로직 없음) — 전체 테스트 244 passed로 무회귀 확인, 브라우저로 6개 설정 탭(에이전트/도구/MCP/인터셉트/알림/스킬) 전부 재확인.
5. **디자인 톤 — 구현 완료(2026-11-02)**: 다크테마는 유지. 레퍼런스 프로젝트 참고해 목록 테이블(실행/발견 사항/트래픽/감사 로그) 셀 패딩을 `px-4 py-2` → `px-3 py-1.5`로 좁혀 한 화면에 더 많은 행이 보이게 함. `runs/[id]` 상세 페이지는 테이블과 카드가 같은 패딩 클래스를 섞어 써서 범위에서 제외(건드리면 카드 레이아웃까지 바뀜 — 더 다듬으려면 상세 페이지를 테이블/카드용 클래스로 먼저 분리해야 함).

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

## E. MCP/스킬 레퍼런스 프로젝트 동일 적용 + 미확인 모듈 재조사(2026-11-02)

재점검(별도 fork 조사)으로 레퍼런스 프로젝트 `web/src/app/(main)/system/mcp/`·`system/skills/`의 실제 레이아웃·로직과, 이전에 "미확인"으로 남겼던 `llmpool/`·`llmrec/`·`selfupdate/`·`mcphttp/`를 확인했다.

- **MCP stdio 지원 — 레퍼런스 프로젝트와 동일하게 적용(사용자 확정)**: 레퍼런스 프로젝트의 stdio(웹에서 받은 command를 이 PC에서 실행) 로직은 애초 CLAUDE.md §8과 충돌한다고 보고했으나, 사용자가 "그대로 옮기기"를 선택해 적용했다. `admin_mcp.py`에 `transport=stdio` 지원 추가, 단 **레퍼런스 프로젝트엔 없는 안전장치**로 `confirm_stdio` 별도 확인을 항상 요구하고 env는 raw 값이 아니라 호스트 환경변수 이름(`env_from`)으로만 받는다. 등록 이후의 실제 도구 '호출'은 stdio든 sse든 기존과 동일하게 `policy.decide()`를 통과한다 — 이번 변경은 '누가 서버를 등록할 수 있는지'만 바꿨다. 프론트(`McpAddCard.tsx`)에 전송 방식 선택 UI 추가. 테스트 2건 추가.
- **스킬 ZIP 업로드 — 레퍼런스 프로젝트와 동일하게 적용**: `admin_skills.py`에 `POST /api/admin/skills/import-zip` 추가. ZIP 안에서 **SKILL.md 하나만** 읽고 scripts/ 등 다른 파일은 열어보지도 않는다(우리 스킬은 LLM 프롬프트 지침일 뿐 실행 대상이 아니므로 애초에 실행 경로가 없음 — 이 설계는 유지). 프론트(`SkillsSection.tsx`)에 파일 업로드 입력 추가. 테스트 1건(scripts/run.sh가 무시되는지 포함) 추가.
- **화면 디자인 — 레이아웃만 참고, 색상은 다크 유지(사용자 확정)**: 레퍼런스 프로젝트는 카드그리드+Sheet 드로어(MCP), 파일트리+상세(스킬)에 라이트 테마다. 이번엔 기능(stdio, ZIP)만 이식하고 전체 레이아웃 재구성은 하지 않았다 — 시간·비용 대비 가치가 낮다고 판단해 범위에서 뺌(필요하면 별도 작업).
- **`llmpool/`(클라우드 LLM 장애조치 체인)** — 미채택. 기존 세션 판단("크레딧 소진 대응용이라 제외")이 코드로 재확인됨, 로컬 Ollama 중심 구조라 불필요.
- **`llmrec/`(LLM 호출 원문 기록) — 채택, 구현 완료**: `model_calls.request_text`/`response_text`(마이그레이션 0020) 추가, 저장 전 `mask_secrets`로 마스킹(explore.py의 `_ModelLog.record_model_call`, `audit/local_model.py`, `audit/claude_cli_model.py` 세 곳 모두 연결). `GET /api/llm-records` + `/llm-records` 전역 페이지(사이드바 "작업" 그룹에 추가). 이전 조사에서 찾은 "LLM 호출 기록 전역 화면 없음" 갭을 메움. 테스트 1건 추가.
- **`selfupdate/`(바이너리 자가 업데이트)** — 미채택. Python 소스 체크아웃 배포라 전제 자체가 안 맞음.
- **`mcphttp/`(SSE/HTTP MCP 전송 자체 구현)** — 미채택, 이미 더 나은 대안 보유. 우리는 공식 Python MCP SDK(`audit/mcp_client.py`)가 stdio+sse 전송을 이미 지원.

---

## F. 에이전트/MCP/스킬 — 레퍼런스 프로젝트와 "코드~UI 완전 동일" 계획(2026-11-02 작성, 하위 항목(F1~F5) 전부 구현 완료)

사용자가 세 화면을 코드 레벨부터 웹 UI 디자인까지 완벽하게 똑같이 만들라고 요청. MCP·스킬은 핵심 로직(stdio, ZIP)을 이미 이식했으므로(§E) 여기선 **남은 것**(UI 레이아웃 재현)과 **에이전트**(이번에 처음 조사)를 다룬다. 에이전트 쪽은 안전한 추가 기능과 CLAUDE.md 구조와 충돌하는 기능이 섞여 있어 구분해서 적는다.

### F1. MCP 화면 — 레이아웃을 레퍼런스 프로젝트와 동일하게(기능은 이미 동일)

현재: `settings` 페이지 안에 `McpAddCard.tsx`(등록 폼) + 목록(세로 나열 리스트). 레퍼런스 프로젝트: 카드 그리드(`grid-cols-1 sm:2 lg:4`, 맨 앞 점선 "+추가" 카드) → 카드 클릭 시 우측 Sheet 드로어, 드로어 안 Tabs("설정"/"도구(N)").

구현 단계:
1. `web/src/components/ui.tsx`에 `Sheet`(우측 드로어, `ConfirmDialog`와 같은 포지셔닝 패턴 재사용) 컴포넌트 추가 — 없으면 새로 작성, 다크테마 토큰 사용.
2. `settings` MCP 섹션을 카드 그리드로 교체: 각 카드 = 아이콘+이름+transport 뱃지+Switch(켜기/끄기)+삭제, 하단에 도구 개수.
3. 카드 클릭 → Sheet 열기, 탭 2개("설정": 현재 `McpAddCard.tsx` 폼을 편집 모드로 재사용, "도구": 등록된 도구 이름+위험도+scope_args 읽기전용 표로 보여줌 — 새 백엔드 불필요, `GET /api/admin/tools`로 이미 조회 가능).
4. "에이전트별 가시성" 체크박스(레퍼런스 프로젝트 고유 멀티에이전트 개념)는 F3 에이전트 자유생성이 구현될 때만 의미가 생김 — 그 전까지는 이식 보류.

새 백엔드 불필요(API는 §E에서 이미 stdio까지 맞춤). 프론트 전용 작업.

### F2. 스킬 화면 — 레이아웃을 레퍼런스 프로젝트와 동일하게(기능은 이미 동일)

현재: `settings` 페이지 안에 `SkillsSection.tsx`(가져오기 폼 + 세로 카드 목록). 레퍼런스 프로젝트: 좌측 264px 파일트리 + 우측 상세/편집 패널, 상단 "누락된 스킬" 배지.

구현 단계:
1. 좌측 트리: 스킬 이름 목록(우리는 단일 파일 개념이라 레퍼런스 프로젝트의 다중 파일 트리와 달리 평평한 목록이면 충분 — "파일트리처럼 보이는 단일 목록"으로 레이아웃만 맞춤).
2. 우측 패널: 현재 `SkillEditor`를 그대로 옮기되 레이아웃만 2단 분할로 변경.
3. "누락된 스킬" 배지: 우리 스킬은 역할에 자동 첨부되는 구조라(이름으로 호출 안 함) 레퍼런스 프로젝트의 "호출됐는데 없음" 개념 자체가 성립 안 함 — **이식 안 함**(기능이 없는 게 아니라 개념이 안 맞음, §E에서 이미 결론).

새 백엔드 불필요. 프론트 전용 작업.

### F3. 에이전트 화면 — 가장 큼, 안전/위험 기능 분리 필요

**우리 현재**: `admin_agents.py`(역할별 프롬프트 GET/PUT/reset) + `AgentsSection.tsx`(역할 5개 고정: planner/worker/analyst/goals/reporter, 프롬프트 텍스트영역만). 모델 지정은 `/runs/new`에 따로 있음.

**레퍼런스 프로젝트**: 카드 그리드로 에이전트 **무제한 생성**(builtin+custom), 카드 클릭 시 Sheet 드로어에 탭 6개 — 설정+프롬프트(LLM 프로파일·max_turns·run_seconds·web_search·**interactive_shell** 토글, 프롬프트 버전관리+diff), wrapup 프롬프트, MCP 가시성, Skill 가시성, Tool 바인딩, **Triggers**(interval/on_finding/on_goal_met/on_task_timeout/on_tool_call/on_task_create 조건으로 **운영자 개입 없이 새 작업을 자동 기동**).

#### F3a. 충돌 없음 — 그대로 구현 가능

| 기능 | 구현 위치 | 비고 |
|---|---|---|
| LLM 프로파일 개별 바인딩 | `admin_agents.py`에 `llm_profile` 필드 추가, `agent_prompts` 테이블에 컬럼 추가(새 마이그레이션) | 이미 `/runs/new`에 같은 개념(역할별 모델) 있음 — 재사용/통합 검토 |
| 프롬프트 버전관리 + diff | 새 테이블(`agent_prompt_versions`, 기존 `agent_prompts` 갱신 시 이전 값 보존) | `finding_review_history`와 같은 append-only 이력 패턴 재사용 |
| ~~max_turns / run_seconds~~ | **구현 안 함(재검토 결과)** — 레퍼런스 프로젝트는 멀티턴 에이전트 루프가 있어 "턴 수"가 의미 있지만, 이 프로젝트의 worker.py 는 백로그 작업 1건당 LLM 호출 1번(도구 1개 선택)으로 끝나 "턴"이라는 단위가 아예 없다. 유사한 것을 새로 만들면 CLAUDE.md §5/§8-10 "작업수·실행시간 하드리밋은 체크리스트 작업에만 적용, 자율 탐색은 하드리밋 없음"과 충돌한다(이미 있는 max_tasks/max_explorations/budget.timeout_s 가 바로 그 체크리스트 전용 한도). 호출 1건의 타임아웃은 이미 llm_profiles.json 의 profile별 `timeout_s`로 있고 F3a-2(역할별 기본 프로파일)로 역할마다 그 프로파일을 고를 수 있게 됐으니 추가 컬럼 없이 커버됨 |
| web_search 토글 | 현재 web_search 기능 자체가 없음 — 토글 UI만으로는 의미 없음, **토글이 실제로 켜는 기능이 있어야 함**(범위 밖, 별도 과제) |
| ~~Skill 가시성 바인딩~~ | **이미 있음(재확인)** — `agent_skills.roles`(마이그레이션 0017)와 SkillsSection.tsx 의 역할 체크박스로 F2 이전부터 구현돼 있었다. 새로 만들 필요 없음 |
| ~~MCP/Tool 가시성 바인딩~~ | **구현함**(사용자가 보류 권고에도 진행 요청) — `config/tools.local.json`에 `hidden_from: {tool_name: [role,...]}` 추가(스키마: `audit/schemas.py` TOOL_OVERRIDES), `prompts.visible_tools()`가 그 역할의 제안 후보만 걸러내고, `explore.py::_context`(planner)·`worker.py`의 worker-decide(worker) 두 지점에만 적용. `registry()`(실행 경로, policy.decide() 가 보는 전체 목록)는 전혀 안 건드림 — 숨겨도 다른 경로로 호출되면 그대로 판정됨. 도구 설정 화면에 역할별(planner/worker) 체크박스로 노출. MCP 서버는 도구 단위로 이미 이 목록에 개별 항목으로 들어 있어 서버 전체용 별도 토글은 안 만듦(중복) |
| ~~에이전트 "생성"(제한적)~~ | **구현함(2026-11-10)** — 5개 고정 역할은 그대로 두고, 새 테이블 `agent_profiles`(role, name, prompt_overrides jsonb, llm_profile)로 역할마다 이름 있는 프로파일을 여러 개 둘 수 있게 했다. `prompts.get()` 이 `profile_name` 을 받으면 전역 `agent_prompts` 오버라이드보다 먼저 그 프로파일의 재정의를 본다(없으면 전역값, 그것도 없으면 기본값 — 순수 추가 기능). `runs.create_run` 은 `agent_profile_names`(역할→프로파일 이름)를 검증하고, 프로파일의 `llm_profile` 을 명시적 `agent_models` 지정이 없는 역할의 기본값으로만 채운다(명시적 지정이 우선). 설정 화면(에이전트 탭, 역할별 "이름 있는 프로파일" 섹션)에서 만들기/삭제, `/runs/new` 화면에서 역할별 드롭다운으로 선택. 레퍼런스 프로젝트처럼 완전 자유 역할 생성은 여전히 안 함(CLAUDE.md §6 재설계가 필요해서) — 그 절충안 그대로 구현 |

#### F3b. 충돌 있음 — 설계 결정 (2026-11-10 완료, 사용자가 둘 다 "확장 설계부터" 선택)

##### F3b-1. Triggers — 확장 설계(기존 `enrich.py` 패턴을 그대로 재사용) — **구현 완료(2026-11-10)**

핵심 발견: `enrich.py`(자산 보강)가 이미 "LLM 판단 없이 planner 가 매 계획 주기에 스스로 조건을 보고 작업을
큐에 올리는" 바로 그 패턴이다(`enrich.enqueue(ctx)` → `exploration_nodes(origin='enrich')` + `backlog_items
(kind='exploration')` 삽입 → `planner._admit()` 가 다음 줄에서 모든 PENDING 항목에 `policy.decide()` 를
돌려 READY/BLOCKED 를 가린다). Triggers 는 이 패턴을 조건 종류만 늘려 그대로 쓰면 되고, **새 실행 경로나
새 스케줄러를 만들 필요가 없다** — `planner.plan()` 바깥에서 독립적으로 도는 코드가 전혀 없으므로
CLAUDE.md §5/§8 이 요구하는 "예산·Kill Switch 가 그 루프 안을 전제"하는 조건이 설계만으로 자동 만족된다:

- `planner.plan()` 은 오직 `coordinator.run_loop()`(매 반복에서 먼저 `stop_file`/`deadline`/`CANCELLED` 확인)
  또는 `_wait_for_wake()`(하트비트/디바운스 전에 먼저 `_stop_reason()` 확인) 안에서만 호출된다 — 그 바깥에서
  트리거를 평가하는 별도 프로세스를 만들지 않는 한 Kill Switch·시간예산을 벗어날 길이 없다.
- 설사 그 틈에 트리거가 작업을 큐에 넣었더라도 `policy.decide()` 자체가 `cfg.stop_file.exists()` 를
  독립적으로 다시 확인하므로(policy.py:75) 이중으로 막힌다.

**새 테이블** `run_triggers`(마이그레이션 예정, 아직 미적용):
```sql
CREATE TABLE run_triggers (
    id              bigserial PRIMARY KEY,
    run_id          uuid NOT NULL REFERENCES runs(id),
    name            text NOT NULL,
    condition_kind  text NOT NULL CHECK (condition_kind IN
                     ('interval','on_finding','on_goal_met','on_task_timeout','on_tool_call','on_task_create')),
    condition_args  jsonb NOT NULL DEFAULT '{}',
    tool            text NOT NULL,
    args            jsonb NOT NULL DEFAULT '{}',
    target_asset_id uuid REFERENCES assets(id),
    enabled         boolean NOT NULL DEFAULT true,
    last_checked_at timestamptz NOT NULL DEFAULT now(),  -- 조건별 "마지막으로 본 시점" 커서(중복 발화 방지)
    created_by      text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);
```

**새 모듈** `triggers.py`(`enrich.py` 와 같은 모양):
```python
def evaluate(ctx) -> int:
    """활성 트리거를 조건별로 확인해 작업을 큐에 올린다. LLM 을 부르지 않는다(결정론적 규칙)."""
    conn, run_id = ctx.conn, ctx.run["id"]
    accepted = 0
    for t in conn.execute("SELECT * FROM run_triggers WHERE run_id=%s AND enabled", (run_id,)).fetchall():
        fired_key = _check(ctx, t)   # 조건 종류별 분기. 발동했으면 고유 컨텍스트 문자열(예: finding id), 아니면 None
        conn.execute("UPDATE run_triggers SET last_checked_at=now() WHERE id=%s", (t["id"],))
        if fired_key is None:
            continue
        key = f"trigger:{t['id']}:{fired_key}"
        if conn.execute("SELECT 1 FROM backlog_items WHERE run_id=%s AND idempotency_key=%s",
                        (run_id, key)).fetchone():
            continue
        node = conn.execute(
            "INSERT INTO exploration_nodes (run_id, kind, origin, payload) VALUES (%s,'intent','trigger',%s) "
            "RETURNING id", (run_id, Jsonb({"trigger_id": t["id"], "tool": t["tool"], "args": t["args"]}))
        ).fetchone()["id"]
        conn.execute("INSERT INTO exploration_anchors (node_id, asset_id) VALUES (%s,%s)", (node, t["target_asset_id"]))
        conn.execute(
            "INSERT INTO backlog_items (run_id, kind, exploration_node_id, asset_id, priority, idempotency_key, "
            "reason, payload, max_attempts) VALUES (%s,'exploration',%s,%s,70,%s,%s,%s,1) "
            "ON CONFLICT (run_id, idempotency_key) DO NOTHING",
            (run_id, node, t["target_asset_id"], key, f"트리거 '{t['name']}' 발동",
             Jsonb({"tool": t["tool"], "args": t["args"]})))
        accepted += 1
    return accepted
```
`_check(ctx, t)` 조건별 분기(전부 `t["last_checked_at"]` 이후의 새 행만 봄 — 이미 처리한 과거 이벤트가 다시
발동하지 않게):
- `interval`: `now() - t["last_checked_at"] >= condition_args.seconds` 면 발동(fired_key = 그 시각의 분 단위 문자열).
- `on_finding`: `findings WHERE run_id=... AND created_at > last_checked_at` 에 새 행이 있으면 그 `id` 로 발동.
- `on_task_timeout`: `backlog.reclaim_expired()` 가 이미 추적하는 lease 만료 항목 중 `last_checked_at` 이후 것.
- `on_tool_call`: Worker Activity(`traces`)에서 `tool = condition_args.tool` 인 새 호출.
- `on_task_create` / `on_goal_met`: 각각 `backlog_items`/목표-체크리스트 달성 여부를 같은 방식으로 커서 비교.

`planner.plan()` 에 딱 한 줄 추가: `enrich.enqueue(ctx)` 바로 옆에 `"triggered": triggers.evaluate(ctx) if
ctx.cfg.triggers_enabled else 0,`. 체크리스트 작업(`max_tasks`)과 달리 트리거 작업은 탐색(exploration)과
같은 취급 — **하드리밋을 걸지 않음**(CLAUDE.md §5 "자율 탐색은 하드리밋 없음"과 일관). 대신 UI에서 `interval`
최소값(예: 30초)을 강제해 폭주를 막는 소프트 가드만 둔다.

##### F3b-2. interactive_shell — 확장 설계(새 정책 코드 불필요, 기존 게이트에 올리기만 하면 됨) — **구현 완료(2026-11-10)**

핵심 발견: `explore.py::_context()` 의 `offered_risks = {"read", "write"}` 가 이미 `dangerous` 위험도 도구를
LLM 자율 제안에서 항상 제외한다("dangerous 는 항상 뺀다" 주석, explore.py:234). 즉 셸 도구를 `risk="dangerous"`
로 등록만 하면 **Planner/LLM 은 애초에 셸을 자율적으로 열거나 쓸 수 없고**, 운영자가 `collect`(cli)/webapi
의 명시적 단일 호출 경로로만 쓸 수 있다 — 그 경로는 이미 `--approve-as`(읽기 전용이 아닌 도구 승인자 지정)를
요구한다. CLAUDE.md §8-7 "LLM 출력 문자열을 검증 없이 셸로 실행하지 않음"을 지키기 위해 **새 정책 코드를
만들 필요가 없다** — 기존 위험도 분류·collect 승인 경로에 얹기만 하면 된다. 새 실행 경로(소켓을 직접 열어
LLM 출력을 바로 셸에 쓰는 것)를 만들지 않는 것이 유일한 규칙이다.

설계:
1. `tools.py` 에 `shell.open_session`/`shell.send`/`shell.close_session` 세 개를 보통 `ToolSpec`으로 등록
   (`risk="dangerous"`, `scope_args={"host": "host"}` 등 대상과 묶음). `shell.send` 의 `args_schema` 는
   `{"session_id": str, "command": str}` — **명령 문자열도 다른 도구 인자와 똑같이 `ctx.policy.authorize()`/
   인터셉트 규칙을 거친다**: "이 세션에 뭘 써도 되는가"가 아니라 "이 명령 하나"가 매번 독립적으로 판정받는다.
2. 세션 상태(PTY 핸들)는 작은 테이블 `shell_sessions(id, run_id, asset_id, pid, opened_at, closed_at)` +
   같은 Worker 프로세스 안의 레지스트리(dict)로 보관. Worker 프로세스가 재시작되면 세션도 끝남 — 레퍼런스 프로젝트 수준의
   "완전한 영속"은 아니지만, 그 이상(별도 장수 프로세스 관리자)은 이번 단계에서 과하다고 판단해 범위 밖으로 둠.
3. 운영자에게 권장: `shell\.` 패턴의 인터셉트 규칙을 기본으로 추가해 `collect` 호출자 자신 말고 **다른 사람의
   승인**을 강제(선택적 강화 — CLAUDE.md 가 요구하진 않지만 레퍼런스 프로젝트 Go 서버의 실제 검사 여부가 미확인인 채로
   남아 있으니 우리 쪽은 한 겹 더 두는 쪽을 권장).

**구현 완료(2026-11-10)**: 마이그레이션 0023(`run_triggers`)+0024(target_asset_id NOT NULL), `triggers.py`
(조건 6종), `planner.plan()` 연결, `tools.py` 의 `shell.open_session`/`send`/`close_session`(risk="dangerous",
파이프 기반, Kill Switch 반장 스레드), 런 상세 화면의 "트리거" 탭(만들기/켜기·끄기/삭제). `shell_sessions`
테이블은 안 만들었다(F3b-2 설계대로 — 열기/명령/닫기 각 호출이 기존 tool_invocations/evidence 에 그대로
남아 새 테이블이 필요 없었음). 테스트: `test_triggers.py`(7), `test_triggers_api.py`(6), `test_shell_tools.py`
(6) — 실제 planner.plan() 한 사이클로 트리거가 backlog_items(READY)까지 가는 것과 셸 도구가 LLM 자율
제안 프롬프트에 전혀 안 나타나는 것(explore._context 출력 직접 확인)을 끝까지 확인함.

### F5. 커스텀 에이전트(레퍼런스 프로젝트 자유 에이전트 생성, 2026-11-13) — 구현 완료

F3에서 "완전 자유 역할 생성은 CLAUDE.md §6 재설계가 필요해서" 보류했던 부분을 다른 설계로 메웠다. 5개
고정 역할(자율 탐색 루프 자체)은 그대로 두고, Triggers(F3b-1)를 확장해 "운영자가 이름·프롬프트·모델·
허용 도구를 자유롭게 정의하고 무제한 만들고 지울 수 있는 에이전트"를 그 위에 얹었다 — 트리거가 발동할
때 고정 tool/args 대신 그 에이전트의 LLM 이 자기 허용 도구 안에서 직접 고른다. 고른 뒤의 실행 경로는
완전히 같다(backlog_items → policy.decide()).

새 테이블 `custom_agents`(0026), `run_triggers.custom_agent_id`(0027). `triggers.py::_agent_decide()`가
LLM 실패/허용 밖 응답이면 그 발동만 건너뛴다(새 정책 코드 없음). `explore.make_llm_for_spec()`를
`make_llm()` 옆에 독립적으로 추가(기존 5역할 경로 무변경). 웹: 설정→에이전트 화면에 카드그리드+Sheet로
생성/수정/삭제(참조 중인 트리거가 있으면 409로 삭제 차단), 트리거 만들기 폼에 "고정 도구/커스텀 에이전트"
라디오. 테스트: `test_custom_agents_api.py`(7), `test_triggers.py`+4, `test_triggers_api.py`+3 — 전체
308건 통과(기존 무관 실패 1건 제외). 브라우저로 생성→트리거 연결→표시까지 끝까지 확인.

### F4. 권장 순서 — 전부 완료

1. F1(MCP 레이아웃), F2(스킬 레이아웃) — 완료
2. F3a(에이전트 안전 기능: 프롬프트 버전이력, 역할별 기본 모델, 도구/MCP 역할별 가시성, 이름 있는 프로파일) — 완료
3. F3b(Triggers, interactive_shell) — 설계 + 구현 완료(위 F3b-1/F3b-2)
4. F1c(에이전트 레이아웃, 2026-11-10) — **완료**. 레퍼런스 프로젝트 `web/src/app/(main)/system/agents/page.tsx` +
   `agent-editor.tsx` 조사 결과: 카드 그리드(에이전트 목록) + 클릭 시 Sheet 드로어(탭 6개: prompt/wrapup/
   mcp/skill/tools/triggers) 구조. F1(MCP)·F2(스킬) 때 이미 쓴 "카드그리드+Sheet" 패턴을 `AgentsSection.tsx`
   에도 그대로 적용 — 역할 5개를 카드로, 클릭하면 Sheet 안에 탭 3개(프롬프트/프로파일/가시성). 레퍼런스 프로젝트와 다른
   점(의도적):
   - 역할 "카드"가 레퍼런스 프로젝트처럼 무제한 생성되는 에이전트가 아니라 고정 5역할 그대로(F3a 때 이미 확정한 "이름
     있는 프로파일" 절충안과 일관) — 그래서 각 카드에 "삭제" 버튼이 없음(고정 역할이라 지울 수 없음).
   - wrapup 탭 없음 — 그 개념(응답 후 마무리 프롬프트) 자체가 이 프로젝트 구조에 없음(worker.py 가 작업당
     호출 1번으로 끝남, F3a 때 max_turns/run_seconds 안 만든 것과 같은 이유).
   - Triggers 탭 없음 — 우리 트리거는 role 이 아니라 run 단위(`run_triggers.run_id`)라 "이 역할의 트리거"
     라는 개념 자체가 없음(F3b-1 설계: 트리거는 진단 실행 화면의 "트리거" 탭에서 관리).
   - MCP/Skill/Tools 세 탭을 "가시성" 한 탭으로 합침 — 우리는 opt-in 허용목록이 아니라 opt-out
     숨기기(hidden_from)/스킬은 이미 role 배열 방식이라, 체크박스 두 목록(스킬/도구)이면 충분해서 탭을
     나눌 필요가 없었음. 새 백엔드 코드 없음 — 기존 `/api/admin/skills`·`/api/admin/tools` PUT 을 그대로
     재사용(다른 화면의 편집 로직과 중복 없음, 같은 API를 다른 화면에서도 부르는 것뿐).

---

## G. 발견 사항 품질·검토·이력 업그레이드 (2026-11-13 작성, 결정 D1~D7 확정, G8 작업표 전부 구현 완료)

외부 제안서(GPT 작성 "Local LLM Security Workbench" 개선안)를 커밋 `fbc353c` 기준 실제 코드와 대조해
**이미 있는 것 / 원칙과 충돌하는 것 / 진짜 공백**으로 나눈 뒤, 진짜 공백만 작업 단위로 옮긴 계획이다.
CLAUDE.md §14 절차(조사 → 비교 → 최소 수정 계획 → 점진적 구현)를 따르며, 아래 "조사 결과"는 실제로 코드를
열어 확인한 것만 적었다.

### G0. 제안서 대조 결과 (구현 대상 선정 근거)

**이미 구현됨 — 다시 만들지 않음**

| 제안 | 실제 위치 |
|---|---|
| 승인 만료 | `approvals.expires_at`(0001), `policy.py:107` 이 미만료 승인만 인정 |
| Kill Switch UI | `admin.py` `/kill-switch/engage·release`, `data/STOP` |
| Worker 장애 복구 | `lease_s` 만료 → READY, 시도 초과 → FAILED (`backlog.reclaim_expired`) |
| 작업 재개 / 재시도 / 승인 | `run start`(PAUSED·COMPLETED 재개), `backlog/{id}/retry`, `retry-all`, `approve` |
| 반복 실패 방지 | `worker.blocked_streak()` + Planner 전략 전환(§D) |
| 보고서 승인 | `POST /runs/{run}/report/{filename}/approve` |
| 내보내기 | `GET /runs/{run}/findings/export`, `GET /api/admin/audit/export` |
| 재검증(retest) | `worker.create_retest()` + `_resolve_retest()` → `finding_review_history` 에 REPRODUCED/FIXED/INCONCLUSIVE |
| 실행 단위 증적 뷰어 | `runs/[id]` Evidence 탭 `EvidenceViewer`, `/traffic` |
| 모델 호출 지표 원천 | `model_calls`(duration_ms, ok, 토큰), 대시보드 `token_daily` |

**원칙 충돌 — 채택 안 함(사유 기록)**

- *자율 탐색 도구 호출·비용 하드 예산*: CLAUDE.md §5/§8-10 의 "자율 탐색은 하드리밋 없음" 결정과 정면 충돌.
  로컬 Ollama 라 비용 개념도 없음. 무의미 반복은 `blocked_streak`·중복 감지·벽시계 타임아웃이 이미 담당.
- *동시 실행 제한*: Worker 순차 실행이 설계 결정이라 해당 문제 자체가 없음.
- *AI 진단 지식베이스(과거 판정 재사용)*: §3 "관찰과 추론 구분", §9 확정 조건과 충돌 위험이 커서 보류(G7).

**진짜 공백 — 이번 계획의 대상**

1. 탐색 finding 에 중복 방지 키가 없다. `findings.duplicate_of`(0001)는 `src/` 어디서도 쓰이지 않고,
   `UNIQUE(run_id, run_checklist_item_id)` 는 체크리스트 항목이 NULL 인 탐색 finding 을 걸러내지 못한다
   (`worker._create_finding`, `worker.create_llm_finding` 둘 다 무조건 INSERT). 같은 지점의 같은 SQLi 가
   한 실행 안에서 여러 행이 될 수 있고, LLM finding 의 `title` 은 자유 문자열이라 비교 기준도 없다. → **G1**
2. `create_llm_finding()` 은 모델 하나가 실존 증거 id 만 인용하면 바로 `status='CONFIRMED'` 를 쓴다.
   현재 가드레일에서 가장 얇은 지점. → **G2**
3. CLAUDE.md §10 이 요구하는 "설명/대응방안 수정"이 없다(심각도·영향도는 D5·D7 로 대상에서 빠짐) — `POST .../review` 는
   `status` 만 바꾼다(`worker.set_finding_status`). → **G3**
4. `web/src/app/(main)/runs/[id]/page.tsx` 가 1,444줄 단일 파일(탭 컴포넌트 13개 동거). → **G4**
5. 증적→도구 호출→작업→탐색 노드→finding 의 출처 체인과, 실행을 가로지르는 자산 화면이 없다(데이터는 있음). → **G5**
6. 같은 자산의 이전/현재 실행 비교가 없다. G1 의 fingerprint 가 전제. → **G6**

### G-결정. 구현 전 사용자 확인 필요 사항

**전부 사용자 확정(2026-11-13)** — 남은 미결 사항 없음. 각 행의 "결정"이 구현 기준이며, 바꾸려면 이 표부터
고친 뒤 해당 단계를 수정한다.

| ID | 질문 | 결정 | 영향 단계 |
|---|---|---|---|
| D1 | 경로를 어디까지 같은 것으로 볼지 | **확정: 정확한 경로** — 쿼리스트링만 제거하고 경로 문자열은 그대로 비교(ID 세그먼트 치환·소문자화·끝 `/` 제거 모두 하지 않음). `/api/user/123/profile` 과 `/api/user/456/profile` 은 다른 finding. 잘못 합쳐지는 일이 없는 대신 객체 ID 마다 중복이 남는 것은 감수한다(사용자 선택, 권장안은 ID 치환이었음) | G1 |
| D2 | `vuln_class` 를 모르는 LLM finding(OTHER) 처리 | **확정: 중복 의심만 표시** — 자동 병합 없음(fingerprint NULL). 분류를 뺀 위치 해시 `locator_hash` 가 같은 finding 끼리 "중복 의심" 배지를 띄우고 사람이 한 번에 병합 | G1 |
| D3 | 같은 실행에서 REJECTED 된 finding 이 다시 관찰되면 | **확정: 새 종류 근거일 때만 재검토** — 재관찰 근거의 종류(RULE / LLM)가 그 finding 에 이미 있던 근거 종류에 없으면 NEEDS_REVIEW 로 재오픈하고 알림. 같은 종류면 REJECTED 유지, 이력·감사 이벤트만 기록 | G1 |
| D3b | **다른 실행**에서 기각된 것과 fingerprint 가 같은 finding 이 새 실행에서 나오면 | **확정: 아무것도 하지 않음** — 실행끼리 완전히 독립. 이전 기각 정보를 힌트로도 붙이지 않는다(권장안은 힌트 표시였음). G6 실행 비교 화면에서 기준 실행의 상태를 보여 주는 것은 이 결정과 별개로 유지 | G1, G6 |
| D1+ | D2 "중복 의심" 표시에만 ID 세그먼트 치환 경로를 쓸지 | **확정: 쓰지 않음** — 의심 표시도 D1 과 같은 정확한 경로 기준. 객체 ID 만 다른 같은 취약점은 목록에서 사람이 찾는다 | G1 |
| D4 | 교차 검증 모델 미지정 실행의 LLM finding 처리 | **확정: 현행 유지** — 검증 모델이 없으면 지금처럼 단일 모델이 `LLM_CONFIRMED`/`CONFIRMED`. 교차 검증은 실행별로 켜는 선택 기능. 알려진 위험(검증 모델을 지정하지 않으면 LLM 단독 확정이 그대로 남음)은 감수 — 대신 실행 생성 화면에 "교차 검증 없음" 안내 문구를 둔다(G2) | G2 |
| D5 | "영향도(impact)" 필드 신설 여부 | **확정: 만들지 않음** — `pentest_report.py` 에 영향도 출력 자리가 없고 회사 템플릿도 저장소 밖이라 확인 불가. 템플릿에 칸이 생기면 그때 컬럼 1개 + 편집 필드 1개로 추가 | G3 |
| D6 | 보고서 승인 후 finding 편집 허용 여부 | **확정: 편집 허용 + 보고서 경고** — 승인된 보고서에 "승인 이후 편집됨 — 재생성 필요" 표시 | G3 |
| D7 | finding 심각도(severity) | **확정: finding 의 심각도 개념 자체를 제거** — 저장·표시·필터·알림 기준·LLM 스키마에서 모두 뺀다. 기존 DB 값은 보존하고 사용만 중단. 체크리스트 **항목**의 원문 위험도(`checklist_items.severity`, `risk-1..5`)는 공식 기준 원문 속성이라 유지(CLAUDE.md §13 원본 임의 수정 금지, A1 때 원문 위험도를 별개 개념으로 남긴 것과 같은 판단) → **G-S** | G-S, G1, G2, G3 |

### G-S. Finding 심각도(severity) 제거 — P1, G1 보다 먼저 (D7) — **구현 완료(2026-11-13)**

**목표**: finding 에서 심각도 개념을 없앤다. finding 은 "무엇이(분류·위치) 어떤 근거로(증거·판정 주체) 어떤
상태인가"만 가진다. A1(도구 위험도 삭제)과 같은 방식으로 **강제·표시 로직은 지우고, 기존 데이터와 공식 기준
원문은 보존**한다. G1 이 `vuln_class` 를 도입하므로 심각도가 하던 "분류·집계" 역할은 분류로 대체한다.

**구현 결과(계획과 달라진 점)**: 아래 "처리" 표의 "→ `vuln_class` 로 교체" 항목들(대시보드 `by_severity`, 발견
사항 내보내기 열, `?severity=` 필터)은 **교체하지 않고 그냥 제거**했다 — `vuln_class` 는 아직 G1 에서 도입할
컬럼이라 이 단계에는 존재하지 않는다(현재는 RULE 경로의 지역 변수 `vclass` 뿐, DB 컬럼도 LLM/체크리스트
경로의 분류값도 없음). G1 이 `findings.vuln_class` 를 실제로 추가하면 그때 대시보드 카드·내보내기 열·필터를
새로 붙인다 — 지금 억지로 만들면 G1 때 다시 갈아엎어야 하는 중복 작업이라 범위에서 뺐다. 마이그레이션
0028 적용. 전체 테스트 313 passed(신규 `test_severity_retired.py` 6건 포함), TypeScript 컴파일·브라우저
수동 확인(대시보드/발견 사항/실행 상세/알림 설정)에서 심각도 UI 전부 사라진 것과 기능 정상 동작 확인.
`config/mcp_servers.json` 류와 달리 이 작업은 이 PC 에 국한되지 않는 코드 변경이라 커밋에 포함.

**조사 결과(커밋 `fbc353c`)** — finding 심각도가 쓰이는 곳 전부:

| 위치 | 현재 용도 | 처리 |
|---|---|---|
| `findings.severity`(0001, NOT NULL) | 저장 | NOT NULL 해제, 새 행은 쓰지 않음. 기존 값 보존 |
| `worker._create_finding()`(worker.py:509-511) | `signals.SEVERITY[vclass]` 기록 | 컬럼 제외 |
| `worker.create_llm_finding()`(worker.py:251-269) | 인자·INSERT | 인자·컬럼 제거 |
| `worker._run_checklist()`(worker.py:456-459), `cli.py:392-396` | 체크리스트 FAIL → finding 에 항목 위험도 복사 | 복사 중단(원문 위험도는 `checklist_items` 에 그대로 있음) |
| `audit/signals.py:286` `SEVERITY` | 신호 분류별 기본 심각도 | 삭제 |
| `explore.py:45-47` `_FINDING_ITEM` | LLM 출력 스키마 required | 필드 제거 + 시스템 프롬프트 문구 정리 |
| `explore.py:527,533` | LLM finding 생성·알림 | 인자 제거 |
| `notify.py:47-69` `_SEVERITY_RANK`·`_passes_severity`·`notify_finding_confirmed(severity=)` | 채널별 최소 심각도 필터, 알림 본문 | 필터 폐지(확정 알림은 활성 채널 전부로), 본문에서 제거 → 본문에 `vuln_class` 추가 |
| `schemas.py:296` `NOTIFY_CHANNEL.min_severity` | 알림 설정 검증 | **스키마에는 남기되 무시**(`additionalProperties: False` 라 지우면 기존 `notify.json` 이 검증 실패함). `doctor` 가 "min_severity 는 더 이상 쓰이지 않음" 경고 |
| `admin_notify.py:26,62,93-105` | min_severity 조회·저장 | API 필드 제거, 저장 시 기존 키도 지움 |
| `findings.py:20` `_EXPORT_COLS`, `findings.py:107` 알림 호출 | 내보내기 열, 알림 인자 | 열 제거(→ `vuln_class` 추가), 인자 제거 |
| `global_views.py:16-28` | `?severity=` 필터 | 제거 → `?vuln_class=` 필터 |
| `dashboard.py:21-23,45` `by_severity` | 확정 finding 심각도별 집계 | `by_vuln_class` 로 교체 |
| `cli.py:517` `finding list` | `[severity]` 출력 | `[vuln_class]` 출력 |
| `reporting.py:28` Reporter 다듬기 프롬프트 | "severity 를 추가하지 말 것" 문구 | 문구만 정리(동작 동일). 자율 탐색 보고서(`pentest_report`)는 원래 finding 심각도를 쓰지 않음 — 확인함 |
| 웹: `lib/api.ts:103,239,466,472`, `findings/page.tsx`(필터·정렬·열), `runs/[id]/page.tsx:218,303,398-402,431,462,485`(요약·필터·정렬·열), `dashboard/page.tsx:74,104-137`, `FindingPanel.tsx:231`, `settings/NotificationsSection.tsx`(최소 심각도 선택·안내 문구), `lib/labels.ts:27-39`(`SEVERITY_ORDER`·라벨) | 표시·필터·정렬 | 심각도 열·필터·정렬 제거 → 분류(`vuln_class`) 열·필터, 정렬은 상태 → 최근 관찰 순. `SEVERITY_ORDER` 와 심각도 라벨 삭제 |

**유지(지우지 않음)**
- `checklist_items.severity` 와 `audit/importer.py:170`(`risk-1..5`), `audit/schemas.py:233` 체크리스트 항목 스키마,
  `audit/report.py` 체크리스트 보고서의 "심각도" 열(18-19, 123, 154-155, 209, 222-227) — 전부 **공식 평가기준
  원문 항목의 위험도**이고 finding 판정이 아니다. 체크리스트 보고서는 이 값을 `checklist_items` 에서 읽으므로
  finding 쪽 변경과 무관하게 그대로 동작한다.
- `findings.severity` 컬럼과 기존 값(삭제·백필 없음, 기존 데이터 보존 원칙 §11).

**DB — `migrations/0028_finding_severity_retired.sql`**
```sql
-- D7: finding 심각도 개념 폐지. 값은 보존하고 새 행에는 쓰지 않는다(체크리스트 항목 원문 위험도는 별개, 유지).
ALTER TABLE findings ALTER COLUMN severity DROP NOT NULL;
COMMENT ON COLUMN findings.severity IS 'retired 2026-11 (D7): 읽기 전용 과거 값. 새 finding 은 NULL';
```

**문서**: CLAUDE.md §10 검토자 작업 목록에서 "심각도/영향도 수정" 삭제, README §4 의 `finding list` 설명·
§2 의 `notify.json` 설명에서 심각도 언급 정리. PRODUCT.md 는 심각도 언급 없음(확인함).

**테스트** — 기존 테스트 중 severity 를 쓰는 12개 파일 수정: `test_autonomous_run.py`, `test_dashboard_api.py`,
`test_finding_review.py`, `test_findings_export.py`, `test_llm_finding.py`, `test_notify.py`(min_severity 필터 →
"설정에 남아 있어도 무시되고 전 채널로 감" 으로 바뀜), `test_real_mcp_sqli.py`, `test_retest_verdict.py`,
`test_rule_overlays.py`, `test_settings_menus.py`, `test_triggers.py`, fixture
`tests/fixtures/checklist/versions/sample-local-config/0.1.0.json`(체크리스트 항목 severity 는 **유지** — 수정
불필요할 수 있음, 확인 후 판단). 신규 `test_severity_retired.py`: 새 finding 3경로(규칙·LLM·체크리스트 FAIL)
모두 severity NULL, 기존 행 값 보존, LLM 응답에 severity 필드가 들어 있으면 `_FINDING_ITEM` 스키마 위반으로
거부되는지(`additionalProperties: False` 유지 확인), min_severity 가 남은 notify.json 이 검증을 통과하고 doctor
가 경고를 내는지.

**완료 기준**: `grep -rn "severity" src web/src` 결과가 체크리스트 항목 경로(`importer.py`, `report.py`,
`schemas.py` 체크리스트·알림 deprecated 항목, `runs.py:117-120` 체크리스트 등록)만 남음 + 전체 테스트 통과 +
브라우저에서 발견 사항 목록·실행 상세·대시보드·알림 설정에 심각도가 보이지 않는지 확인.

### G1. Finding fingerprint 와 중복 제거 — P1 — **구현 완료(2026-11-13)**

**구현 결과(계획과 달라진 점)**: `upsert_exploration_finding(conn, cfg, run_id, asset_id, ...)` — 계획에
`cfg` 가 빠져 있었는데 D3 재오픈 알림(`notify.notify_finding_reopened`)이 `cfg.home` 으로 `notify.json`
을 읽어야 해서 추가했다. 반환값도 `finding_id` 하나가 아니라 `(finding_id, created)` 로 바꿨다 — 새로
만들었는지를 호출자가 알아야 "최초 확정" 이력/알림을 한 번만 남기고, 병합된 경우 중복으로 다시 남기지
않을 수 있어서다. `_create_finding` 은 병합된 경우에도 `exploration_anchors` 로 기존 finding_node 를 찾아
새 `proves` 엣지만 긋는다(병합돼도 그 fact 가 이 finding 을 뒷받침한다는 사실은 남겨야 함 — 계획엔
명시 안 됐지만 G5 출처 체인과 일관되게 자연스러운 확장). 마이그레이션 0029 적용, 전체 테스트
333 passed(신규 3개 파일 24건 포함), TypeScript 컴파일·브라우저로 발견 사항 패널(위치/발생 횟수/근거
종류/중복 의심 섹션) 확인.

**목표**: 같은 실행에서 같은 자산·같은 취약점 클래스·같은 주입 지점이 다시 관찰되면 새 finding 을 만들지 않고
기존 finding 에 증거만 더한다. 실행을 가로질러서도 같은 식별자를 써서 G6 비교의 기준이 되게 한다.

**fingerprint 정의**
```
fingerprint  = sha256( asset_id | vuln_class | METHOD | path | sorted(param_names) )   -- 자동 병합 기준
locator_hash = sha256( asset_id |              METHOD | path | sorted(param_names) )   -- "중복 의심" 기준(D2)
```
- `path` 는 D1 에 따라 **쿼리스트링만 떼어 낸 원래 경로 문자열**이다. 퍼센트 인코딩도 풀지 않는다(풀면
  `%2F` 와 `/` 가 같아지는 등 서로 다른 요청이 합쳐질 수 있음).
- `fingerprint` 는 `vuln_class` 가 7개 신호 분류(`signals.SIGNALS`) 중 하나일 때만 만든다. `OTHER` 이거나
  체크리스트 finding 이면 NULL.
- `locator_hash` 는 분류와 무관하게 위치를 얻을 수 있으면 항상 만든다. 같은 위치에서 분류가 다른 finding
  (예: OTHER 와 SQLI, 또는 OTHER 두 건)을 사람에게 "중복 의심"으로 보여 주는 용도이고, 자동 병합에는 쓰지 않는다.
- `asset_id` 를 넣는 이유: `assets` 가 `UNIQUE(company, kind, value)`(0015)라 회사 격리가 자동으로 따라온다.
  `run_id` 는 넣지 않는다 — 실행을 가로지르는 동일성 판단(G6)에 그대로 쓰기 위해.
- `METHOD`/`path`/`param_names` 는 **Python 이 증거의 원 인자에서 추출**한다. LLM 이 적은
  값은 쓰지 않는다(신뢰 경계 유지, §8-5).
  - 원시 HTTP 요청이 있으면 `signals._raw_http_request(args)` 의 요청 줄에서 메서드·경로를 얻는다.
  - 없으면 `args` 의 `url` 류 값에서 경로를 얻고 메서드는 `GET` 으로 둔다.
  - `param_names` 는 `signals._INJECTION_PATTERNS` 에 걸리는 값을 가진 키 이름들(새 헬퍼, 아래).
- 추출 실패(경로도 매개변수도 못 얻음) 시 fingerprint 는 NULL — 중복 제거 대상에서 빠지고 기존처럼 새 행.
  "모르면 합치지 않는다"가 기본(잘못 합쳐 증거가 묻히는 쪽이 중복보다 나쁨).

**DB — `migrations/0029_finding_fingerprint.sql`**
```sql
ALTER TABLE findings ADD COLUMN vuln_class       text;          -- SQLI/XSS/.../OTHER, 체크리스트 finding 은 NULL
ALTER TABLE findings ADD COLUMN fingerprint      text;
ALTER TABLE findings ADD COLUMN locator          jsonb NOT NULL DEFAULT '{}';  -- {method, path, params} 표시용
ALTER TABLE findings ADD COLUMN occurrence_count integer NOT NULL DEFAULT 1;
ALTER TABLE findings ADD COLUMN last_seen_at     timestamptz NOT NULL DEFAULT now();
ALTER TABLE findings ADD COLUMN locator_hash     text;          -- D2 "중복 의심" 묶음
-- D3: 이 finding 을 뒷받침한 근거 종류 집합. 'RULE'(결정론적 신호) | 'LLM'(LLM 주장). 사람 확정은 상태 이력에 이미 남음.
ALTER TABLE findings ADD COLUMN evidence_kinds   text[] NOT NULL DEFAULT '{}';
UPDATE findings SET evidence_kinds = CASE verification
    WHEN 'RULE_CONFIRMED' THEN ARRAY['RULE'] WHEN 'LLM_CONFIRMED' THEN ARRAY['LLM'] ELSE '{}' END;

-- 백필: _create_finding 이 남긴 제목 형식("탐색 기반 발견: <CLASS>")에서만 vuln_class 를 복원한다.
-- fingerprint 는 백필하지 않는다 — 과거 행의 주입 지점을 확실히 복원할 수 없음(모르면 합치지 않음).
UPDATE findings SET vuln_class = substring(title FROM '^탐색 기반 발견: (\w+)$')
 WHERE run_checklist_item_id IS NULL AND title ~ '^탐색 기반 발견: \w+$';

CREATE UNIQUE INDEX findings_run_fingerprint_live
    ON findings (run_id, fingerprint) WHERE fingerprint IS NOT NULL AND duplicate_of IS NULL;
CREATE INDEX ON findings (fingerprint) WHERE fingerprint IS NOT NULL;   -- G6 실행 간 조회용
CREATE INDEX ON findings (run_id, locator_hash) WHERE locator_hash IS NOT NULL AND duplicate_of IS NULL;
```
기존 행 삭제 없음(추가 컬럼 + 제목 기반 vuln_class·verification 기반 evidence_kinds 백필만).
`finding_evidence`/이력 테이블 무변경.

**코드**
- 새 모듈 `src/diagnostic_workbench/fingerprint.py` — DB·LLM 의존 없는 순수 함수만:
  - `strip_query(path) -> str` (D1: 쿼리스트링 제거만, 나머지는 원문 유지)
  - `locate(args: dict) -> dict | None` → `{"method", "path", "params"}`
  - `compute(asset_id, vuln_class, args) -> tuple[str | None, str | None, dict]` → `(fingerprint, locator_hash, locator)`
- `src/audit/signals.py` — `injection_params(args) -> list[str]` 추가(기존 `_INJECTION_PATTERNS`·
  `_flatten_strings` 재사용). 기존 함수 동작 변경 없음.
- `src/diagnostic_workbench/worker.py`
  - 새 `upsert_exploration_finding(conn, run_id, asset_id, *, vuln_class, args, evidence_kind, insert_fn,
    evidence_ids, actor)` (`evidence_kind` 는 `'RULE'` 또는 `'LLM'`):
    `SELECT ... FOR UPDATE` 로 `(run_id, fingerprint, duplicate_of IS NULL)` 기존 행을 찾고,
    - 있으면: `finding_evidence` 에 `ON CONFLICT DO NOTHING` 으로 증거 추가, `occurrence_count += 1`,
      `last_seen_at = now()`, `finding_review_history` 에 `old=new=현재 상태`,
      `reason='동일 지점 재관찰(증거 N건 추가, 근거 종류 <kind>)'`, `actor='system:dedup'` 1행.
      REJECTED 가 아니면 **상태는 바꾸지 않는다.**
    - 기존 행이 REJECTED 면 D3 규칙:
      - `evidence_kind ∉ evidence_kinds` (사람이 기각할 때 없던 종류의 근거) → 기존
        `set_finding_status(..., 'NEEDS_REVIEW', reason='기각 후 새 종류 근거(<kind>) 관찰 — 재검토 필요',
        actor='system:dedup')` 로 재오픈(이력은 그 함수가 남김), 감사 이벤트 `finding_reopened_new_evidence_kind`,
        새 `notify.notify_finding_reopened()`(기존 `notify_finding_confirmed` 와 같은 웹훅 채널 재사용. 심각도
        필터는 G-S 로 폐지돼 활성 채널 전부로 감).
      - `evidence_kind ∈ evidence_kinds` (사람이 이미 본 종류) → REJECTED 유지, 위 재관찰 이력 + 감사 이벤트
        `finding_reobserved_after_reject` 만.
      - 재오픈 뒤 다시 기각되면, 그때는 두 종류 모두 `evidence_kinds` 에 들어 있으므로 같은 근거로 또 열리지 않는다
        (같은 오탐이 기각할 때마다 다시 열리는 루프 방지).
    - 어느 분기든 끝에 `evidence_kinds = evidence_kinds ∪ {evidence_kind}`.
    - D3b: 이 함수는 **같은 run_id 안에서만** 찾는다. 다른 실행의 같은 fingerprint 는 조회하지도 참조하지도 않는다.
    - 없으면: 기존 INSERT 경로를 그대로 타고 새 컬럼만 채운다. 동시 삽입 경합은 부분 UNIQUE 인덱스가
      막으므로 `UniqueViolation` 시 재조회 후 "있으면" 분기로 한 번만 재시도.
  - `_create_finding()`: 위 함수 경유로 변경(`vuln_class` 는 이미 인자로 받음, args 는 `it` 의 호출 인자).
  - `create_llm_finding()`: `vuln_class` 인자 추가. 주입 지점은 **인용한 첫 증거의 `evidence.args`** 에서
    추출. `vuln_class == 'OTHER'` 면 D2 대로 fingerprint NULL(자동 병합 안 함) — 이때도 `locator_hash` 는 채운다.
    G2(교차 검증)가 붙으면 이 경로의 `evidence_kind` 는 `'LLM'` 그대로다(교차 검증은 판정 상태만 바꿈).
- 중복 의심 조회(D2) — `findings.py`
  - 목록 응답에 finding 별 `suspected_duplicates`(같은 run·같은 `locator_hash`·`duplicate_of IS NULL`·자기 자신
    제외 건수)를 붙인다. 자동 병합(fingerprint 일치)된 것은 이미 한 행이라 여기 나오지 않는다.
  - `GET /runs/{run}/findings/{id}/similar` → 그 후보 목록(제목·분류·상태·증거 수).
- `src/diagnostic_workbench/explore.py` — `_FINDING_ITEM` 스키마에 `"vuln_class": {"enum": [*signals.SIGNALS, "OTHER"]}`
  를 required 로 추가, `_report_llm_finding()` 이 그대로 넘김. 시스템 프롬프트에 한 줄 설명 추가.
- 수동 병합/해제 — `src/webapi/routers/findings.py`
  - `POST /runs/{run}/findings/{id}/merge` `{into, reason}`: 같은 run, 대상이 자신이 아님, 대상이 이미
    중복이 아님(체인 금지)을 확인. `duplicate_of=into` 설정, 원본의 증거를 대상에도 링크(원본 링크는
    보존), 양쪽 `finding_review_history` 에 기록, 감사 이벤트 `finding_merged`.
  - `POST /runs/{run}/findings/{id}/unmerge` `{reason}`: `duplicate_of=NULL`, 이력 기록. 증거 링크는 되돌리지
    않는다(추가만 하는 원칙).
- 목록·보고서에서 중복 제외: `findings.py` 목록과 `global_views.py` `/api/findings` 에 `include_duplicates`
  (기본 false), `reporting._findings_for_pentest_report()`(reporting.py:259) 조건에 `AND f.duplicate_of IS NULL`.

**UI** — `FindingPanel.tsx`: 위치(locator)·발생 횟수·최근 관찰 시각·근거 종류(규칙/LLM) 표시, "다른
finding 으로 병합" 선택 상자(같은 `locator_hash` 후보를 맨 위에), 병합된 finding 은 목록에서 기본 숨김 +
"중복 N건" 배지. 목록 행에 `suspected_duplicates > 0` 이면 "⚠ 중복 의심 N건" 배지(텍스트 포함, 색만으로
구분하지 않음) — 클릭하면 후보 목록과 "이 finding 으로 병합" 버튼. D3 로 재오픈된 finding 은 "기각 후 재관찰"
배지와 재오픈 사유를 상단에 표시.

**테스트**
- 신규 `tests/unit/test_finding_fingerprint.py`: 쿼리 제거, **경로는 원문 유지**(`/user/1` ≠ `/user/2`,
  `/Admin` ≠ `/admin`, `/a/` ≠ `/a`, `%2F` 미해석), 원시 HTTP 요청·url 인자 양쪽, 매개변수 순서 무관성,
  OTHER 면 fingerprint None·locator_hash 있음, 추출 실패 시 둘 다 None.
- 신규 `tests/unit/test_finding_dedup_db.py`: 같은 신호 2회 → finding 1행·증거 2건·occurrence 2·이력 1행 추가;
  다른 매개변수 → 2행; 다른 실행 → 각각 1행이지만 같은 fingerprint, 서로 참조 없음(D3b);
  OTHER 두 건 같은 위치 → 2행·서로 `suspected_duplicates=1`; 병합/해제 API; 보고서 대상에서 중복 제외.
- D3 전용 `tests/unit/test_finding_reject_reobserve.py`:
  LLM 근거로 생긴 finding 기각 → RULE 신호 재관찰 → NEEDS_REVIEW·이력·감사 이벤트·알림 1회;
  RULE finding 기각 → 같은 RULE 재관찰 → REJECTED 유지·알림 없음;
  재오픈 후 다시 기각 → RULE·LLM 어느 쪽 재관찰에도 다시 열리지 않음.
- 수정: `test_signal_promotion_db.py`, `test_sqli_boolean_diff_db.py`, `test_llm_finding.py`(vuln_class 인자).

**완료 기준**: 위 테스트 PASS + 기존 전체 테스트 회귀 없음 + 실제 Burp/Playwright 로 같은 엔드포인트를 두 번
찌르는 로컬 실행에서 finding 1건으로 남는지 브라우저로 확인.

### G2. LLM 판정 교차 검증 게이트 — P1

**목표**: LLM 이 제안한 finding 은 **다른 모델이 같은 증거를 독립적으로 보고 동의할 때만** `LLM_CONFIRMED`.
의견이 갈리거나 검증 모델이 실패하면 `NEEDS_REVIEW` 로 사람에게 넘긴다. 정책 코드는 새로 만들지 않는다 —
`create_llm_finding()` 의 상태 결정만 바꾼다.

**DB — `migrations/0030_llm_cross_check.sql`**
```sql
-- 실행 생성 시 고정(autonomy·llm_profile_name 과 같은 방식). NULL = 교차 검증 안 함(D4: 현행 동작).
ALTER TABLE runs ADD COLUMN verifier_model text;

-- 단일 모델 제안 상태(사람 검토 대기)를 구분하는 판정 주체 값 추가.
ALTER TABLE findings DROP CONSTRAINT findings_verification_check;
ALTER TABLE findings ADD CONSTRAINT findings_verification_check CHECK (
    verification IN ('RULE_CONFIRMED', 'REVIEWER_CONFIRMED', 'LLM_CONFIRMED', 'LLM_PROPOSED'));

-- 모델별 판정 기록(추가 전용). LLM 성능 비교(판정 일치율)의 원천 데이터도 된다.
CREATE TABLE finding_llm_votes (
    id           bigserial PRIMARY KEY,
    finding_id   uuid NOT NULL REFERENCES findings(id),
    model        text NOT NULL,
    stage        text NOT NULL CHECK (stage IN ('proposer', 'verifier')),
    verdict      text NOT NULL CHECK (verdict IN ('CONFIRMS', 'REFUTES', 'INSUFFICIENT', 'ERROR')),
    rationale    text NOT NULL DEFAULT '',
    evidence_ids uuid[] NOT NULL DEFAULT '{}',
    model_call_id bigint REFERENCES model_calls(id),
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER finding_llm_votes_append_only BEFORE UPDATE OR DELETE ON finding_llm_votes
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
```

**흐름 (`worker.create_llm_finding` 재구성)**
1. 기존과 동일하게 인용 증거 실존 확인 → 하나도 없으면 기록 안 함.
2. `run.verifier_model` 이 NULL 이면 현행 그대로(`LLM_CONFIRMED`/`CONFIRMED`) + proposer 표 1행만 기록.
3. 있으면 finding 을 먼저 `LLM_PROPOSED`/`NEEDS_REVIEW` 로 INSERT(G1 upsert 경유) → proposer 표 기록 →
   새 `explore.verify_llm_finding(ctx, finding, evidence_ids)` 호출:
   - 모델: `explore.make_llm_for_spec(ctx, run.verifier_model)` 재사용(신규 클라이언트 코드 없음).
   - 입력: 주장(`vuln_class`, `title`, 대상, locator)과 인용 증거 본문(`evidence.read_bytes`, 길이 제한,
     `mask_secrets` 적용). **1차 모델의 rationale 은 주지 않는다** — 앵커링을 줄여 독립 판정에 가깝게.
     증거 본문은 신뢰 불가 데이터로 감싸 전달(§8-5, 기존 프롬프트의 데이터 경계 표기 재사용).
   - 출력 스키마 `LLM_VERIFY`: `{verdict: CONFIRMS|REFUTES|INSUFFICIENT, rationale, evidence_ids}`.
     Python 이 `evidence_ids ⊆ 제공한 증거` 를 확인, 아니면 `INSUFFICIENT` 로 취급.
   - `CONFIRMS` → `UPDATE ... verification='LLM_CONFIRMED', status='CONFIRMED'` + 이력
     (`actor='llm:<proposer>+llm:<verifier>'`) + 기존 `notify_finding_confirmed`.
   - 그 외/`LLMError` → 상태 유지(NEEDS_REVIEW), 이력에 사유. 알림 없음.
4. 감사 이벤트 `llm_finding_cross_checked {finding_id, proposer, verifier, verdict}`.

**제약 검증 (`src/webapi/routers/runs.py` 생성 API, `cli.py run create --verifier`)**: 검증 모델 spec 이
proposer(planner 역할 모델)와 같으면 422 — 같은 모델 두 번은 교차 검증이 아니다. 모델 존재 여부는
`llm-profiles --check` 와 같은 경로로 확인.

**프롬프트**: `prompts.py` 의 analyst 역할 그룹(prompts.py:148)에 `verifier_system` 등록 → 기존 프롬프트
편집·버전 이력(0021) 화면에서 그대로 수정 가능.

**UI**: `runs/new` 에 "교차 검증 모델(선택)" 선택 상자(기존 역할별 모델 선택 컴포넌트 재사용). D4 에 따라
비워 두면 막지는 않되, 선택 상자 아래에 "교차 검증 없음 — LLM 이 혼자 낸 finding 도 바로 확정됩니다"를
텍스트로 표시하고, 실행 상세 요약에도 "교차 검증: 없음/모델명"을 보여 준다(선택 사실을 숨기지 않음).
`FindingPanel.tsx` 에 모델별 표(제안/검증, 판정, 근거, 인용 증거) 표시. 상태 배지에 `LLM_PROPOSED` 를
"단일 모델 제안"으로 표기(PRODUCT.md: 화면 용어 한국어 통일).

**테스트**: 신규 `tests/unit/test_llm_cross_check.py` — 가짜 LLM 두 개로 동의/반박/불충분/예외/인용 위조
각각의 상태·이력·표·알림 여부; verifier NULL 이면 기존 동작과 완전히 같음(기존 `test_llm_finding.py` 무변경
통과로 확인); 같은 모델 지정 시 422.

### G3. 검토자 편집(제목·근거·대응방안)과 수정 이력 — P1 — **구현 완료(2026-11-14)**

계획대로 구현. `migrations/0031_finding_revisions.sql`(`finding_revisions` 추가전용 테이블 + `findings.
human_edited`), `worker.edit_finding()`(허용 밖 필드·빈 사유 거부, 값이 같으면 기록 안 함), 웹 API
`POST .../edit`·`GET .../revisions`, `reporting._findings_for_pentest_report()`가 `human_edited=true`
finding 은 `llm=None`과 같은 경로로 Reporter 다듬기를 건너뜀, `GET .../report` 응답에 D6의 `stale`/
`edited_after_approval`(승인 시각 이후 `finding_revisions` 건수). UI: `FindingPanel.tsx`에 편집
폼(`EditSection`)·수정 이력(`RevisionHistory`)·"사람이 수정함" 배지, `ReportTab`에 stale 경고 배지.
테스트: `test_finding_edit.py`(6건: 필드별 이력, 무변경 무기록, 사유 누락 거부, 허용 밖 필드 거부, 추가전용
트리거, Reporter 다듬기 건너뛰기), `test_report_api.py`에 stale 라운드트립 1건 추가(회사 템플릿 필요,
이 환경은 skip). 전체 347건 통과(기존 무관 실패 1건 제외). 브라우저로 생성→수정→이력 표시까지 실제 확인.

**목표**: CLAUDE.md §10 의 미구현 요구사항. 원문은 지우지 않고, 누가 언제 왜 무엇을 바꿨는지 모두 남긴다.
편집 대상은 **제목·근거(rationale)·대응방안(remediation) 세 필드뿐**이다 — 심각도는 G-S(D7)로 개념이
없어졌고, 영향도는 D5 로 만들지 않는다.
"Report Studio"(브라우저 Word 미리보기·페이지 편집)는 docx 렌더링 비용 대비 효과가 낮아 채택하지 않고,
**보고서에 들어갈 내용 자체를 DB 에서 고치는 것**으로 같은 목적을 달성한다.

**DB — `migrations/0031_finding_revisions.sql`**
```sql
CREATE TABLE finding_revisions (
    id          bigserial PRIMARY KEY,
    finding_id  uuid NOT NULL REFERENCES findings(id),
    field       text NOT NULL CHECK (field IN ('title', 'rationale', 'remediation')),
    old_value   text NOT NULL,
    new_value   text NOT NULL,
    reason      text NOT NULL,
    actor       text NOT NULL,
    changed_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER finding_revisions_append_only BEFORE UPDATE OR DELETE ON finding_revisions
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
ALTER TABLE findings ADD COLUMN human_edited boolean NOT NULL DEFAULT false;
```
(D5: 영향도는 만들지 않는다. 나중에 템플릿에 칸이 생기면 새 마이그레이션으로 `findings.impact` 컬럼과 CHECK
값을 추가 — 적용된 이 파일은 고치지 않는다.)

**코드**
- `worker.py` 에 `edit_finding(conn, finding_id, changes: dict, reason, *, actor)`: 행 잠금 → 바뀐 필드만
  `finding_revisions` 에 1행씩 → `findings` 갱신 + `human_edited=true` → 감사 이벤트 `finding_edited`.
  `reason` 빈 문자열 거부. 값이 같으면 기록하지 않음.
- `changes` 에 세 필드 외의 키(예: 과거 습관대로 보낸 `severity`)가 있으면 422 로 거부 — 조용히 무시하지 않는다.
- API: `POST /runs/{run}/findings/{id}/edit` `{changes, reason}`, `GET .../revisions`.
- CLI: `finding edit <run> <id> [--title ..] [--rationale-file ..] [--remediation-file ..] --reason .. --editor ..`
  (CLI 단독 동작 유지).
- 보고서: `reporting._polish_finding_text()`(Reporter 에이전트 다듬기)는 `human_edited=true` 인 finding 은
  **건너뛴다** — 사람이 확정한 문장을 LLM 이 다시 바꾸지 않게. 보고서 notes 에 "검토자 수정 반영" 표기.
- D6(편집 허용 + 경고): 승인된 보고서 파일마다, 그 승인 시각(`audit_events.event='report_approved'`,
  runs.py:592-602 가 이미 읽는 값) 이후에 그 실행의 `finding_revisions` 가 있으면 `GET .../report` 응답에
  `stale: true` 와 `edited_after_approval: <건수>`. 편집 자체는 막지 않는다. 승인 기록은 감사 이벤트라 지우거나
  되돌리지 않는다(재생성한 새 파일을 다시 승인하는 흐름).
  UI(`ReportTab`): 해당 파일 옆에 "⚠ 승인 이후 finding N건 편집됨 — 재생성 필요" 텍스트 배지.

**UI**: `FindingPanel.tsx` 에 편집 모드(필드별 원문/현재값 비교, 사유 필수 입력), "수정 이력" 목록.
PRODUCT.md 원칙 3 에 따라 편집 화면 옆에 연결된 증거 목록을 함께 둔다.

**테스트**: 신규 `tests/unit/test_finding_edit.py` — 필드별 이력, 사유 누락 거부, 허용 밖 필드(`severity` 등)
거부, 이력 테이블 UPDATE/DELETE 거부(트리거), 다듬기 건너뛰기, 승인 후 편집 시 편집은 성공하고 stale·건수 표시,
승인 전 편집은 stale 아님. `test_report_api.py` 보강.

### G4. 실행 상세 화면 분리(동작 변경 없음) — P2, G5 착수 전 선행

- `web/src/app/(main)/runs/[id]/page.tsx`(1,444줄)의 탭 컴포넌트(`FindingsTab`, `BacklogTab`, `EvidenceTab`,
  `AssetsTab`, `TriggersTab`+`NewTriggerForm`, `GraphTab`, `TrafficTab`, `ChatTab`, `ActivityTab`,
  `ServerLogTab`, `ReportTab`)를 `runs/[id]/_tabs/<이름>.tsx` 로 옮기고, `RunControls`/`DeletedBanner`/
  `Summary` 는 `runs/[id]/_parts/` 로. `page.tsx` 에는 탭 전환과 폴링만 남긴다.
- 순수 이동만 한다 — 문구·스타일·폴링 주기(4초/2초)·API 호출 변경 금지. 이동과 기능 변경을 한 커밋에 섞지 않는다.
- 완료 기준: `npm run lint`, `npm run build` 통과 + 브라우저로 탭 11개 각각 열어 이전과 같은지 확인.

**구현 완료(2026-11-14)** — 계획대로 순수 이동만 수행. `page.tsx`(1,432줄)를 `_shared.ts`(공용 타입·상수·
`useAssets`/`parseJsonOr`), `_parts/{RunControls,DeletedBanner,Summary}.tsx`, `_tabs/{FindingsTab,
BacklogTab,EvidenceTab,AssetsTab,TriggersTab,GraphTab,TrafficTab,ChatTab,ActivityTab,ServerLogTab,
ReportTab}.tsx` 로 분리하고 `page.tsx` 에는 `RunDetailBody`/`RunDetailPage`(탭 전환·폴링)만 남김.
`npx tsc --noEmit`·`npm run build` 모두 오류 없이 통과, 실제 실행을 하나 만들어 탭 11개를 모두 열어
문구·빈 상태·폴링이 이전과 동일함을 확인 후 테스트용 실행은 소프트 삭제로 정리. 문구·스타일·폴링 주기·
API 호출 변경 없음(순수 이동). 백엔드(Python) 변경이 없어 전체 테스트는 재실행하지 않음.

### G5. 증거 출처 체인과 전역 자산 화면 — P2

**G5-a. 증거 출처 체인** — 새 마이그레이션 없음(기존 외래키로 모두 연결됨).
- `GET /api/runs/{run}/evidence/{id}/provenance` → 한 응답에:
  `evidence` ← `tool_invocations.evidence_id`(도구·마스킹된 인자·정책 판정·사유) ← `backlog_items`(작업 사유)
  ← `exploration_nodes`(+`exploration_edges` 를 goal 까지 거슬러 올라감, 노드별 origin: llm/trigger/enrich/worker)
  → `finding_evidence` → `findings`(+ G2 의 표). 노드마다 `kind: "observed" | "inferred"` 를 붙인다:
  증거·도구 호출·규칙 판정 = observed, LLM 제안 rationale·LLM finding = inferred.
- UI: 증거 뷰어 옆 패널에 세로 체인으로 표시. observed/inferred 를 색과 **텍스트 라벨 둘 다**로 구분
  (PRODUCT.md 원칙 1, 접근성: 색에만 의존하지 않음).
- 테스트: `test_evidence_provenance_api.py` — 탐색 경로/트리거 경로/재검증 경로 각각 체인이 끊기지 않는지.

**G5-b. 전역 자산 화면** — 새 마이그레이션 없음.
- `GET /api/assets?company=&kind=&q=` : 자산별 관찰 실행 수(`run_assets`), 상태별 finding 수(중복 제외),
  최근 관찰 시각, `verification`(SCOPE_DECLARED/OBSERVED/UNVERIFIED).
- `GET /api/assets/{id}` : `asset_edges` 양방향, 실행 이력, 실행별 finding 목록(fingerprint 기준 묶음).
- 프론트: `/assets`(표 + 필터), `/assets/[id]`(상세 + 관계 목록). 기존 `GraphTab` 그래프 컴포넌트를 재사용해
  자산 중심 1-hop 그래프. 사이드바 "작업" 그룹에 "자산" 추가.
- 범위 밖(이번에 안 함): OS·기술 스택 구조화 저장 — 현재 담을 구조화된 자리가 없어 별도 설계가 필요.

**구현 완료(2026-11-14)** — 계획대로 두 읽기 전용 API + 프론트 화면만 추가, 마이그레이션 없음.
- G5-a: `traces.get_evidence_provenance()`(`src/diagnostic_workbench/traces.py`) + `GET /api/runs/{run}/evidence/{id}/provenance`
  (`src/webapi/routers/runs.py`). `run_tool()`/`record_invocation()`이 탐색·트리거·자동보강·재검증 모든
  경로에서 `tool_invocations.backlog_item_id` 를 항상 채운다는 점을 이용해 evidence → tool_invocation →
  backlog_item → exploration_node(goal 까지) 체인 하나로 네 경로를 모두 커버함(계획에 적었던
  exploration_anchors 역방향 조회는 실제로 도달 불가능한 경로라 구현 중 제거). origin=llm 노드만 rationale
  을 inferred 로 분리 표시, 나머지(목표·트리거·자동보강·재검증)는 observed. finding 은 verification 이
  LLM_PROPOSED/LLM_CONFIRMED 면 inferred, 그 외는 observed. 프록시로 받은 트래픽처럼 탐색 결정과 무관한
  증거는 빈 체인으로 돌아옴(오류 아님). UI는 `FindingPanel.tsx`의 `EvidenceViewer`에 "출처 보기" 토글을
  추가해 세로 체인으로 표시, observed/inferred 를 색+텍스트 라벨(관찰됨/LLM 추론) 둘 다로 구분.
  `tests/unit/test_evidence_provenance_api.py`(6개, 탐색/트리거/재검증 경로·finding 분류·빈 체인·미존재
  증거 각각 확인) 전체 통과.
- G5-b: `GET /api/assets`, `GET /api/assets/{id}`(`src/webapi/routers/global_views.py`) — 둘 다
  소프트 삭제된 실행은 집계에서 제외(`r.deleted_at IS NULL`). 프론트 `/assets`(목록+필터),
  `/assets/{id}`(상세+관계+실행 이력+finding). RunDetail 의 `GraphTab` 원형 레이아웃 렌더링을
  `components/AssetGraph.tsx`(`AssetGraphSvg`)로 뽑아 자산 상세의 1-hop 그래프와 공유(계획대로 재사용,
  `GraphTab` 자체의 동작은 바꾸지 않음). 사이드바 "작업" 그룹에 "자산" 추가.
  `tests/unit/test_global_assets_api.py`(4개) 전체 통과. 계획에 없던 테스트지만(G5-b 는 테스트 파일이
  명시되지 않았음) 검증 없이 완료로 보고하지 않기 위해 추가(CLAUDE.md §15).
- `npx tsc --noEmit`·`npm run build` 통과. 실제 실행에 수동으로 탐색(origin=llm) 체인·finding 을 만들어
  브라우저에서 "출처 보기" 렌더링을 확인(체인 끊김 없음, 라벨 중복 없음 — 첫 구현에서 요약과 rationale 이
  중복 표시되던 걸 요약 로직에서 rationale 을 빼 수정)하고 테스트용 실행은 소프트 삭제로 정리. `/assets`
  목록·상세의 자산(localhost)도 실제 데이터로 확인. 전체 백엔드 테스트 332 통과, 1건
  실패(`test_autonomous_run.py::test_explore_propose_bootstraps_without_prior_evidence`)는 이 세션
  이전부터 있던 인코딩 문제로 G5 와 무관 — 수정하지 않음(범위 밖).

### G6. 실행 간 비교(신규·지속·미재현) — P2, G1 의존

- `GET /api/compare?base=<run>&head=<run>` — 두 실행의 자산 company 가 다르면 422.
  `fingerprint` 로 묶어 분류:
  | 분류 | 조건 |
  |---|---|
  | 신규 | head 에만 있음 |
  | 지속 | 양쪽에 있음(중복 제외 행 기준) |
  | 미재현(조치 추정) | base 에만 있고, head 에 같은 locator 를 겨눈 **실제 증거**가 있으며 신호가 뜨지 않았거나 재검증 결과가 FIXED |
  | 이번 실행에서 미확인 | base 에만 있고, head 에 그 locator 를 겨눈 증거가 없음 |
  | 재발 | base 에서 REJECTED 가 아니라 FIXED 재검증 이력이 있었는데 head 에서 다시 관찰 |
- **"조치 완료"라고 단정하지 않는다** — 관찰되지 않음은 고쳐졌다는 증거가 아니다(`_resolve_retest` docstring 과
  같은 원칙, CLAUDE.md §15 "확인하지 않은 것을 확인했다고 하지 않음"). 그래서 "미재현(조치 추정)"과
  "미확인"을 반드시 분리한다.
- fingerprint 가 NULL 인 finding(과거 행, OTHER)은 "비교 불가" 묶음으로 따로 보여 주고 자동 분류하지 않는다.
- 프론트: `/runs/compare`(실행 2개 선택 → 분류별 표), 자산 상세(G5-b)에서 "이 자산의 실행 비교" 바로가기.
- 보고서 연계(선택, 후속): 재진단 보고서에 분류 표를 넣는 것은 회사 템플릿 확인 후 별도 결정.
- 테스트: `test_run_compare_api.py` — 분류 5종, 회사 불일치 거부, NULL fingerprint 처리.

**구현 완료(2026-11-14)** — 계획대로 마이그레이션 없음. `src/diagnostic_workbench/compare.py`(순수 분류
함수 `compare_runs()`, `run_companies()`) + `GET /api/compare?base=&head=`(`global_views.py`, 회사
불일치 시 422). fingerprint 가 `asset_id`를 포함해 계산되고(G1, `fingerprint.py`) 자산이 실행을 가로질러
공유되는 전역 행이라, 서로 다른 실행의 finding 을 fingerprint 값으로 직접 맞출 수 있었음(계획에 적지
않았던 세부 — 비교가 왜 가능한지의 전제).
- "미재현(조치 추정)" 판정은 계획 문구의 두 조건("head 증거 있음+신호 없음" 또는 "재검증 결과 FIXED")을
  **OR**로 구현 — 재검증은 `worker.create_retest()`가 항상 그 finding 이 속한 실행 안에서만 만들어
  (findings.py `/retest` 라우터가 `run["id"]`를 그대로 씀) head 실행과 무관하게 base 쪽에 독립적으로
  존재하는 신호이기 때문. `finding_review_history.reason`에 `"재검증 결과: FIXED — ..."` 문자열로만
  남고 별도 판정 컬럼이 없어 `LIKE` 매칭으로 찾음.
- "재발"은 지속(양쪽에 같은 fingerprint)인 쌍 중 base 가 REJECTED 가 아니면서 FIXED 재검증 이력이 있는
  경우만 지속에서 떼어 재분류 — base 가 REJECTED 인데 FIXED 이력이 있는 쌍은 스펙 문구 그대로 지속에 남김.
- `tests/unit/test_run_compare_api.py`(9개: 분류 5종 + REJECTED 예외 + NULL fingerprint + 회사 불일치)
  전체 통과.
- 프론트: `/runs/compare`(기준/대상 셀렉트 + 비교 버튼, `?base=&head=` 쿼리로 바로 결과 표시), 자산
  상세(G5-b)에 "이 자산의 실행 비교" 바로가기(가장 최근 실행 2개를 자동으로 base/head 에 넣음, 실행이
  2개 미만이면 숨김). `npx tsc --noEmit`·`npm run build` 통과. 실제 실행 2개에 분류 5종을 전부 만드는
  finding/증거를 넣어 브라우저에서 5개 구간(신규/지속/재발/미재현/미확인)과 자산 상세의 바로가기 링크가
  올바른 실행 쌍을 가리키는지 확인 후 테스트용 실행은 소프트 삭제로 정리. 전체 백엔드 테스트 341 통과,
  G5 때부터 있던 무관한 인코딩 테스트 실패 1건은 그대로(범위 밖).
- 보고서 연계는 계획대로 이번에 하지 않음(범위 밖, 후속 과제로 남김).

### G7. 보류 항목 (사유 기록)

| 항목 | 보류 사유 | 재검토 조건 |
|---|---|---|
| AI 진단 지식베이스 | 과거 사실이 Planner 입력에 섞이면 관찰/추론·현재/과거 구분이 흐려짐(§3, §9) | 출처 꼬리표("과거 실행 X 의 관찰") + finding 인용 금지를 Python 에서 강제하는 설계가 먼저 나올 때 |
| 보고서 WYSIWYG(브라우저 Word 편집) | docx 렌더링 비용 큼, G3 로 내용 편집 목적 대부분 충족 | 템플릿이 여러 회사로 늘어 서식 선택이 실제 병목이 될 때 |
| LLM 성능 센터(별도 메뉴) | 원천(`model_calls`)이 이미 있어 대시보드 카드로 충분. 판정 일치율은 G2 의 `finding_llm_votes` 이후에야 측정 가능 | G2 운영 데이터가 쌓인 뒤 대시보드에 모델별 지연·실패율·일치율 카드 추가 |
| 자율 탐색 하드 예산 | §5/§8-10 결정과 충돌 | 사용자가 정책을 바꿀 때만. 필요 시 기본 꺼짐 소프트 경고로만 |
| 명령 팔레트·저장된 검색·진단 템플릿 | 1~소수 사용자 로컬 도구에서 체감 효과 작음 | 사용 중 반복 불편이 실제로 보고될 때 |
| 메뉴 26개 재편 | 화면 수만 늘림. 현재 사이드바(B1)에 G5/G6 항목만 추가 | — |

### G8. 작업 순서

| 순서 | 작업 | 마이그레이션 | 선행 조건 |
|---|---|---|---|
| 0 | G-결정 D1~D7 전부 확정 — **완료(2026-11-13)** | — | — |
| 1 | G-S finding 심각도 제거 — **완료(2026-11-13)** | 0028 | 0 |
| 2 | G1 fingerprint·중복 제거 — **완료(2026-11-13)** | 0029 | 1(새 INSERT 경로에 severity 를 넣지 않도록) |
| 3 | G2 교차 검증 게이트 | 0030 | 2(upsert 경유) |
| 4 | G3 검토자 편집 | 0031 | 1 (2·3 과 병렬 가능, FindingPanel 충돌만 주의) |
| 5 | G4 실행 상세 분리 | — | 1 이후 권장(심각도 UI 제거가 같은 파일을 크게 건드림) |
| 6 | G5 출처 체인·전역 자산 | — | 4, 5 |
| 7 | G6 실행 비교 | — | 2 |

각 단계는 CLAUDE.md §16 형식(PASS/FAIL/NOT CHECKED/BLOCKED/PARTIAL)으로 보고하고, 이 섹션의 해당 항목에
"구현 완료(날짜)"와 실제로 바뀐 점(계획과 달라진 부분 포함)을 덧붙인다. 적용된 마이그레이션은 수정하지 않고
번호를 새로 추가한다(README §8).
