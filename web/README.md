# web — 워크벤치 로컬 대시보드 (Next.js)

이 디렉터리는 루트 [`README.md`](../README.md) §5a("웹 UI")가 설명하는 프론트엔드다. 백엔드는
`src/webapi`(FastAPI, 루트에서 `workbench web` 으로 실행)이고 여기는 그 위에 얹는 화면만 담는다.

```bash
npm install
npm run dev     # http://localhost:3000, .env.local 의 NEXT_PUBLIC_API_BASE 가 백엔드 주소
```

새 진단 로직은 여기 두지 않는다 — 화면과 폼 검증만. 실제 판정·정책은 전부 백엔드(그리고 그 아래
`diagnostic_workbench`/`audit` 모듈)가 한다.
