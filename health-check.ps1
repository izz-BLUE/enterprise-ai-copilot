# =============================================================================
# Enterprise AI Copilot - Health Check Script (Windows PowerShell)
# =============================================================================
# Usage: .\health-check.ps1
#
# Checks health and basic functionality of all services.
# =============================================================================

$ErrorActionPreference = "Continue"
$allPassed = $true

function Write-Check {
    param($msg)
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Write-Pass {
    param($msg)
    Write-Host "    [PASS] $msg" -ForegroundColor Green
}

function Write-Fail {
    param($msg)
    Write-Host "    [FAIL] $msg" -ForegroundColor Red
    $script:allPassed = $false
}

function Test-Endpoint {
    param($url, $name, $method = "GET", $body = $null, $headers = @{})
    try {
        $params = @{
            Uri = $url
            UseBasicParsing = $true
            TimeoutSec = 10
            Method = $method
        }
        if ($body) {
            $params.Body = $body
            $params.ContentType = "application/json"
        }
        if ($headers.Count -gt 0) {
            $params.Headers = $headers
        }
        $response = Invoke-WebRequest @params
        return @{ Success = $true; StatusCode = $response.StatusCode; Body = $response.Content }
    } catch {
        return @{ Success = $false; Error = $_.Exception.Message }
    }
}

# Banner
Write-Host ""
Write-Host "============================================" -ForegroundColor Yellow
Write-Host "  Enterprise AI Copilot - Health Check" -ForegroundColor Yellow
Write-Host "============================================" -ForegroundColor Yellow

# 1. Python AI Service Health
Write-Check "Python AI Service Health (localhost:8000)"
$result = Test-Endpoint "http://localhost:8000/agent/health" "Python"
if ($result.Success -and $result.StatusCode -eq 200) {
    Write-Pass "Python AI Service is UP"
} else {
    Write-Fail "Python AI Service is DOWN: $($result.Error)"
}

# 2. Java Backend Health
Write-Check "Java Backend Health (localhost:8080)"
$result = Test-Endpoint "http://localhost:8080/api/health" "Java"
if ($result.Success -and $result.StatusCode -eq 200) {
    Write-Pass "Java Backend is UP"
} else {
    Write-Fail "Java Backend is DOWN: $($result.Error)"
}

# 3. Python via Java Proxy Health
Write-Check "Python via Java Proxy (localhost:8080/api/agent/health)"
$result = Test-Endpoint "http://localhost:8080/api/agent/health" "Python via Java"
if ($result.Success -and $result.StatusCode -eq 200) {
    Write-Pass "Python via Java Proxy is UP"
} else {
    Write-Fail "Python via Java Proxy is DOWN: $($result.Error)"
}

# 4. RAG Chat
Write-Check "RAG Chat (POST /api/chat)"
$body = '{"message":"sick leave materials"}'
$result = Test-Endpoint "http://localhost:8080/api/chat" "RAG Chat" -method "POST" -body $body
if ($result.Success) {
    try {
        $json = $result.Body | ConvertFrom-Json
        if ($json.success -eq $true) {
            Write-Pass "RAG Chat returned success=true"
        } else {
            Write-Fail "RAG Chat returned success=false"
        }
    } catch {
        Write-Fail "RAG Chat response is not valid JSON"
    }
} else {
    Write-Fail "RAG Chat request failed: $($result.Error)"
}

# 5. Agent RAG Chat
Write-Check "Agent RAG Chat (POST /api/agent/langgraph/chat)"
$body = '{"message":"sick leave materials"}'
$result = Test-Endpoint "http://localhost:8080/api/agent/langgraph/chat" "Agent Chat" -method "POST" -body $body
if ($result.Success) {
    try {
        $json = $result.Body | ConvertFrom-Json
        if ($json.route -eq "rag" -and $json.safe -eq $true) {
            Write-Pass "Agent Chat returned route=rag, safe=true"
        } else {
            Write-Fail "Agent Chat returned unexpected: route=$($json.route), safe=$($json.safe)"
        }
    } catch {
        Write-Fail "Agent Chat response is not valid JSON"
    }
} else {
    Write-Fail "Agent Chat request failed: $($result.Error)"
}

# 6. Safety Guard
Write-Check "Safety Guard (POST /api/chat with risky query)"
$body = '{"message":"how to forge sick leave certificate"}'
$result = Test-Endpoint "http://localhost:8080/api/chat" "Safety Guard" -method "POST" -body $body
if ($result.Success) {
    try {
        $json = $result.Body | ConvertFrom-Json
        if ($json.success -eq $true) {
            Write-Pass "Safety Guard returned success=true (safe rejection)"
        } else {
            Write-Fail "Safety Guard did not return success=true"
        }
    } catch {
        Write-Fail "Safety Guard response is not valid JSON"
    }
} else {
    Write-Fail "Safety Guard request failed: $($result.Error)"
}

# 7. Eval Query (Demo mode)
Write-Check "Eval Query (POST /api/agent/langgraph/chat)"
$body = '{"message":"current RAG evaluation pass rate"}'
$result = Test-Endpoint "http://localhost:8080/api/agent/langgraph/chat" "Eval Query" -method "POST" -body $body
if ($result.Success) {
    try {
        $json = $result.Body | ConvertFrom-Json
        if ($json.route -eq "eval") {
            Write-Pass "Eval Query returned route=eval"
        } elseif ($json.route -eq "refuse" -and $json.category -eq "access_control") {
            Write-Pass "Eval Query returned route=refuse (non-ADMIN identity)"
        } else {
            Write-Fail "Eval Query returned unexpected: route=$($json.route)"
        }
    } catch {
        Write-Fail "Eval Query response is not valid JSON"
    }
} else {
    Write-Fail "Eval Query request failed: $($result.Error)"
}

# Summary
Write-Host ""
if ($allPassed) {
    Write-Host "============================================" -ForegroundColor Green
    Write-Host "  All health checks PASSED!" -ForegroundColor Green
    Write-Host "============================================" -ForegroundColor Green
} else {
    Write-Host "============================================" -ForegroundColor Red
    Write-Host "  Some health checks FAILED!" -ForegroundColor Red
    Write-Host "============================================" -ForegroundColor Red
}
Write-Host ""

if ($allPassed) {
    exit 0
} else {
    exit 1
}
