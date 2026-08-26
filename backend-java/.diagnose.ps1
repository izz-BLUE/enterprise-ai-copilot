# Diagnose why DemoAuthAccountInitializer didn't run. Add debug via --debug and check env.
# Actually simpler: hit /actuator/configprops or hit /api/demo/identities if exposed.
$ErrorActionPreference = 'Continue'
$logPath = 'G:\跳槽计划\项目\enterprise-ai-copilot\backend-java\java-app.log'

# 1. Did the DemoAuthAccountInitializer actually instantiate? Its only log is implicit (no @Slf4j used).
# We can infer: if any seed ran, password_hash of zhangsan would be BCrypt of "Demo@12345".
# Pull hash and test it with Python or bcrypt CLI.

# 2. Hit /api/demo/identities (permitAll route from SecurityConfig)
Write-Host "--- /api/demo/identities ---"
try {
    $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8080/api/demo/identities' -UseBasicParsing -TimeoutSec 5
    Write-Host $r.StatusCode
    Write-Host $r.Content
} catch { Write-Host "ERR: $_" }

# 3. Print last 200 log lines
Write-Host "`n--- last 60 log lines ---"
Get-Content $logPath -Tail 60