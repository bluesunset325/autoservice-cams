# Установка на ПК с Windows

## Быстрый способ: установщик (рекомендуется)
1. На Mac собрать архив: `tools/build_windows_bundle.sh` → `~/Desktop/autoservice-cams-PC.zip`
   (скачивает Python 3.12, Visual C++, go2rtc, ffmpeg, rclone, NSSM, библиотеки под Windows; кеш — `~/.cache/autoservice-win-bundle`).
   В архиве настройки уже под Windows (`host: 0.0.0.0`, пути к `.exe`) и секреты — не пересылать.
2. На ПК: распаковать архив, правый клик по `INSTALL.bat` → «Запуск от имени администратора». Интернет не нужен.
   Установщик (`deploy/windows/installer/install.ps1`) проверяет сеть и камеры, ставит Python, библиотеки, модели,
   программы, отключает сон, делает сеть «Частной», открывает порт 8000 (локальная сеть + Tailscale),
   ставит и запускает службу, ждёт ответа АП. Повторный запуск безопасен; при обновлении не трогает
   `config.yaml`, `go2rtc.yaml`, `rclone.conf`, `data.db`, записи.
3. Tailscale — вручную (раздел 4 ниже). Удаление — `UNINSTALL.bat`.

Ниже — ручная установка (если установщик не подошёл).

## 1. Подготовка ПК
- Электропитание → «Никогда не переводить в спящий режим» (иначе заезды не будут записываться):
  `powercfg /change standby-timeout-ac 0`
- ПК подключён к той же сети, что и камеры (лучше кабелем).

## 2. Программа
1. Установить Python 3.12 (x64) с python.org, галочка «Add python.exe to PATH» и «Install for all users» ("для всех пользователей").
2. Скопировать папку проекта в `C:\autoservice-cams` (без `.venv`).
3. В PowerShell:
   ```
   cd C:\autoservice-cams
   py -3.12 -m venv .venv
   .venv\Scripts\pip install fastapi "uvicorn[standard]" jinja2 python-multipart itsdangerous bcrypt pyyaml httpx websockets numpy opencv-python-headless "fast-alpr[onnx]"
   ```
4. Скачать go2rtc ≥ 1.9.14 для Windows: github.com/AlexxIT/go2rtc/releases → `go2rtc_win64.zip`,
   положить `go2rtc.exe` в `C:\autoservice-cams`.
5. Скачать NSSM: nssm.cc/download → из архива `win64\nssm.exe` в `C:\autoservice-cams`.
6. `copy config.example.yaml config.yaml`, в `config.yaml`:
   - вписать источники камер (`source`);
   - `go2rtc_path: go2rtc.exe`.
   - Спецсимволы в пароле камеры (`@ : / # ? & % +` и т.п.) в RTSP-ссылке нужно кодировать
     (URL-encode), например `@` -> `%40`. Это касается и `config.yaml`, и вывода `tools\find_rtsp.py`.
7. Создать пользователя-админа:
   ```
   .venv\Scripts\python -m app.manage create-user admin --role admin
   ```
8. Первый пробный запуск (при первом старте скачаются модели распознавания — нужен интернет):
   ```
   .venv\Scripts\python -m app.main
   ```
   Открыть http://localhost:8000, проверить видео. Оставить программу запущенной для следующего шага
   (позже остановить — Ctrl+C).
9. Камера Xiaomi («Офис»). Вход в Xiaomi выполняется на этом ПК, после пробного запуска:
   1. Открыть `http://127.0.0.1:1984/go2rtc/` **на самом ПК сервиса** (с других устройств этот адрес закрыт),
      в веб-интерфейсе go2rtc войти в аккаунт Xiaomi (Mi Home) и найти свою камеру.
   2. Скопировать строку источника `xiaomi://…` и вставить её в `config.yaml` как `source` камеры `office`.
   3. Токены аккаунта go2rtc сам дописывает в `C:\autoservice-cams\go2rtc.yaml` (раздел `xiaomi`).
      Если переносите их с другого ПК (например, с Mac) — скопируйте раздел с аккаунтом/`xiaomi`
      из того `go2rtc.yaml` в `C:\autoservice-cams\go2rtc.yaml`. Программа сохраняет все разделы,
      кроме `streams:`, — он всегда пересоздаётся из `config.yaml`.
   4. Остановить программу (Ctrl+C) и запустить заново (`.venv\Scripts\python -m app.main`), проверить видео «Офиса».
   - Если видео чёрное: в приложении камеры (iCSee / Mi Home) переключить поток на **H.264**.
     H.265 в браузере (MSE) требует аппаратной поддержки: в Firefox не работает, iPhone — только iOS 17.1 и новее.
10. Установить службу (PowerShell **от администратора**):
   ```
   cd C:\autoservice-cams
   Set-ExecutionPolicy -Scope Process Bypass
   .\deploy\windows\install_service.ps1
   ```
   Служба стартует при включении ПК и перезапускается при сбое. При первом старте службы скачаются модели распознавания (нужен интернет); если распознавание не начинает работать, проверьте `logs\app.log`.
   Скрипт открывает порт 8000 в брандмауэре для профилей «Домен» и «Частная» только для локальной подсети.
   Если сеть Windows определена как «Общедоступная» (Public), доступ с других устройств будет закрыт:
   Параметры → Сеть и Интернет → свойства подключения → профиль сети **«Частная»**
   (или измените правило «Autoservice AP 8000» в брандмауэре Windows).

## 3. Запись видео и Google Диск

1. Скачать **ffmpeg** для Windows (сборка «essentials» с gyan.dev или BtbN на GitHub), из архива положить `bin\ffmpeg.exe` в `C:\autoservice-cams`.
2. Скачать **rclone** для Windows (rclone.org/downloads), положить `rclone.exe` в `C:\autoservice-cams`.
3. В `config.yaml` (раздел `recording`): `ffmpeg_path: ffmpeg.exe`, `rclone_path: rclone.exe`.
4. Подключить Google Диск (вход делаете вы сами в браузере):
   ```
   cd C:\autoservice-cams
   .\rclone.exe --config C:\autoservice-cams\rclone.conf config
   ```
   - `n` (новый remote), имя `gdrive`, тип `drive` (Google Drive);
   - client_id и client_secret — оставить пустыми (Enter);
   - scope — `1` (полный доступ);
   - «Use web browser to automatically authenticate» — `y`: откроется браузер, войдите в свой Google и нажмите «Разрешить»;
   - «Configure this as a Shared Drive» — `n`; подтвердить `y`, выйти `q`.
5. Перезапустить службу (`nssm restart autoservice-cams`) и в АП: «Настройки → Запись видео → Проверить Google Диск» — должно быть «✅ Google Диск подключён».
6. Облегчённый поток офиса: для iCSee — тот же адрес с `stream=1`. Для Xiaomi «Поток для записи» оставьте пустым — пишется тот же поток, что и живое видео: второй поток `xiaomi://` к той же камере может мешать живому видео. Если всё же пробуете отдельный облегчённый поток Xiaomi, проверьте вручную: при идущей записи живое видео «Офиса» на главной должно показываться без обрывов.
7. Где что лежит: записи — `C:\autoservice-cams\recordings\`, логи ffmpeg — `logs\ffmpeg-<камера>.log`, на Диске — папка `Автосервис\<камера>\<дата>\`.
8. Если видео в «Архиве» не играет (чёрный экран), а файл скачивается и открывается в плеере — камера пишет H.265: переключите её на H.264 в iCSee/Mi Home.
9. `rclone.conf` должен оставаться доступным для записи: rclone сам обновляет в нём токен доступа Google. При переносе программы копируйте `rclone.conf` вместе со всей папкой `C:\autoservice-cams`, иначе загрузка на Диск перестанет работать.

### Проверка записи

После установки пройдите по списку:

- [ ] Поток виден ffmpeg'у: `.\ffmpeg.exe -hide_banner -rtsp_transport tcp -i rtsp://127.0.0.1:8554/box` (или `.\ffprobe.exe rtsp://127.0.0.1:8554/box`, если положили ffprobe) — в выводе есть строка `Video:` с кодеком (`h264` или `hevc`). Сообщение ffmpeg «At least one output file must be specified» здесь нормально.
- [ ] После 9:00 в `C:\autoservice-cams\recordings\<камера>\` появляются файлы `ГГГГ-ММ-ДД_ЧЧ-ММ-СС.mp4`, а в `logs\ffmpeg-<камера>.log` нет ошибок.
- [ ] Кусок открывается в «Архиве» и перематывается — в Chrome на ПК и в Safari на iPhone.
- [ ] Кусок дошёл до Google Диска и получил ID:
  ```
  .\rclone.exe --config C:\autoservice-cams\rclone.conf lsjson --files-only --original "gdrive:Автосервис/<камера>/<дата>/<файл>"
  ```
  в ответе есть поле `"ID"` (`<камера>` — имя камеры, например `Бокс`; `<файл>` — время начала, например `09-00-04.mp4`).
- [ ] После перезапуска службы (`nssm restart autoservice-cams`) в Диспетчере задач нет лишних `ffmpeg.exe` и `rclone.exe`: по одному `ffmpeg.exe` на записывающую камеру, `rclone.exe` — не больше одного-двух, и только пока идёт загрузка.

## 4. Доступ из дома (Tailscale)
1. Установить Tailscale на ПК сервиса (tailscale.com/download), войти в аккаунт.
2. Установить приложение Tailscale на свой телефон и телефон владельца, войти **в тот же аккаунт**
   (или пригласить владельца в свою сеть Tailscale).
3. В приложении Tailscale посмотреть имя/адрес ПК (например `100.x.y.z`).
4. На телефоне открыть `http://100.x.y.z:8000`.
   Порты на роутере открывать **не нужно**.
5. Если не открывается — разрешить порт 8000 для Tailscale в брандмауэре:
   ```
   New-NetFirewallRule -DisplayName "Autoservice AP Tailscale" -Direction Inbound -Protocol TCP -LocalPort 8000 -RemoteAddress 100.64.0.0/10 -Action Allow
   ```
6. Чтобы доступ не пропадал сам:
   - **Отключить истечение ключа ПК**: login.tailscale.com → Machines → «…» у ПК сервиса → **Disable key expiry**.
     Иначе примерно через 180 дней Tailscale попросит войти заново, и из дома АП не откроется, пока кто-то не войдёт на месте.
   - **Run unattended**: значок Tailscale в трее → Settings (Preferences) → включить **Run unattended**.
     Тогда Tailscale работает и после перезагрузки ПК, даже если в Windows никто не вошёл.
7. Проверить доступ можно заранее, без камер: папка `tailscale-test` (тестовая страница на порту 8000, инструкция в `README.txt`).
   Настоящую АП на время проверки остановить.

На телефоне Tailscale держать включённым **не нужно**: включил → открыл АП → выключил.
Работает из любого места с интернетом (другой дом, мобильный интернет). На телефоне одновременно может
работать только один VPN — если включён другой VPN, на время просмотра АП его выключить.

## 5. Обслуживание
- Логи: `C:\autoservice-cams\logs\app.log`, `logs\go2rtc.log`, `logs\service.out.log`, `logs\service.err.log`
- Бэкапы базы: `C:\autoservice-cams\backups\` (14 последних дней)
- Скриншоты: `C:\autoservice-cams\screenshots\`
- Перезапуск: в АП «Настройки → Перезапустить» или `nssm restart autoservice-cams`
- Обновление программы: остановить службу (`nssm stop autoservice-cams`), заменить папку `app\`,
  запустить (`nssm start autoservice-cams`).
