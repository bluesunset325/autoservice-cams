#!/bin/bash
# Собирает на Mac архив-установщик для Windows: ~/Desktop/autoservice-cams-PC.zip
#   INSTALL.bat / UNINSTALL.bat / README.txt, installer/*.ps1,
#   payload/app (проект с настройками под Windows и секретами), payload/bin (*.exe),
#   payload/python (установщики Python и Visual C++), payload/wheels (библиотеки под Windows),
#   payload/models (модели распознавания), tailscale-test/.
# Скачанное кешируется в ~/.cache/autoservice-win-bundle — повторная сборка без интернета.
# Запуск: tools/build_windows_bundle.sh
set -euo pipefail

PROJ="$(cd "$(dirname "$0")/.." && pwd)"
CACHE="${CACHE:-$HOME/.cache/autoservice-win-bundle}"
OUT="${OUT:-$HOME/Desktop/autoservice-cams-PC.zip}"
NAME="autoservice-cams-PC"
PY_VER="3.12.10"          # последняя 3.12 с готовым установщиком для Windows
GO2RTC_VER="v1.9.14"

mkdir -p "$CACHE/dl" "$CACHE/wheels"
WORK="$(mktemp -d)"
STAGE="$WORK/$NAME"
trap 'rm -rf "$WORK"' EXIT

fetch() {  # fetch <url> <файл в кеше> [запасной url]
    local url="$1" out="$CACHE/dl/$2" alt="${3:-}"
    if [ -s "$out" ]; then echo "  есть: $2"; return; fi
    echo "  качаю: $2"
    if ! curl -fL --retry 3 --connect-timeout 20 -o "$out.part" "$url"; then
        [ -n "$alt" ] || { echo "Не удалось скачать $url" >&2; exit 1; }
        echo "  основной адрес не ответил, пробую запасной"
        curl -fL --retry 3 --connect-timeout 20 -o "$out.part" "$alt"
    fi
    mv "$out.part" "$out"
}

echo "== 1. Скачивание программ для Windows"
fetch "https://www.python.org/ftp/python/$PY_VER/python-$PY_VER-amd64.exe" "python-$PY_VER-amd64.exe"
fetch "https://aka.ms/vs/17/release/vc_redist.x64.exe" "vc_redist.x64.exe"
fetch "https://github.com/AlexxIT/go2rtc/releases/download/$GO2RTC_VER/go2rtc_win64.zip" "go2rtc_win64.zip"
fetch "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" "ffmpeg-release-essentials.zip"
fetch "https://downloads.rclone.org/rclone-current-windows-amd64.zip" "rclone-windows-amd64.zip"
fetch "https://nssm.cc/ci/nssm-2.24-101-g897c7ad.zip" "nssm.zip" "https://nssm.cc/release/nssm-2.24.zip"

echo "== 2. Библиотеки Python под Windows (cp312, win_amd64)"
if [ ! -x "$CACHE/uvenv/bin/uv" ]; then
    python3 -m venv "$CACHE/uvenv"
    "$CACHE/uvenv/bin/pip" install --quiet uv
fi
# версии — те же, что проверены на Mac (constraints), набор пакетов — как нужно Windows
"$PROJ/.venv/bin/pip" freeze --exclude-editable > "$WORK/mac-freeze.txt"
"$CACHE/uvenv/bin/uv" pip compile "$PROJ/pyproject.toml" --quiet \
    --python-platform x86_64-pc-windows-msvc --python-version 3.12 \
    --constraint "$WORK/mac-freeze.txt" --no-header --no-annotate -o "$CACHE/requirements.txt"
pipdl() {   # колёса под Windows; --no-deps: список уже полный (uv pip compile)
    "$PROJ/.venv/bin/pip" download --quiet --disable-pip-version-check -r "$CACHE/requirements.txt" \
        --platform win_amd64 --python-version 3.12 --implementation cp --abi cp312 --abi abi3 --abi none \
        --only-binary=:all: --no-deps "$@"
}
pipdl -d "$CACHE/wheels" --find-links "$CACHE/wheels"
echo "  пакетов: $(grep -c '==' "$CACHE/requirements.txt")"

echo "== 3. Сборка папки"
mkdir -p "$STAGE/installer" "$STAGE/payload/bin" "$STAGE/payload/python" "$STAGE/payload/wheels" "$STAGE/payload/models"
cp "$PROJ/deploy/windows/installer/INSTALL.bat" "$PROJ/deploy/windows/installer/UNINSTALL.bat" \
   "$PROJ/deploy/windows/installer/README.txt" "$STAGE/"
cp "$PROJ/deploy/windows/installer/install.ps1" "$PROJ/deploy/windows/installer/uninstall.ps1" "$STAGE/installer/"

unzip -q -j -o "$CACHE/dl/go2rtc_win64.zip" "go2rtc.exe" -d "$STAGE/payload/bin"
unzip -q -j -o "$CACHE/dl/ffmpeg-release-essentials.zip" "*/bin/ffmpeg.exe" -d "$STAGE/payload/bin"
unzip -q -j -o "$CACHE/dl/rclone-windows-amd64.zip" "*/rclone.exe" -d "$STAGE/payload/bin"
unzip -q -j -o "$CACHE/dl/nssm.zip" "*/win64/nssm.exe" -d "$STAGE/payload/bin"
cp "$CACHE/dl/python-$PY_VER-amd64.exe" "$CACHE/dl/vc_redist.x64.exe" "$STAGE/payload/python/"
cp "$CACHE/requirements.txt" "$STAGE/payload/wheels/"
pipdl --no-index --find-links "$CACHE/wheels" -d "$STAGE/payload/wheels"   # только нужные версии, из кеша

for m in open-image-models fast-plate-ocr; do cp -R "$HOME/.cache/$m" "$STAGE/payload/models/"; done

rsync -a --exclude '.venv' --exclude '.git' --exclude '.pytest_cache' --exclude '.superpowers' \
    --exclude '__pycache__' --exclude '.DS_Store' --exclude 'logs' --exclude 'admin_login.txt' \
    --exclude 'data.db*' --exclude '/go2rtc' --exclude 'tests' "$PROJ/" "$STAGE/payload/app/"
sqlite3 "$PROJ/data.db" ".backup '$STAGE/payload/app/data.db'"
( cd "$STAGE/payload/app" && "$PROJ/.venv/bin/python" - <<'EOF'
from app.config import load_config, save_config
c = load_config("config.yaml")
c.host = "0.0.0.0"                     # доступ с телефонов и через Tailscale
c.go2rtc_path = "go2rtc.exe"
c.recording.ffmpeg_path = "ffmpeg.exe"
c.recording.rclone_path = "rclone.exe"
save_config(c, "config.yaml")
EOF
)
TS_TEST="$PROJ/tools/tailscale-test"; [ -d "$TS_TEST" ] || TS_TEST="$PROJ/../tailscale-test"
if [ -d "$TS_TEST" ]; then rsync -a --exclude '.DS_Store' "$TS_TEST/" "$STAGE/tailscale-test/"; fi

for f in go2rtc ffmpeg rclone nssm; do [ -s "$STAGE/payload/bin/$f.exe" ] || { echo "Нет $f.exe" >&2; exit 1; }; done

echo "== 4. Архив"
rm -f "$OUT"
( cd "$WORK" && zip -qr -X "$OUT" "$NAME" )
echo "Готово: $OUT ($(du -h "$OUT" | cut -f1))"
echo "ВНИМАНИЕ: в архиве пароли камер и доступ к Google Диску — не пересылайте его."
