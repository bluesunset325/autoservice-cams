# Полное удаление АП с ПК: служба, правила брандмауэра, папка программы (с записями и базой).
# Python и Visual C++ не удаляются — их можно убрать в «Установленных приложениях».
param([string]$Target = "C:\autoservice-cams")

$ErrorActionPreference = "Stop"
$ServiceName = "autoservice-cams"
$exitCode = 0
try {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) { throw "нужны права администратора: правый клик по UNINSTALL.bat -> «Запуск от имени администратора»" }

    Write-Host "Будет удалено: служба $ServiceName, правила брандмауэра «Autoservice…» и папка $Target" -ForegroundColor Yellow
    Write-Host "вместе с базой (журнал, пользователи), настройками, доступом к Google Диску и записями на этом ПК." -ForegroundColor Yellow

    $venvPy = Join-Path $Target ".venv\Scripts\python.exe"
    $db = Join-Path $Target "data.db"
    if ((Test-Path $venvPy) -and (Test-Path $db)) {
        try {
            $pending = & $venvPy -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); print(c.execute('select count(*) from recordings where drive_id is null and local_deleted_at is null').fetchone()[0])" $db
            if ($LASTEXITCODE -eq 0 -and [int]$pending -gt 0) {
                Write-Host "ВНИМАНИЕ: $pending кусков записи ещё НЕ загружены на Google Диск — после удаления они пропадут." -ForegroundColor Red
            }
        } catch { }
    }
    $answer = Read-Host "Чтобы удалить, напишите да и нажмите Enter"
    if ($answer.Trim().ToLower() -ne "да") { Write-Host "Отменено, ничего не удалено."; return }

    $nssm = Join-Path $Target "nssm.exe"
    if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
        if (Test-Path $nssm) { & $nssm remove $ServiceName confirm | Out-Null }
        else { & sc.exe delete $ServiceName | Out-Null }
        Write-Host "OK: служба удалена" -ForegroundColor Green
    }
    Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($Target, [StringComparison]::OrdinalIgnoreCase) } |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2

    foreach ($name in @("Autoservice AP 8000", "Autoservice AP Tailscale", "Autoservice go2rtc")) {
        Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    }
    Write-Host "OK: правила брандмауэра удалены" -ForegroundColor Green

    if (Test-Path $Target) {
        Remove-Item -Path $Target -Recurse -Force
        Write-Host "OK: папка $Target удалена" -ForegroundColor Green
    }
    Write-Host ""
    Write-Host "Готово. Python 3.12 и Visual C++ остались — при желании удалите их в «Параметры -> Приложения»." -ForegroundColor Green
}
catch {
    $exitCode = 1
    Write-Host "ОШИБКА: $($_.Exception.Message)" -ForegroundColor Red
}
finally {
    Write-Host ""
    Read-Host "Нажмите Enter, чтобы закрыть окно" | Out-Null
}
exit $exitCode
