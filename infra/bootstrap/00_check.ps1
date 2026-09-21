# 00_check.ps1 — 스택을 띄우기 전 환경 점검. 아무것도 바꾸지 않는다.
#
#   powershell -ExecutionPolicy Bypass -File infra/bootstrap/00_check.ps1
#
# ⚠ 이 파일은 **UTF-8 BOM** 으로 저장한다. Windows PowerShell 5.1 은 BOM 없는 파일을
#   시스템 코드페이지(cp949)로 읽어 한글이 깨지고 따옴표 짝이 어긋나 파싱 오류가 난다.
#   tests/test_scripts.py 가 이것을 검사한다.
# ⚠ -ExecutionPolicy Bypass 는 이 프로세스에만 적용된다. 시스템 정책은 바꾸지 않는다.
$ErrorActionPreference = "Continue"
$env:WSL_UTF8 = "1"   # wsl.exe 가 UTF-16 대신 UTF-8 로 출력하게 한다 (콘솔 깨짐 방지)
$bin = "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin"
if (Test-Path $bin) { $env:PATH = "$bin;$env:PATH" }

Write-Host "=== docker CLI ==="
$d = Get-Command docker -ErrorAction SilentlyContinue
if (-not $d) { Write-Host "FAIL  docker 를 PATH 에서 못 찾았다"; exit 1 }
docker --version

Write-Host "`n=== WSL ==="
wsl --status 2>&1 | Select-Object -First 1
if ($LASTEXITCODE -ne 0 -and -not (Get-Command wsl -ErrorAction SilentlyContinue)) {
  Write-Host "FAIL  WSL 이 없다. 관리자 PowerShell 에서 'wsl --install' 후 재부팅."
  exit 1
}

Write-Host "`n=== Docker Desktop ==="
$dd = Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue
if (-not $dd) {
  Write-Host "FAIL  Docker Desktop 이 실행 중이 아니다."
  Write-Host "      시작 메뉴에서 Docker Desktop 을 실행하고 'Engine running' 이 될 때까지 기다린다."
  exit 1
}
Write-Host "OK    실행 중 (PID $($dd[0].Id))"

Write-Host "`n=== 데몬 ==="
# 엔진이 기동 중이면 docker 가 오류 문구를 내고도 종료코드 0 을 줄 때가 있다.
# 종료코드만 믿지 말고 버전 문자열 형태까지 확인한다.
$info = (docker info --format "{{.ServerVersion}}" 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $info -notmatch '^\d+\.\d+(\.\d+)?$') {
  if ($info) { Write-Host "      응답: $info" }
  Write-Host "FAIL  엔진이 응답하지 않는다. Docker Desktop 이 막 켜졌다면 1~2분 뒤 다시 돌린다."
  exit 1
}
Write-Host "OK    ServerVersion=$info"
Write-Host "`n환경 점검 통과. 다음: docker compose -f infra/docker-compose.yml up -d"
