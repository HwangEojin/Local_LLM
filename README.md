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
| `config/settings.json` | Ollama 주소·모델·한도, 증적·보고서·체크리스트·템플릿 경로, 도구 호출 한도. 루프백이 아닌 모델 주소는 `external_api.enabled=false` 이면 거부. `ollama.timeout_s`(기본 120)는 모델 크기에 맞춰 늘린다 — 16GB 노트북에서 `qwen2.5:7b` 는 호출당 10~90초, `qwen2.5:14b` 는 타임아웃을 600초 정도로 늘려야 응답을 받을 수 있었다 |
| `config/mcp_servers.json` | MCP 서버와 허용 도구(`risk`, `scope_args`, `args_schema`). 이 PC 의 경로는 `mcp_servers.local.json` 에 같은 서버 이름으로 적는다 |
| `config/workbench.json` (선택) | 제외 대상 `exclude`, 예산 `budget`, `lease_s`, `min_call_interval_ms`, `nmap_path`, `testssl_path` (`workbench.example.json` 참고) |

**testssl.sh (WEB-SER-037 재협상 오버레이용, 선택):** `git clone --branch 3.2 --depth 1 https://github.com/drwetter/testssl.sh.git data/tools/testssl.sh`(저장소에는 안 올라감, `/data/` 가 `.gitignore` 대상). Git-for-Windows 의 MSYS 에는 `hexdump`/`pkill` 이 없어 `data/tools/hexdump-shim/`(이 저장소에 포함, testssl.sh 가 실제로 쓰는 호출 형태만 정확히 구현)을 testssl.sh 실행 시 PATH 맨 앞에 둔다 — `tools.py` 의 `tls.renegotiation` 도구가 자동으로 해 준다. `config/workbench.json` 에 `testssl_path`를 설정해야 도구가 등록된다.

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
& $wb checklist overlays                              # config/rule_overlays.json 교차검증
& $wb checklist reapply-overlays fsec-2026-1-server 2026-1   # 이미 DB에 import된 버전에 오버레이 소급 적용
```

- 원본 xlsx 의 시트마다 체크리스트 하나(`fsec-2026-1-<시트>`)가 `draft` 로 만들어진다. 원문은 요약·수정하지 않고 `source_reference`(파일/시트/행)와 원본 SHA-256 을 남긴다.
- 활성화하면 해시가 고정된다. 이후 파일이 바뀌면 실행과 보고서가 거부된다. 새 내용은 새 버전으로 만든다.
- 원본에 판단기준이 없는 항목은 `incomplete` 이고 기준을 지어내지 않는다. 현재 공식 12종은 자동 수집 도구가 지정되지 않아 **모두 사람 검토 항목**이다.
- 판정 방법: `rule`(정규식), `manual`, `llm_assisted`(의견만 기록). platform 별 항목이 있으면 `scope.json` 대상에 `platform` 이 필요하다.

## 4. 진단 실행

```powershell
& $wb run create --targets baseline-demo --operator 이름 --llm [--max-explorations N]           # 자율 탐색만(체크리스트 없음)
& $wb run create --checklist fsec-2026-1-web-mobile-hts@2026-1 --targets baseline-demo --operator 이름 [--llm] [--check-ids A,B] [--tools a,b] [--autonomy approval|partial|full]
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
& $wb trace tag <탐색노드ID> --run <run> --check-id <check_id>   # 검증된 탐색 발견을 체크리스트에 사후 매핑(판정은 안 바꿈)
```

- `--checklist` 는 선택이다. 생략하면(`--llm` 필요) 체크리스트 항목 없이 목표(goal) 노드와 대상 자산만 등록되고, 로컬 LLM 이 그 목표와 대상만 근거로 최초 탐색(정찰)을 제안한다 — 체크리스트 증거가 하나도 없어도 탐색이 시작된다. `--checklist` 를 주면 기존처럼 체크리스트 작업이 먼저 돌고, 그 판정 결과가 생긴 뒤에만 탐색이 보조로 이어진다(동작 변경 없음).
- 실행 ID 는 앞 몇 글자만 써도 된다.
- 도구 위험도: `read` 는 범위 안이면 실행, `write`/`dangerous` 는 `scope.json` 의 `allowed_risks` 에 있어야 한다. 그 안에서 승인이 얼마나 필요한지는 실행 생성 시 정한 `--autonomy` 로 정해진다(`approval` 기본값: 매번 승인 / `partial`: write 자동, dangerous 승인 / `full`: 둘 다 자동, 범위 내). 실행 중 바꿀 수 없다. 승인이 필요할 때는 실행·대상·도구·정확한 인자에 묶이며 24시간 유효.
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
& $wb report <run> --meta report_meta.json [--findings findings.json]   # 회사 템플릿 서식의 결과보고서(기본, 체크리스트 실행)
& $wb report <run> --basic                                              # 내부 점검 요약(Word 표 + Excel, 커버리지 포함, 체크리스트 실행만)
& $wb report <run> --meta report_meta.json                              # 체크리스트 없는(자율 탐색 전용) 실행도 같은 Word 서식, --basic 은 지원 안 함
```

- 체크리스트 없는 실행도 서식은 새로 만들지 않고 **같은 회사 템플릿(`audit.pentest_report`)을 그대로 쓴다**. 다른 점은 발견 사항의 출처뿐이다: 체크리스트 실행은 FAIL 판정 + `findings.json` 을 연동하고, 체크리스트 없는 실행은 `findings` 테이블(탐색 신호가 확정한 발견 사항)을 그 서식이 기대하는 모양으로 바로 채운다. `notes` 란에 "검증: RULE_CONFIRMED/REVIEWER_CONFIRMED (사람 최종 검토 전)" 을 명시해 아직 사람이 확인·승인하지 않았음을 적는다(검토 워크플로는 아직 없음, §8b). PASS/FAIL 집계·커버리지가 없으므로 `--basic` 은 지원하지 않는다.
- 기본 보고서는 `templates/word/` 의 `웹*결과보고서*.docx` 서식을 그대로 쓰고, 보호대책은 `*웹*보안권고안*.docx` 에서 텍스트와 이미지만 가져온다. 템플릿 유형은 대상 platform 으로 정한다(현재 WEB 만 지원).
- `report_meta.json`: 고객사, 프로젝트, 대상(`target_id`, 이름, URL), 일정, 인력. 형식은 `src/audit/schemas.py` 의 `REPORT_META`.
- `findings.json`: `check_id` + `target_id` 가 DB 의 **FAIL 판정과 일치**해야 한다. 진단결과, 발생경로, 사례(CASE)와 단계(`text`/`code`/`image`/`caption`), 권고사항. 형식은 `FINDINGS`.
- FAIL 인데 findings 에 없으면 `(작성 필요)` 로 포함하고 경고한다. 취약점명이 권고안 제목과 다르면 `recommendation_titles` 로 지정한다. 찾지 못한 보호대책은 지어내지 않는다.
- 생성 후 파일을 다시 열어 취약점 누락을 확인한다. 기존 파일은 덮어쓰지 않는다(`-2`, `-3`).
- Word 에서 `필드를 업데이트하시겠습니까?` 가 나오면 `예`(목차·번호 갱신). 화면상의 모양은 직접 확인해야 한다.
- 보고서와 감사 로그에서 비밀 값 패턴은 마스킹된다. 원본 증적은 `data/evidence/` 에 그대로 남는다.

## 6. 구조

| 위치 | 책임 |
|---|---|
| `migrations/*.sql`, `db.py` | 스키마, 마이그레이션 실행기(해시 고정), 감사·변경 이벤트 |
| `config.py`, `runs.py` | 설정 로딩, 실행 생성(체크리스트는 선택), 체크리스트 버전을 DB 로 가져오기(있을 때만), 자산 등록, 실행 컨텍스트, 도구 레지스트리 |
| `policy.py` | `ALLOW / ALLOW_WITH_APPROVAL / DENY / NEEDS_SCOPE_CONFIRMATION` 판정(범위·제외·DNS·중지·승인·도착 주소) |
| `planner.py`, `backlog.py`, `coordinator.py` | 변경 기반 계획, 상태 전이와 `SKIP LOCKED` 점유, 실행 반복·중지·취소. 체크리스트 없는 실행은 매 주기 탐색을 시도(부트스트랩) |
| `worker.py`, `evidence.py`, `tools.py` | 도구 호출 → 증적 → 규칙 판정 → 발견 사항, 단계별 Activity, 자산 관찰, Nmap |
| `explore.py`, `traces.py` | LLM 탐색 제안과 검증, 탐색 그래프, Activity 검색, 체크리스트 사후 매핑(`tag_node`). 체크리스트 증거가 없는 실행은 목표(goal)·대상 자산만 근거로 최초 탐색을 제안(bootstrap) |
| `reporting.py` | DB 기록을 보고서 모듈 형식으로 넘기는 어댑터, 커버리지. 체크리스트 없는 실행은 `generate_autonomous`가 `findings` 테이블을 `audit.pentest_report._Builder` 가 기대하는 모양으로 바꿔 **같은 Word 서식**에 채운다(새 서식을 만들지 않음) |
| `src/audit/` | 공용 라이브러리: 스키마 검증, 범위 정책, 체크리스트 로더·가져오기, 규칙 판정, 오버레이, 탐색 발견 검증 신호(`signals.py`), MCP 클라이언트, Ollama 클라이언트, Word/Excel 보고서 |

판정 값: `PASS, FAIL, NOT_APPLICABLE, NEEDS_REVIEW, INCONCLUSIVE, NOT_RUN`. 작업 상태: `PENDING → READY → RUNNING → SUCCEEDED | FAILED | BLOCKED | NEEDS_REVIEW | CANCELLED`.

## 7. 구현 상태 (2026-10-19)

| 기능 | 상태 | 근거 및 제한 사항 |
|---|---|---|
| 설정 검증 | 구현 | `doctor` 가 DSN 누락·미적용 마이그레이션을 FAIL 로 표시 |
| PostgreSQL 스키마·마이그레이션 | 구현 | 전용 DB 에 5개 적용(0005: `runs.checklist_version_id` nullable), 추가 전용 트리거가 증적 삭제를 거부 |
| **체크리스트 없는 자율 진단** | **구현** | CLAUDE.md §3.2/§5.1/§6 — 체크리스트는 필수 조건이 아니라 선택적으로 곁들이는 기능으로 분리. `runs.create_run`이 `checklist_id`를 선택 인자로 받아 없으면 checklist import·platform 검증을 전부 생략하고 goal 노드(`"자율 진단: <targets>"`)와 자산만 등록(`--llm` 없으면 `ConfigError`로 거부). `explore.propose`(`explore.py`)가 체크리스트 판정/사실이 전혀 없어도(`known` 이 비어 있어도) GOAL·TARGETS 만 근거로 1차 탐색을 제안하는 bootstrap 경로 추가(증적 인용 요구를 이 경우만 생략). `planner.plan`은 체크리스트 없는 실행에서 `verdict/fact/evidence/approval` 이벤트 유무와 무관하게 매 주기 탐색을 시도(체크리스트 있는 실행의 기존 게이트는 그대로 유지, 회귀 없음). CLI: `run create --targets .. --operator .. --llm`(체크리스트 생략), `run status` 의 체크리스트 표시 NoneType 버그도 실제로 CLI 를 돌려보다 발견해 수정. 단위테스트 3건(`tests/unit/test_autonomous_run.py`, 실제 PostgreSQL) + 기존 91건 전부 PASS. **실제 로컬 LLM(qwen2.5:7b)+실제 MCP(Burp)로 end-to-end 확인**: 체크리스트 없이 `baseline-demo` 대상 실행을 만들어 `run start` → LLM 이 사전 증거 없이 Burp 프록시 히스토리 수집을 1차 탐색으로 제안 → 정책 통과 → 실제 실행 → 증적 1건 생성 → 실행 `COMPLETED`까지 확인. 체크리스트 사후 부착(기존 run 에 나중에 체크리스트를 붙이는 기능)은 아직 미구현 |
| **체크리스트 없는 실행의 보고서** | 구현 | `reporting.generate_autonomous` — **새 보고서 서식을 만들지 않고 `audit.pentest_report` 의 Word 조립 로직(`_Builder`/`fill_overview`/`fill_summary`/`fill_improvements`/`fill_details`/`fill_protection`/`verify`)을 그대로 재사용**한다. 바뀐 건 발견 사항의 출처뿐: 체크리스트의 `collect_findings`(FAIL 판정 + findings.json 매칭) 대신 `_findings_for_pentest_report`가 `findings` 테이블을 같은 모양(`check_id`/`target_id`/`name`/`result`/`path`/`notes`/`cases`/`recommendation`/`rec_titles`)으로 변환해 넘긴다. `notes` 란에 검증 방식과 "사람 최종 검토 전"을 적어 RULE_CONFIRMED 도 확정이 아님을 보고서에 명시(CLAUDE.md §9/§10). `--meta` 는 체크리스트 실행과 동일하게 필요하고, PASS/FAIL 집계가 없어 `--basic` 은 거부. 실제 end-to-end 확인: `findings` 테이블에 행을 넣고 `report --meta ..` 실행 → 회사 템플릿 docx 생성 → 다시 열어 발견 사항 제목이 본문에 있는지(`pentest_report.verify`) 확인(문제 0건). 단위테스트 1건(`test_report_reuses_pentest_report_format_with_db_findings`, 실제 PostgreSQL + 실제 템플릿). **미구현**: 사람이 finding 을 확정/기각하는 리뷰 워크플로 자체가 없어 "검토 전" 상태를 벗어날 방법이 아직 없음(§9 FAIL, 다음 과제) |
| 범위·정책 강제 | 부분 구현 | 범위 밖·경로 이탈·제외 대상·DNS·중지 스위치·허용 도구·승인·호출 간격 확인. 3단계 자율성 스위치(`approval`/`partial`/`full`)가 `scope.json` 의 `allowed_risks` 범위 안에서만 승인을 건너뛰는 것을 write(nmap)·dangerous(frida) 도구로 확인. 리다이렉트 이탈은 Playwright 출력만 검사하며 페이지의 하위 요청은 검사하지 않음 |
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
| 이중 그래프·anchor 연결 | 구현 | 탐색 그래프 다단계 체인(`goal→intent→fact→(derived_from)→intent→fact`) 을 가짜 LLM 응답(결정론적)과 실제 LLM 양쪽으로 확인. 처음엔 모델이 사실의 내용은 올바르게 서술하면서도 `based_on_evidence_ids` 에 옛 증적만 인용했다 — 프롬프트에서 최신 사실(LATEST_FACT)을 맨 앞에 강조하고 "같은 대상을 다루는 제안은 최신 사실을 인용해야 함"을 Python 검증 규칙으로 추가(다른 대상으로의 병렬 탐색은 그대로 허용)한 뒤 실제 체인이 생성됨 확인. qwen2.5:7b-instruct 로 1단계 체인, qwen2.5:14b(`ollama.timeout_s` 를 늘려서 확인)로 **거부 없이 깊이 5단계까지** 이어지는 연속 체인을 확인(Burp MCP 실연동). 자산은 범위 선언 + 관찰(`fs.list_dir`, Playwright, Nmap)만. Burp 이력 기반 자산 추출 없음 |
| 공유 TodoList·선행 조건 | 부분 구현 | 멱등 키와 선행 조건 대기 확인. 선행 관계를 자동으로 만드는 규칙 없음 |
| 체크리스트·탐색 도구 호출 예산 분리 | 구현 | `budget.max_tool_calls_exploration`(신규, 기본 50)을 `max_tool_calls`(체크리스트용, 기본 500)와 완전히 분리. `worker._tool_calls_used(ctx, kind)`가 `tool_invocations`를 `backlog_items.kind`로 나눠 세고, `run_tool()`이 그 작업의 kind 에 맞는 예산만 본다 — 탐색이 전부 써도 체크리스트 쪽 카운트·한도는 그대로. `run status` CLI 도 두 예산을 따로 표시. 실제 PostgreSQL 로 두 kind 의 집계가 독립적임을 확인(`tests/unit/test_budget_separation.py`, 2건 PASS) |
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
| 7 | 모바일 Word 템플릿, 리다이렉트 하위 요청 범위 검사, Burp 이력 기반 자산 추출 | 계획 — §7 의 기존 제한 사항 |
| 8a | 체크리스트 없는 실행을 위한 보고서 생성 | **구현(§7)** — 새 서식 없이 `audit.pentest_report` 를 그대로 재사용, `findings` 테이블만 그 모양으로 변환해 채움("사람 검토 전" 명시) |
| 8b | 기존 실행에 체크리스트를 나중에 붙이는 기능 | 계획 — 아직 미착수 |

3(자동 점검 A)은 오버레이 데이터 6/6건이 전부 등록돼 WEB 플랫폼의 config-check형 항목은 더 추가할 게 없다 — 남은 건 §6 HTS 등으로의 확장(§7A는 platform·checklist_id 를 하드코딩하지 않으므로 데이터만 추가하면 됨, 이번에는 보류). 4(자동 점검 B)는 §7B 가 이름 붙인 7종 신호·SQLi 차등 검증·실제 MCP 연동까지 전부 끝나 "구현"으로 올렸다. 5(예산 분리)도 완료. 0a(체크리스트 분리·자율 탐색 bootstrap)와 8a(자율 탐색 전용 보고서)도 완료. 남은 우선순위는 8b(체크리스트 사후 부착) → 6(HTS 확장, 보류 중) → 7(모바일 템플릿 등 기존 제한 사항).

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
