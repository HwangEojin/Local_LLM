# 로컬 LLM 보안 진단 워크벤치

체크리스트 중심의 보안 진단 도구. PostgreSQL 작업 큐 위에서 실행하고, 판정은 **등록된 도구로 수집한 증적 + 결정론적 규칙 + 사람 검토**로 내린다. 로컬 LLM(Ollama)은 참고 의견과 후속 확인 제안만 낸다. 설계 원칙은 [CLAUDE.md](CLAUDE.md).

## 1. 준비

| 항목 | 버전 |
|---|---|
| Python | 3.12 이상 (검증 3.13.5) |
| PostgreSQL | 13 이상 (검증 18.6) |
| Ollama | 0.5.0 이상, 구조화 출력 필요 (검증 0.35.1, `qwen2.5:7b-instruct`) |
| 선택 | Nmap, MCP 서버(Burp, Playwright, Frida, Ghidra, JADX) |

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
| `config/settings.json` | Ollama 주소·모델·한도, 증적·보고서·체크리스트·템플릿 경로, 도구 호출 한도. 루프백이 아닌 모델 주소는 `external_api.enabled=false` 이면 거부 |
| `config/mcp_servers.json` | MCP 서버와 허용 도구(`risk`, `scope_args`, `args_schema`). 이 PC 의 경로는 `mcp_servers.local.json` 에 같은 서버 이름으로 적는다 |
| `config/workbench.json` (선택) | 제외 대상 `exclude`, 예산 `budget`, `lease_s`, `min_call_interval_ms`, `nmap_path` (`workbench.example.json` 참고) |

```powershell
$wb = ".venv\Scripts\python", "-m", "diagnostic_workbench"
& $wb migrate      # 스키마 적용
& $wb doctor       # 설정·DB·범위·모델 점검 (--probe-mcp 로 MCP 연결 확인)
& $wb tools        # 등록된 도구와 위험도
```

## 3. 체크리스트

```powershell
& $wb checklist import "D:\가이드\평가기준(제2026-1호).xlsx" 2026-1 --pdf "D:\가이드\주통 가이드.pdf"
& $wb checklist validate
& $wb checklist activate fsec-2026-1-server 2026-1
```

- 원본 xlsx 의 시트마다 체크리스트 하나(`fsec-2026-1-<시트>`)가 `draft` 로 만들어진다. 원문은 요약·수정하지 않고 `source_reference`(파일/시트/행)와 원본 SHA-256 을 남긴다.
- 활성화하면 해시가 고정된다. 이후 파일이 바뀌면 실행과 보고서가 거부된다. 새 내용은 새 버전으로 만든다.
- 원본에 판단기준이 없는 항목은 `incomplete` 이고 기준을 지어내지 않는다. 현재 공식 12종은 자동 수집 도구가 지정되지 않아 **모두 사람 검토 항목**이다.
- 판정 방법: `rule`(정규식), `manual`, `llm_assisted`(의견만 기록). platform 별 항목이 있으면 `scope.json` 대상에 `platform` 이 필요하다.

## 4. 진단 실행

```powershell
& $wb run create --checklist fsec-2026-1-web-mobile-hts@2026-1 --targets baseline-demo --operator 이름 [--llm] [--check-ids A,B] [--tools a,b]
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
& $wb evidence <run>       # 증적 목록 + 파일 해시 대조
& $wb graph <run>          # 자산 그래프와 탐색 그래프
& $wb trace search <검색어> [--tool ..] [--status FAILED]
```

- 실행 ID 는 앞 몇 글자만 써도 된다.
- 도구 위험도: `read` 는 범위 안이면 실행, `write`/`dangerous` 는 `scope.json` 의 `allowed_risks` 에 있고 호출마다 승인돼야 실행된다. 승인은 실행·대상·도구·정확한 인자에 묶이며 24시간 유효.
- `PASS`/`FAIL` 은 연결된 증적이 있어야 기록할 수 있다.
- 전체 중지: `data/STOP` 파일을 만들면 모든 도구 호출이 거부되고 실행은 PAUSED 가 된다.
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
& $wb report <run> --meta report_meta.json [--findings findings.json]   # 회사 템플릿 서식의 결과보고서(기본)
& $wb report <run> --basic                                              # 내부 점검 요약(Word 표 + Excel, 커버리지 포함)
```

- 기본 보고서는 `templates/word/` 의 `웹*결과보고서*.docx` 서식을 그대로 쓰고, 보호대책은 `*웹*보안권고안*.docx` 에서 텍스트와 이미지만 가져온다. 템플릿 유형은 대상 platform 으로 정한다(현재 WEB 만 지원).
- `report_meta.json`: 고객사, 프로젝트, 대상(`target_id`, 이름, URL), 일정, 인력. 예시 `examples/report/report_meta.sample.json`, 형식 `src/audit/schemas.py` 의 `REPORT_META`.
- `findings.json`: `check_id` + `target_id` 가 DB 의 **FAIL 판정과 일치**해야 한다. 진단결과, 발생경로, 사례(CASE)와 단계(`text`/`code`/`image`/`caption`), 권고사항. 형식은 `FINDINGS`.
- FAIL 인데 findings 에 없으면 `(작성 필요)` 로 포함하고 경고한다. 취약점명이 권고안 제목과 다르면 `recommendation_titles` 로 지정한다. 찾지 못한 보호대책은 지어내지 않는다.
- 생성 후 파일을 다시 열어 취약점 누락을 확인한다. 기존 파일은 덮어쓰지 않는다(`-2`, `-3`).
- Word 에서 `필드를 업데이트하시겠습니까?` 가 나오면 `예`(목차·번호 갱신). 화면상의 모양은 직접 확인해야 한다.
- 보고서와 감사 로그에서 비밀 값 패턴은 마스킹된다. 원본 증적은 `data/evidence/` 에 그대로 남는다.

## 6. 구조

| 위치 | 책임 |
|---|---|
| `migrations/*.sql`, `db.py` | 스키마, 마이그레이션 실행기(해시 고정), 감사·변경 이벤트 |
| `config.py`, `runs.py` | 설정 로딩, 체크리스트 버전을 DB 로 가져오기, 자산 등록, 실행 컨텍스트, 도구 레지스트리 |
| `policy.py` | `ALLOW / ALLOW_WITH_APPROVAL / DENY / NEEDS_SCOPE_CONFIRMATION` 판정(범위·제외·DNS·중지·승인·도착 주소) |
| `planner.py`, `backlog.py`, `coordinator.py` | 변경 기반 계획, 상태 전이와 `SKIP LOCKED` 점유, 실행 반복·중지·취소 |
| `worker.py`, `evidence.py`, `tools.py` | 도구 호출 → 증적 → 규칙 판정 → 발견 사항, 단계별 Activity, 자산 관찰, Nmap |
| `explore.py`, `traces.py` | LLM 탐색 제안과 검증, 탐색 그래프, Activity 검색 |
| `reporting.py` | DB 기록을 보고서 모듈 형식으로 넘기는 어댑터, 커버리지 |
| `src/audit/` | 공용 라이브러리: 스키마 검증, 범위 정책, 체크리스트 로더·가져오기, 규칙 판정, MCP 클라이언트, Ollama 클라이언트, Word/Excel 보고서 |

판정 값: `PASS, FAIL, NOT_APPLICABLE, NEEDS_REVIEW, INCONCLUSIVE, NOT_RUN`. 작업 상태: `PENDING → READY → RUNNING → SUCCEEDED | FAILED | BLOCKED | NEEDS_REVIEW | CANCELLED`.

## 7. 구현 상태 (2026-10-13)

| 기능 | 상태 | 근거 및 제한 사항 |
|---|---|---|
| 설정 검증 | 구현 | `doctor` 가 DSN 누락·미적용 마이그레이션을 FAIL 로 표시 |
| PostgreSQL 스키마·마이그레이션 | 구현 | 전용 DB 에 3개 적용, 추가 전용 트리거가 증적 삭제를 거부 |
| 범위·정책 강제 | 부분 구현 | 범위 밖·경로 이탈·제외 대상·DNS·중지 스위치·허용 도구·승인·호출 간격 확인. 리다이렉트 이탈은 Playwright 출력만 검사하며 페이지의 하위 요청은 검사하지 않음 |
| 체크리스트 가져오기 | 구현 | 활성·해시 고정 버전만 DB 로 가져오고 원문 보존 |
| 결정론적 체크리스트 점검 | 구현 | 예시 체크리스트로 5가지 판정 확인. 공식 12종은 전 항목 사람 검토 |
| 로컬 LLM 연동 | 구현 | 분석·탐색 제안이 스키마 검증 통과. 16GB 노트북에서 호출당 10~90초 |
| 작업 큐·Worker 조정 | 구현 | 동시 실행 중복 0건, 임대 만료 복구, 재개·취소 확인. Worker 는 프로세스당 1개 |
| MCP 어댑터 | 부분 구현 | Playwright·Frida·Burp 요청 전송은 실제 출력 확인. Burp 프록시 이력 파싱, Ghidra, JADX 는 `CONNECTED_UNVERIFIED` |
| Nmap | 구현 | 승인·범위·인자 거부와 실제 스캔, 서비스 자산 등록 확인. NSE·OS 탐지·UDP 미지원 |
| 증적 저장 | 구현 | 해시 대조 통과, 스크린샷은 원본과 연결된 파생 증적. 마스킹 파생본 없음 |
| Word 보고서 템플릿 | 부분 구현 | 웹 결과보고서를 실제 대상 실행으로 생성 확인. 모바일 템플릿 미지원 |
| Excel 보고서 템플릿 | 부분 구현 | 요약 + 커버리지 시트 확인. 사용자 지정 Excel 템플릿은 미확인 |
| 탐색·재계획 반복 | 구현 | 제안 → 정책 검증 → 실행 → 사실 기록 → 재계획 → 예산 거부 확인. 읽기 전용 도구만 |
| 이중 그래프·anchor 연결 | 부분 구현 | 탐색 그래프 동작. 자산은 범위 선언 + 관찰(`fs.list_dir`, Playwright, Nmap)만. Burp 이력 기반 추출 없음 |
| 공유 TodoList·선행 조건 | 부분 구현 | 멱등 키와 선행 조건 대기 확인. 선행 관계를 자동으로 만드는 규칙 없음 |
| Worker Activity 검색·재사용 | 부분 구현 | `trace` 동작, 실패 이력을 탐색 프롬프트에 참고로 제공. 대량 데이터 성능 미확인 |

도커 데모 대상(`baseline-demo`, `localhost:9999`)으로 실행 생성 → Nmap·Playwright·Burp 증적 → 검토 판정 → 결과보고서까지 확인했다. 그 밖의 확인은 임시 PostgreSQL 과 예시 체크리스트로 했다.

## 8. 테스트와 복구

```powershell
.venv\Scripts\python -m unittest discover -s tests -t .
$env:WORKBENCH_TEST_PG_DSN = "postgresql://user@127.0.0.1:5432/workbench_test"   # 워크벤치 흐름 테스트(전용 DB 필요)
```

`tests/fixtures/legacy_run/` 은 보고서·필터 테스트에 넣을 실행 결과를 만드는 테스트 전용 SQLite 실행기다(제품 코드 아님).

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
