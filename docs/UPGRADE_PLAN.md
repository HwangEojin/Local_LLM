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

---

## F. 에이전트/MCP/스킬 — artex와 "코드~UI 완전 동일" 계획(2026-11-02 작성, 미구현)

사용자가 세 화면을 코드 레벨부터 웹 UI 디자인까지 완벽하게 똑같이 만들라고 요청. MCP·스킬은 핵심 로직(stdio, ZIP)을 이미 이식했으므로(§E) 여기선 **남은 것**(UI 레이아웃 재현)과 **에이전트**(이번에 처음 조사)를 다룬다. 에이전트 쪽은 안전한 추가 기능과 CLAUDE.md 구조와 충돌하는 기능이 섞여 있어 구분해서 적는다.

### F1. MCP 화면 — 레이아웃을 artex와 동일하게(기능은 이미 동일)

현재: `settings` 페이지 안에 `McpAddCard.tsx`(등록 폼) + 목록(세로 나열 리스트). artex: 카드 그리드(`grid-cols-1 sm:2 lg:4`, 맨 앞 점선 "+추가" 카드) → 카드 클릭 시 우측 Sheet 드로어, 드로어 안 Tabs("설정"/"도구(N)").

구현 단계:
1. `web/src/components/ui.tsx`에 `Sheet`(우측 드로어, `ConfirmDialog`와 같은 포지셔닝 패턴 재사용) 컴포넌트 추가 — 없으면 새로 작성, 다크테마 토큰 사용.
2. `settings` MCP 섹션을 카드 그리드로 교체: 각 카드 = 아이콘+이름+transport 뱃지+Switch(켜기/끄기)+삭제, 하단에 도구 개수.
3. 카드 클릭 → Sheet 열기, 탭 2개("설정": 현재 `McpAddCard.tsx` 폼을 편집 모드로 재사용, "도구": 등록된 도구 이름+위험도+scope_args 읽기전용 표로 보여줌 — 새 백엔드 불필요, `GET /api/admin/tools`로 이미 조회 가능).
4. "에이전트별 가시성" 체크박스(artex 고유 멀티에이전트 개념)는 F3 에이전트 자유생성이 구현될 때만 의미가 생김 — 그 전까지는 이식 보류.

새 백엔드 불필요(API는 §E에서 이미 stdio까지 맞춤). 프론트 전용 작업.

### F2. 스킬 화면 — 레이아웃을 artex와 동일하게(기능은 이미 동일)

현재: `settings` 페이지 안에 `SkillsSection.tsx`(가져오기 폼 + 세로 카드 목록). artex: 좌측 264px 파일트리 + 우측 상세/편집 패널, 상단 "누락된 스킬" 배지.

구현 단계:
1. 좌측 트리: 스킬 이름 목록(우리는 단일 파일 개념이라 artex의 다중 파일 트리와 달리 평평한 목록이면 충분 — "파일트리처럼 보이는 단일 목록"으로 레이아웃만 맞춤).
2. 우측 패널: 현재 `SkillEditor`를 그대로 옮기되 레이아웃만 2단 분할로 변경.
3. "누락된 스킬" 배지: 우리 스킬은 역할에 자동 첨부되는 구조라(이름으로 호출 안 함) artex의 "호출됐는데 없음" 개념 자체가 성립 안 함 — **이식 안 함**(기능이 없는 게 아니라 개념이 안 맞음, §E에서 이미 결론).

새 백엔드 불필요. 프론트 전용 작업.

### F3. 에이전트 화면 — 가장 큼, 안전/위험 기능 분리 필요

**우리 현재**: `admin_agents.py`(역할별 프롬프트 GET/PUT/reset) + `AgentsSection.tsx`(역할 5개 고정: planner/worker/analyst/goals/reporter, 프롬프트 텍스트영역만). 모델 지정은 `/runs/new`에 따로 있음.

**artex**: 카드 그리드로 에이전트 **무제한 생성**(builtin+custom), 카드 클릭 시 Sheet 드로어에 탭 6개 — 설정+프롬프트(LLM 프로파일·max_turns·run_seconds·web_search·**interactive_shell** 토글, 프롬프트 버전관리+diff), wrapup 프롬프트, MCP 가시성, Skill 가시성, Tool 바인딩, **Triggers**(interval/on_finding/on_goal_met/on_task_timeout/on_tool_call/on_task_create 조건으로 **운영자 개입 없이 새 작업을 자동 기동**).

#### F3a. 충돌 없음 — 그대로 구현 가능

| 기능 | 구현 위치 | 비고 |
|---|---|---|
| LLM 프로파일 개별 바인딩 | `admin_agents.py`에 `llm_profile` 필드 추가, `agent_prompts` 테이블에 컬럼 추가(새 마이그레이션) | 이미 `/runs/new`에 같은 개념(역할별 모델) 있음 — 재사용/통합 검토 |
| 프롬프트 버전관리 + diff | 새 테이블(`agent_prompt_versions`, 기존 `agent_prompts` 갱신 시 이전 값 보존) | `finding_review_history`와 같은 append-only 이력 패턴 재사용 |
| ~~max_turns / run_seconds~~ | **구현 안 함(재검토 결과)** — artex는 멀티턴 에이전트 루프가 있어 "턴 수"가 의미 있지만, 이 프로젝트의 worker.py 는 백로그 작업 1건당 LLM 호출 1번(도구 1개 선택)으로 끝나 "턴"이라는 단위가 아예 없다. 유사한 것을 새로 만들면 CLAUDE.md §5/§8-10 "작업수·실행시간 하드리밋은 체크리스트 작업에만 적용, 자율 탐색은 하드리밋 없음"과 충돌한다(이미 있는 max_tasks/max_explorations/budget.timeout_s 가 바로 그 체크리스트 전용 한도). 호출 1건의 타임아웃은 이미 llm_profiles.json 의 profile별 `timeout_s`로 있고 F3a-2(역할별 기본 프로파일)로 역할마다 그 프로파일을 고를 수 있게 됐으니 추가 컬럼 없이 커버됨 |
| web_search 토글 | 현재 web_search 기능 자체가 없음 — 토글 UI만으로는 의미 없음, **토글이 실제로 켜는 기능이 있어야 함**(범위 밖, 별도 과제) |
| ~~Skill 가시성 바인딩~~ | **이미 있음(재확인)** — `agent_skills.roles`(마이그레이션 0017)와 SkillsSection.tsx 의 역할 체크박스로 F2 이전부터 구현돼 있었다. 새로 만들 필요 없음 |
| ~~MCP/Tool 가시성 바인딩~~ | **구현함**(사용자가 보류 권고에도 진행 요청) — `config/tools.local.json`에 `hidden_from: {tool_name: [role,...]}` 추가(스키마: `audit/schemas.py` TOOL_OVERRIDES), `prompts.visible_tools()`가 그 역할의 제안 후보만 걸러내고, `explore.py::_context`(planner)·`worker.py`의 worker-decide(worker) 두 지점에만 적용. `registry()`(실행 경로, policy.decide() 가 보는 전체 목록)는 전혀 안 건드림 — 숨겨도 다른 경로로 호출되면 그대로 판정됨. 도구 설정 화면에 역할별(planner/worker) 체크박스로 노출. MCP 서버는 도구 단위로 이미 이 목록에 개별 항목으로 들어 있어 서버 전체용 별도 토글은 안 만듦(중복) |
| 에이전트 "생성"(제한적) | 5개 고정 역할은 유지하되, 역할별로 **이름 있는 프로파일을 여러 개** 만들 수 있게(예: worker-aggressive, worker-cautious) — artex처럼 완전 자유 역할 생성은 CLAUDE.md §6(Planner=제안, Python=검증을 역할 단위로 서술) 재설계가 필요해 절충안 |

#### F3b. 충돌 있음 — 설계 결정 (2026-11-10 완료, 사용자가 둘 다 "확장 설계부터" 선택)

##### F3b-1. Triggers — 확장 설계(기존 `enrich.py` 패턴을 그대로 재사용)

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

##### F3b-2. interactive_shell — 확장 설계(새 정책 코드 불필요, 기존 게이트에 올리기만 하면 됨)

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
   같은 Worker 프로세스 안의 레지스트리(dict)로 보관. Worker 프로세스가 재시작되면 세션도 끝남 — artex 수준의
   "완전한 영속"은 아니지만, 그 이상(별도 장수 프로세스 관리자)은 이번 단계에서 과하다고 판단해 범위 밖으로 둠.
3. 운영자에게 권장: `shell\.` 패턴의 인터셉트 규칙을 기본으로 추가해 `collect` 호출자 자신 말고 **다른 사람의
   승인**을 강제(선택적 강화 — CLAUDE.md 가 요구하진 않지만 artex Go 서버의 실제 검사 여부가 미확인인 채로
   남아 있으니 우리 쪽은 한 겹 더 두는 쪽을 권장).

**아직 구현 전**: 두 설계 모두 테이블/모듈을 실제로 만들지 않았다(이번 세션은 "설계" 단계만 요청받음). 다음
단계는 `run_triggers`/`shell_sessions` 마이그레이션과 `triggers.py`/셸 `ToolSpec` 구현, 설정 화면 UI.

### F4. 권장 순서

1. F1(MCP 레이아웃), F2(스킬 레이아웃) — 완료
2. F3a(에이전트 안전 기능: 프롬프트 버전이력, 역할별 기본 모델, 도구/MCP 역할별 가시성) — 완료
3. F3b 설계(Triggers, interactive_shell) — 완료(위 F3b-1/F3b-2). **구현은 아직 안 함** — 다음 단계는
   `run_triggers`/`shell_sessions` 마이그레이션 + `triggers.py` + 셸 `ToolSpec` 3종 + 두 화면 UI
