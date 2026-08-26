$ErrorActionPreference = 'Continue'
$procs = Get-CimInstance Win32_Process -Filter "Name='java.exe'"
foreach ($p in $procs) {
    Write-Host ("PID={0}  Parent={1}" -f $p.ProcessId, $p.ParentProcessId)
    Write-Host ("CmdLine: {0}" -f $p.CommandLine)
    Write-Host ("--- EnvVar DEMO_AUTH_ENABLED = {0}" -f $p.GetEnvironmentVariable('DEMO_AUTH_ENABLED'))
    Write-Host ("--- EnvVar DEMO_AUTH_DEFAULT_PASSWORD set = {0}" -f [bool]$p.GetEnvironmentVariable('DEMO_AUTH_DEFAULT_PASSWORD'))
    Write-Host ("--- EnvVar AUTH_JWT_SECRET length = {0}" -f (([string]$p.GetEnvironmentVariable('AUTH_JWT_SECRET')).Length))
    Write-Host ""
}