# 진단 워크벤치 (`diagnostic_workbench`)

PostgreSQL 작업 큐 위에서 체크리스트 진단을 돌리는 새 실행 경로다. 설계 기준은 [CLAUDE.md](CLAUDE.md).
기존 `src/audit/`(SQLite, 순차 실행)는 수정하지 않았고 그대로 쓸 수 있다. 워크벤치는 `audit` 의 정책·범위 검사,
체크리스트 로더, 규칙 판정, MCP 클라이언트·도구 레지스트리, Ollama 클라이언트, Word/Excel 보고서를 import 해서 쓴다.
ARTEX(Go/Next.js)는 설계만 참고했다(탐색 그래프의 goal/intent/fact·anchor, 변경 기반 재계획, 영속 Todo).

## 설치와 설정

```powershell
.venv\Scripts\python -m pip install -r requirements.lock
.venv\Scripts\python -m pip install --no-deps -e .
```

1. PostgreSQL 에 역할과 DB 를 만든다(예: `workbench`). `gen_random_uuid()` 를 쓰므로 13 이상.
2. `.env.example` 을 `.env` 로 복사하고 `WORKBENCH_PG_DSN` 을 맞춘다. 비밀번호는 DSN 대신 `.env` 의 `PGPASSWORD` 또는 `pgpass.conf` 에 둔다. 다른 애플리케이션(예: ARTEX)과 DB 를 같이 쓰면 테이블 이름이 겹치므로 반드시 전용 스키마를 쓴다: `CREATE SCHEMA workbench;` 후 DSN 끝에 `?options=-csearch_path%3Dworkbench`.
3. `python -m diagnostic_workbench migrate` → `python -m diagnostic_workbench doctor`
4. 선택: `config/workbench.example.json` 을 `config/workbench.json` 으로 복사해 제외 대상·예산·임대 시간을 조정.

범위(`config/scope.json`), 모델·한도(`config/settings.json`), MCP(`config/mcp_servers*.json`), 체크리스트
(`checklist/`, `audit checklist import/activate`)는 기존 파일을 그대로 쓴다.

## 사용

```powershell
$wb = ".venv\Scripts\python", "-m", "diagnostic_workbench"
& $wb run create --checklist fsec-2026-1-server@2026-1 --targets sample-server --operator 홍길동 [--llm] [--check-ids A,B]
& $wb run plan  <run>      # 실행 예정 작업과 정책 분류 미리 보기(도구 실행 없음)
& $wb run start <run>      # 시작/재개. Ctrl+C 로 멈추면 PAUSED, 다시 start 로 이어감
& $wb run status <run>     # 판정 분포, 작업 큐, 차단 사유, 도구·LLM 사용량
& $wb backlog <run> [--status BLOCKED]
& $wb approve <run> <작업ID> --approver 이름 [--reject]     # 읽기 전용이 아닌 도구 호출 승인
& $wb retry   <run> <작업ID> --operator 이름 --reason 사유   # FAILED/BLOCKED 를 정책 판정부터 다시
& $wb review  <run> <check_id> <target_id> PASS|FAIL|NOT_APPLICABLE|NEEDS_REVIEW|INCONCLUSIVE --reason .. --reviewer ..
& $wb evidence <run>       # 증적 목록 + 파일 해시 대조
& $wb trace search <검색어> [--tool ..] [--status FAILED] [--run 제외할실행]
& $wb graph <run>          # 자산 그래프(대상)와 탐색 그래프
& $wb report <run> [--type web --meta report_meta.json --findings findings.json]
& $wb run cancel <run>
```

실행 ID 는 앞 몇 글자만 써도 된다. 전체 중지 스위치: `data/STOP` 파일을 만들면 모든 도구 호출이 거부되고 실행은 PAUSED 가 된다.

## 구조

| 모듈 | 책임 |
|---|---|
| `migrations/*.sql`, `db.py` | 스키마(23개 테이블), 마이그레이션 실행기(해시 고정, advisory lock), 감사·변경 이벤트 |
| `config.py` | DSN(환경 변수/.env), `workbench.json`, 기존 설정 로딩 |
| `runs.py` | 체크리스트 버전을 DB 로 가져오기, 자산 등록, 실행별 체크리스트 행, 실행 컨텍스트 |
| `policy.py` | `ALLOW / ALLOW_WITH_APPROVAL / DENY / NEEDS_SCOPE_CONFIRMATION` 판정(범위·제외·중지·승인) |
| `planner.py` | 변경 이벤트로 깨어나 미실행 항목을 작업으로 제안, 정책 판정으로 READY/BLOCKED 결정 |
| `backlog.py` | 상태 전이 검증, `FOR UPDATE SKIP LOCKED` 점유, 임대 만료 복구 |
| `worker.py` | 도구 호출 → 증적 → 규칙 판정 → 발견 사항, 단계별 Activity(`tool_invocations`) |
| `explore.py` | LLM 탐색 제안과 Python 검증, 탐색 그래프(intent/fact, anchor) |
| `traces.py` | Worker Activity 검색(`search_all_worker_traces`, `get_worker_trace`) |
| `coordinator.py` | 계획→점유→실행→재계획 반복, 중지·취소·시간 예산 |
| `reporting.py` | PostgreSQL 기록을 기존 보고서 모듈 형식으로 보여 주는 어댑터 + Excel '커버리지' 시트 |

판정 값은 `PASS, FAIL, NOT_APPLICABLE, NEEDS_REVIEW, INCONCLUSIVE, NOT_RUN`. 기존 `audit` 의 `MANUAL_REVIEW` 는
`NEEDS_REVIEW` 로 저장된다.

## 구현 상태 (2026-10-13 기준)

확인은 스크래치 폴더의 임시 PostgreSQL 18.6 클러스터(포트 55432)와 테스트용 예시 체크리스트로 했다.
이후 이 PC 의 PostgreSQL(5432)에 전용 계정 `kingcrimson` 과 전용 DB `workbench` 를 만들어 마이그레이션 2개를 적용했다. ARTEX 가 쓰는 `artex` DB 에는 워크벤치 객체가 남아 있지 않다.

| 기능 | 상태 | 근거 및 제한 사항 |
|---|---|---|
| 설정 검증 | 구현 | `doctor`: DSN 누락·미적용 마이그레이션을 FAIL 로 표시하는 것 확인 |
| PostgreSQL 스키마·마이그레이션 | 구현 | `migrate` 적용/재실행(변경 없음) 확인, 추가 전용 트리거가 `DELETE FROM evidence` 를 거부 |
| 범위·정책 강제 | 부분 구현 | 범위 밖 대상·경로 이탈·제외 대상·중지 스위치·실행별 허용 도구(`run create --tools`)가 도구 호출 전에 차단됨 확인. 호출 간격 제한(`min_call_interval_ms`)은 코드만 있고 실측하지 않음. 리다이렉트/DNS 해석 결과 검사는 없음 |
| 체크리스트 가져오기 | 구현 | 활성·해시 고정된 버전만 DB 로 가져옴(원문 `body` 보존, 자동화 수준 분류). xlsx→JSON 변환은 기존 `audit checklist import` |
| 결정론적 체크리스트 점검 | 구현 | 예시 체크리스트로 FAIL/PASS/NOT_APPLICABLE/NEEDS_REVIEW/INCONCLUSIVE 확인. 공식 `fsec-2026-1-server@2026-1`(70항목) 실행 → 전부 NEEDS_REVIEW(원본에 자동 판정 기준이 없는 수동확인 항목), 보고서 생성까지 확인 |
| 로컬 LLM 연동 | 구현 | qwen2.5:7b-instruct 로 분석 3회·탐색 제안 2회가 스키마 검증 통과. 16GB 노트북에서 호출당 10~90초 |
| 작업 큐·Worker 조정 | 구현 | 점유·상태 전이·재개·취소 확인. 두 프로세스 동시 실행에서 중복 실행 0건(작업 3건 규모). 점유만 하고 방치한 작업이 임대 만료 뒤 다시 실행됨(시도 2회) 확인. Worker 는 프로세스당 1개이며 더 필요하면 `run start` 를 여러 개 띄운다 |
| MCP 어댑터 | 부분 구현 | 기존 `audit` 레지스트리·클라이언트 재사용. 테스트용 stdio MCP 서버로 출력 수신·증적 저장·규칙 판정, 시간 초과(5초) → FAILED, 출력 속 지시문이 판정에 영향 없음 확인. Burp·Playwright·Ghidra·JADX·Frida 실제 서버로는 이 경로에서 확인하지 않음(`CONNECTED_UNVERIFIED`) |
| 증적 저장 | 구현 | 파일 SHA-256·크기와 DB 행 대조(`evidence`) 통과, 실패·거부도 행으로 남음. 스크린샷 등 미디어 유형 구분, 마스킹 파생본은 없음 |
| Word 보고서 템플릿 | 부분 구현 | 기존 기본 보고서와 웹 모의해킹 템플릿 보고서가 어댑터로 생성·재검증됨. 미실행 항목 표기는 Word 에 없음(Excel 커버리지 시트와 CLI 출력에만) |
| Excel 보고서 템플릿 | 부분 구현 | 기존 시트 + '커버리지' 시트 생성 후 다시 열어 확인. 사용자 지정 Excel 템플릿으로는 확인하지 않음 |
| 탐색·재계획 반복 | 구현 | 실제 모델 제안 → 정책 검증 → 실행 → 사실 기록 → 재계획 → 예산 초과 거부까지 확인. 읽기 전용 도구만 허용 |
| 이중 그래프·anchor 연결 | 부분 구현 | 탐색 그래프(goal/intent/fact, spawns/yields/derived_from, anchor) 동작. 자산 그래프는 범위에 선언된 대상 + `fs.list_dir` 가 관찰한 경로와 `contains` 관계(출처 증적 ID 기록)만 채운다. 호스트·서비스·엔드포인트 추출은 없음 |
| 공유 TodoList·선행 조건 | 부분 구현 | 영속 큐와 멱등 키(재계획 시 중복 0건) 확인. `backlog_deps` 에 선행 관계를 넣으면 선행 작업 완료 전까지 BLOCKED 였다가 실행됨 확인. 선행 관계를 자동으로 만드는 Planner 규칙은 없음 |
| Worker Activity 검색·재사용 | 부분 구현 | `trace search/show` 동작, 다른 실행의 실패 이력을 탐색 프롬프트에 참고로 넣음. ILIKE 검색이라 대량 데이터 성능은 미확인 |

## 최소 동작 확인 기록

| 확인 | 결과 | 근거 |
|---|---|---|
| 애플리케이션 시작·설정 검증 | PASS | `doctor` |
| PostgreSQL 연결·마이그레이션 | PASS | `migrate`, `migrate --status` (임시 클러스터) |
| 실행·작업 큐 저장 후 재시작 조회 | PASS | 새 프로세스에서 `run status`, `backlog` |
| 범위 밖 대상 차단 | PASS | 범위 밖 target_id 로 실행 생성 거부, 경로 이탈 항목 `DENY`(도구 호출 0건) |
| 작업 중복 할당 없음 | PASS | 동시 `run start` 2개, 작업당 `worker_runs` 1건 |
| 도구 실패·시간 초과 저장 | PASS | 없는 파일·연결 실패·MCP 시간 초과가 `FAILED` + `INCONCLUSIVE` 로 저장됨 |
| 증적 해시와 DB 일치 | PASS | `evidence` 명령, 테스트 |
| Word·Excel 다시 열기 | PASS | 기존 `report.verify` + 시트 목록 확인 |
| 로컬 모델 구조화 응답 | PASS | `model_calls` 에 ok=true, 스키마 검증 통과 |
| MCP 도구 결과 수신·파싱 | PASS(테스트 서버) / NOT CHECKED(실제 진단 도구) | `tests/fixtures/test_mcp_server.py` stdio 서버의 출력이 증적으로 저장됨 |
| 운영 DB(5432) 적용 | PASS | 전용 DB `workbench`(소유자 `kingcrimson`)에 테이블 23개 생성, `doctor` 통과. 확인용 실행 1건을 저장·재조회한 뒤 삭제했다(현재 실행 0건) |

재현: `set WORKBENCH_TEST_PG_DSN=...` 후 `python -m unittest tests.integration.test_workbench` (전용 DB 필요).

## 복구

- Worker 가 죽으면 임대(`lease_s`)가 지난 뒤 다음 계획 주기에 READY 로 돌아가거나 시도 횟수 초과 시 FAILED 가 된다.
- 변경 이벤트를 놓쳐도 `run start` 가 DB 상태(미실행 판정, 열린 작업)에서 다시 계획한다.
- 적용된 마이그레이션 파일은 수정하지 말고 새 번호의 파일을 추가한다(해시가 다르면 실행을 거부한다).
