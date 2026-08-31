# =============================================================================
# Enterprise AI Copilot - Local Startup Script (Windows PowerShell)
# =============================================================================
# Usage: .\start-local.ps1
#
# Starts three services in order:
#   1. Python AI Service (port 8000)
#   2. Java Backend (port 8080)
#   3. Frontend (port 5173)
#
# Each service is health-checked before the next one starts.
# Press Ctrl+C to stop all services.
# =============================================================================

$ErrorActionPreference = "Stop"

# Color output helpers
function Write-Step {
    param($msg)
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Write-Ok {
    param($msg)
    Write-Host "    [OK] $msg" -ForegroundColor Green
}

function Write-Err {
    param($msg)
    Write-Host "    [FAIL] $msg" -ForegroundColor Red
}

# Wait for a service to become available
function Wait-ForService {
    param($url, $name, $maxRetries = 30, $intervalSec = 2)
    for ($i = 1; $i -le $maxRetries; $i++) {
        try {
            $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5
            if ($response.StatusCode -eq 200) {
                Write-Ok "$name is UP"
                return $true
            }
        } catch {
            # Service not ready yet, keep waiting
        }
        Start-Sleep -Seconds $intervalSec
    }
    Write-Err "$name failed to start after $($maxRetries * $intervalSec) seconds"
    return $false
}

# Banner
Write-Host ""
Write-Host "============================================" -ForegroundColor Yellow
Write-Host "  Enterprise AI Copilot - Local Startup" -ForegroundColor Yellow
Write-Host "============================================" -ForegroundColor Yellow

# Check .env file
$envFile = "agent-python\.env"
if (-not (Test-Path $envFile)) {
    Write-Host ""
    Write-Host "[WARNING] $envFile not found!" -ForegroundColor Yellow
    Write-Host "  Please create it from .env.example:" -ForegroundColor Yellow
    Write-Host "    cd agent-python" -ForegroundColor Yellow
    Write-Host "    copy .env.example .env" -ForegroundColor Yellow
    Write-Host "  Then edit .env and set DEEPSEEK_API_KEY and LANGGRAPH_CHECKPOINT_DSN." -ForegroundColor Yellow
    Write-Host ""
    $continue = Read-Host "Continue anyway? (y/N)"
    if ($continue -ne "y" -and $continue -ne "Y") {
        Write-Host "Aborted." -ForegroundColor Red
        exit 1
    }
}

Write-Host "  Python requires a reachable PostgreSQL checkpoint DSN." -ForegroundColor Yellow
Write-Host "  Start local PostgreSQL with: docker compose -f deploy/docker-compose.local.yml up -d postgres" -ForegroundColor Yellow
Write-Host "  Compose default DSN: postgresql://copilot:copilot_dev@localhost:5432/enterprise_ai_copilot" -ForegroundColor Yellow

# Step 1: Python AI Service
Write-Step "Starting Python AI Service (port 8000)..."
$pythonJob = Start-Job -ScriptBlock {
    Set-Location $using:PWD
    cd agent-python
    uv sync 2>&1 | Out-Null
    uv run uvicorn app.main:app --reload --port 8000
}

Write-Host "    Waiting for Python service..."
if (-not (Wait-ForService "http://localhost:8000/agent/health" "Python AI Service") ) {
    Write-Err "Python AI Service failed to start. Check logs with: Receive-Job $($pythonJob.Id)"
    Stop-Job $pythonJob
    exit 1
}

# Step 2: Java Backend
Write-Step "Starting Java Backend (port 8080)..."
$javaJob = Start-Job -ScriptBlock {
    Set-Location $using:PWD
    cd backend-java
    .\mvnw.cmd spring-boot:run
}

Write-Host "    Waiting for Java service..."
if (-not (Wait-ForService "http://localhost:8080/api/health" "Java Backend") ) {
    Write-Err "Java Backend failed to start. Check logs with: Receive-Job $($javaJob.Id)"
    Stop-Job $javaJob
    Stop-Job $pythonJob
    exit 1
}

# Step 3: Frontend
Write-Step "Starting Frontend (port 5173)..."
$frontendJob = Start-Job -ScriptBlock {
    Set-Location $using:PWD
    cd frontend
    npm install 2>&1 | Out-Null
    npm run dev
}

Write-Host "    Waiting for Frontend..."
Start-Sleep -Seconds 5
try {
    $response = Invoke-WebRequest -Uri "http://localhost:5173" -UseBasicParsing -TimeoutSec 10
    Write-Ok "Frontend is UP"
} catch {
    Write-Host "    [WARN] Frontend may still be starting. Check http://localhost:5173" -ForegroundColor Yellow
}

# Done
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  All services started!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Frontend:      http://localhost:5173" -ForegroundColor White
Write-Host "  Java Backend:  http://localhost:8080" -ForegroundColor White
Write-Host "  Python Agent:  http://localhost:8000" -ForegroundColor White
Write-Host ""
Write-Host "  Health check:  .\health-check.ps1" -ForegroundColor White
Write-Host ""
Write-Host "  Press Ctrl+C to stop all services." -ForegroundColor Yellow
Write-Host ""

# Wait for user interrupt
try {
    while ($true) {
        Start-Sleep -Seconds 1
    }
} finally {
    Write-Host ""
    Write-Host "Stopping services..." -ForegroundColor Yellow
    Stop-Job $pythonJob, $javaJob, $frontendJob -ErrorAction SilentlyContinue
    Remove-Job $pythonJob, $javaJob, $frontendJob -Force -ErrorAction SilentlyContinue
    Write-Host "All services stopped." -ForegroundColor Green
}
