# Registers the application as a Windows service via NSSM.
# Run from PowerShell (as administrator): .\deploy\windows\install_service.ps1
param(
    [string]$Root = "C:\autoservice-cams",
    [string]$Nssm = "C:\autoservice-cams\nssm.exe",
    [string]$Name = "autoservice-cams"
)
$ErrorActionPreference = "Stop"
$python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Python not found at $python - create venv first (see INSTALL.md)" }
if (-not (Test-Path $Nssm)) { throw "NSSM not found at $Nssm - download NSSM first (see INSTALL.md)" }

# Check if service already exists
$prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
$null = & $Nssm status $Name 2>&1
$exists = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $prev
if ($exists) {
    Write-Host "Service already exists. Remove it with: nssm remove $Name confirm"
    exit 1
}

New-Item -ItemType Directory -Force -Path (Join-Path $Root "logs") | Out-Null

& $Nssm install $Name $python "-m" "app.main" "--config" (Join-Path $Root "config.yaml")
if ($LASTEXITCODE -ne 0) { throw "NSSM install failed with exit code $LASTEXITCODE" }

& $Nssm set $Name AppDirectory $Root
& $Nssm set $Name DisplayName "Autoservice cameras and plates"
& $Nssm set $Name Start SERVICE_AUTO_START
& $Nssm set $Name AppExit Default Restart
& $Nssm set $Name AppRestartDelay 5000
# Give the app 20 s to stop ffmpeg (closes the current clip) and rclone before NSSM kills it
& $Nssm set $Name AppStopMethodConsole 20000
& $Nssm set $Name AppStopMethodWindow 20000
& $Nssm set $Name AppStdout (Join-Path $Root "logs\service.out.log")
& $Nssm set $Name AppStderr (Join-Path $Root "logs\service.err.log")
& $Nssm set $Name AppRotateFiles 1
& $Nssm set $Name AppRotateBytes 10485760

# Create firewall rule only if it does not exist
if (-not (Get-NetFirewallRule -DisplayName "Autoservice AP 8000" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "Autoservice AP 8000" -Direction Inbound -Protocol TCP `
        -LocalPort 8000 -Profile Domain,Private -RemoteAddress LocalSubnet -Action Allow | Out-Null
}

& $Nssm start $Name
if ($LASTEXITCODE -ne 0) { throw "NSSM start failed with exit code $LASTEXITCODE (see logs\service.err.log)" }
Write-Host "Done. Open http://localhost:8000"
