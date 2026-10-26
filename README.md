# 로컬 LLM 보안 진단 워크벤치

체크리스트 없이도 시작하는 자율 보안 진단 워크벤치. PostgreSQL 작업 큐 위에서 실행하고, 판정은 **등록된 도구로 수집한 증적 + 결정론적 규칙 + 사람 검토**로 내린다. 로컬 LLM(Ollama)은 다음 탐색 작업을 제안하지만(체크리스트가 없어도), 실제 실행은 항상 Python 정책 계층을 통과해야 한다. 체크리스트는 공식 기준 점검이 필요할 때 곁들이는 선택적 기능이다. 설계 원칙은 [CLAUDE.md](CLAUDE.md).

## 1. 준비

| 항목 | 버전 |
|---|---|
| Python | 3.12 이상 (검증 3.13.5) |
| PostgreSQL | 13 이상 (검증 18.6) |
| Ollama | 0.5.0 이상, 구조화 출력 필요 (검증 0.35.1, `qwen2.5:7b-instruct`) |
| 선택 | Nmap, MCP 서버(Burp, Playwright, Frida, Ghidra, JADX), [testssl.sh](https://testssl.sh)(재협상 취약점 점검용, 아래 참고) |

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.lock
.venv\Scripts\python -m pip install --no-deps -e .
```

저장소에 없는 것(각자 준비): 공식 평가기준 원본과 변환된 체크리스트(`checklist/`), 회사 보고서 템플릿(`templates/word/*.docx`), `.env`, `config/*.local.json`, `data/`, `reports/`.

## 2. 설정

| 파일 | 내용 |
|---|---|
| `.env` | `WORKBENCH_PG_DSN`, `PGPASSWORD` (`.env.example` 복사). 다른 앱과 DB 를 같이 쓰면 전용 스키마: DSN 끝에 `?options=-csearch_path%3Dworkbench` |
| `config/scope.json` | 승인된 범위: `engagement_id`, `approved_by`, `valid_until`, `allowed_risks`, `targets`(`path`/`host`/`url`, 필요 시 `platform`). 만료되면 모든 실행이 거부된다 |
| `config/settings.json` | Ollama 주소·모델·한도, 증적·보고서·체크리스트·템플릿 경로, 도구 호출 한도. 루프백이 아닌 모델 주소는 `external_api.enabled=false` 이면 거부. `ollama.timeout_s`(기본 240)는 모델 크기·시스템 부하에 맞춰 늘린다 — 16GB 노트북에서 `qwen2.5:7b` 는 호출당 10~90초(다른 프로세스 부하 겹치면 더 걸림), `qwen2.5:14b` 는 타임아웃을 600초 정도로 늘려야 응답을 받을 수 있었다. **임시 테스트 전용**: `WORKBENCH_LLM_PROVIDER=claude_cli` 환경 변수를 설정하면 Ollama 대신 설치된 `claude` CLI(Claude Code, 구독 로그인 — API 과금 아님)를 호출한다. `claude` 는 `--tools ""` 로 도구 접근을 전부 막은 채 돌며(실제 도구 실행은 여전히 Python 정책 계층만 거침), 쓰려면 터미널에서 `claude login` 이 먼저 돼 있어야 한다. `config/settings.json` 에 `claude_cli`(모델·타임아웃 등, 선택) 블록으로 세부 조정 가능(`src/audit/claude_cli_model.py`) |
| `config/mcp_servers.json` | MCP 서버와 허용 도구(`risk`, `scope_args`, `args_schema`). 이 PC 의 경로는 `mcp_servers.local.json` 에 같은 서버 이름으로 적는다 |
| `config/workbench.json` (선택) | 제외 대상 `exclude`, 예산 `budget`, `lease_s`, `min_call_interval_ms`, `nmap_path`, `testssl_path` (`workbench.example.json` 참고) |
| `config/notify.json` (선택) | finding 이 CONFIRMED 될 때 알릴 웹훅 채널 목록(`notify.example.json` 참고). 없으면 알림 없이 조용히 넘어간다 |
| `config/llm_profiles.json` (선택) | 이름 있는 LLM 프로파일 목록(`llm_profiles.example.json` 참고). 각 프로파일은 settings.json 의 `ollama`/`claude_cli` 블록 위에 바꿀 값만 덮어쓴다. 없으면 `--llm-provider` 하나만으로 기존처럼 동작 |

**testssl.sh (WEB-SER-037 재협상 오버레이용, 선택):** `git clone --branch 3.2 --depth 1 https://github.com/drwetter/testssl.sh.git data/tools/testssl.sh`(저장소에는 안 올라감, `/data/` 가 `.gitignore` 대상). Git-for-Windows 의 MSYS 에는 `hexdump`/`pkill` 이 없어 `data/tools/hexdump-shim/`(이 저장소에 포함, testssl.sh 가 실제로 쓰는 호출 형태만 정확히 구현)을 testssl.sh 실행 시 PATH 맨 앞에 둔다 — `tools.py` 의 `tls.renegotiation` 도구가 자동으로 해 준다. `config/workbench.json` 에 `testssl_path`를 설정해야 도구가 등록된다.

```powershell
$wb = ".venv\Scripts\python", "-m", "diagnostic_workbench"
& $wb migrate      # 스키마 적용
& $wb doctor       # 설정·DB·범위·모델 점검 (--probe-mcp 로 MCP 연결 확인)
& $wb tools        # 등록된 도구와 위험도
& $wb llm-profiles --check   # config/llm_profiles.json 의 프로파일 목록과 실제 연결 가능 여부
```

## 3. 체크리스트

```powershell
& $wb checklist import "D:\가이드\평가기준(제2026-1호).xlsx" 2026-1 --pdf "D:\가이드\주통 가이드.pdf"
& $wb checklist validate
& $wb checklist activate fsec-2026-1-server 2026-1
& $wb checklist overlays                              # config/rule_overlays.json 교차검증
& $wb checklist reapply-overlays fsec-2026-1-server 2026-1   # 이미 DB에 import된 버전에 오버레이 소급 적용
```

- 원본 xlsx 의 시트마다 체크리스트 하나(`fsec-2026-1-<시트>`)가 `draft` 로 만들어진다. 원문은 요약·수정하지 않고 `source_reference`(파일/시트/행)와 원본 SHA-256 을 남긴다.
- 활성화하면 해시가 고정된다. 이후 파일이 바뀌면 실행과 보고서가 거부된다. 새 내용은 새 버전으로 만든다.
- 원본에 판단기준이 없는 항목은 `incomplete` 이고 기준을 지어내지 않는다. 현재 공식 12종은 자동 수집 도구가 지정되지 않아 **모두 사람 검토 항목**이다.
- 판정 방법: `rule`(정규식), `manual`, `llm_assisted`(의견만 기록). platform 별 항목이 있으면 `scope.json` 대상에 `platform` 이 필요하다.

## 4. 진단 실행

```powershell
& $wb run create --targets baseline-demo --operator 이름 --llm           # 자율 탐색만(체크리스트 없음, 호출 수 무제한)
& $wb run create --checklist fsec-2026-1-web-mobile-hts@2026-1 --targets baseline-demo --operator 이름 [--llm] [--check-ids A,B] [--tools a,b] [--autonomy approval|full] [--llm-provider ollama|claude_cli | --llm-profile <이름>]
& $wb run plan  <run>      # 실행 예정 작업과 정책 분류 미리 보기
& $wb run start <run>      # 시작/재개 (Ctrl+C → PAUSED)
& $wb run status <run>     # 판정 분포, 작업 큐, 차단 사유, 사용량
& $wb backlog <run> [--status BLOCKED]
& $wb approve <run> <작업ID> --approver 이름 [--reject]
& $wb retry   <run> <작업ID> --operator 이름 --reason 사유
& $wb run cancel <run>
```

증적 수집과 검토(사람 검토 항목은 이 경로로 증적을 붙인다):

```powershell
& $wb collect <run> <check_id> <target_id> <도구> --args '{...}' --operator 이름 [--approve-as 승인자]
& $wb collect <run> <check_id> <target_id> --steps '@steps.json' --operator 이름    # 브라우저처럼 상태가 이어지는 호출
& $wb review  <run> <check_id> <target_id> PASS|FAIL|NOT_APPLICABLE|NEEDS_REVIEW|INCONCLUSIVE --reason .. --reviewer ..
& $wb finding list <run>                     # findings 테이블(체크리스트 유무 무관)의 발견 사항과 검토 상태
& $wb finding review <run> <finding_id> CANDIDATE|NEEDS_REVIEW|CONFIRMED|REJECTED|INCONCLUSIVE|NOT_TESTED --reason .. --reviewer .. [--evidence id1,id2]
& $wb finding retest <run> <finding_id> --reason .. --reviewer ..   # 증거를 낸 도구·인자를 새 탐색 작업으로 다시 큐에 올림. 완료되면 같은 신호로 REPRODUCED/FIXED/INCONCLUSIVE 자동 판정(판정 이력만, status 는 안 바꿈) → run start 로 실행 후 finding list 로 확인
& $wb question list <run>                                           # LLM 이 탐색 중 되물은 질문(도구로 못 푸는 막힘) 목록
& $wb question answer <run> <question_id> --answer .. --operator .. # 답변 기록 → 다음 계획 주기에서 LLM 이 그 답을 보고 이어감
& $wb evidence <run>       # 증적 목록 + 파일 해시 대조
& $wb graph <run>          # 자산 그래프와 탐색 그래프
& $wb trace search <검색어> [--tool ..] [--status FAILED]
& $wb trace tag <탐색노드ID> --run <run> --check-id <check_id>   # 검증된 탐색 발견을 체크리스트에 사후 매핑(판정은 안 바꿈)
```

- `--checklist` 는 선택이다. 생략하면(`--llm` 필요) 체크리스트 항목 없이 목표(goal) 노드와 대상 자산만 등록되고, 로컬 LLM 이 그 목표와 대상만 근거로 최초 탐색(정찰)을 제안한다 — 체크리스트 증거가 하나도 없어도 탐색이 시작된다. `--checklist` 를 주면 기존처럼 체크리스트 작업이 먼저 돌고, 그 판정 결과가 생긴 뒤에만 탐색이 보조로 이어진다(동작 변경 없음).
- 실행 ID 는 앞 몇 글자만 써도 된다.
- 도구 위험도: `read` 는 범위 안이면 실행, `write`/`dangerous` 는 `scope.json` 의 `allowed_risks` 에 있어야 한다. 그 안에서 승인이 얼마나 필요한지는 실행 생성 시 정한 `--autonomy` 로 정해진다(`approval` 기본값: 매번 승인 / `full`: 둘 다 자동, 범위 내 — 기존 3단계의 `partial` 은 없어짐, CLAUDE.md §1). 실행 중 바꿀 수 없다. 승인이 필요할 때는 실행·대상·도구·정확한 인자에 묶이며 24시간 유효.
- `PASS`/`FAIL` 은 연결된 증적이 있어야 기록할 수 있다.
- 자율 탐색(exploration)의 탐색 횟수·도구 호출 수에는 하드 리밋이 없다(CLAUDE.md §1/§5.4/§8). 체크리스트 작업의 예산(`--max-tasks`)은 그대로 강제된다. 벽시계 타임아웃(`timeout_s`)과 "유효한 후속 작업 없음"만으로 자율 탐색이 끝난다.
- 전체 중지: `data/STOP` 파일을 만들면 신규 도구 호출이 거부되고, **진행 중인 호출(서브프로세스·MCP 호출)도 짧은 폴링 간격(0.2초) 안에 취소된다**(2026-10-19 — 기존에는 신규 디스패치만 막았음, CLAUDE.md §8 Kill Switch 동작). 실행은 PAUSED 가 된다.
- Nmap 예: `collect <run> <check_id> <target_id> nmap.scan --args '{"host": "localhost", "ports": "80,443,8000-8100"}' --operator 이름 --approve-as 승인자` (`-sT -sV` 고정, 포트 최대 1024개).

### MCP 서버

| 서버 | 사용 전 준비 | 비고 |
|---|---|---|
| Burp | Burp 실행 + MCP Server 확장(`http://127.0.0.1:9876/`) | 이력 조회는 Burp 화면에서 승인 필요. 범위 밖 호스트의 항목은 저장 전에 걸러낸다 |
| Playwright | `npx playwright install chromium`, `--executable-path` 를 PC 에 맞게 | 연결 유지(`persistent`). 도착 주소가 범위 밖이면 출력 저장을 막는다 |
| Frida | frida-server/장치 | attach·spawn·hook 은 `dangerous` |
| Ghidra / JADX | 본체에서 대상을 열고 플러그인(8091 / 8650) 켜기 | 어떤 대상이 열렸는지는 범위로 검증할 수 없다(운영자 책임) |

Windows 가 8034–8133 포트를 예약해 둔 PC 에서는 8080·8090 을 쓸 수 없다. Burp 리스너와 진단 대상 포트는 그 범위 밖으로 잡는다.

## 5. 보고서

```powershell
& $wb report <run> --meta report_meta.json [--findings findings.json]   # 회사 템플릿 서식의 결과보고서(기본, 체크리스트 실행)
& $wb report <run> --basic                                              # 내부 점검 요약(Word 표 + Excel, 커버리지 포함, 체크리스트 실행만)
& $wb report <run> --meta report_meta.json                              # 체크리스트 없는(자율 탐색 전용) 실행도 같은 Word 서식, --basic 은 지원 안 함
```

- 체크리스트 없는 실행도 서식은 새로 만들지 않고 **같은 회사 템플릿(`audit.pentest_report`)을 그대로 쓴다**. 다른 점은 발견 사항의 출처뿐이다: 체크리스트 실행은 FAIL 판정 + `findings.json` 을 연동하고, 체크리스트 없는 실행은 `findings` 테이블(탐색 신호가 확정한 발견 사항)을 그 서식이 기대하는 모양으로 바로 채운다. `notes` 란에 실제 검토 상태를 적어 CONFIRMED 와 아직 검토 전인 CANDIDATE/NEEDS_REVIEW 를 구분하고, REJECTED(사람이 오탐으로 확인)는 본문에서 뺀다. 검토 자체는 CLI(`finding review`/`finding retest`)나 웹 UI(`workbench web`, §5a)로 한다. PASS/FAIL 집계·커버리지가 없으므로 `--basic` 은 지원하지 않는다.
- 기본 보고서는 `templates/word/` 의 `웹*결과보고서*.docx` 서식을 그대로 쓰고, 보호대책은 `*웹*보안권고안*.docx` 에서 텍스트와 이미지만 가져온다. 템플릿 유형은 대상 platform 으로 정한다(현재 WEB 만 지원).
- `report_meta.json`: 고객사, 프로젝트, 대상(`target_id`, 이름, URL), 일정, 인력. 형식은 `src/audit/schemas.py` 의 `REPORT_META`.
- `findings.json`: `check_id` + `target_id` 가 DB 의 **FAIL 판정과 일치**해야 한다. 진단결과, 발생경로, 사례(CASE)와 단계(`text`/`code`/`image`/`caption`), 권고사항. 형식은 `FINDINGS`.
- FAIL 인데 findings 에 없으면 `(작성 필요)` 로 포함하고 경고한다. 취약점명이 권고안 제목과 다르면 `recommendation_titles` 로 지정한다. 찾지 못한 보호대책은 지어내지 않는다.
- 생성 후 파일을 다시 열어 취약점 누락을 확인한다. 기존 파일은 덮어쓰지 않는다(`-2`, `-3`).
- Word 에서 `필드를 업데이트하시겠습니까?` 가 나오면 `예`(목차·번호 갱신). 화면상의 모양은 직접 확인해야 한다.
- 보고서와 감사 로그에서 비밀 값 패턴은 마스킹된다. 원본 증적은 `data/evidence/` 에 그대로 남는다.

## 5a. 웹 UI (선택 기능, 2026-10-19 신규)

findings/runs(tasks) 중심의 로컬 대시보드. **새 진단 로직은 없다** — `src/webapi`(FastAPI)가 기존 `diagnostic_workbench` 모듈(`worker.set_finding_status`/`worker.create_retest`/`reporting.coverage`/`backlog.counts` 등)을 그대로 부르는 HTTP 어댑터일 뿐이고, `web/`(Next.js)이 그 위에 얇게 얹힌다. CLI 는 이 없이도 완전히 동작한다.

```powershell
pip install -e '.[web]'                 # fastapi, uvicorn, itsdangerous(선택 의존성)
# .env 에 WORKBENCH_WEB_USER/WORKBENCH_WEB_PASSWORD/WORKBENCH_WEB_SECRET 설정(.env.example 참고) — 없으면 시작 거부
& $wb web --port 8420                   # 백엔드(FastAPI)
cd web && npm install && npm run dev    # 프론트엔드(Next.js, 기본 :3000)
```

둘을 한 번에 띄우려면(각각 새 PowerShell 창으로): `.\scripts\dev.ps1` (포트를 바꾸려면 `-BackendPort`/`-FrontendPort`).

- **인증**: 가벼운 단일 운영자 로그인(다중 사용자·역할 체계는 없음). 아이디/비밀번호는 `.env` 의 환경 변수로만 받고(DB·설정 파일에 평문 저장 안 함), 로그인하면 서명된 쿠키(`itsdangerous`, 12시간) 로 세션을 유지한다. 비밀번호·서명 비밀이 없으면 서버가 시작을 거부한다(기본 거부, CLAUDE.md §8-4 와 같은 원칙).
- **화면**: `/runs`(실행 목록 — 검색(실행 ID·운영자 부분 일치, 입력 디바운스 300ms), 상태 필터(여러 개 동시 선택), 정렬(상태/운영자/생성 시각, 열 클릭으로 토글), 페이징(20건씩) + "새 진단") → `/runs/new`(진단 환경 설정: `scope.json` 대상 선택, 운영자, 체크리스트(선택), `--llm`, 자율성 — 제출하면 `runs.create_run` 그대로 호출해 실행을 만들고 상세로 이동, 환경 상태 패널에 `doctor` 점검(scope/Ollama/MCP)도 같이 보여준다) → `/runs/[id]`(상태·예산 사용량 + **시작/재개·취소 버튼** + Findings/Backlog/Evidence/Activity/서버 로그 탭). Findings 탭은 기존과 동일(검토·재검증). Backlog 탭은 `BLOCKED`+승인 대기 작업에 **승인/거부 버튼**을 보여준다(`approve` CLI 와 동일한 호출). Evidence 탭은 증적 목록 + 무결성 확인(해시 대조) + 원본 파일 다운로드(`evidence verify`/`evidence` CLI 와 동일한 근거). **Activity(명령·결과) 탭**은 `tool_invocations` 를 4초 간격으로 다시 받아오는 최근 도구 호출 로그(모니터링 — `trace` CLI 와 같은 데이터)로, LLM/Worker 가 실제로 호출한 **명령어(도구+인자)**, 정책 판단, 오류를 보여주고, 증적이 있는 호출은 **"결과 보기"**를 눌러 그 자리에서 내용을 미리볼 수 있다(`evidence` 다운로드 엔드포인트를 그대로 fetch). **서버 로그(실시간) 탭**은 `run start` 가 찍는 진행 메시지(CLI 의 `log=print` 와 같은 것 — 계획 수립 시작/완료, 작업 성공·실패)를 2초 간격으로 받아온다. 이 로그는 증거가 아니라 사람이 보는 콘솔 출력이므로 **FastAPI 프로세스 메모리에만** 쌓이고(웹 서버 재시작 시 비워짐, 그 사이 내용은 터미널 stdout 에 그대로 남음), DB 에는 쓰지 않는다.
- **시작/재개**: `coordinator.run_loop`(기존 `run start` 가 쓰는 바로 그 함수)를 웹 서버 프로세스 안의 백그라운드 스레드로 돌린다(오래 걸릴 수 있어 HTTP 요청-응답 주기 밖에서 실행) — 이 스레드 레지스트리는 이 FastAPI 프로세스 안에서만 유효하다(재시작하면 모름, CLI 로 그 사이 또 `run start` 해도 Backlog 점유(SKIP LOCKED)가 안전하게 막아 줌). `RUNNING` 으로 바뀌기 전에 실패하면(LLM 연결 불가 등) `PAUSED` + 사유로 명시적으로 남겨 모니터링 화면에 왜 멈췄는지 보이게 한다(실패를 서버 로그에만 묻지 않음).
- **API**: `GET /api/environment`(대상/체크리스트/도구/자율성/환경 점검), `GET|POST /api/runs`(목록 조회는 `q`/`status`(콤마로 여러 개)/`sort`(`created_at`\|`status`\|`operator`)/`order`(`asc`\|`desc`)/`limit`/`offset` 지원, `{runs, total, limit, offset}` 형태로 응답), `GET /api/runs/{id}`(`loop_running` 포함), `POST /api/runs/{id}/start|cancel`, `GET /api/runs/{id}/backlog`, `POST /api/runs/{id}/backlog/{item}/approve`, `GET /api/runs/{id}/evidence`, `GET /api/runs/{id}/evidence/{id}/download`, `GET /api/runs/{id}/activity`, `GET /api/runs/{id}/server-log`, `GET /api/runs/{id}/findings`, `POST .../findings/{id}/review`, `POST .../findings/{id}/retest`. OpenAPI 문서는 `/api/docs`.
- **범위**: findings + runs(tasks, 환경 설정·시작·승인·증적·모니터링 포함) 화면. assets/traffic/agents/mcp/tools/settings/chat 같은 별도 관리자용 화면은 여전히 범위 밖이다(§7 "다음 구현 단계" 참고).
- **실제 확인**: 로컬 PostgreSQL + 실제 Chromium(headless) 으로 로그인 → "새 진단" 폼으로 실행 생성(대상 체크박스·운영자·자율성) → 상세 페이지에서 시작 클릭(Ollama 가 없는 이 환경에서는 실패가 `PAUSED` + 사유로 바로 보이는 것까지 확인) → Backlog 탭에서 승인 대기 작업에 승인 버튼 클릭 → `approvals` 테이블에 실제로 기록되는 것 확인 → Evidence 탭에서 실제 파일을 내려받아 원본과 바이트 단위로 동일함 확인(`diff`) → Activity 탭 폴링 확인. finding 상태를 브라우저에서 직접 바꿔 `finding_review_history` 에 `actor='reviewer:<웹 로그인 아이디>'` 로 기록되는 것까지 확인. `npm run build`/`tsc --noEmit`/`eslint` 전부 통과.
- **Activity(명령·결과)/서버 로그 탭 추가 확인(2026-10-21)**: 체크리스트 없는 실행을 실제로 시작해 "계획 수립 중..." → "계획 완료: ... 탐색 제안 1건" → `[FAILED] exploration ...`(LLM 이 선택한 사유 포함) 순서로 서버 로그 탭에 실시간으로(2초 폴링) 쌓이는 것을 실제 Chromium 으로 확인. 같은 호출이 Activity 탭에 도구명·**명령어(인자 JSON)**·정책 판단·오류로 함께 뜨는 것도 확인(이 환경엔 Burp 가 안 떠 있어 `ConnectError` 로 실패하는 게 그대로 보임 — 실패도 숨기지 않고 보여주는지까지 검증). `tsc --noEmit`/`eslint` 통과.
- **실행 목록 검색·필터·정렬·페이징 추가 확인(2026-10-23)**: 실제 DB(174건)로 상태 필터(`RUNNING`만 선택 → 3건으로 줄어듦), 운영자 검색("황어진" → 1건), 상태 열 클릭 정렬(`RUNNING`이 맨 위로, 동률은 ID 오름차순), 페이징("다음" 클릭 → 21–40건째로 이동, 9페이지 중 2/9 표시)까지 실제 Chromium 으로 하나씩 확인. `GET /api/runs` 가 배열 대신 `{runs, total, limit, offset}` 로 바뀌었으므로 그 응답을 쓰는 유일한 화면(`/runs`)도 같이 바꿈. `tsc --noEmit`/`eslint` 통과.

## 6. 구조

| 위치 | 책임 |
|---|---|
| `migrations/*.sql`, `db.py` | 스키마, 마이그레이션 실행기(해시 고정), 감사·변경 이벤트 |
| `config.py`, `runs.py` | 설정 로딩, 실행 생성(체크리스트는 선택), 체크리스트 버전을 DB 로 가져오기(있을 때만), 자산 등록, 실행 컨텍스트, 도구 레지스트리 |
| `policy.py` | `ALLOW / ALLOW_WITH_APPROVAL / DENY / NEEDS_SCOPE_CONFIRMATION` 판정(범위·제외·DNS·중지·승인·도착 주소) |
| `planner.py`, `backlog.py`, `coordinator.py` | 변경 기반 계획, 상태 전이와 `SKIP LOCKED` 점유, 실행 반복·중지·취소. 체크리스트 없는 실행은 매 주기 탐색을 시도(부트스트랩) |
| `worker.py`, `evidence.py`, `tools.py` | 도구 호출 → 증적 → 규칙 판정 → 발견 사항, 단계별 Activity, 자산 관찰, Nmap |
| `notify.py` | finding CONFIRMED 전환 시 `config/notify.json` 의 웹훅 채널로 알림(선택 기능) |
| `audit/local_model.py`, `audit/claude_cli_model.py` | LLM 클라이언트(Ollama/claude CLI, 같은 인터페이스). `config/llm_profiles.json`(`audit.config.load_llm_profiles`)의 이름 있는 프로파일이 settings.json 기본 블록을 덮어쓴다(`explore.make_llm`) |
| `explore.py`, `traces.py` | LLM 탐색 제안과 검증, 탐색 그래프, Activity 검색, 체크리스트 사후 매핑(`tag_node`). 체크리스트 증거가 없는 실행은 목표(goal)·대상 자산만 근거로 최초 탐색을 제안(bootstrap) |
| `reporting.py` | DB 기록을 보고서 모듈 형식으로 넘기는 어댑터, 커버리지. 체크리스트 없는 실행은 `generate_autonomous`가 `findings` 테이블을 `audit.pentest_report._Builder` 가 기대하는 모양으로 바꿔 **같은 Word 서식**에 채운다(새 서식을 만들지 않음) |
| `src/audit/` | 공용 라이브러리: 스키마 검증, 범위 정책, 체크리스트 로더·가져오기, 규칙 판정, 오버레이, 탐색 발견 검증 신호(`signals.py`), MCP 클라이언트, Ollama 클라이언트, Word/Excel 보고서 |
| `src/webapi/`(선택), `web/`(선택) | 로컬 웹 UI(§5a). `webapi` 는 새 진단 로직 없이 위 모듈을 그대로 부르는 FastAPI 어댑터, `web` 은 Next.js 프론트엔드. CLI 는 이 없이도 완전히 동작 |

판정 값: `PASS, FAIL, NOT_APPLICABLE, NEEDS_REVIEW, INCONCLUSIVE, NOT_RUN`. 작업 상태: `PENDING → READY → RUNNING → SUCCEEDED | FAILED | BLOCKED | NEEDS_REVIEW | CANCELLED`.

## 7. 구현 상태 (2026-10-19)

| 기능 | 상태 | 근거 및 제한 사항 |
|---|---|---|
| 설정 검증 | 구현 | `doctor` 가 DSN 누락·미적용 마이그레이션을 FAIL 로 표시 |
| PostgreSQL 스키마·마이그레이션 | 구현 | 전용 DB 에 5개 적용(0005: `runs.checklist_version_id` nullable), 추가 전용 트리거가 증적 삭제를 거부 |
| **체크리스트 없는 자율 진단** | **구현** | CLAUDE.md §3.2/§5.1/§6 — 체크리스트는 필수 조건이 아니라 선택적으로 곁들이는 기능으로 분리. `runs.create_run`이 `checklist_id`를 선택 인자로 받아 없으면 checklist import·platform 검증을 전부 생략하고 goal 노드(`"자율 진단: <targets>"`)와 자산만 등록(`--llm` 없으면 `ConfigError`로 거부). `explore.propose`(`explore.py`)가 체크리스트 판정/사실이 전혀 없어도(`known` 이 비어 있어도) GOAL·TARGETS 만 근거로 1차 탐색을 제안하는 bootstrap 경로 추가(증적 인용 요구를 이 경우만 생략). `planner.plan`은 체크리스트 없는 실행에서 `verdict/fact/evidence/approval` 이벤트 유무와 무관하게 매 주기 탐색을 시도(체크리스트 있는 실행의 기존 게이트는 그대로 유지, 회귀 없음). CLI: `run create --targets .. --operator .. --llm`(체크리스트 생략), `run status` 의 체크리스트 표시 NoneType 버그도 실제로 CLI 를 돌려보다 발견해 수정. 단위테스트 3건(`tests/unit/test_autonomous_run.py`, 실제 PostgreSQL) + 기존 91건 전부 PASS. **실제 로컬 LLM(qwen2.5:7b)+실제 MCP(Burp)로 end-to-end 확인**: 체크리스트 없이 `baseline-demo` 대상 실행을 만들어 `run start` → LLM 이 사전 증거 없이 Burp 프록시 히스토리 수집을 1차 탐색으로 제안 → 정책 통과 → 실제 실행 → 증적 1건 생성 → 실행 `COMPLETED`까지 확인. 체크리스트 사후 부착(기존 run 에 나중에 체크리스트를 붙이는 기능)은 아직 미구현 |
| **체크리스트 없는 실행의 보고서** | 구현 | `reporting.generate_autonomous` — **새 보고서 서식을 만들지 않고 `audit.pentest_report` 의 Word 조립 로직(`_Builder`/`fill_overview`/`fill_summary`/`fill_improvements`/`fill_details`/`fill_protection`/`verify`)을 그대로 재사용**한다. 바뀐 건 발견 사항의 출처뿐: 체크리스트의 `collect_findings`(FAIL 판정 + findings.json 매칭) 대신 `_findings_for_pentest_report`가 `findings` 테이블을 같은 모양(`check_id`/`target_id`/`name`/`result`/`path`/`notes`/`cases`/`recommendation`/`rec_titles`)으로 변환해 넘긴다. `notes` 란에 검증 방식과 실제 검토 상태를 적어 CONFIRMED 와 아직 검토 전인 CANDIDATE/NEEDS_REVIEW 를 구분한다(CLAUDE.md §9/§10). REJECTED(사람이 오탐으로 확인)는 본문에서 제외한다(§3.3). `--meta` 는 체크리스트 실행과 동일하게 필요하고, PASS/FAIL 집계가 없어 `--basic` 은 거부. 실제 end-to-end 확인: `findings` 테이블에 행을 넣고 `report --meta ..` 실행 → 회사 템플릿 docx 생성 → 다시 열어 발견 사항 제목이 본문에 있는지(`pentest_report.verify`) 확인(문제 0건). 단위테스트(`test_report_reuses_pentest_report_format_with_db_findings`, 실제 PostgreSQL + 실제 템플릿). **사람의 Finding 검토 워크플로 구현됨**(아래 행 참고) — `finding review` 로 CONFIRMED/REJECTED/NEEDS_REVIEW/INCONCLUSIVE/NOT_TESTED 전환 가능, 보고서가 그 상태를 반영 |
| **Finding 검토 워크플로** | 구현 | `migrations/0006_finding_review.sql` — `findings.status` 를 CLAUDE.md §9 의 6 종 상태로 전환(CHECK 제약, 기존 RULE_CONFIRMED→CANDIDATE/REVIEWER_CONFIRMED→CONFIRMED 로 백필), `reviewed_by`/`reviewed_at`/`review_reason` 컬럼과 추가 전용(append-only trigger) `finding_review_history` 이력 테이블 추가. `worker.set_finding_status()` — CONFIRMED 는 `finding_evidence` 에 연결된 증거가 있어야만 허용(없으면 `AuditError`, §9 확정 조건), REJECTED/NEEDS_REVIEW/INCONCLUSIVE 는 증거 없이도 가능. 모든 전환은 `finding_review_history` 에 이전 상태·사유·검토자·증거 id 를 보존(기존 `verdict_history` 와 같은 패턴). 체크리스트 FAIL 판정 검토는 기존 `review` 명령이 담당하므로(`run_checklist_item_id` 가 있는 finding), 그 경로가 닿지 않는 자율 탐색 발견 사항(`run_checklist_item_id` 없음)까지 포함해 **모든** finding 을 다루는 `finding list`/`finding review` CLI 를 추가. 실제 PostgreSQL 로 end-to-end 확인: 신규 finding 은 CANDIDATE 로 시작 → 증거 없이 CONFIRMED 시도 시 거부 → 증거 연결 후 CONFIRMED 성공 → REJECTED 는 증거 없이도 성공 → 이력 2건 생성 확인, `UPDATE finding_review_history` 시도 시 append-only 트리거로 거부. `_findings_for_pentest_report` 가 REJECTED 를 본문에서 빼고 상태별 건수(`findings_by_status`)를 보고서 결과에 포함하도록 수정. **재검증(retest) 추가(CLAUDE.md §10 "추가 검증 요청")**: `worker.create_retest()` — 그 finding 의 `finding_evidence`가 가리키는 증거(`evidence.tool`/`evidence.args`)를 그대로 새 탐색 백로그 작업으로 다시 올리고(상태는 바꾸지 않음), 요청 사실을 `finding_review_history`에 남긴다(`old_status == new_status`, 판정 변경이 아니라 요청 기록). 연결된 증거가 없으면 `AuditError`로 거부(재실행할 도구/인자가 없음). CLI: `finding retest <run> <finding_id> --reason .. --reviewer ..`. 단위테스트 8건(`tests/unit/test_finding_review.py`, 전부 실제 PostgreSQL) |
| **자율성 확장** | **구현(CLAUDE.md §1)** | 기본 정책으로 구현된 변경 7건(아래 ①~⑦). ① **탐색 호출 수 무제한**: `max_explorations`/`max_tool_calls_exploration` 강제 중단 제거(`explore.py`/`worker.py`, 체크리스트 쪽은 그대로 강제). ② **LLM 최초 라운드 직접 실행**: bootstrap 라운드에 한해 LLM 이 read 전용 도구를 최대 3회까지 `worker.run_tool()`(Scope·제외 목록 검사 그대로 통과)로 백로그 대기 없이 즉시 실행(`explore._bootstrap_probes`, 새 `PROBE_SYSTEM`/`_PROBE_SCHEMA`). 실행 결과는 같은 라운드의 proposals 인용 검증에 바로 쓰인다. dangerous/write 도구나 범위 밖 대상은 거부. 단위테스트 2건(`tests/unit/test_bootstrap_probes.py`). ③ **LLM 직접 finding 확정**: `PROPOSALS` 스키마에 선택적 `findings` 필드 추가, `worker.create_llm_finding()`이 인용된 증거가 실존할 때만 `verification='LLM_CONFIRMED'`·`status='CONFIRMED'`로 즉시 기록(주장의 실체는 검증하지 않음, 사람은 이후에도 `finding review`로 재검토 가능). 단위테스트 3건(`tests/unit/test_llm_finding.py`). ④ **자율성 이분법**: `approval`/`partial`/`full` → `approval`/`full`(기존 `partial` 실행은 `approval`로 백필, `migrations/0007`). 단위테스트 3건(`tests/unit/test_autonomy_binary.py`). ⑤ **Kill Switch 가 진행 중 호출도 취소**: `tools.py`(서브프로세스, `Popen`+폴링) 와 `audit/mcp_client.py`(MCP, 모든 호출을 취소 가능한 future 로 전환)를 고쳐 `data/STOP` 이 생기면 0.2초 폴링 간격 안에 진행 중인 호출도 끊는다(기존에는 신규 디스패치만 막았음 — CLAUDE.md §8 자체 요구사항에 원래 못 미치던 공백). 단위테스트 5건(`tests/unit/test_tools_kill_switch.py`, `tests/unit/test_mcp_kill_switch.py`). ⑥ **깨어나는 조건을 디바운스+하트비트로**: `coordinator.py`의 `_wait_for_wake()` — 할 일이 없으면 즉시 종료하지 않고 이벤트+디바운스(800ms)+하트비트(600s, 폴백) 로 기다리다가 다시 계획해 보고, 하트비트에도 새 작업이 없으면 그제서야 "유효한 후속 작업 없음"으로 끝낸다(PostgreSQL 을 짧은 간격으로 폴링하는 방식 — LISTEN/NOTIFY 는 안 씀). 단위테스트 3건(`tests/unit/test_coordinator_wake.py`, 테스트 중엔 상수를 짧게 바꿔 실제로 기다리지 않음). ⑦ Finding retest — 위 행 참고. 모두 실제 PostgreSQL 로 end-to-end 확인, 전체 단위테스트 112건 중 111건 PASS(나머지 1건은 이 컨테이너에 frida MCP 서버가 없어서 실패하는 환경 제약, 이번 변경과 무관) |
| 범위·정책 강제 | 부분 구현 | 범위 밖·경로 이탈·제외 대상·DNS·중지 스위치·허용 도구·승인·호출 간격 확인. 자율성 스위치(`approval`/`full`, 2026-10-19 이분법으로 단순화)가 `scope.json` 의 `allowed_risks` 범위 안에서만 승인을 건너뛰는 것을 write(nmap)·dangerous(frida) 도구로 확인. **리다이렉트 하위 요청 범위 검사(2026-10-19 구현)**: `policy.landed_out_of_scope`가 `browser_network_requests` 의 출력(번호 붙은 "[METHOD] url => [STATUS]" 목록, @playwright/mcp 실제 소스로 형식 확인)도 같은 범위/제외 검사를 받게 확장 — 페이지 자체는 범위 안이어도 그 안의 XHR/스크립트/이미지 같은 하위 요청이 범위를 벗어나면 잡는다. 단위테스트 4건(`tests/unit/test_network_requests_scope.py`, 합성 데이터 — 이 PC 에 Playwright MCP 가 없어 실제 서버로는 미확인) |
| 체크리스트 가져오기 | 구현 | 활성·해시 고정 버전만 DB 로 가져오고 원문 보존(선택적 기능 — §4 참고) |
| 결정론적 체크리스트 점검 | 구현 | 예시 체크리스트로 5가지 판정 확인. 공식 12종은 전 항목 사람 검토 |
| 자동 점검 (A) 결정론적 규칙 (WEB/Mobile) | 부분 구현 | `config/rule_overlays.json`(스키마 `RULE_OVERLAYS`) → `audit.overlays.load_overlays`(정규식·증거 참조 교차검증) → `runs.import_checklist`에서 원문 `pass_criteria`/`fail_criteria`는 그대로 두고 `verification_method`/`evidence_requirements`만 오버레이로 바꿔 `checklist_items.body`에 저장. 기존 `audit.verdict` 엔진이 그대로 판정(단위테스트 8건, `tests/unit/test_rule_overlays.py`), 판정 사유에 "오버레이(업계 표준 기법) 기준, 원문 판단기준 아님" 자동 표기(`worker.py`). `checklist overlays` CLI로 실제 DB의 활성 체크리스트(`fsec-2026-1-web-mobile-hts` WEB-SER-038)와 교차검증 확인. 이미 import 된 버전에 소급 적용하는 `runs.reapply_overlays` + `checklist reapply-overlays <id> <version>` CLI 추가. 로컬 PostgreSQL 18(+psycopg) 로 end-to-end 확인: `tests/fixtures/checklist` 의 sample-local-config 로 import → 오버레이 적용(재적용으로 AUTOMATABLE 전환, 원문 criteria 불변) → 오버레이 제거 후 재적용하면 다시 원문(MANUAL_REVIEW)으로 복귀 — 전부 PASS. **오버레이 데이터 6건 — WEB-SER-030/034~038 전부 작성**: HTTP 메서드(038, `mcp:burp/send_http1_request`) / 서버 인증서 무결성(030)·취약한 HTTPS 프로토콜(034, 신규 `tls.inspect` 도구 — stdlib `ssl`/`socket`만 사용, 새 의존성 없음) / 취약한 암호 알고리즘(035)·취약한 HTTPS 컴포넌트(036, 신규 `nmap.ssl_scan` 도구 — 이 PC 에 이미 설치된 nmap 의 NSE 스크립트 `ssl-enum-ciphers`/`ssl-heartbleed`/`ssl-ccs-injection` 고정 사용, 임의 `--script` 인자는 받지 않음) / **취약한 재협상(037, 신규 `tls.renegotiation` 도구 — 전용 스캐너 testssl.sh 3.2 도입**, 운영자 승인 하에 공식 저장소에서 다운로드해 `data/tools/testssl.sh/`에 설치, Git 에는 안 올라감). `tls.inspect`는 ① 검증 없이 인증서를 받아 만료·자체서명·CN/SAN-호스트명 일치를 보고 ② SSLv3/TLSv1/TLSv1.1 로 핸드셰이크를 강제 시도해 서버가 받아주는지 본다. 로컬에 자체서명 인증서로 TLS 서버를 띄워 실제 핸드셰이크로 확인(`tests/unit/test_tls_inspect.py` 4건, `nmap.ssl_scan`은 같은 서버에 실제 nmap 7.98 을 돌려 캡처한 실제 출력으로 회귀 테스트, `tests/unit/test_rule_overlays.py` 총 14건 — 전부 실 소켓/실 nmap 실행). **testssl.sh 연동**: Git-for-Windows MSYS 에 없는 `hexdump`/`pkill`을 testssl.sh 가 실제로 쓰는 호출 형태만 정확히 구현한 대체 스크립트(`data/tools/hexdump-shim/`)로 보완(GNU hexdump 출력과 바이트 단위로 대조 확인). 실제 testssl.sh 를 로컬 TLS 서버에 두 번(`-R` 단독, `-p -S` 전체) 돌려 재협상·프로토콜·인증서 판정이 정상 출력됨을 확인했고, `make_tools()`가 돌리는 실제 실행 경로로도 동일 결과를 재확인(`tests/unit/test_testssl_tool.py`, testssl.sh 미설치 환경에서는 자동 skip). **이 과정에서 실제 버그 발견·수정**: ① 취약 프로토콜 프로브가 최신 OpenSSL 기본 보안 레벨(SECLEVEL) 때문에 실제로 취약한 서버에도 전부 "rejected"(거짓 안전)로 보고하는 문제 — 프로브 측 cipher 를 `@SECLEVEL=0`으로 낮춰 수정(테스트로 "accepted" 경로까지 실측 확인). ② `tools.make_tools()`가 nmap 이 없으면 `tls.inspect`까지 통째로 등록하지 않던 버그(nmap 조건부 early-return 안에 있었음) — nmap 의존 도구와 분리해 수정. 나머지 ~12개 후보(세션/인증 등 행동형 항목)는 (A) 적용 대상이 아님으로 재분류 |
| 자동 점검 (B) 탐색 기반 발견 승격 (WEB/Mobile) | 부분 구현 | `audit.signals`(순수 함수) — **CLAUDE.md §7B 가 이름 붙인 7종(SQLi/XSS/CSRF/SSRF/SSTI/XXE/명령 실행) 전부 구현**: `classify_payload`가 공격 페이로드 문자열 시그니처(SQLi/XSS/SSTI/XXE/CMDI) 또는 요청 구조(CSRF: 상태 변경 메서드+인증 쿠키+대상과 다른 Origin/Referer, 공격 "문자열"이 없어 다르게 접근)를 찾고, `verify_*`가 응답에서 클래스별로 다른 신호를 따로 확인 — 반사·오류 문구·성공 코드·평가값 하나만으로 확정하지 않음. SSRF 는 클라우드 메타데이터 응답 키/`/etc/passwd` 같은 "그 자원에만 있는 고유 내용", SSTI 는 "문자열 그대로 반사"가 아니라 "평가된 값(7*7→49)", XXE 는 "엔티티가 실제로 해석된 내부 파일 내용", 명령 실행은 "그 명령 고유의 출력 형태(uid=.., 커널 문자열)"를 요구. 패턴 매칭 순서가 중요함을 실제로 확인(XXE 의 `file://` URI 가 SSRF 스킴 패턴에도 걸려 더 구조적인 XXE 를 먼저 검사하도록 순서 수정 — 테스트로 발견). 단위테스트 34건(`tests/unit/test_signals.py`, 7종 전부). `worker._run_exploration`이 fact 생성 시 신호를 확인해 일치하면 `findings`에 `verification='RULE_CONFIRMED'`로 삽입하고, finding 전용 `exploration_nodes`(`kind='finding'`)를 만들어 `exploration_edges`에 `proves` 엣지를 연결(전부 `exploration_signal_checked` 감사 이벤트로 기록, 불일치해도 기록) — 새 클래스는 `SIGNALS`/`SEVERITY`/`REMEDIATION`에 등록만 하면 기존 배선을 그대로 타므로 `worker.py` 변경 없음. 체크리스트 사후 매핑은 `traces.tag_node` + `trace tag` CLI로 사람이 탐색 노드에 check_id 를 붙임(exploration_anchors.checklist_item_id, 판정 자체는 바꾸지 않음). 로컬 PostgreSQL 로 end-to-end 확인(21개 단언 전부 PASS, SQLi/XSS 기준): XSS 신호 일치 시 finding·finding_evidence·finding 전용 노드·proves 엣지·감사 이벤트가 모두 생성되고, 인코딩된 반사·알려진 시그니처 없음 케이스는 finding 을 만들지 않으며, `trace tag` 매핑과 존재하지 않는 check_id 거부도 확인. **이 과정에서 실제 버그 발견·수정**: ① `exploration_edges` 는 양쪽 다 `exploration_nodes` 를 참조하므로 `findings.id` 를 직접 넣으면 FK 위반 — finding 전용 노드를 만들어 anchor 하도록 수정. ② 분류 패턴 순서 충돌(위 XXE/SSRF). **모든 7종 DB end-to-end 확인 완료**(`tests/unit/test_signal_promotion_db.py`): CSRF/SSRF/SSTI/XXE/CMDI 각각 실제 PostgreSQL 에 finding·finding_evidence·finding 전용 노드·proves 엣지·`exploration_signal_checked` 감사 이벤트가 생성되고, 불일치(SSRF 주소만 있고 메타데이터 내용 없음)일 땐 finding 없이 감사 이벤트만 남는 것까지 확인. `evidence`/`audit_events` 는 추가 전용(DELETE 트리거로 막힘)이라 테스트는 commit 하지 않고 끝에서 rollback 해 실제 DB 에 아무것도 남기지 않는다(디버깅 중 실수로 commit 한 테스트 행 2건만 영구 잔존 — `TEST-SIGNAL-PROMOTION`, 무해한 합성 데이터). **SQLi 불린 기반 차등(diff) 검증 추가**: `classify_sqli_boolean`(`'1'='1'` 참/`'1'='2'` 거짓 구분) + `pairing_key`(참/거짓 리터럴만 같게 바꾼 나머지 인자가 같은지 — 다른 엔드포인트끼리 비교해서 의미 없는 차이를 신호로 오인하지 않게)로 같은 주입 지점의 참/거짓 짝을 찾고, `verify_sqli_boolean_diff`로 SQL 오류 문구 없이도 응답 길이가 의미 있게(잡음 수준 이상) 다르면 확정한다 — SQLi 중 유일한 비-오류-기반 신호. `worker._check_sqli_boolean_diff`가 같은 자산의 이전 증적(`evidence` 테이블)에서 짝을 찾아 `evidence.read_bytes`로 원본 파일을 다시 읽어 비교하고, 맞으면 참/거짓 증적 둘 다 `finding_evidence`로 연결한다. 짝을 못 찾으면 조용히 건너뛰고 기존 오류 기반 신호로 넘어간다. 실제 PostgreSQL + 실제 파일로 end-to-end 확인(`tests/unit/test_sqli_boolean_diff_db.py` 3건: 의미 있는 차이는 승격, 동일 응답은 승격 안 됨, 짝 없으면 조용히 건너뜀 — 전부 실제 파일 읽기/쓰기 포함) + 순수 함수 단위테스트 9건. **실제 MCP 도구 연동 확인 완료**: `baseline-demo`(docker-compose, `localhost:9999`) Level 1 게시판 검색에서 실제 불린 기반 SQLi(`searchType=title&keyword=zzz' OR '1'='1' -- `)를 찾아, 실제 Burp MCP(`send_http1_request`)로 참/거짓 요청을 보내고 그 실제 응답이 `worker._promote_exploration_finding`을 거쳐 finding(심각도 high)으로 승격되는 것까지 확인(`tests/unit/test_real_mcp_sqli.py`, 대상·Burp 가 없으면 자동 skip). **이 과정에서 실제 버그 3건 추가 발견·수정**: ① `verify_csrf`의 상태 코드 정규식이 줄 시작(`^`)에 고정돼 있어 실제 Burp MCP 응답 형식(`HttpRequestResponse{...httpResponse=HTTP/1.1 200...}`, 줄바꿈 없는 JSON 한 줄)에서는 절대 못 찾던 문제 — 줄 위치 고정을 없애 수정(요청 줄의 HTTP/1.1 뒤에는 숫자가 안 와서 혼동 안 됨). ② `classify_sqli_boolean`/`pairing_key`가 리터럴이 그대로(unencoded) 있다고 가정해 실제 GET 요청(URL 인코딩된 `%27%20OR%20...`)에서는 전혀 분류되지 않던 문제 — 원문과 URL 디코딩한 문자열을 둘 다 보도록 수정. ③ WEB-SER-038 오버레이가 Burp 의 실제 파라미터 이름(`content`)이 아니라 틀린 이름(`request`)을 써서 실제로는 호출이 실패했을 문제 — 실제 도구 스키마로 확인 후 수정. 길이 기반 차등만 구현(응답 시간 기반 블라인드 SQLi 는 미구현) |
| 로컬 LLM 연동 | 구현 | 분석·탐색 제안이 스키마 검증 통과. 16GB 노트북에서 호출당 10~90초 |
| 작업 큐·Worker 조정 | 구현 | 동시 실행 중복 0건, 임대 만료 복구, 재개·취소 확인. Worker 는 프로세스당 1개 |
| MCP 어댑터 | 부분 구현 | Playwright·Frida·Burp 요청 전송은 실제 출력 확인. **Burp 프록시 이력**: 도커 데모 대상(`localhost:9999`)에 실제 트래픽을 흘려 `get_proxy_http_history` 파싱·범위 필터를 확인(수신 4건 → 저장 3건, 호스트가 다른 1건은 정확히 제외, 증적 무결성 확인). Ghidra·JADX 는 `CONNECTED_UNVERIFIED` |
| Nmap | 구현 | 승인·범위·인자 거부와 실제 스캔, 서비스 자산 등록 확인. NSE·OS 탐지·UDP 미지원 |
| 증적 저장 | 구현 | 해시 대조 통과, 스크린샷은 원본과 연결된 파생 증적. 마스킹 파생본 없음 |
| Word 보고서 템플릿 | 부분 구현 | 웹 결과보고서를 실제 대상 실행으로 생성 확인. 모바일 템플릿 미지원 |
| Excel 보고서 템플릿 | 부분 구현 | 요약 + 커버리지 시트 확인. 사용자 지정 Excel 템플릿은 미확인 |
| 탐색·재계획 반복 | 구현 | read/write 제안 → 정책 검증(스키마·자산 유형·자율성 스위치) → 실행 → 사실 기록 → 재계획 → 예산 거부 확인. write 제안이 `approval` 모드에서 거부되지 않고 승인 대기(BLOCKED)로 큐에 서고, 승인 후 재개됨을 실제 LLM 과 nmap/Burp 도구로 확인. 완료(COMPLETED)된 실행도 새 증적/사실이 생기면 `run start` 로 다시 깨어남. dangerous 도구는 자율 제안에서 항상 제외 |
| 이중 그래프·anchor 연결 | 구현 | 탐색 그래프 다단계 체인(`goal→intent→fact→(derived_from)→intent→fact`) 을 가짜 LLM 응답(결정론적)과 실제 LLM 양쪽으로 확인. 처음엔 모델이 사실의 내용은 올바르게 서술하면서도 `based_on_evidence_ids` 에 옛 증적만 인용했다 — 프롬프트에서 최신 사실(LATEST_FACT)을 맨 앞에 강조하고 "같은 대상을 다루는 제안은 최신 사실을 인용해야 함"을 Python 검증 규칙으로 추가(다른 대상으로의 병렬 탐색은 그대로 허용)한 뒤 실제 체인이 생성됨 확인. qwen2.5:7b-instruct 로 1단계 체인, qwen2.5:14b(`ollama.timeout_s` 를 늘려서 확인)로 **거부 없이 깊이 5단계까지** 이어지는 연속 체인을 확인(Burp MCP 실연동). 자산은 범위 선언 + 관찰(`fs.list_dir`, Playwright, Nmap)에 **Burp 이력 기반 자산 추출(2026-10-19 구현)** 이 더해졌다 — `audit.adapters.burp.request_urls()`가 `get_proxy_http_history`/`get_proxy_http_history_regex`/`get_organizer_items`/`get_scanner_issues`(이미 `scope_filter` 로 범위 밖 호스트가 걸러진 뒤) 결과에서 요청 줄+Host 헤더(또는 구조화된 url/baseUrl 키)로 URL 을 복원해 `url` 자산과 `serves` 엣지로 등록한다. 복원한 전체 URL 로 다시 한 번 범위를 확인한다(호스트만 보는 `scope_filter` 와 별개). 단위테스트 6건(`tests/unit/test_burp_asset_extraction.py`, 실제 확인된 Burp MCP 응답 샘플 — `test_signals.VerifyCsrf` 에서 실측한 형식 재사용. 실제 Burp 인스턴스로는 미확인) |
| 공유 TodoList·선행 조건 | 부분 구현 | 멱등 키와 선행 조건 대기 확인. 선행 관계를 자동으로 만드는 규칙 없음 |
| 체크리스트·탐색 도구 호출 예산 분리 | 구현 | `budget.max_tool_calls_exploration`(기본 50)을 `max_tool_calls`(체크리스트용, 기본 500)와 분리했던 것은 그대로지만, **CLAUDE.md §1 정책에 따라 탐색 쪽은 강제하지 않는다** — `worker._BUDGET_KEY` 에 `exploration` 키를 빼서 `run_tool()`이 탐색 작업에는 예산 검사를 하지 않고, `explore.propose()`의 `max_explorations` 상한도 없앴다. 체크리스트 쪽(`max_tool_calls`/`max_tasks`)은 전과 동일하게 강제된다. 필드 자체는 `run status` 표시용으로 남아 있다(집계는 보여주지만 막지 않음). `worker._tool_calls_used(ctx, kind)`가 `tool_invocations`를 `backlog_items.kind`로 나눠 세는 구조는 그대로 재사용. 실제 PostgreSQL 로 두 kind 의 집계가 독립적임을 확인(`tests/unit/test_budget_separation.py`, 2건 PASS) |
| Worker Activity 검색·재사용 | 부분 구현 | `trace` 동작, 실패 이력을 탐색 프롬프트에 참고로 제공. 대량 데이터 성능 미확인 |

도커 데모 대상(`baseline-demo`, `localhost:9999`)으로 실행 생성 → Nmap·Playwright·Burp 증적 → 검토 판정 → 결과보고서까지 확인했다. 그 밖의 확인은 임시 PostgreSQL 과 예시 체크리스트로 했다.

## 8. 다음 구현 단계

CLAUDE.md 는 바뀌지 않는 규칙만 담는다. 단계별 계획은 상태가 바뀔 때마다 여기를 고친다.

```text
탐색/체크리스트 공유 큐 (구현) ──┬── 자동 점검 (A) 결정론적 규칙  [부분 구현]
                                 └── 자동 점검 (B) 탐색 기반 발견 승격 [구현]
                                          │
                                   체크리스트 사후 매핑 (check_id 태그) [구현]
                                          │
                                   커버리지·보고서 반영 [부분 구현]
```

| 단계 | 내용 | 현재 상태 |
|---|---|---|
| 0a | **체크리스트를 선택 기능으로 분리, 체크리스트 없이 자율 탐색 시작(bootstrap)** | **구현(§7)** — `run create`가 체크리스트 없이도 동작, `explore.propose`/`planner.plan`이 사전 증거 없이도 탐색을 시작. 실제 LLM+MCP로 end-to-end 확인 |
| 0 | 체크리스트 가져오기·판정·보고서 기본 경로 | 구현 (§7, 선택적 기능) |
| 1 | 자산/탐색 이중 그래프, 다단계 탐색 체인, 자율성 스위치 | 구현 (§7) |
| 2 | 공유 작업 큐·Worker 조정·Activity 검색 | 구현/부분 구현 (§7) |
| 3 | 자동 점검 (A): WEB/Mobile 결정론적 규칙 등록(`check_id`→도구·인자·판정) | 부분 구현(§7) — 엔진·로더·CLI·재적용 명령 완료, **오버레이 데이터 6/6건**(HTTP 메서드·인증서 무결성·취약 프로토콜·취약 암호·취약 컴포넌트·재협상). 신규 도구 3개: `tls.inspect`(stdlib ssl), `nmap.ssl_scan`(기존 nmap NSE 고정 스크립트), `tls.renegotiation`(신규 도입 testssl.sh 3.2 + MSYS 용 hexdump/pkill 대체 스크립트). 테스트 24건 전부 실제 소켓/실제 nmap/실제 testssl.sh 로 확인(건수는 §7 참고). (A) 범위에 해당하는 WEB 후보는 모두 처리 — 나머지는 행동형 항목이라 범위 밖 |
| 4 | 자동 점검 (B): 탐색 발견의 취약점별 검증 신호 정의, `RULE_CONFIRMED` 승격, 체크리스트 매핑 태그 | **구현(§7)** — §7B 가 이름 붙인 7종 신호 + SQLi 차등 검증 + 실제 MCP(Burp) 도구·실제 대상(baseline-demo) 연동까지 전부 확인(버그 5건 발견·수정: FK 위반, 분류 순서 충돌, CSRF 상태코드 줄시작 고정, SQLi 불린 리터럴 URL 인코딩 미처리, WEB-SER-038 오버레이의 Burp 파라미터 이름 오류). 합성 데이터와 실제 트래픽 둘 다로 검증된 유일한 메커니즘 |
| 5 | 필수 체크리스트 작업과 탐색 작업의 예산 분리(§2 "굶지 않게 한다") | 구현(§7) — `max_tool_calls_exploration` 분리, DB 로 독립 집계 확인 |
| 6 | HTS 등 다른 platform/체크리스트로 오버레이 확장 | 계획 — 데이터 추가만으로 가능하도록 설계(§7), 실제 확장은 미착수 |
| 7 | 모바일 Word 템플릿, 리다이렉트 하위 요청 범위 검사, Burp 이력 기반 자산 추출 | **웹 두 항목 구현(2026-10-19)** — 모바일 Word 템플릿은 그대로 계획(범위 밖, §7 의 기존 제한 사항) |
| 8a | 체크리스트 없는 실행을 위한 보고서 생성 | **구현(§7)** — 새 서식 없이 `audit.pentest_report` 를 그대로 재사용, `findings` 테이블만 그 모양으로 변환해 채움(검토 상태를 notes 에 명시) |
| 8b | 기존 실행에 체크리스트를 나중에 붙이는 기능 | 계획 — 아직 미착수 |
| 9 | Finding 검토 워크플로(사람의 확정/기각, CLAUDE.md §9/§10) | **구현(§7)** — `findings.status` 6 종 상태 전환 + `finding_review_history` 이력 + `finding review`/`finding retest` CLI. CONFIRMED 는 증거 필수 |
| 10 | 자율성 확장 | **구현(§7, CLAUDE.md §1)** — 탐색 호출 수 무제한·LLM 최초 라운드 직접 실행·LLM 직접 finding 확정·자율성 이분법·Kill Switch 즉시 취소·디바운스+하트비트 wake-up (총 6건, CLAUDE.md 기본 정책) |
| 11 | 웹 UI MVP(findings/runs, 2026-10-19) | **구현(§5a)** — FastAPI(`src/webapi`, 새 진단 로직 없음) + Next.js(`web/`). 로그인, 진단 환경 설정(대상·체크리스트·자율성 선택 후 실행 생성)·시작/취소, 승인 대기 작업 승인/거부, 증적 목록·다운로드·무결성 확인, Activity 모니터링, runs 목록/상세, finding review/retest. 실제 Chromium 으로 end-to-end 확인. assets/traffic/agents/mcp/tools/settings/chat 등 나머지 화면은 범위 밖 |
| 12 | Finding 확정 알림(웹훅) | **구현** — `notify.py`가 `config/notify.json` 의 웹훅 채널로 finding CONFIRMED 전환(사람 검토·LLM 직접 확정 양쪽)을 POST 한다. 실패해도 판정/실행을 막지 않고 `audit_events`(`notification_sent`/`notification_failed`)에만 남긴다. 실제 로컬 HTTP 서버 + 실제 PostgreSQL 로 end-to-end 확인(`tests/unit/test_notify.py` 8건). Email/Telegram/DingTalk 등 다른 채널 종류는 미구현(웹훅만, 필요해지면 확장) |
| 13 | 다중 LLM 프로파일 풀 | **구현** — `migrations/0012`(`runs.llm_profile_name`, NULL 이면 기존처럼 `llm_provider` 하나만 씀) + `audit.config.load_llm_profiles`(`config/llm_profiles.json`, 선택) + `explore.make_llm`이 프로파일 값을 settings.json 기본 블록 위에 덮어씀. `run create --llm-profile <이름>`, 웹 API(`CreateRunBody.llm_profile`)와 `/api/environment`(`llm_profiles` 목록)에도 연결. `workbench llm-profiles [--check]` CLI로 프로파일별 실제 연결 가능 여부 확인. 실제 Ollama + 실제 `claude` CLI 양쪽으로 end-to-end 확인(`tests/unit/test_llm_profiles.py` 7건, `llm-profiles --check`로 모델 미설치 프로파일이 정확히 FAIL 로 구분됨도 확인). 주기적 헬스체크 데몬은 없음 — 필요할 때 `--check` 로 직접 확인하는 방식만 구현(상시 폴링 안 함) |
| 14 | 전용 재검증 판정(REPRODUCED/FIXED/INCONCLUSIVE) | **구현** — `worker.create_retest`가 재검증 작업을 큐에 올릴 때 원래 인자로 취약점 클래스를 분류해(`signals.classify_payload`) intent payload 에 같이 저장한다. `worker._resolve_retest`(`_run_exploration`이 재검증 작업 완료 시 호출)가 그 클래스의 신호(`signals.SIGNALS`)로 새 증거를 다시 확인해 REPRODUCED(여전히 재현)/FIXED(신호가 더 이상 안 뜸)/INCONCLUSIVE(결정론적 신호가 없는 체크리스트·LLM 확정 finding, 또는 도구 실행 실패)를 판정한다. `findings.status` 는 바꾸지 않는다 — "신호가 안 뜬다"는 "고쳐졌다"의 충분한 증거가 아닐 수 있어(CLAUDE.md §5.4) 최종 판단은 여전히 사람이 `finding review`로 한다. 판정은 `finding_review_history`(`actor='system:retest'`)에 이력으로만 남고, REPRODUCED/FIXED 는 새 증거를 `finding_evidence`에도 연결한다. `finding list` CLI 가 각 finding 의 최근 재검증 판정을 한 줄로 보여준다. 전용 LLM 대화형 에이전트가 아니라 결정론적 규칙 재대조이며(CLAUDE.md §1 "LLM 혼자 확정 안 함"과 일치), 판정 가능 범위는 탐색 신호로 확정된 finding 뿐이다(체크리스트 규칙·LLM 확정 finding 은 자동 판정 대상 아님, 사람 검토로 그대로 남음). 실제 PostgreSQL 로 end-to-end 확인(`tests/unit/test_retest_verdict.py` 5건: REPRODUCED/FIXED/도구 실패 INCONCLUSIVE/신호 없음 INCONCLUSIVE/재검증 아닌 intent는 무시) + `worker.create_retest`→`_resolve_retest` 실제 흐름 수동 확인 |
| 15 | 사이드 질문(human-in-the-loop) | **구현** — `exploration_nodes.kind='question'`은 마이그레이션 0001 부터 CHECK 제약에 있었지만 이번에 처음 쓴다. `explore.PROPOSALS` 스키마에 선택적 `question`(`text`, 선택적 `target_id`) 필드 추가, LLM 이 도구로 못 푸는 막힘(자격 증명·업무 맥락·권한 밖 결정)에서만 쓰도록 SYSTEM 프롬프트에 명시. `explore._ask_question`이 질문을 `kind='question'` 노드(기본 상태 OPEN)로 기록하고, 이미 OPEN 인 질문이 있으면 새 질문은 조용히 버린다(한 번에 하나, 스팸 방지, `question_rejected` 감사 이벤트). `explore._context`가 OPEN/ANSWERED 질문을 QUESTIONS 블록으로 다음 라운드 프롬프트에 넣어 LLM 이 답변을 보고 이어가거나 중복 질문을 피하게 한다. CLI `question list`/`question answer <id> --answer .. --operator ..`로 사람이 답하면 `state='ANSWERED'`로 바뀌고 `db.notify_change`로 다음 계획 주기를 깨운다. 사람 답변이 와도 그 자체로 새 작업을 만들지 않는다 — 다음 LLM 제안 라운드가 답변을 참고해 알아서 판단한다(기존 제안→정책 검증 경로를 그대로 재사용, 새 실행 경로 없음). 웹 UI 연동은 범위 밖(CLI 만). 실제 PostgreSQL + 가짜 LLM 으로 end-to-end 확인(`tests/unit/test_side_question.py` 4건: 질문 생성, 스팸 방지, 답변이 다음 컨텍스트에 노출, question 필드 없으면 아무 일도 안 함) |

| 16 | 거부된 탐색 제안 기억 | **구현** — 이전에는 정책 거부·미등록 도구 등으로 거부된 제안이 `audit_events`에만 남고 다음 라운드 프롬프트(`ALREADY_TRIED`)에는 전혀 안 보여서, LLM 이 똑같이 거부될 제안을 라운드마다 다시 낼 수 있었다(실행은 안 되니 안전하지만 라운드 낭비, CLAUDE.md §6 "동일한 실패를 무한 반복하지 않는다"의 취지에 못 미침). `explore.propose`가 거부 시(이미 작업이 있는 경우는 제외 — 그건 원래 작업이 이미 보임) `exploration_nodes(kind='intent', state='REJECTED')`로 가볍게 남기고(`backlog_items` 는 안 만듦 — 실행 대상이 아님), `explore._context`의 `ALREADY_TRIED` 쿼리가 이를 `UNION ALL`로 같이 보여준다. 실제 PostgreSQL + 가짜 LLM 으로 end-to-end 확인(`tests/unit/test_rejected_proposal_memory.py` 3건: 미등록 도구 거부 기록, 다음 컨텍스트에 노출, 범위 밖 대상은 자산 anchor 없이 기록) |
| 17 | 선행관계 자동 생성(보류 항목 7번) | **구현** — `explore.PROPOSALS`의 각 proposal 에 선택적 `depends_on`(같은 응답의 이전 proposal 0-based 인덱스) 필드. 수락 시 `backlog_deps` 행을 만든다 — 게이팅(READY/BLOCKED 전환) 자체는 기존 `planner._admit`이 이미 하므로 새 로직 없이 연결만 채움. `tests/unit/test_dependency_chain.py` 2건 |
| 18 | 비동기 자산 보강(보류 항목 8번) | **구현(옵트인)** — 신규 읽기 전용 도구 `net.dns_resolve`/`net.http_head`(`audit/adapters/net_tools.py`) + `planner.enrich.enqueue`가 이 실행이 아는 host/url 자산마다 보강 작업을 자동으로 큐에 올린다. `config/workbench.json`의 `enrich_enabled`(기본 `false`)로 옵트인 — 기본 켜두면 host/url 자산이 있는 모든 실행에 작업이 생겨 "할 일 없음"을 전제하는 기존 테스트가 깨지는 걸 발견해 옵트인으로 바꿈. `tests/unit/test_enrich.py` 3건(`net.dns_resolve`는 실제 localhost 소켓 조회까지 확인) |
| 19 | 트래픽/도구 호출 인터셉트 규칙(보류 항목 4번) | **구현** — `config/intercept_rules.json`(선택)에 도구 이름 정규식 + 선택적 인자 정규식을 등록하면 `policy.decide()`가 위험도(read 포함)·자율 모드(full 포함)와 무관하게 `ALLOW_WITH_APPROVAL`로 강제한다. 새 실행 경로 없음 — 기존 승인 플로우 재사용. `tests/unit/test_intercept_rules.py` 5건 |
| 20 | 동적 에이전트 프롬프트(DB 편집, 보류 항목 9번) | **구현** — `migrations/0013`(`agent_prompts` 테이블). 행이 없으면 코드 기본값 그대로(동작 변경 없음). `explore.propose`/`_bootstrap_probes`가 LLM 호출 직전 `prompts.get()`으로 DB 오버라이드를 읽는다. CLI `prompt list/show/set/reset`. `tests/unit/test_agent_prompts.py` 3건 |
| 21 | 단일 LLM → 역할별 에이전트 분리(1~3단계, artex 참고) | **구현** — 기존 Planner(`explore.propose`) 는 그대로 두고 그 위·아래에 역할을 하나씩 더함. **(1) Worker 에이전트화**: `worker._worker_decide`가 탐색 작업을 실행하기 직전에 Planner 제안(tool/args)을 그대로 쓸지, 같은 대상·read 위험도 안에서 다른 도구로 바꿀지 LLM 에게 한 번 더 묻는다 — 고른 결과도 `worker.run_tool()`(`policy.decide()`) 을 그대로 거치므로 실행 권한 경계는 안 바뀐다(CLAUDE.md §7/§8). write/dangerous 제안(사람 승인이 정확한 tool+args 해시에 묶임)과 재검증(retest, 원래 신호와 대조해야 함) 작업은 재판단하지 않고 Planner 제안 그대로 실행. **(2) 목표 분해**: `runs.create_run`이 운영자가 직접 쓴 목표(`--goal`)가 있고 `--llm` 이면 `runs._decompose_goal`로 대상별 하위 목표로 선택적으로 나눠 `goal` 노드를 여러 개 만든다(기존 1개 고정에서 확장) — `explore._context`의 GOAL 블록이 목표가 하나면 기존처럼 글자 그대로, 여러 개면 목록으로 보여준다. **(3) Reporter**: `reporting._polish_finding_text`가 보고서 출력용 문장(`result`/`recommendation`)만 다듬고, `findings.rationale`/`remediation`(DB 원문)과 대조용 `reason` 필드는 그대로 둔다(CLAUDE.md §3.3 — 확정 판정·근거는 LLM 다듬기와 무관하게 안전). 셋 다 LLM 이 없거나 실패/엉뚱한 응답이면 조용히 원래 값으로 폴백(CLAUDE.md §7). `tests/unit/test_worker_agent_decide.py` 5건(확인/override/write 제안 비재판단/retest 비재판단/엉뚱한 응답 폴백) + 기존 `test_pentest_report.py`/`test_bootstrap_probes.py` 등 나머지 193건 전부 통과로 회귀 없음 확인(총 198건, pre-existing 4건 실패는 `python3` 바이너리 없음 등 이 변경과 무관한 환경 문제). 사이드 질문처럼 운영자가 직접 끼어드는 4단계는 범위 밖(미착수) |
| 22 | 에이전트별 모델 지정(artex 의 역할별 LLM 바인딩 참고) | **구현** — 이전엔 실행(run) 하나에 모델 하나를 모든 역할이 공유했다. `migrations/0016`(`runs.llm_agent_models` jsonb, 기본 `{}` = 동작 변경 없음)에 역할(`planner`/`worker`/`analyst`/`goals`/`reporter`)→모델을 실행 생성 시 고정한다(scope·autonomy 와 같은 방식, LLM 이 못 바꿈). 값은 `config/llm_profiles.json` 프로파일 이름 또는 `ollama:<설치된 모델>`. 모델 결정 순서는 artex 와 같은 3단계: 역할 지정 → 실행의 `llm_profile`/`llm_provider` → (없으면 LLM 미사용). `explore.llm_for(ctx, role)`가 역할 전용 클라이언트를 만들어 같은 지정끼리 공유하고, `create_run`이 알 수 없는 역할·프로파일·미설치 Ollama 모델·`--llm` 없는 지정을 거부한다(Ollama 연결 불가면 설치 검증만 건너뜀). CLI: `run create --agent-model worker=ollama:qwen2.5:3b`(여러 번), `workbench llm-models`(설치된 모델 목록). 웹: `/api/environment`에 `ollama_models`·`agent_roles`, 새 진단 폼에 역할별 드롭다운, 진단 상세 Overview 에 지정 표시. 모든 모델의 도구 호출은 그대로 `policy.decide()`를 거친다. artex 의 폴백 체인·장애 모델 차단은 클라우드 API 크레딧 소진 대응용이라 이번엔 제외. 실제 PostgreSQL+Ollama 로 환경 목록·거부·저장 확인, `tests/unit/test_agent_models.py` 4건 |
| 21 | 자체 트래픽 레코딩 프록시(보류 항목 6번, 전체판) | **구현(실제 MITM)** — `tls_ca.py`가 로컬 루트 CA + 호스트별 리프 인증서를 생성·캐시(`cryptography`, 이미 고정된 전이 의존성). `proxy.py`의 `do_CONNECT`가 더 이상 블라인드 터널링을 하지 않고, 리프 인증서로 클라이언트와 실제 TLS 핸드셰이크를 맺어 복호화된 HTTPS 요청/응답을 evidence(`tool='proxy.https'`)로 남긴 뒤 실제 목적지로 새 TLS 연결을 열어 전달한다. CA 미설치 클라이언트는 그 호스트만 핸드셰이크 실패로 끊긴다(평문 HTTP 은 영향 없음). CLI `workbench proxy`가 운영자가 설치할 CA 경로를 출력. 실제 TLS 핸드셰이크로 end-to-end 확인(`tests/unit/test_proxy.py` 3건, 자체 서명 업스트림까지 포함). OpenSSL 3.2+/Python 3.13 의 엄격한 체인 검증(Authority/SubjectKeyIdentifier 필요)과, urllib 전역 opener 가 생성 시점에 기본 HTTPS 컨텍스트를 확정해 캐시하는 실제 버그 2건을 발견·수정 |
| 22 | 자산 멀티테넌시(보류 항목 11번, 전체판) | **구현(실제 격리)** — `migrations/0015`가 `assets`의 `UNIQUE(kind, value)`를 `UNIQUE(company, kind, value)`로 바꾼다. 같은 호스트명이라도 `company`가 다르면 서로 다른 자산 행이라, 한 회사의 발견 사항·증적이 다른 회사의 자산에 섞이지 않는다. 탐색 중 새로 발견한 자산(`worker._observe_assets`)도 그 작업이 속한 대상의 company 를 그대로 물려받는다. `tests/unit/test_asset_company_tag.py`(`AssetCompanyIsolation`)가 같은 호스트명을 다른 회사로 선언한 두 실행이 실제로 다른 자산 id 를 받는지 확인 |
| 23 | 웹 UI 화면 확장(보류 항목 10번, 전체판) | **구현** — 대시보드(`GET /api/dashboard` + `/dashboard` 페이지: 실행 상태·확정 심각도별 집계, 최근 진단), 트래픽(`GET /api/runs/{id}/traffic` + "트래픽" 탭: `proxy.http`/`proxy.https` evidence 를 모아 보여주고 기존 증적 다운로드 재사용), 탐색 그래프("탐색 그래프" 탭: 기존 `/assets` 조회를 SVG 로 시각화, 새 백엔드 없음), 질문/채팅(`GET/POST /api/runs/{id}/questions` + "질문" 탭: `cli.cmd_question`과 같은 동작을 웹에서). TypeScript 컴파일·ESLint·`next build` 통과 확인, **실제 브라우저(로그인 포함)로 네 화면 모두 렌더링 확인** — 대시보드 실데이터 표시, 그래프 SVG 렌더링, 트래픽 행 추가 후 표시, 질문에 실제로 답변을 기록해 "답변됨"으로 바뀌는 것까지 end-to-end 확인 |

**보류 7건 처리 결과**: 4,7,8,9 는 처음부터 전체판으로 구현했고, 6(자체 MITM 프록시)·11(멀티테넌시)·10(웹 UI 화면 확장)은 먼저 범위를 축소한 축소판을 구현한 뒤, 이어서 전부 전체판으로 교체했다 — 6 은 실제 TLS 가로채기(호스트별 리프 인증서 발급, 복호화된 HTTPS 기록), 11 은 `assets`의 유니크 키 자체에 company 를 넣은 실제 격리, 10 은 대시보드·트래픽·탐색 그래프·질문(채팅) 네 화면을 전부 추가했다. 전부 실제 PostgreSQL(+실제 TLS 소켓, 실제 브라우저 로그인 세션)로 end-to-end 확인했고, 이 과정에서 버그 여러 건(enrich 기본 켜짐으로 기존 테스트 4건 회귀 → 옵트인으로 수정, proxy 의 한글 send_error 가 latin-1 인코딩에서 깨짐 → 영문 메시지로 수정, X.509 Authority/SubjectKeyIdentifier 누락으로 OpenSSL 3.2+ 체인 검증 실패, urllib 전역 opener 의 HTTPS 컨텍스트가 생성 시점에 캐시돼 테스트 순서에 따라 달라지는 문제, CONNECT 포트 유실로 MITM 전달이 항상 443 으로 가던 문제)을 발견·수정했다.

**갭 분석 마무리**: 실용적 가치가 큰 5건(알림, 다중 LLM 프로파일, 재검증 판정, 사이드 질문, 거부된 제안 기억)을 구현했다. LLM 승인 판단 위임(도구 호출 가부를 LLM 이 직접 판단하게 하는 방식) 같은 항목은 CLAUDE.md §7 "LLM이 자신의 판단을 안전하다고 평가했다는 이유로 실행을 허용하지 않는다"와 정면으로 충돌해 **의도적으로 채택하지 않았다**. 남은 항목(트래픽 요청 단위 인터셉트, 자체 MITM 레코딩 프록시, 멀티테넌시 회사 분리, DB/UI 로 편집 가능한 동적 프롬프트·스킬, 웹 UI 의 assets/traffic/coverage 그래프/chat 화면)은 각각 이 프로젝트의 현재 아키텍처(단일 engagement, 외부 MCP 프록시 재사용, Python/JSON 파일 설정)와 전제가 달라 **별도 설계 논의가 필요한 더 큰 작업으로 보류**한다 — CLAUDE.md §3.1 "대규모 재작성보다 작고 검증 가능한 변경을 우선"에 따라 이번엔 손대지 않았다.

3(자동 점검 A)은 오버레이 데이터 6/6건이 전부 등록돼 WEB 플랫폼의 config-check형 항목은 더 추가할 게 없다 — 남은 건 §6 HTS 등으로의 확장(§7A는 platform·checklist_id 를 하드코딩하지 않으므로 데이터만 추가하면 됨, 이번에는 보류). 4(자동 점검 B)는 §7B 가 이름 붙인 7종 신호·SQLi 차등 검증·실제 MCP 연동까지 전부 끝나 "구현"으로 올렸다. 5(예산 분리)도 완료(단, 10 번으로 탐색 쪽 강제는 없어짐). 0a(체크리스트 분리·자율 탐색 bootstrap), 8a(자율 탐색 전용 보고서), 9(Finding 검토 워크플로), 10(자율성 확장), 12(알림)도 완료. 7은 웹 관련 두 항목(리다이렉트 하위 요청 범위 검사, Burp 이력 기반 자산 추출)을 2026-10-19 에 마무리했고 모바일 Word 템플릿만 남았다. **웹 UI(§5a)도 2026-10-19 에 findings/runs(tasks) MVP 를 구현**(나머지 화면은 범위 밖). 남은 우선순위는 8b(체크리스트 사후 부착) → 6(HTS 확장, 보류 중) → 7(모바일 템플릿) — 웹 UI 확장(대시보드/자산/탐색 그래프/트래픽/질문)은 2026-10-25 에 전부 구현했다.

## 9. 복구와 문제 해결

보고서 서식 코드(`src/audit/pentest_report.py`)는 서식·보호대책 연결·파일 무결성을 검증하는 테스트가 지킨다. 서식 코드를 고친 뒤 실행한다(실제 체크리스트 `fsec-2026-1-web-mobile-hts` 와 `templates/word/` 템플릿이 있어야 한다).

```powershell
.venv\Scripts\python -m unittest tests.unit.test_pentest_report
```

- `run start` 는 `COMPLETED` 실행도 다시 받는다 — 완료는 '그 시점에 더 할 일이 없었다'는 뜻일 뿐이며, `collect` 로 증적을 더 모으거나 탐색이 새 사실을 내면 다시 할 일이 생길 수 있다. `CANCELLED` 만 재개하지 않는다.
- Worker 가 죽으면 임대(`lease_s`)가 지난 뒤 READY 로 돌아가거나 시도 횟수 초과 시 FAILED 가 된다.
- 변경 이벤트를 놓쳐도 `run start` 가 DB 상태에서 다시 계획한다.
- 적용된 마이그레이션 파일은 수정하지 말고 새 번호의 파일을 추가한다.

| 증상 | 조치 |
|---|---|
| `환경 변수 WORKBENCH_PG_DSN 가 없음` | `.env` 작성 |
| `scope 가 ... 에 만료됨` | `scope.json` 의 `valid_until` 갱신 |
| `모델 '...' 이(가) 설치되어 있지 않음` | `ollama pull <model>` 또는 `settings.json` 수정 |
| 포트 바인딩 실패(`access permissions`) | Windows 예약 범위(8034–8133) 밖 포트 사용 |
| `PermissionError [WinError 32]` | 보고서 파일을 Excel/Word 가 열고 있음 |
| 한글이 깨짐 | Windows Terminal 사용 또는 `chcp 65001` |
