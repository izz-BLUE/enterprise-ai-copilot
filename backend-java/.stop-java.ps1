# Stop backend-java by killing the mvnw (PID 19960 earlier) and its java children
# Spring Boot's spring-boot:run forks the app from mvnw. Kill the tree.
$ErrorActionPreference = 'SilentlyContinue'
$procs = Get-CimInstance Win32_Process -Filter "Name='java.exe'"
$target = $procs | Where-Object { $_.CommandLine -match 'enterprise-ai-copilot|EnterpriseAiCopilot' -or $_.CommandLine -match 'spring-boot:run' }
foreach ($p in $target) {
    Write-Host "Killing PID=$($p.ProcessId)"
    Stop-Process -Id $p.ProcessId -Force
}
# Also kill any mvnw.cmd holding port 8080
$mvnw = Get-CimInstance Win32_Process -Filter "Name='mvnw.cmd'"
foreach ($p in $mvnw) {
    Write-Host "Killing mvnw PID=$($p.ProcessId)"
    Stop-Process -Id $p.ProcessId -Force
}
# Kill anything bound to 8080
$conn = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
foreach ($c in $conn) {
    Write-Host "Killing port-holder PID=$($c.OwningProcess)"
    Stop-Process -Id $c.OwningProcess -Force
}
Start-Sleep -Seconds 2
$still = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
if ($still) { Write-Host "WARN: 8080 still listening" } else { Write-Host "8080 free" }