param(
    [string]$CondaEnv = "TongHua",
    [string]$ProbeGoal = "分析宁德时代近三年营业收入和利润变化",
    [string]$ExpectedThscode = "300750.SZ",
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 3000,
    [switch]$SkipProbe,
    [switch]$SkipDependencyBootstrap,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendRoot = $projectRoot
$frontendRoot = Join-Path $projectRoot "web\skeleton"
$backendBase = "http://127.0.0.1:$BackendPort"
$frontendBase = "http://127.0.0.1:$FrontendPort"
$backendProcess = $null
$frontendProcess = $null

function Test-CommandExists {
    param([string]$Name)
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [string]$WorkingDirectory = $projectRoot,
        [string]$Description = $FilePath
    )
    Write-Host $Description -ForegroundColor DarkGray
    Push-Location $WorkingDirectory
    try {
        & $FilePath @ArgumentList
        if ($LASTEXITCODE -ne 0) {
            throw "$Description failed with exit code $LASTEXITCODE."
        }
    } finally {
        Pop-Location
    }
}

function Get-ListeningConnection {
    param([int]$Port)
    return Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
}

function Assert-PortFree {
    param([int]$Port, [string]$Name)
    $conn = Get-ListeningConnection -Port $Port
    if ($null -ne $conn) {
        throw "$Name port $Port is already in use by PID $($conn.OwningProcess). Stop the old process first."
    }
}

function Wait-ForHttp {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSec = 90,
        [string]$Name = $Url
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    do {
        try {
            return Invoke-RestMethod -Uri $Url -TimeoutSec 5
        } catch {
            Start-Sleep -Seconds 2
        }
    } while ((Get-Date) -lt $deadline)
    throw "Timed out waiting for $Name at $Url."
}

function Wait-ForPage {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSec = 90,
        [string]$Name = $Url
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    do {
        try {
            $response = Invoke-WebRequest -Uri $Url -TimeoutSec 5
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
                return $response
            }
        } catch {
            Start-Sleep -Seconds 2
        }
    } while ((Get-Date) -lt $deadline)
    throw "Timed out waiting for $Name at $Url."
}

function Stop-StartedProcesses {
    foreach ($proc in @($frontendProcess, $backendProcess)) {
        if ($null -ne $proc -and -not $proc.HasExited) {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        }
    }
}

function Get-EnvLine {
    param([string]$Path, [string]$Prefix)
    if (-not (Test-Path $Path)) {
        return $null
    }
    return Select-String -Path $Path -Pattern ("^{0}" -f [regex]::Escape($Prefix)) | Select-Object -First 1
}

function Start-ServiceWindow {
    param(
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][string]$Title
    )
    return Start-Process powershell -ArgumentList @(
        "-NoExit",
        "-Command",
        "& { `$Host.UI.RawUI.WindowTitle = '$Title'; $Command }"
    ) -WorkingDirectory $WorkingDirectory -PassThru
}

function Get-DatabaseState {
    $python = "import json; from sqlalchemy import create_engine, inspect, text; from app.config.settings import get_settings; engine = create_engine(get_settings().database_url); inspector = inspect(engine); tables = inspector.get_table_names(); has_version = False; " +
        "conn = engine.connect(); " +
        "has_version = ('alembic_version' in tables) and bool(conn.execute(text('select version_num from alembic_version')).fetchall()); " +
        "conn.close(); " +
        "print(json.dumps({'table_count': len(tables), 'has_threads': 'threads' in tables, 'has_alembic_version': has_version}))"
    $json = conda run -n $CondaEnv --no-capture-output python -c $python
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to inspect database state."
    }
    return $json | ConvertFrom-Json
}

function Sync-DatabaseSchema {
    Write-Step "Applying database migrations"
    $dbState = Get-DatabaseState
    if ($dbState.has_threads -and -not $dbState.has_alembic_version) {
        Write-Host "Existing local tables detected without alembic_version; stamping current schema to head." -ForegroundColor Yellow
        Invoke-Native -FilePath "conda" -ArgumentList @(
            "run", "-n", $CondaEnv, "--no-capture-output", "python", "-m", "alembic", "stamp", "head"
        ) -WorkingDirectory $backendRoot -Description "Stamp existing database to head"
        return
    }

    Invoke-Native -FilePath "conda" -ArgumentList @(
        "run", "-n", $CondaEnv, "--no-capture-output", "python", "-m", "alembic", "upgrade", "head"
    ) -WorkingDirectory $backendRoot -Description "Run alembic upgrade"
}

function Invoke-RealProbe {
    Write-Step "Running real-data probe"

    $threadPayload = @{ goal = $ProbeGoal } | ConvertTo-Json
    $thread = Invoke-RestMethod -Method Post -Uri "$backendBase/api/threads" -ContentType "application/json" -Body $threadPayload
    if (-not $thread.thread_id) {
        throw "Probe failed: create thread did not return thread_id."
    }

    $runPayload = @{ background = $false } | ConvertTo-Json
    $run = Invoke-RestMethod -Method Post -Uri "$backendBase/api/threads/$($thread.thread_id)/runs" -ContentType "application/json" -Body $runPayload
    if (-not $run.run_id) {
        throw "Probe failed: create run did not return run_id."
    }
    if ($run.status -notin @("COMPLETED", "STOPPED")) {
        throw "Probe failed: run ended with status '$($run.status)'."
    }

    $evidencePayload = Invoke-RestMethod -Method Get -Uri "$backendBase/api/runs/$($run.run_id)/evidences"
    $costPayload = Invoke-RestMethod -Method Get -Uri "$backendBase/api/runs/$($run.run_id)/cost"
    $evidences = @($evidencePayload.evidences)
    if ($evidences.Count -eq 0) {
        throw "Probe failed: no evidences were produced."
    }

    $sources = @($evidences | ForEach-Object { $_.source } | Where-Object { $_ } | Sort-Object -Unique)
    if (-not ($sources -contains "fuyao")) {
        throw "Probe failed: evidences do not contain source=fuyao."
    }
    if (($costPayload.llm_calls -as [int]) -lt 1) {
        throw "Probe failed: llm_calls < 1."
    }

    $searchCodes = @(
        $evidences |
            Where-Object { $_.tool_name -eq "search_ticker" } |
            ForEach-Object { @($_.value.item) } |
            ForEach-Object { $_.thscode } |
            Where-Object { $_ }
    ) | Sort-Object -Unique
    if ($searchCodes.Count -eq 0) {
        throw "Probe failed: search_ticker did not produce any thscode."
    }
    if ($searchCodes -notcontains $ExpectedThscode) {
        throw "Probe failed: search_ticker codes '$($searchCodes -join ", ")' do not include expected '$ExpectedThscode'."
    }

    $financialCodes = @(
        $evidences |
            Where-Object { $_.tool_name -eq "get_financials" } |
            ForEach-Object { @($_.value.item) } |
            ForEach-Object { $_.thscode } |
            Where-Object { $_ }
    ) | Sort-Object -Unique
    if ($financialCodes.Count -eq 0) {
        throw "Probe failed: get_financials did not produce any rows."
    }
    if (@($financialCodes | Where-Object { $_ -ne $ExpectedThscode }).Count -gt 0) {
        throw "Probe failed: get_financials returned unrelated codes '$($financialCodes -join ", ")'."
    }

    Write-Host ("Probe passed: run={0}, llm_calls={1}, sources={2}, thscode={3}" -f `
        $run.run_id, $costPayload.llm_calls, ($sources -join "/"), $ExpectedThscode) -ForegroundColor Green
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
    throw "Backend .env was not found. Real local validation requires a local .env."
}

if (-not (Test-Path $frontendEnvFile)) {
    throw "Frontend .env.local was not found. Real local validation requires a local frontend API config."
}

$mockLine = Get-EnvLine -Path $backendEnvFile -Prefix "MOCK_DATA_MODE="
if ($null -eq $mockLine -or $mockLine.Line.Trim().ToLower() -ne "MOCK_DATA_MODE=false".ToLower()) {
    throw "Backend .env must contain 'MOCK_DATA_MODE=false' for real validation."
}

$apiBaseLine = Get-EnvLine -Path $frontendEnvFile -Prefix "NEXT_PUBLIC_API_BASE="
if ($null -eq $apiBaseLine) {
    throw "Frontend .env.local must define NEXT_PUBLIC_API_BASE."
}
if ($apiBaseLine.Line -notmatch "^NEXT_PUBLIC_API_BASE=http://127\.0\.0\.1:$BackendPort$" -and
    $apiBaseLine.Line -notmatch "^NEXT_PUBLIC_API_BASE=http://localhost:$BackendPort$") {
    throw "Frontend NEXT_PUBLIC_API_BASE must point to the local backend on port $BackendPort."
}

Write-Step "Checking local toolchain"
Invoke-Native -FilePath "conda" -ArgumentList @("run", "-n", $CondaEnv, "--no-capture-output", "python", "--version") -Description "Check Python in conda env"
Invoke-Native -FilePath "npm" -ArgumentList @("--version") -Description "Check npm"

if (-not $SkipDependencyBootstrap) {
    Write-Step "Checking backend dependencies"
    try {
        Invoke-Native -FilePath "conda" -ArgumentList @(
            "run", "-n", $CondaEnv, "--no-capture-output", "python", "-c",
            "import fastapi, uvicorn, alembic, sqlalchemy; import app.api"
        ) -WorkingDirectory $backendRoot -Description "Import backend packages"
    } catch {
        Invoke-Native -FilePath "conda" -ArgumentList @(
            "run", "-n", $CondaEnv, "--no-capture-output", "python", "-m", "pip", "install", "-e", ".[dev]"
        ) -WorkingDirectory $backendRoot -Description "Install backend dependencies"
    }

    Write-Step "Checking frontend dependencies"
    if (-not (Test-Path (Join-Path $frontendRoot "node_modules"))) {
        Invoke-Native -FilePath "npm" -ArgumentList @("--prefix", "web/skeleton", "ci") -WorkingDirectory $backendRoot -Description "Install frontend dependencies"
    } else {
        Invoke-Native -FilePath "npm" -ArgumentList @("--prefix", "web/skeleton", "--version") -WorkingDirectory $backendRoot -Description "Confirm frontend toolchain"
    }
}

Sync-DatabaseSchema

if ($CheckOnly) {
    Write-Host ""
    Write-Host "Check-only mode passed. Environment is ready for real local startup." -ForegroundColor Green
    return
}

Assert-PortFree -Port $BackendPort -Name "Backend"
Assert-PortFree -Port $FrontendPort -Name "Frontend"

try {
    Write-Step "Starting backend"
    $backendCommand = "conda run -n $CondaEnv --no-capture-output python -m uvicorn app.api:create_app --factory --host 127.0.0.1 --port $BackendPort"
    $backendProcess = Start-ServiceWindow -WorkingDirectory $backendRoot -Command $backendCommand -Title "InvestmentXBuddy Backend"

    Write-Step "Waiting for backend health"
    $health = Wait-ForHttp -Url "$backendBase/api/health" -Name "backend health"
    if ($health.status -ne "ok") {
        throw "Health check failed: status='$($health.status)'."
    }
    if ($health.mock_data_mode) {
        throw "Health check failed: backend is still in mock mode."
    }
    if (-not $health.llm_configured) {
        throw "Health check failed: llm_configured=false."
    }
    Write-Host "Backend health passed: mock_data_mode=false, llm_configured=true" -ForegroundColor Green

    Write-Step "Starting frontend"
    $frontendCommand = "npm run dev"
    $frontendProcess = Start-ServiceWindow -WorkingDirectory $frontendRoot -Command $frontendCommand -Title "InvestmentXBuddy Frontend"

    Write-Step "Waiting for frontend page"
    [void](Wait-ForPage -Url "$frontendBase/console" -Name "frontend console")
    Write-Host "Frontend page is reachable: $frontendBase/console" -ForegroundColor Green

    if (-not $SkipProbe) {
        Invoke-RealProbe
    } else {
        Write-Host "Skipped real-data probe by request." -ForegroundColor Yellow
    }

    Write-Host ""
    Write-Host "Local real-mode startup is ready." -ForegroundColor Green
    Write-Host "  Backend:  $backendBase"
    Write-Host "  Frontend: $frontendBase/console"
    Write-Host ""
    Write-Host "Close either PowerShell window to stop that service." -ForegroundColor DarkGray
} catch {
    Stop-StartedProcesses
    throw
}
