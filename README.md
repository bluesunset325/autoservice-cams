# Видеоаналитика на ваших камерах

Веб-панель для небольшого бизнеса: живое видео с IP-камер, запись по расписанию,
архив в облаке и журнал событий. Работает на обычном Windows-ПК (или Mac) на объекте,
новые камеры не нужны.

Первый объект — автосервис: распознавание госномеров, журнал заездов и выездов,
архив видео на Google Диске.

## Возможности

- **Живое видео** со всех камер в браузере (go2rtc): RTSP-камеры (iCSee / XMEye и др.) и Xiaomi.
- **Распознавание номеров РФ** (fast-alpr: YOLO + OCR) → журнал заездов/выездов по дням,
  время в боксе, скриншот, ручная правка номера, поиск по базе.
- **Запись** по расписанию кусками по 15 минут (ffmpeg, без перекодирования),
  метка `hvc1` для H.265, чтобы архив играл в Safari/iPhone.
- **Архив в облаке**: загрузка на Google Диск (rclone, лимит скорости), хранение N дней,
  очистка компьютера только после подтверждённой загрузки. Страница «Архив» со шкалой дня.
- **Роли**: администратор, владелец, сотрудник.
- **Удалённый доступ** через Tailscale — без открытых портов на роутере.
- **Установщик для Windows** без интернета: служба (NSSM), брандмауэр, автозапуск.

## Стек

Python 3.12, FastAPI + Jinja2, SQLite, go2rtc, fast-alpr (ONNX), ffmpeg, rclone.

## Быстрый старт (Mac / Linux)

```bash
python3.12 -m venv .venv
.venv/bin/pip install fastapi "uvicorn[standard]" jinja2 python-multipart itsdangerous bcrypt pyyaml httpx websockets numpy opencv-python-headless "fast-alpr[onnx]" pytest
cp config.example.yaml config.yaml        # впишите источники камер
# положите бинарник go2rtc (github.com/AlexxIT/go2rtc/releases) в корень проекта
.venv/bin/python -m app.manage --config config.yaml create-user admin --role admin
.venv/bin/python -m app.main --config config.yaml
```

Открыть http://localhost:8000. Тесты: `.venv/bin/python -m pytest -q`.

## Установка на Windows

См. [deploy/windows/INSTALL.md](deploy/windows/INSTALL.md): архив-установщик собирается
на Mac скриптом `tools/build_windows_bundle.sh`, на ПК — `INSTALL.bat`.

## Секреты

В репозиторий не попадают (см. `.gitignore`): `config.yaml` (адреса и пароли камер),
`go2rtc.yaml` (токен Xiaomi), `rclone.conf` (доступ к Google Диску), `data.db`
(пользователи и журнал), записи и логи. Архив `autoservice-cams-PC.zip`, который собирает
`build_windows_bundle.sh`, содержит секреты — не публикуйте его.

## Контакты

Александр — Telegram [@akayuoo](https://t.me/akayuoo)
