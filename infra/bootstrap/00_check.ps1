# 00_check.ps1 — 스택을 띄우기 전 환경 점검. 아무것도 바꾸지 않는다.
$ErrorActionPreference = "Continue"
$bin = "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin"
if (Test-Path $bin) { $env:PATH = "$bin;$env:PATH" }

Write-Host "=== docker CLI ==="
$d = Get-Command docker -ErrorAction SilentlyContinue
if (-not $d) { Write-Host "FAIL  docker 를 PATH 에서 못 찾았다"; exit 1 }
docker --version

Write-Host "`n=== WSL ==="
wsl --status 2>&1 | Select-Object -First 3

Write-Host "`n=== 데몬 ==="
$info = docker info --format "{{.ServerVersion}}" 2>&1
if ($LASTEXITCODE -ne 0 -or -not $info) {
  Write-Host "FAIL  엔진이 응답하지 않는다."
  Write-Host "      WSL2 가 없으면: 관리자 PowerShell 에서 'wsl --install' 후 재부팅."
  Write-Host "      그다음 Docker Desktop 을 실행해 'Engine running' 이 될 때까지 기다린다."
  exit 1
}
Write-Host "OK    ServerVersion=$info"
Write-Host "`n환경 점검 통과. 다음: docker compose -f infra/docker-compose.yml up -d"
