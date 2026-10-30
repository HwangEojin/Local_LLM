# 로컬 LLM 보안 진단 워크벤치

체크리스트 없이도 시작하는 자율 보안 진단 워크벤치. PostgreSQL 작업 큐 위에서 실행하고, 판정은 **등록된 도구로 수집한 증적 + 결정론적 규칙 + 사람 검토**로 내린다. 로컬 LLM(Ollama)이 다음 탐색 작업을 제안하지만(체크리스트가 없어도), 실제 실행은 항상 Python 정책 계층을 통과해야 한다. 체크리스트는 공식 기준 점검이 필요할 때 곁들이는 선택적 기능이다. 설계 원칙은 [CLAUDE.md](CLAUDE.md), 현재 진행 중인 업그레이드 계획은 [docs/UPGRADE_PLAN.md](docs/UPGRADE_PLAN.md).

## 1. 준비

| 항목 | 버전 |
|---|---|
| Python | 3.12 이상 (검증 3.13.5) |
| PostgreSQL | 13 이상 (검증 18.6) |
| Ollama | 0.5.0 이상, 구조화 출력 필요 (검증 0.35.1, `qwen2.5:7b-instruct`) |
| 선택 | Nmap, MCP 서버(Burp, Playwright, Frida, Ghidra, JADX), [testssl.sh](https://testssl.sh)(재협상 취약점 점검용) |

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.lock
.venv\Scripts\python -m pip install --no-deps -e .
```

저장소에 없는 것(각자 준비): 공식 평가기준 원본과 변환된 체크리스트(`checklist/`), 회사 보고서 템플릿(`templates/word/*.docx`), `.env`, `config/*.local.json`, `data/`, `reports/`.

## 2. 설정

| 파일 | 내용 |
|---|---|
| `.env` | `WORKBENCH_PG_DSN`, `PGPASSWORD`(`.env.example` 복사). 다른 앱과 DB 를 같이 쓰면 전용 스키마: DSN 끝에 `?options=-csearch_path%3Dworkbench` |
| `config/scope.json` | 승인된 범위: `engagement_id`, `approved_by`, `valid_until`, `allowed_risks`, `targets`(`path`/`host`/`url`, 필요 시 `platform`). 만료되면 모든 실행이 거부된다 |
| `config/settings.json` | Ollama 주소·모델·한도, 증적·보고서·체크리스트·템플릿 경로, 도구 호출 한도. 루프백이 아닌 모델 주소는 `external_api.enabled=false` 면 거부. `WORKBENCH_LLM_PROVIDER=claude_cli`(임시 테스트 전용) 설정 시 설치된 `claude` CLI(구독 로그인, API 과금 아님)를 대신 호출 — 도구 접근은 막고 실제 도구 실행은 그대로 Python 정책 계층만 거친다 |
| `config/mcp_servers.json` | MCP 서버와 허용 도구(`risk`, `scope_args`, `args_schema`). 이 PC 의 경로는 `mcp_servers.local.json` 에 같은 서버 이름으로 적는다 |
| `config/workbench.json` (선택) | 제외 대상 `exclude`, 예산 `budget`, `lease_s`, `min_call_interval_ms`, `nmap_path`, `testssl_path`(`workbench.example.json` 참고) |
| `config/notify.json` (선택) | finding 이 CONFIRMED 될 때 알릴 웹훅 채널 목록(`notify.example.json` 참고) |
| `config/llm_profiles.json` (선택) | 이름 있는 LLM 프로파일 목록(`llm_profiles.example.json` 참고). settings.json 기본 블록 위에 바꿀 값만 덮어쓴다 |
| `config/intercept_rules.json` (선택) | 도구 이름·인자 정규식에 매치되면 위험도·자율 모드와 무관하게 사람 승인을 강제(ask 전용). 이 PC 전용 `intercept_rules.local.json` 과 합쳐진다 |

```powershell
$wb = ".venv\Scripts\python", "-m", "diagnostic_workbench"
& $wb migrate      # 스키마 적용
& $wb doctor       # 설정·DB·범위·모델 점검 (--probe-mcp 로 MCP 연결 확인)
& $wb tools        # 등록된 도구와 위험도
& $wb llm-profiles --check   # 프로파일별 실제 연결 가능 여부
```

## 3. 체크리스트 (선택 기능)

```powershell
& $wb checklist import "D:\가이드\평가기준(제2026-1호).xlsx" 2026-1 --pdf "D:\가이드\주통 가이드.pdf"
& $wb checklist validate
& $wb checklist activate fsec-2026-1-server 2026-1
```

원본 시트마다 체크리스트 하나가 `draft` 로 만들어지고, 활성화하면 해시가 고정된다(원문 변경 시 실행·보고서 거부). 판단기준이 없는 항목은 지어내지 않고 `incomplete` 로 남긴다.

## 4. 진단 실행

```powershell
& $wb run create --targets baseline-demo --operator 이름 --llm   # 자율 탐색만(체크리스트 없음, 호출 수 무제한)
& $wb run create --checklist fsec-2026-1-web-mobile-hts@2026-1 --targets baseline-demo --operator 이름 [--llm] [--autonomy approval|full]
& $wb run plan  <run>      # 실행 예정 작업과 정책 분류 미리 보기
& $wb run start <run>      # 시작/재개 (Ctrl+C → PAUSED)
& $wb run status <run>     # 판정 분포, 작업 큐, 차단 사유, 사용량
& $wb backlog <run> [--status BLOCKED]
& $wb approve <run> <작업ID> --approver 이름 [--reject]
& $wb finding list <run>
& $wb finding review <run> <finding_id> CANDIDATE|NEEDS_REVIEW|CONFIRMED|REJECTED|INCONCLUSIVE|NOT_TESTED --reason .. --reviewer ..
& $wb evidence <run>       # 증적 목록 + 파일 해시 대조
& $wb graph <run>          # 자산 그래프와 탐색 그래프
```

- `--checklist` 는 선택이다. 생략하면(`--llm` 필요) 체크리스트 없이 목표(goal)와 대상 자산만으로 최초 탐색이 시작된다.
- `read` 도구는 범위 안이면 실행, `write`/`dangerous` 는 `scope.json` 의 `allowed_risks` 안에서만 허용된다. 그 안에서 승인 요구 여부는 `--autonomy`(기본 `approval`: 매번 승인 / `full`: 범위 내 자동 실행)로 정해지고, 실행 중 바꿀 수 없다.
- 자율 탐색의 탐색 횟수·도구 호출 수에는 하드 리밋이 없다 — 벽시계 타임아웃과 "유효한 후속 작업 없음"만으로 끝난다. 체크리스트 작업의 예산(`--max-tasks`)은 그대로 강제된다.
- 전체 중지: `data/STOP` 파일을 만들면 신규 호출이 거부되고 진행 중인 호출도 짧은 폴링 간격(0.2초) 안에 취소된다.

### MCP 서버

| 서버 | 사용 전 준비 | 비고 |
|---|---|---|
| Burp | Burp 실행 + MCP Server 확장(`http://127.0.0.1:9876/`) | 범위 밖 호스트 항목은 저장 전에 걸러낸다 |
| Playwright | `npx playwright install chromium` | 도착 주소가 범위 밖이면 출력 저장을 막는다 |
| Frida | frida-server/장치 | attach·spawn·hook 은 `dangerous` |
| Ghidra / JADX | 본체에서 대상을 열고 플러그인(8091 / 8650) 켜기 | 어떤 대상이 열렸는지는 범위로 검증할 수 없다(운영자 책임) |

Windows 가 8034–8133 포트를 예약해 둔 PC 에서는 8080·8090 을 쓸 수 없다.

## 5. 보고서

```powershell
& $wb report <run> --meta report_meta.json [--findings findings.json]   # 체크리스트 실행
& $wb report <run> --meta report_meta.json                              # 자율 탐색 전용 실행(같은 서식 재사용)
```

체크리스트 없는 실행도 새 서식을 만들지 않고 같은 회사 템플릿을 그대로 쓴다 — 발견 사항의 출처만 `findings` 테이블로 바뀐다. `report_meta.json`/`findings.json` 형식은 `src/audit/schemas.py`의 `REPORT_META`/`FINDINGS`. 보고서·감사 로그의 비밀 값 패턴은 마스킹된다.

## 6. 웹 UI (선택)

findings/runs 중심의 로컬 대시보드. **새 진단 로직은 없다** — `src/webapi`(FastAPI)가 기존 `diagnostic_workbench` 모듈을 그대로 부르는 HTTP 어댑터이고, `web/`(Next.js)이 그 위에 얹힌다. CLI 는 이것 없이도 완전히 동작한다.

```powershell
pip install -e '.[web]'
& $wb web --port 8420                   # 백엔드
cd web && npm install && npm run dev    # 프론트엔드(기본 :3000)
```

둘을 한 번에 띄우려면: `.\scripts\dev.ps1`. 인증은 `.env` 의 환경 변수(`WORKBENCH_WEB_USER`/`PASSWORD`/`SECRET`)로만 받는 단일 운영자 로그인이며, 값이 없으면 서버가 시작을 거부한다.

## 7. 구조

| 위치 | 책임 |
|---|---|
| `migrations/*.sql`, `src/diagnostic_workbench/db.py` | 스키마, 마이그레이션 실행기, 감사·변경 이벤트 |
| `src/diagnostic_workbench/config.py`, `runs.py` | 설정 로딩, 실행 생성, 자산 등록, 도구 레지스트리 |
| `src/diagnostic_workbench/policy.py`, `src/audit/policy.py` | `ALLOW / ALLOW_WITH_APPROVAL / DENY / NEEDS_SCOPE_CONFIRMATION` 판정(범위·제외·DNS·중지·승인·도착 주소) |
| `planner.py`, `backlog.py`, `coordinator.py` | 변경 기반 계획, 상태 전이와 `SKIP LOCKED` 점유, 실행 반복·중지·취소 |
| `worker.py`, `evidence.py`, `tools.py` | 도구 호출 → 증적 → 규칙 판정 → 발견 사항, 자산 관찰 |
| `explore.py`, `traces.py` | LLM 탐색 제안과 검증, 탐색 그래프, Activity 검색 |
| `reporting.py` | DB 기록을 보고서 모듈 형식으로 넘기는 어댑터 |
| `src/audit/` | 공용 라이브러리: 스키마 검증, 범위 정책, 체크리스트 로더, 규칙 판정, MCP/Ollama 클라이언트, Word/Excel 보고서 |
| `src/webapi/`, `web/` | 로컬 웹 UI(§6). 새 진단 로직 없이 위 모듈을 그대로 부르는 어댑터 |

판정 값: `PASS, FAIL, NOT_APPLICABLE, NEEDS_REVIEW, INCONCLUSIVE, NOT_RUN`. 작업 상태: `PENDING → READY → RUNNING → SUCCEEDED | FAILED | BLOCKED | NEEDS_REVIEW | CANCELLED`.

구현 현황과 진행 중인 작업은 git 커밋 이력과 [docs/UPGRADE_PLAN.md](docs/UPGRADE_PLAN.md)를 참고한다 — 이 README 에는 변하지 않는 사용법만 담는다.

## 8. 문제 해결

| 증상 | 조치 |
|---|---|
| `환경 변수 WORKBENCH_PG_DSN 가 없음` | `.env` 작성 |
| `scope 가 ... 에 만료됨` | `scope.json` 의 `valid_until` 갱신 |
| `모델 '...' 이(가) 설치되어 있지 않음` | `ollama pull <model>` 또는 `settings.json` 수정 |
| 포트 바인딩 실패(`access permissions`) | Windows 예약 범위(8034–8133) 밖 포트 사용 |
| `PermissionError [WinError 32]` | 보고서 파일을 Excel/Word 가 열고 있음 |
| 한글이 깨짐 | Windows Terminal 사용 또는 `chcp 65001` |

- `run start` 는 `COMPLETED` 실행도 다시 받는다 — 새 증적/사실이 생기면 다시 할 일이 생길 수 있다. `CANCELLED` 만 재개하지 않는다.
- Worker 가 죽으면 임대(`lease_s`)가 지난 뒤 READY 로 돌아가거나 시도 횟수 초과 시 FAILED 가 된다.
- 적용된 마이그레이션 파일은 수정하지 말고 새 번호의 파일을 추가한다.
