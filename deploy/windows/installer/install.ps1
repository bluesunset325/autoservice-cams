# Установщик АП для Windows. Запускается из INSTALL.bat (от имени администратора).
# Повторный запуск безопасен: уже сделанное пропускается, данные и настройки на ПК не затираются.
param([string]$Target = "C:\autoservice-cams")

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $Here
$Payload = Join-Path $Root "payload"
$ServiceName = "autoservice-cams"
$Total = 10
$script:Warnings = @()

function Step($n, $text) { Write-Host ""; Write-Host "[$n/$Total] $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "   OK: $text" -ForegroundColor Green }
function Warn($text) { Write-Host "   ВНИМАНИЕ: $text" -ForegroundColor Yellow; $script:Warnings += $text }
function Fail($text) { throw $text }

function Copy-Tree($from, $to, [switch]$OnlyNew) {
    # robocopy: /MIR — точная копия (код), /E /XC /XN /XO — только файлы, которых ещё нет (данные)
    [string[]]$opts = @("/MIR")
    if ($OnlyNew) { $opts = @("/E", "/XC", "/XN", "/XO") }
    [string[]]$roboArgs = @([string]$from, [string]$to) + $opts + @("/R:2", "/W:2", "/NFL", "/NDL", "/NJH", "/NP")
    $out = & robocopy.exe $roboArgs
    $code = $LASTEXITCODE
    if ($code -ge 8) {
        Write-Host ($out | Out-String)
        Fail "не удалось скопировать $from -> $to (robocopy, код $code; ответ robocopy выше)"
    }
}

function Find-Python312 {
    $candidates = @("$env:ProgramFiles\Python312\python.exe", "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe")
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    try {
        $exe = & py -3.12 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Path $exe.Trim())) { return $exe.Trim() }
    } catch { }
    return $null
}

function Test-Port8000 {
    $client = New-Object System.Net.Sockets.TcpClient
    try { $client.Connect("127.0.0.1", 8000); return $true } catch { return $false } finally { $client.Close() }
}

$LogFile = Join-Path $Root "install-log.txt"
try { Start-Transcript -Path $LogFile -Append | Out-Null } catch { }

$exitCode = 0
try {
    Write-Host "Установка АП (камеры + номера + архив) в $Target" -ForegroundColor White

    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) { Fail "нужны права администратора: правый клик по INSTALL.bat -> «Запуск от имени администратора»" }
    if (-not [Environment]::Is64BitOperatingSystem) { Fail "нужна 64-битная Windows" }
    if (-not (Test-Path (Join-Path $Payload "app\app\main.py"))) {
        Fail "не найдена папка payload рядом с установщиком — распакуйте архив целиком и запустите INSTALL.bat из него"
    }
    # файлы с флешки/из интернета могут быть помечены как «скачанные» — снимаем пометку
    Get-ChildItem -Path $Root -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue

    # ---------------------------------------------------------------------------------------------
    Step 1 "Проверка сети и камер"
    # адреса камер берём из config.yaml (rtsp://[логин:пароль@]адрес...), у Xiaomi адреса нет — пропускаем
    $cfgPath = Join-Path $Target "config.yaml"
    if (-not (Test-Path $cfgPath)) { $cfgPath = Join-Path $Payload "app\config.yaml" }
    $camHosts = @()
    if (Test-Path $cfgPath) {
        $cfgText = Get-Content -Path $cfgPath -Raw -Encoding UTF8
        $camHosts = @([regex]::Matches($cfgText, 'rtsp://(?:[^@/\s]*@)?([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)') |
            ForEach-Object { $_.Groups[1].Value } | Select-Object -Unique)
    }
    $prefixes = @($camHosts | ForEach-Object { (($_ -split '\.')[0..2]) -join '.' } | Select-Object -Unique)
    $lan = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object {
        $ip = $_.IPAddress; @($prefixes | Where-Object { $ip.StartsWith("$_.") }).Count -gt 0 })
    if ($prefixes.Count -eq 0) { Ok "в config.yaml нет камер с IP-адресом — проверка сети пропущена" }
    elseif ($lan.Count -gt 0) { Ok "адрес ПК в сети камер: $($lan[0].IPAddress)" }
    else { Warn "ПК не в одной сети с камерами ($(($prefixes | ForEach-Object { "$_.x" }) -join ', ')) — камеры работать не будут (на тестовом ПК это нормально)" }
    foreach ($h in $camHosts) {
        if (Test-Connection -ComputerName $h -Count 1 -Quiet -ErrorAction SilentlyContinue) { Ok "камера $h отвечает" }
        else { Warn "камера $h не отвечает" }
    }
    $vpn = @(Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object {
        $_.Status -eq "Up" -and $_.InterfaceDescription -match "VPN|TAP-|TUN|WireGuard|Wintun|OpenVPN" -and
        $_.InterfaceDescription -notmatch "Tailscale" -and $_.Name -notmatch "Tailscale" })
    if ($vpn.Count -gt 0) { Warn "похоже, включён VPN ($($vpn[0].InterfaceDescription)). С VPN камера Xiaomi работает через раз — выключите его" }

    # ---------------------------------------------------------------------------------------------
    Step 2 "Остановка прежней версии (если уже установлена)"
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($svc) {
        if ($svc.Status -ne "Stopped") {
            Stop-Service -Name $ServiceName -Force   # NSSM даёт программе 20 с закрыть текущий кусок записи
            Ok "служба остановлена — будет обновление"
        } else { Ok "служба уже остановлена — будет обновление" }
    } else { Ok "первая установка" }
    Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($Target, [StringComparison]::OrdinalIgnoreCase) } |
        Stop-Process -Force -ErrorAction SilentlyContinue
    if (Test-Port8000) { Fail "порт 8000 занят другой программой (АП, запущенная вручную, или тест Tailscale?) — закройте её и запустите установщик снова" }

    # ---------------------------------------------------------------------------------------------
    Step 3 "Копирование программы в $Target"
    $src = Join-Path $Payload "app"
    New-Item -ItemType Directory -Force -Path $Target | Out-Null
    foreach ($d in @("app", "deploy", "tools", "docs")) {
        if (Test-Path (Join-Path $src $d)) { Copy-Tree (Join-Path $src $d) (Join-Path $Target $d) }
    }
    foreach ($f in @("pyproject.toml", "config.example.yaml", "HANDOFF.md")) {
        if (Test-Path (Join-Path $src $f)) { Copy-Item (Join-Path $src $f) $Target -Force }
    }
    Ok "программа скопирована"
    # настройки, секреты и база: только если их ещё нет — при обновлении ничего не затираем
    foreach ($f in @("config.yaml", "go2rtc.yaml", "rclone.conf", "data.db", "camera_login.txt")) {
        $from = Join-Path $src $f; $to = Join-Path $Target $f
        if (Test-Path $to) { Ok "$f уже есть на ПК — оставлен как был" }
        elseif (Test-Path $from) { Copy-Item $from $to; Ok "$f скопирован" }
    }
    foreach ($d in @("recordings", "backups", "screenshots")) {
        if (Test-Path (Join-Path $src $d)) { Copy-Tree (Join-Path $src $d) (Join-Path $Target $d) -OnlyNew }
    }

    # ---------------------------------------------------------------------------------------------
    Step 4 "Программы go2rtc, ffmpeg, rclone, nssm"
    foreach ($exe in @("go2rtc.exe", "ffmpeg.exe", "rclone.exe", "nssm.exe")) {
        $from = Join-Path $Payload "bin\$exe"
        if (-not (Test-Path $from)) { Fail "в архиве нет payload\bin\$exe — архив собран не полностью" }
        Copy-Item $from $Target -Force
    }
    Ok "go2rtc.exe, ffmpeg.exe, rclone.exe, nssm.exe"

    # ---------------------------------------------------------------------------------------------
    Step 5 "Системные библиотеки Microsoft (Visual C++)"
    $vcExe = Join-Path $Payload "python\vc_redist.x64.exe"
    if (Test-Path $vcExe) {
        $p = Start-Process -FilePath $vcExe -ArgumentList "/install", "/quiet", "/norestart" -Wait -PassThru
        if (@(0, 1638, 3010) -contains $p.ExitCode) { Ok "установлены (код $($p.ExitCode))" }   # 1638 — уже есть новее
        else { Warn "установщик Visual C++ вернул код $($p.ExitCode) — если АП не запустится, поставьте vc_redist.x64.exe вручную" }
    } else { Warn "в архиве нет vc_redist.x64.exe — пропущено" }

    # ---------------------------------------------------------------------------------------------
    Step 6 "Python 3.12"
    $py = Find-Python312
    if ($py) { Ok "уже установлен: $py" }
    else {
        $inst = Get-ChildItem -Path (Join-Path $Payload "python") -Filter "python-3.12*-amd64.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $inst) { Fail "в архиве нет установщика Python (payload\python\python-3.12.x-amd64.exe)" }
        Write-Host "   ставлю $($inst.Name) (1–3 минуты)..."
        $p = Start-Process -FilePath $inst.FullName -Wait -PassThru -ArgumentList `
            "/quiet", "InstallAllUsers=1", "PrependPath=1", "Include_launcher=1", "Include_test=0", "Include_doc=0"
        if ($p.ExitCode -ne 0) { Fail "установщик Python завершился с кодом $($p.ExitCode)" }
        $py = Find-Python312
        if (-not $py) { Fail "Python установлен, но не найден — перезагрузите ПК и запустите установщик снова" }
        Ok "установлен: $py"
    }

    # ---------------------------------------------------------------------------------------------
    Step 7 "Библиотеки Python (без интернета, из архива)"
    $venvPy = Join-Path $Target ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPy)) {
        & $py -m venv (Join-Path $Target ".venv")
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPy)) { Fail "не удалось создать окружение Python (.venv)" }
    }
    $wheels = Join-Path $Payload "wheels"
    Write-Host "   ставлю библиотеки (1–3 минуты)..."
    & $venvPy -m pip install --no-index --find-links $wheels -r (Join-Path $wheels "requirements.txt") --disable-pip-version-check --quiet
    if ($LASTEXITCODE -ne 0) { Fail "pip не смог поставить библиотеки (подробности выше)" }
    & $venvPy -c "import fastapi, uvicorn, cv2, onnxruntime, fast_alpr, yaml, bcrypt; print('   OK: библиотеки загружаются')"
    if ($LASTEXITCODE -ne 0) { Fail "библиотеки установлены, но не загружаются — см. раздел «Если что-то не работает» в README.txt" }

    # ---------------------------------------------------------------------------------------------
    Step 8 "Модели распознавания номеров"
    $models = Join-Path $Payload "models"
    if (Test-Path $models) {
        # служба работает от имени SYSTEM — её «домашняя» папка systemprofile; плюс текущий пользователь для ручного запуска
        $homes = @((Join-Path $env:SystemRoot "System32\config\systemprofile"), $env:USERPROFILE) | Select-Object -Unique
        foreach ($h in $homes) { Copy-Tree $models (Join-Path $h ".cache") -OnlyNew }
        Ok "модели скопированы (интернет при первом запуске не нужен)"
    } else { Warn "в архиве нет моделей — при первом запуске они скачаются из интернета" }

    # ---------------------------------------------------------------------------------------------
    Step 9 "Настройки Windows: сон, сеть, брандмауэр"
    & powercfg /change standby-timeout-ac 0 | Out-Null
    & powercfg /change hibernate-timeout-ac 0 | Out-Null
    Ok "ПК не будет засыпать (от сети)"
    foreach ($ip in $lan) {
        $prof = Get-NetConnectionProfile -InterfaceIndex $ip.InterfaceIndex -ErrorAction SilentlyContinue
        if ($prof -and $prof.NetworkCategory -eq "Public") {
            Set-NetConnectionProfile -InterfaceIndex $ip.InterfaceIndex -NetworkCategory Private
            Ok "сеть «$($prof.Name)» переведена в «Частную»"
        }
    }
    $rules = @(
        @{ Name = "Autoservice AP 8000"; Args = @{ Direction = "Inbound"; Protocol = "TCP"; LocalPort = 8000; Profile = "Domain,Private"; RemoteAddress = "LocalSubnet" } },
        @{ Name = "Autoservice AP Tailscale"; Args = @{ Direction = "Inbound"; Protocol = "TCP"; LocalPort = 8000; RemoteAddress = "100.64.0.0/10" } },
        @{ Name = "Autoservice go2rtc"; Args = @{ Direction = "Inbound"; Program = (Join-Path $Target "go2rtc.exe"); Profile = "Domain,Private"; RemoteAddress = "LocalSubnet" } }
    )
    foreach ($r in $rules) {
        if (-not (Get-NetFirewallRule -DisplayName $r.Name -ErrorAction SilentlyContinue)) {
            $a = $r.Args
            New-NetFirewallRule -DisplayName $r.Name -Action Allow @a | Out-Null
        }
    }
    Ok "брандмауэр: порт 8000 для сети сервиса и Tailscale"

    # ---------------------------------------------------------------------------------------------
    Step 10 "Служба Windows (автозапуск) и проверка"
    if (-not (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue)) {
        & (Join-Path $Target "deploy\windows\install_service.ps1") -Root $Target -Nssm (Join-Path $Target "nssm.exe")
    } else {
        Start-Service -Name $ServiceName
    }
    Write-Host "   жду запуска АП (до 3 минут)..."
    $deadline = (Get-Date).AddMinutes(3)
    while (-not (Test-Port8000) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 3 }
    if (-not (Test-Port8000)) { Fail "служба не открыла порт 8000 за 3 минуты — смотрите $Target\logs\service.err.log и app.log" }
    Ok "АП работает: http://localhost:8000"

    Write-Host ""
    Write-Host "=== Готово ===" -ForegroundColor Green
    Write-Host "На этом ПК:         http://localhost:8000   (логин admin, ваш пароль)"
    if ($lan.Count -gt 0) { Write-Host "С телефона в Wi-Fi: http://$($lan[0].IPAddress):8000" }
    Write-Host "Из дома:            поставьте Tailscale (см. README.txt, раздел «Удалённый доступ»)"
    if ($script:Warnings.Count -gt 0) {
        Write-Host ""
        Write-Host "Предупреждения:" -ForegroundColor Yellow
        $script:Warnings | ForEach-Object { Write-Host " - $_" -ForegroundColor Yellow }
    }
    Start-Process "http://localhost:8000"
}
catch {
    $exitCode = 1
    Write-Host ""
    Write-Host "ОШИБКА: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Установка остановлена. Исправьте причину и запустите INSTALL.bat ещё раз — сделанные шаги повторятся без вреда." -ForegroundColor Red
    Write-Host "Подробный журнал: $LogFile"
}
finally {
    try { Stop-Transcript | Out-Null } catch { }
    Write-Host ""
    Read-Host "Нажмите Enter, чтобы закрыть окно" | Out-Null
}
exit $exitCode
