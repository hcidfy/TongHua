$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendRoot = $projectRoot
$frontendRoot = Join-Path $projectRoot "web\skeleton"

function Test-CommandExists {
    param([string]$Name)
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

if (-not (Test-CommandExists "conda")) {
    throw "conda was not found in PATH."
}

if (-not (Test-CommandExists "npm")) {
    throw "npm was not found in PATH."
}

$backendEnvFile = Join-Path $backendRoot ".env"
$frontendEnvFile = Join-Path $frontendRoot ".env.local"

if (-not (Test-Path $backendEnvFile)) {
    Write-Host "Warning: backend .env was not found. The backend will rely on inherited environment variables." -ForegroundColor Yellow
}

if (-not (Test-Path $frontendEnvFile)) {
    Write-Host "Warning: frontend .env.local was not found. The frontend will use its built-in default API base." -ForegroundColor Yellow
}

$backendCommand = "conda run -n TongHua --no-capture-output python -m uvicorn app.api:create_app --factory --host 127.0.0.1 --port 8000"
$frontendCommand = "npm run dev"

Write-Host "Starting backend: http://127.0.0.1:8000" -ForegroundColor Cyan
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    $backendCommand
) -WorkingDirectory $backendRoot

Write-Host "Starting frontend: http://127.0.0.1:3000/console" -ForegroundColor Cyan
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    $frontendCommand
) -WorkingDirectory $frontendRoot

Write-Host ""
Write-Host "Two windows were started:" -ForegroundColor Green
Write-Host "  - Backend:  http://127.0.0.1:8000"
Write-Host "  - Frontend: http://127.0.0.1:3000/console"
Write-Host ""
Write-Host "Close either PowerShell window to stop that service."
