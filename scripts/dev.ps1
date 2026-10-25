<#
백엔드(FastAPI)와 프론트엔드(Next.js)를 각각 새 PowerShell 창에서 동시에 띄운다(README §5a).
끌 때는 각 창에서 Ctrl+C. .env 에 WORKBENCH_WEB_USER/WORKBENCH_WEB_PASSWORD/WORKBENCH_WEB_SECRET
이 없으면 백엔드가 시작을 거부한다(기본 거부 원칙, CLAUDE.md §8-4).

사용법: .\scripts\dev.ps1 [-BackendPort 8420] [-FrontendPort 3000]
#>
param(
    [int]$BackendPort = 8420,
    [int]$FrontendPort = 3000
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$root = Split-Path -Parent $PSScriptRoot
$workbench = Join-Path $root ".venv\Scripts\workbench.exe"
$webDir = Join-Path $root "web"

if (-not (Test-Path $workbench)) {
    Write-Error "가상환경이 없습니다: $workbench`n먼저 저장소 루트에서 'pip install -e .[web]' 로 설치하세요."
    exit 1
}
if (-not (Test-Path (Join-Path $webDir "node_modules"))) {
    Write-Error "web/node_modules 가 없습니다.`n먼저 'cd web && npm install' 을 실행하세요."
    exit 1
}

Write-Host "백엔드(FastAPI) 127.0.0.1:$BackendPort 새 창에서 시작..."
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$root'; & '$workbench' web --port $BackendPort"

Write-Host "프론트엔드(Next.js) 127.0.0.1:$FrontendPort 새 창에서 시작..."
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$webDir'; npm run dev -- --port $FrontendPort"

Write-Host "`n두 창 모두 켜졌습니다. 브라우저: http://127.0.0.1:$FrontendPort (끌 때는 각 창에서 Ctrl+C)"
