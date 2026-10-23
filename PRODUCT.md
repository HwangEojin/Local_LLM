# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

보안 진단 담당자 1명~소수. 로컬 PC(127.0.0.1)에서 자율 진단 Run을 시작해 장시간 지켜보며, 도구 실행 승인과 finding 검토·확정·기각, 보고서 준비를 한다. 어두운 방에서 오래 켜 두는 사용 상황이 확인됨.

## Product Purpose

Python + PostgreSQL 기반 자율 AI 보안 진단 워크벤치의 웹 대시보드. LLM이 계획하고 실행한 진단의 진행 상황·증적·취약점 후보를 사람이 감독하고, 최종 판정과 승인을 내리는 자리. 성공 = 담당자가 지금 무슨 일이 일어나는지 즉시 파악하고, 증적을 근거로 안전하게 판정하는 것.

## Positioning

AI의 판단과 실제 증적을 분리해 보여 주고, 모든 finding이 도구 호출·증적에 연결되며 사람의 검토 이력이 보존되는 감독 콘솔.

## Operating Context

- 화면: 로그인, Run 목록, 새 진단 생성(환경 점검 포함), Run 상세(Findings / Backlog / Evidence / Activity / 서버 로그).
- Run 상세는 4초/2초 주기로 폴링하며, 서버 로그는 실시간처럼 갱신.
- 백엔드: FastAPI(`src/webapi`), 프런트: `web/` Next.js(App Router) + Tailwind v4.

## Capabilities and Constraints

- 사람이 하는 일: Run 시작/재개/취소, Backlog 승인/거부, finding 검토 기록·재검증 요청, 증적 다운로드·미리보기.
- finding 상태: CANDIDATE, NEEDS_REVIEW, CONFIRMED, REJECTED, INCONCLUSIVE, NOT_TESTED. LLM이 기록한 CONFIRMED도 사람이 재검토 가능.
- 화면 용어는 한국어로 통일하고 영문 병기는 하지 않는다(API 값은 내부 유지). 2026-10-23 확인.
- 다크 테마 필수(장시간 모니터링), 라이트는 보조.
- 안전 통제(승인, Kill Switch, 정책)를 UI가 우회하거나 약화시키면 안 된다.

## Product Principles

1. 관찰된 사실과 AI의 추론을 시각적으로 구분한다.
2. 상태와 위험은 한눈에, 세부는 한 클릭 뒤에 둔다.
3. 되돌릴 수 없는 판정·승인은 근거(증적)를 옆에 둔 채 내리게 한다.
4. 오래 켜 두어도 눈이 피로하지 않고, 갱신이 작업을 방해하지 않는다.

## Accessibility & Inclusion

장시간 어두운 환경 사용(다크 기본). 기본 WCAG AA 대비. 상태는 색에만 의존하지 않고 텍스트를 함께 쓴다.
