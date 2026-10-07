# Installs DRS as a Windows service with NSSM (https://nssm.cc, public domain, free).
# Run in an elevated PowerShell from the DRS folder, after the installation steps of
# docs/deployment.md. nssm.exe must be on PATH (or give its full path below).
#
#   .\deploy\windows\install-service.ps1 -DbPassword '...' -SecretKey '...'

param(
    [Parameter(Mandatory = $true)] [string] $DbPassword,
    [Parameter(Mandatory = $true)] [string] $SecretKey,
    [string] $ServiceName = "DRS",
    [string] $Nssm = "nssm.exe"
)
$ErrorActionPreference = "Stop"
$Home_ = (Resolve-Path ".").Path
$Python = Join-Path $Home_ ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "No virtual environment at $Python - run the installation steps first." }
New-Item -ItemType Directory -Force -Path (Join-Path $Home_ "runtime\logs") | Out-Null

& $Nssm install $ServiceName $Python "-m" "drs" "serve"
& $Nssm set $ServiceName AppDirectory $Home_
& $Nssm set $ServiceName DisplayName "DRS - Dynamic Report System"
& $Nssm set $ServiceName Start SERVICE_AUTO_START
# The variables are stored in the service's registry key, readable by administrators only.
& $Nssm set $ServiceName AppEnvironmentExtra "DRS_HOME=$Home_" "DRS_DB_PASSWORD=$DbPassword" "DRS_SECRET_KEY=$SecretKey"
& $Nssm set $ServiceName AppStdout (Join-Path $Home_ "runtime\logs\service.out.log")
& $Nssm set $ServiceName AppStderr (Join-Path $Home_ "runtime\logs\service.err.log")
& $Nssm set $ServiceName AppRotateFiles 1
& $Nssm set $ServiceName AppRotateBytes 10485760
& $Nssm start $ServiceName
Write-Host "Service $ServiceName installed and started. Check: Get-Service $ServiceName; runtime\logs\drs.log"
