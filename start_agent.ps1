# Helper script to launch Tata Voice Agent Server & Router
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "         STARTING CLINIGO VOICE AGENT & WEBSOCKET ROUTER        " -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan

$PYTHON = "C:\Python314\python.exe"

if (-not (Test-Path $PYTHON)) {
    Write-Host "[ERROR] Could not find Python at $PYTHON" -ForegroundColor Red
    exit 1
}

Write-Host "`n1. Checking active processes on port 8080 and 8081..." -ForegroundColor Yellow
$p8080 = Get-NetTCPConnection -LocalPort 8080 -ErrorAction SilentlyContinue
$p8081 = Get-NetTCPConnection -LocalPort 8081 -ErrorAction SilentlyContinue

if ($p8081) {
    Write-Host "   -> Voice Server (port 8081) is already running (PID $($p8081.OwningProcess))." -ForegroundColor Green
} else {
    Write-Host "   -> Starting Voice Server on port 8081..." -ForegroundColor Yellow
    Start-Process $PYTHON -ArgumentList "tata_voice_server.py" -NoNewWindow
}

if ($p8080) {
    Write-Host "   -> Router (port 8080) is already running (PID $($p8080.OwningProcess))." -ForegroundColor Green
} else {
    Write-Host "   -> Starting Router on port 8080..." -ForegroundColor Yellow
    Start-Process $PYTHON -ArgumentList "router.py" -NoNewWindow
}

Write-Host "`n=================================================================" -ForegroundColor Cyan
Write-Host "  Setup Complete! Active Ngrok tunnel should point to port 8080. " -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
