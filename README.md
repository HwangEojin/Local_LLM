# Local Audit Workstation

로컬 LLM(Ollama)을 보조로 사용하는 모듈형 보안 취약점 진단 워크스테이션.
판정은 **등록된 도구로 수집한 증거 + 결정론적 규칙**으로 내리고, LLM은 참고 의견만 낸다.
설계 원칙과 요구사항은 [CLAUDE.md](CLAUDE.md) 참고.

> 공식 평가기준(제2026-1호) xlsx 에서 변환한 체크리스트 12개를 사용한다(§5.1). 엔진 시험용 예시 체크리스트는
> `tests/fixtures/` 에만 있으며 실제 진단에 쓰지 않는다.

## 0. 저장소에 포함되지 않는 것

공개 저장소라서 다음은 올리지 않는다. 각자 준비한다.

| 항목 | 이유 | 준비 방법 |
|---|---|---|
| 공식 평가기준 xlsx/pdf 와 변환된 체크리스트(`checklist/manifest.json`, `checklist/versions/`, `checklist/sources/`) | 공식 문서에서 파생된 내용 | `python -m audit checklist import <xlsx> 2026-1 --pdf <pdf>` (§5.1) |
| 결과보고서 템플릿과 보안권고안 `templates/word/*.docx` | 회사 문서 | `templates/word/` 에 넣는다 (§7.1) |
| `config/mcp_servers.local.json` | 이 PC 의 경로 | §3.2 |
| `data/`, `reports/`, `.venv/` | 실행 결과, 증거, 가상환경 | 실행하면 생성 |

체크리스트나 템플릿이 없어도 테스트는 통과하며, 해당 자산이 필요한 테스트(`test_pentest_report`, `test_playwright_live` 등)는 건너뛴다.

## 1. 요구사항

| 항목 | 버전 | 비고 |
|---|---|---|
| Windows 10/11 | — | 개발·검증 환경: Windows 11 Pro |
| Python | 3.12 이상 | 검증: 3.13.5 |
| Ollama | 0.5.0 이상 | JSON-schema 구조화 출력 필요. 검증: 0.35.0 |
| 외부 패키지 | `mcp==1.27.0`, `python-docx==1.2.0`, `openpyxl==3.1.5` | `pyproject.toml`에 고정, 전이 의존성은 `requirements.lock` |

## 2. 설치 (PowerShell)

```powershell
cd C:\path\to\local-audit
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.lock
.venv\Scripts\python -m pip install --no-deps -e .
.venv\Scripts\python -m audit doctor
```

`Activate.ps1`이 실행 정책 때문에 막히면 활성화 없이 위처럼 `.venv\Scripts\python`을 직접 호출하면 된다(실행 정책 변경 불필요).

## 3. 설정

모든 경로는 프로젝트 루트 기준(또는 `--home` / 환경변수 `AUDIT_HOME`). 설정 오류는 실행 거부(fail-closed).

### `config/settings.json`
- `ollama.base_url` — 기본 `http://127.0.0.1:11434`. 루프백이 아닌 주소는 `external_api.enabled=false`이면 **거부**된다.
- `ollama.model`, `timeout_s`, `max_request_bytes`, `max_response_bytes`, `max_calls_per_run`, `max_retries`(출력 스키마 검증 실패 시 재시도 횟수).
- `limits.max_tool_calls_per_run`, `run_timeout_s`, `max_evidence_bytes`(초과 시 증거에 truncated 표시 → "부재" 판정 불가 → 진단불가).

### `config/scope.json` — 승인된 범위
```json
{
  "engagement_id": "ENG-2026-001",
  "approved_by": "승인자 이름",
  "valid_until": "2026-12-31",
  "allowed_risks": ["read"],
  "targets": [
    {"target_id": "web01-src", "type": "path", "value": "D:/audit/web01"},
    {"target_id": "web01",     "type": "host", "value": "web01.internal"},
    {"target_id": "web01-api", "type": "url",  "value": "https://web01.internal/api/"}
  ]
}
```
- 유효기간이 지나면 모든 명령이 거부된다.
- `allowed_risks`에 없는 위험도의 도구는 승인해도 실행되지 않는다. `write`/`dangerous`는 추가로 **실행 시 TTY에서 사람이 `y`로 승인**해야 한다(비대화형이면 자동 거부).
- 경로 인자는 심볼릭 링크/정션을 해석한 뒤 대상 루트 안인지 검사한다.

### `config/mcp_servers.json`
서버별 `enabled`, `transport`(`stdio`|`sse`), `command`/`args` 또는 `url`, `timeout_s`, `allowed_tools`.
```json
"allowed_tools": {
  "send_http1_request": {"risk": "write", "scope_args": {"targetHostname": "host"},
                          "args_schema": {"type": "object"}}
}
```
- 허용 목록에 없는 도구는 레지스트리에 등록되지 않아 호출할 수 없다. 체크리스트에서는 `mcp:<server>/<tool>`로 참조한다.
- `scope_args`로 지정한 인자는 scope 검사(`path`/`host`/`url`)를 거친다.
- 인자는 설정의 `args_schema`와 **서버가 광고한 inputSchema** 둘 다로 검증한다. 지원하지 않는 스키마 키워드(`$ref` 등)는 거부.
- 비밀값은 설정 파일에 쓰지 않는다. `"env_from": {"API_TOKEN": "MY_HOST_ENV_VAR"}`로 호스트 환경변수에서 주입.
- **이 PC 전용 설정**: 저장소의 `config/mcp_servers.json` 은 경로가 비어 있는 템플릿(Burp 만 활성)이다. 이 PC 의 실제 경로는 `config/mcp_servers.local.json` 에 같은 서버 이름으로 적는다 (Git 에 올라가지 않으며, 서버 항목을 통째로 교체하고 새 서버도 추가할 수 있다).
- 연결 상태와 서버별 준비 사항은 §3.2 참고. 새 서버를 추가할 때는 `mcp list --probe` 로 제공 도구를 확인하고 허용할 도구만 `allowed_tools` 에 넣는다.

연결 확인: `python -m audit mcp list --probe` 또는 `python -m audit doctor --probe-mcp`

### 3.1 Burp Suite 연동 (웹 모의해킹)

Burp Suite 의 PortSwigger `MCP Server` 확장을 MCP 서버로 연결한다. 프록시 이력, 조직자 항목, 스캐너 이슈를 읽어 **수동확인 항목의 증거**로 저장한다.

```powershell
.venv\Scripts\python -m audit mcp list --probe        # 연결 확인 (예: 연결됨: burp-suite/1.1.2; 서버 제공 도구 27개)
.venv\Scripts\python -m audit evidence add <run_id> WEB-SER-001 web01 mcp:burp/get_scanner_issues --args '{"count": 20, "offset": 0}'
.venv\Scripts\python -m audit evidence add <run_id> WEB-SER-001 web01 mcp:burp/get_proxy_http_history --args '{"count": 50, "offset": 0}'
.venv\Scripts\python -m audit evidence list <run_id>
```

- **주소**: `config/mcp_servers.json` 의 `burp.url` 은 `http://127.0.0.1:9876/` 이다(이 확장은 `/sse` 가 아니라 루트 경로가 SSE 엔드포인트). 포트는 Burp 확장 설정에서 확인한다.
- **승인**: 프록시 이력 등은 Burp 화면에 뜨는 **사용자 승인 창**에서 `Approve` 해야 응답한다. 사람이 승인할 시간을 주려고 `timeout_s` 를 180초로 두었다. 승인은 우회하지 않는다.
- **진단 범위 필터**: Burp 이력에는 작업과 무관한 다른 사이트 트래픽도 섞일 수 있다. 증거로 저장하기 전에 각 항목의 **요청 호스트**가 대상(scope 의 `host`/`url`)의 호스트와 같은 항목만 남기고, 범위 밖이거나 호스트를 알 수 없는 항목은 **저장하지 않는다**(fail-closed). 제외 건수는 명령 출력과 감사 로그에 남는다. 출력이 `max_evidence_bytes` 를 넘어 잘리면 필터를 적용할 수 없어 저장하지 않고 실패로 기록한다(`count` 를 줄일 것).
- **정책**: 허용 목록에 있는 읽기 도구만 쓴다(`get_proxy_http_history`, `get_proxy_http_history_regex`, `get_organizer_items`, `get_scanner_issues`). `count` 는 1~100 으로 제한한다. `send_http1_request` 같은 쓰기 도구는 위험도 `write` 라서 scope 의 `allowed_risks` 에 `write` 를 넣고 터미널에서 승인해야만 실행되며, 기본 설정에서는 거부된다.
- 증거는 판정을 바꾸지 않는다. 결과의 증거 목록에 연결되고(SHA-256 기록), 판정은 `review` 로 사람이 기록한다.
- 한계: 실제 Burp 에서 **트래픽 항목의 출력 형식은 아직 확인하지 못했다**(연결 시 Burp 에 이력/이슈가 없어 `Reached end` 만 반환됨). 호스트 판별은 JSON 항목, 연속 JSON, 텍스트 요청(`Host:` 헤더)을 모두 시도한다. 첫 사용 때 `범위 필터: 수신 N건 → 저장 M건` 을 확인하고, `저장 0건` 이면서 `호스트 불명 제외` 가 많으면 형식 불일치 경고가 출력되니 `src/audit/adapters/burp.py` 의 판별 규칙을 맞춘다.

### 3.2 연결된 MCP 도구 (Burp / Ghidra / Frida / JADX / Playwright)

`python -m audit mcp list --probe` 로 연결을 확인한다. 각 서버는 실제로 연결해 제공 도구를 읽어 허용 목록을 만들었고,
이름 규칙에 없는 도구는 허용하지 않는다(fail-closed).

| 서버 | 연결 | 허용 도구 (위험도) | 사용 전 준비 |
|---|---|---|---|
| Burp | SSE `http://127.0.0.1:9876/` | 읽기 4 (+ `send_http1_request` write) | Burp 실행 + MCP Server 확장, 이력은 Burp 화면 승인 |
| Ghidra | stdio (`bridge_mcp_ghidra.py`) | read 19 / write 8 (`rename_*`, `set_*`) | Ghidra 에서 프로그램을 열고 GhidraMCP 플러그인(8091) 켜기 |
| Frida | stdio (`frida-mcp`), **persistent** | read 6 / **dangerous 7** (attach, spawn, hook, 실행, 종료) | frida-server/장치 준비 |
| JADX | stdio (`uv run jadx_mcp_server.py`) | read 26 / write 6 (`rename_*`, `clear_cache`) | JADX-GUI 에 APK 를 열고 AI MCP 플러그인(8650) 켜기 |
| Playwright | stdio (`npx @playwright/mcp@0.0.83`), **persistent** | read 11 (navigate/tabs 는 url 을 scope 로 검사) / write 12 / **dangerous 2** (`browser_evaluate`, `browser_run_code_unsafe`) | `npx playwright@1.64.0-alpha-1790635538000 install chromium` |

- **위험도와 승인**: `write`/`dangerous` 도구는 scope 의 `allowed_risks` 에 해당 위험도가 있고, 터미널에서 호출마다 사람이 승인해야 실행된다. 기본 scope 는 `read` 만 허용한다. Frida 의 `execute_in_session` 은 대상 프로세스에서 JavaScript 를 실행하므로 `dangerous` 다.
- **연결 유지(`persistent`)**: 한 호출이 만든 상태를 다음 호출이 써야 하는 서버(Frida 의 `session_id`, Playwright 의 브라우저 페이지)는 `persistent: true` 로 한 프로세스 안에서 연결 하나를 유지한다. 한 번의 `run` 안에서 여러 도구 호출이 같은 서버를 공유하며, 프로세스가 끝나면 서버를 종료한다. 별도의 `evidence add` 명령은 서로 다른 프로세스라 상태가 이어지지 않는다.
- **오류 응답 감지(`error_patterns`)**: Ghidra/JADX 브리지는 뒤의 프로그램이 꺼져 있을 때 오류 플래그 없이 본문으로 `Request failed: …`, `{"error": …}` 를 돌려준다. 서버별 정규식에 맞으면 실패로 처리해 증거로 저장하지 않는다.
- **Playwright 주의**: 링크 클릭 등으로 범위 밖 사이트로 나가는 것은 도구 인자로 막을 수 없다. 필요하면 `playwright.args` 에 `--allowed-origins "https://대상"` 을 추가한다. 로컬 파일을 쓰는 `filename` 인자는 모든 도구에서 허용하지 않는다. 설정의 `--executable-path` 는 PC 마다 다르니 환경에 맞게 고친다.
- **scope 한계**: Ghidra/JADX/Frida 도구는 열려 있는 프로그램, 앱, 프로세스에 작용하며 도구 인자에 경로나 호스트가 없어 **어떤 대상이 열렸는지는 scope 로 검증할 수 없다**(운영자 책임). 증거는 scope 에 등록된 대상에 연결한다.

## 4. Ollama 연결

```powershell
ollama pull qwen2.5:7b-instruct     # settings.json 의 ollama.model 과 같은 이름
.venv\Scripts\python -m audit doctor   # [OK  ] ollama: v... 모델=... capabilities=[...]
```
- 실행 전 `check_ready()`가 버전(≥0.5.0), 모델 설치 여부, `/api/show` capabilities를 확인한다.
- 계획이 `use_llm: true`인데 모델을 쓸 수 없으면 **실행을 거부**한다(규칙 전용으로 몰래 전환하거나 다른 모델/외부 API로 전환하지 않음).
- 토큰은 Ollama가 보고한 `prompt_eval_count`/`eval_count`만 기록한다. 보고가 없으면 `unavailable`로 남기고 추정하지 않는다.

## 5. 체크리스트

- `checklist/manifest.json`이 버전 목록·상태(`draft|active|superseded|retired`)·SHA-256을 관리한다.
- 새 버전: `checklist/versions/<id>/<ver>.json` 작성 → manifest에 `"status": "draft"`로 추가 → 검증 후 활성화:
  ```powershell
  .venv\Scripts\python -m audit checklist validate
  .venv\Scripts\python -m audit checklist activate <checklist_id> <version>
  ```
  활성화 시 해시가 고정되고 이전 active 버전은 `superseded`(삭제 안 됨, 과거 결과 유지). 활성화 후 파일이 바뀌면 실행·보고서가 거부된다.
- 공식 원문을 확보하지 못한 항목은 `"item_status": "incomplete"` → 항상 수동확인.
- 판정 방법: `rule`(정규식, `fail_if: match|no_match`), `manual`(항상 수동확인), `llm_assisted`(LLM 의견만 기록, 항상 수동확인).
- 증거 인자에서 `{target}`은 대상 값으로 치환된다. 내장 도구: `fs.read_file`, `fs.list_dir`(읽기 전용).

### 5.1 공식 평가기준 xlsx 가져오기

공식 원본(예: `전자금융기반시설_보안_취약점_평가기준(제2026-1호).xlsx`)을 코드가 읽어 체크리스트 JSON으로 변환한다.
LLM이 런타임에 원본을 읽지 않는다(판정 재현성과 추적성 때문). 원문은 요약·수정하지 않는다.

```powershell
.venv\Scripts\python -m audit checklist import "D:\가이드\전자금융기반시설_보안_취약점_평가기준(제2026-1호).xlsx" 2026-1 --pdf "D:\가이드\2026년 주통 가이드.pdf"
.venv\Scripts\python -m audit checklist validate
.venv\Scripts\python -m audit checklist activate fsec-2026-1-server 2026-1
```

- 시트 12개(표지 제외)가 체크리스트 12개(`fsec-2026-1-<시트>`)로 변환되며 모두 `draft` 로 등록된다. 이미 있는 파일·버전은 덮어쓰지 않는다.
- 원본 xlsx/pdf 는 `checklist/sources/` 에 복사하고 SHA-256 을 체크리스트의 `sources` 에 기록한다(Git 제외). 항목마다 `source_reference` 에 `파일 / 시트 / 행` 이 남는다.
- 서버·WAS·DB·OS 가상화·컨테이너·클라우드처럼 플랫폼별 판단기준이 다른 시트는 항목을 플랫폼별(`SRV-001.LINUX`)로 펼친다.
  네트워크 장비·정보보호시스템 장비는 판단기준이 공통이라 한 항목에 `platforms` 목록으로 담는다.
- 판단기준에서 `양호`/`취약`(`미흡`)이 각각 한 구간일 때만 나눈다. 단일 서술이거나 서비스별 구간이 여러 개이면 **원문 전체를 양쪽에 그대로** 둔다(`source_data` 의 `판단기준 서식` 참고).
- 원본에 판단기준 열이 없는 시트(정보보호 관리체계, 네트워크 인프라, 웹/모바일/HTS)는 `incomplete` 이며 기준을 만들어 넣지 않는다. 상세설명·확인 자료·법령 근거·관련 주통 평가기준은 `source_data` 에 원문 그대로 보존한다.
- 자동 수집 도구가 없으므로 변환된 항목은 모두 `manual`(수동확인)이다. 보고서에는 담당자가 확인할 원문 점검 절차와 양호/취약 기준이 실린다.
- 위험도는 원본 숫자 그대로 `severity: risk-N` 으로 저장한다(등급 해석을 덧붙이지 않음).
- `--pdf` 로 주통 가이드 PDF 를 주면 항목의 `관련 주통 평가기준` 코드(U-59 등)를 PDF 쪽 번호에 연결해 `source_data` 의 `주통 가이드 위치`(예: `p.143-144 — U-59 ...`)에 기록한다. 보고서의 수동확인/진단불가 항목에도 표시된다.
  - 쪽 번호만 연결한다. PDF 의 표 서식이 항목마다 달라 조치 방법 등 본문은 신뢰성 있게 추출할 수 없어 **추출하지 않는다**. 담당자가 해당 쪽을 직접 확인한다.
  - PDF 에 없는 코드(정보보호 정책 `A-xx`, `P-xx`)는 연결하지 않는다. xlsx 의 항목명과 PDF 표기가 다르면 경고만 하고 코드 일치를 기준으로 연결한다.
  - 쪽 번호 추출에는 `pdftotext`(poppler)가 필요하다. 없으면 `--pdf` 사용 시 오류로 중단한다.
- 같은 버전이 아직 `draft` 이면 `--replace-draft` 로 다시 가져올 수 있다. 활성화된 버전은 항상 보호된다.

### 5.2 대상 platform 지정

platform별 항목이 있는 체크리스트를 실행하려면 `scope.json` 의 대상에 `platform` 을 지정해야 한다(없거나 목록에 없으면 계획 검증이 거부된다).
```json
{"target_id": "web01", "type": "host", "value": "web01.internal", "platform": "LINUX"}
```
계획에는 해당 platform 의 항목만 포함된다(다른 platform 용 항목은 `NOT_APPLICABLE` 로 쌓이지 않고 작업에서 제외).
서버 항목은 `target_types` 가 `host` 이므로 대상 `type` 도 `host` 여야 한다.

## 6. 실행 흐름

```powershell
.venv\Scripts\python -m audit plan examples\plans\sample_plan.json
.venv\Scripts\python -m audit run  examples\plans\sample_plan.json --dry-run
.venv\Scripts\python -m audit run  examples\plans\sample_plan.json
.venv\Scripts\python -m audit results <run_id>
.venv\Scripts\python -m audit review <run_id> <check_id> <target_id> PASS --reason "근거" --reviewer 홍길동
.venv\Scripts\python -m audit report <run_id>
```
- 중단(Ctrl+C)·시간 초과 시 `run --resume <run_id>`로 이어서 실행. 체크리스트나 계획이 바뀌었으면 재개를 거부한다.
- 계획의 `requires_approval` 필드는 정보용일 뿐 권한을 부여하지 않는다.
- 종료 코드: `0` 성공, `1` 실패/거부/검증 실패, `2` 사용법 오류.
- 모든 출력·오류 메시지는 한글이다. 오류는 `오류: <예외클래스>: <내용>` 형식으로 stderr 에 출력된다.

### 판정 규칙
| 상태 | 의미 | 결정 조건 |
|---|---|---|
| PASS | 양호 | 규칙 검사 PASS (+ LLM 사용 시 LLM도 PASS) |
| FAIL | 취약 | 규칙 검사 FAIL (+ LLM 사용 시 LLM도 FAIL) |
| MANUAL_REVIEW | 수동확인 | manual/llm_assisted 항목, 미완성 항목, 규칙·LLM 불일치, LLM 오류 |
| NOT_APPLICABLE | 해당없음 | 대상 유형이 `target_types`에 없음 |
| INCONCLUSIVE | 진단불가 | 증거 수집 실패·정책 거부·잘린 증거로 부재 판단 불가 |

## 7. 보고서

`reports/<run_id>/report_<run_id>.docx|.xlsx` + `verification.json`. 기존 파일은 덮어쓰지 않는다(`-2`, `-3` 접미사).
- Word: 개요, 대상·범위, 방법, 종합 결과, 항목별 판정, 증거·근거, 조치 권고, 제한 사항, 부록(증거 목록·판정 변경 이력).
- Excel 시트: `요약`, `판정결과`(담당자/조치상태/재진단결과 열 포함), `체크리스트`, `증거`.
- 생성 후 두 파일을 다시 열어 항목별 판정, 5개 상태 집계, 필수 필드, 증거 SHA-256을 DB와 대조한다. 불일치 시 종료 코드 1.
- 사용자 템플릿: `templates/word/report_template.docx`(스타일 유지, 본문 뒤에 추가), `templates/excel/report_template.xlsx`(같은 이름의 시트가 있으면 1행 헤더 이름으로 열을 매핑해 **템플릿의 열 순서 유지**, 모르는 열은 비워둠).
- 보고서·감사 로그의 비밀값 패턴(password=, token, api_key, Bearer, AKIA…, 개인키)은 마스킹된다. 원본 증거는 `data/evidence/`에 원형 그대로 보존된다.

### 7.1 웹 모의해킹 결과보고서 (Word 템플릿)

`templates/word/` 의 `웹 모의해킹 결과보고서 탬플릿_*.docx` 서식을 그대로 사용해 결과보고서를 만든다.
보호대책은 같은 폴더의 `*웹*보안권고안*.docx`(전자금융 웹, 주요정보통신기반시설 웹)에서 복사한다.

```powershell
# 1) 수동 검토로 취약 판정을 기록 (웹 체크리스트 항목은 모두 수동확인으로 시작한다)
.venv\Scripts\python -m audit review <run_id> WEB-SER-001 web01 FAIL --reason "SQL 인젝션 확인" --reviewer 홍길동
# 2) 고객/일정/대상 정보(meta)와 발견 내용(findings)을 작성한 뒤 보고서 생성
.venv\Scripts\python -m audit report <run_id> --type web --meta examples\report\report_meta.sample.json --findings examples\report\findings.sample.json
```

- `report_meta.json`: 고객사, 프로젝트, 개정이력, 진단 대상(`target_id`, 이름, URL), 일정, 수행인력. 예시: `examples/report/report_meta.sample.json`
- `findings.json`: 취약점별 `check_id` + `target_id`(DB의 **FAIL 판정과 반드시 일치**), 진단결과, 발생경로, 특이사항, 사례(CASE)와 단계(Step), 권고사항. 예시: `examples/report/findings.sample.json`
  - 단계(Step)는 사용자가 작성한 참고 보고서처럼 쓴다: `text`(`[Step N]` 뒤 문장), `code`(코드 줄 목록 → 회색 음영 문단), `image`(캡처 파일), `caption`(`[그림 N]` 설명, 생략하면 `text`). 옛 `title` 도 `text` 로 인식한다.
  - `improvements`(최상위): 3.2 개선 방안을 주제별 제목+본문으로 직접 작성한다. 생략하면 취약점별 권고사항(없으면 권고안 조치 방안)으로 구성한다.
  - `report_meta.json` 의 선택 항목: `business_name`(사업명), `purpose`(사업 목적), `scope`(사업 범위 목록), `diagnostic_items`(수행 방법의 진단 항목 표: 코드/취약점명/설명). 생략하면 템플릿 문구를 쓴다.
  - 판정이 FAIL 이 아닌 항목을 findings 에 넣으면 오류로 중단한다.
  - FAIL 판정인데 findings 에 없으면 누락하지 않고 `(작성 필요)` 로 포함하고 경고한다.
  - 캡처 이미지는 참고 보고서처럼 표 없이 가운데 정렬 문단으로 넣고 `[그림 N]` 설명을 붙인다. 이미지를 생략하면 `화면 캡쳐` 안내 문단이 남고, 경로를 줬는데 파일이 없으면 오류로 중단한다.
- **서식은 항상 템플릿 기준**: 보고서의 문단, 표, 캡션, 제목은 모두 템플릿 요소를 복제해 채운다. 보안권고안에서는 **텍스트와 이미지만** 가져오며, 권고안의 글꼴/크기/색/표 속성/코드 서식(스타일 포함)은 가져오지 않는다. 템플릿에 없는 스타일은 문서에 추가되지 않는다.
- **보호대책 연결**: 취약점명은 결과보고서(findings 의 `name`, 생략하면 체크리스트 항목명)가 기준이다. 이 이름이 권고안 제목(Heading 1)과 같으면 해당 권고안의 `취약점 설명`은 템플릿 본문 문단으로, `조치 방안`/`적용 방안`(소스 예제 포함)은 템플릿의 보호대책 표(구분/내용) 칸으로 옮긴다(캡션은 템플릿의 `[표 N] <취약점명> 취약점 보호대책`).
  이름이 다르면 `recommendation_titles` 로 권고안 제목을 직접 지정한다(여러 권고안을 지정하면 모두 복사하고 출처를 표시). 찾지 못하면 내용을 지어내지 않고 `보호대책 미연결`로 표시하고 경고한다.
  체크리스트의 웹 항목 48개는 전자금융 웹 권고안 제목과 모두 일치한다. 주요정보통신기반시설 권고안(예: `SQL 인젝션`)은 이름이 달라 `recommendation_titles` 지정이 필요하다.
- 생성 후 파일을 다시 열어 모든 취약점이 들어갔는지 확인한다(누락 시 종료 코드 1). 템플릿 안내 문구가 남았거나 권고안을 연결하지 못한 경우는 경고로 알려준다.
- 결과: `reports/<run_id>/web_pentest_report_<run_id>.docx` (기존 파일은 덮어쓰지 않음)
- **Word 에서 열 때** `필드를 업데이트하시겠습니까?` 안내가 나오면 `예`를 선택한다. 목차와 표/그림 번호가 갱신된다(선택하지 않으면 목차에 템플릿의 예시 항목이 보인다).
- 권고안의 부분 서식(굵게, 색, 고정폭 글꼴)은 보존되지 않는다. 소스 예제는 템플릿 칸 서식의 일반 문단으로 들어가며 들여쓰기 탭은 공백 4칸이 된다. 글머리 기호 목록은 `•` 문자로 옮긴다. 권고안의 `진단 예시` 구역은 보호대책에 쓰지 않는다.
- **참고 보고서 작성 방식**: 상세 진단 결과는 대상별로 쓰며 취약점이 없는 대상도 `진단 기간 내 발견된 취약점 없음`으로 포함한다(요약 표에는 `0개`). 요약 문장은 `웹 서비스 N개를 대상으로 모의해킹 진단 결과 총 M개의 취약점이 발견 되었다. <대상> 를 대상으로 …` 형식이고, 참고 보고서에 없는 취약점 발생 현황 그래프는 넣지 않는다. 대상이 바뀌면 새 쪽에서 시작한다.
- **글꼴 통일**: 문서 전체(스타일, 기본값, 본문, 머리글/바닥글)를 `맑은 고딕` 하나로 통일한다. 템플릿에 섞여 있던 바탕/바탕체/굴림/Arial 지정과 글꼴 지정이 없어 바탕체로 나오던 한글을 모두 맞춘다(글머리 기호용 Symbol 은 예외).
- 한계: 모바일 템플릿/권고안은 아직 지원하지 않는다(`--type web` 만). 템플릿의 고정 문구(모의해킹 방법론 등)는 수정하지 않는다(`diagnostic_items` 로 진단 항목 표만 교체 가능). `개수`는 사례(CASE) 수이며 사례가 없으면 1이다.
  생성 파일은 구조 무결성(북마크/스타일/번호/이미지 참조)까지만 자동 검증했다. Word 에서 보이는 모양은 직접 확인해야 한다.

## 8. 테스트

```powershell
.venv\Scripts\python -m unittest discover -s tests\unit -t .          # 단위 (네트워크 없음)
.venv\Scripts\python -m unittest discover -s tests\integration -t .   # 모의 Ollama HTTP 서버 + 실제 MCP stdio 테스트 서버
$env:AUDIT_LIVE_OLLAMA="1"; .venv\Scripts\python -m unittest tests.integration.test_ollama_mock   # 실제 Ollama + 모델 필요
```

## 9. 데이터 위치

| 경로 | 내용 | Git |
|---|---|---|
| `data/audit.db` | 실행·판정·증거 메타·감사 로그·모델 호출 (증거·감사·이력 테이블은 트리거로 수정/삭제 금지) | 제외 |
| `data/evidence/<run_id>/` | 원본 증거 (`EV-*.bin`, 한 번만 쓰기) | 제외 |
| `reports/` | 보고서 | 제외 |
| `checklist/sources/` | 가져온 가이드 원본(xlsx, pdf) 복사본 | 제외 |

## 10. 문제 해결

| 증상 | 조치 |
|---|---|
| `[WARN] ollama: Ollama(...)에 연결할 수 없음` | Ollama 앱/서비스 실행 확인: `curl http://127.0.0.1:11434/api/version` |
| `모델 '...' 이(가) 설치되어 있지 않음` | `ollama pull <model>` 또는 `settings.json`의 `ollama.model` 수정 |
| `로컬이 아닌 LLM 엔드포인트 ... 는 거부함` | 의도된 동작. 외부 전송은 기본 비활성 |
| `scope 가 ... 에 만료됨` | `scope.json`의 `valid_until`을 승인 문서에 맞게 갱신 |
| `경로가 범위를 벗어남` | 대상 루트 밖 경로 또는 링크로 범위 밖을 가리킴 — 범위를 확인 |
| `계획에 정책이 거부한 도구 호출이 포함됨` | 도구가 허용 목록에 없음/MCP 서버 비활성/위험도 미허용. `run --dry-run`으로 확인 |
| `내용의 SHA-256 이 manifest 와 다름` | 활성화 후 체크리스트가 수정됨. 새 버전으로 만들고 `checklist activate` |
| `승인이 필요하나 승인되지 않음` | 쓰기/위험 도구는 대화형 터미널에서 실행해 `y`로 승인 |
| MCP `연결 실패` / `MCP 시간 초과` | `command`/`url` 경로, 서버 기동 여부, `timeout_s` 확인. 다른 실행 경로로 자동 우회하지 않음 |
| 한글이 깨짐 | Windows Terminal 사용 또는 `chcp 65001` |
| `PermissionError [WinError 32]` | DB/보고서 파일을 다른 프로그램(Excel 등)이 열고 있음 |

## 11. 개발 규칙

- 소스 주석, docstring, 사용자에게 보이는 오류·출력 메시지, 문서는 한글로 작성한다.
- 영문으로 유지하는 것: 모델에 보내는 시스템 프롬프트, `[OK]/[WARN]/[FAIL]` 상태 태그, 판정값(`PASS` 등), 감사 이벤트 이름, 설정 키, 예외 클래스 이름.
- 오류 메시지를 바꾸면 그 문구를 검사하는 테스트도 함께 수정한다(예: `tests/integration/test_mcp.py`, `tests/unit/test_report.py`).
- 임의 셸 실행, 범위 우회, 외부 API 자동 전환 기능은 추가하지 않는다([CLAUDE.md](CLAUDE.md) §12).

## 12. 구현 현황과 검증 상태

| 영역 | 상태 | 검증 |
|---|---|---|
| 정책 엔진, 증거, 판정, 체크리스트, 저장소, CLI, 보고서 | 구현 완료 | 단위 테스트 74개 통과 |
| Ollama 클라이언트 | 구현 완료 | 모의 서버 통합 테스트 + **실제 모델(`qwen2.5:7b-instruct`, CPU 추론) 검증**: 규칙 검사와 3/3 일치, 호출당 약 9~14초. 3B 모델은 판단 품질이 부족했음. 내장 GPU 는 쓰지 않음 |
| MCP 클라이언트 | 구현 완료 | 테스트용 MCP 서버와 stdio 연결 통과. **실제 Burp 서버 연결 확인**(burp-suite/1.1.2, 도구 27개, 사용자 승인 흐름 동작). Ghidra/Frida 는 미연결 |
| Ghidra / Frida / JADX MCP | 실제 검증 | 3개 서버 모두 실제 연결, 도구를 위험도로 분류. 읽기 도구 실제 호출 성공: Frida(장치/프로세스), Ghidra(함수/세그먼트), JADX(AndroidManifest). 꺼진 프로그램의 오류 응답은 증거로 저장하지 않도록 처리 |
| Playwright MCP | 실제 검증 | 설치 후 도구 25개를 분류. 로컬 시험 페이지로 이동→스냅샷(연결 유지 확인), 범위 밖 URL/위험·쓰기 도구/`filename` 인자 거부 확인. **이 PC 에서는 전체 Chromium(`chrome.exe`)이 보안 소프트웨어에 막혀** 헤드리스 셸을 `--executable-path` 로 지정해 사용 |
| Burp 증거 수집(`evidence add`) | 구현 완료 | 테스트용 Burp 형식 서버로 범위 필터/정책/CLI 검증. **실제 Burp 트래픽 항목 형식은 미확인**(이력 비어 있음) |
| 공식 평가기준 xlsx 가져오기 | 구현 완료 | 실제 제2026-1호 xlsx 를 변환(1,913개 항목)하고 원본 셀과 대조해 불일치 0건 확인. 단위 테스트 포함 |
| 가져온 항목의 자동 판정 | 미구현 | 수집 도구(SSH, DB 접속 등)가 없어 모두 수동확인. 판단기준이 없는 340개 항목은 incomplete |
| 웹 모의해킹 결과보고서(템플릿) | 구현 완료 | 실제 템플릿/권고안/웹 체크리스트로 생성하고 다시 열어 확인, 단위 테스트 포함. **Word 화면 모양은 미확인**, 모바일 미구현 |
| 주통 가이드 PDF 쪽 번호 연결 | 구현 완료 | 코드 360개를 PDF 874쪽에서 찾아 항목 1,070개에 연결(xlsx 의 U/W/D/N 등 288개 코드는 모두 PDF 에서 발견). 본문(조치 방법 등) 추출은 신뢰성 문제로 미구현 |

## 13. 알려진 제한 사항

- 동봉 체크리스트는 예시이며, 공식 가이드(예: 기관 진단 가이드) 내용은 포함되어 있지 않다.
- 가져온 공식 항목은 자동 판정되지 않는다. 항목별 증거 수집 도구와 규칙을 추가해야 한다.
- 변환기의 시트별 대상 유형(`target_types`)은 엔진 라우팅용 값이며 원본 내용이 아니다. 필요하면 `importer.py` 의 `SHEETS` 를 수정한다.
- 새 개정판 xlsx 에 시트나 열 구성이 바뀌면 변환기가 알 수 없는 시트에서 중단한다(조용히 건너뛰지 않음).
- 규칙 종류는 정규식 하나뿐이다. 구조화 파싱(JSON/INI 키 비교 등)이 필요하면 규칙 종류를 추가해야 한다.
- MCP 호출마다 서버 세션을 새로 연다(단순·격리 우선). 호출이 많으면 느리다.
- MCP 전송은 `stdio`, `sse`만 지원(streamable HTTP 미지원).
- 경로 검사와 파일 읽기 사이의 시간차(TOCTOU)는 막지 않는다. 진단 대상 디렉터리를 진단 중에 조작할 수 있는 공격자는 범위 밖이다.
- 정규식은 체크리스트 작성자를 신뢰한다(ReDoS 방어 없음).
- 승인은 터미널 대화형 프롬프트뿐이다(원격 승인 워크플로 없음).
- LLM 진단 계획 자동 생성은 구현하지 않았다. 계획은 사람이 작성하고 정책 엔진이 검증한다.
