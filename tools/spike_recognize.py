"""Проверка распознавания номеров на кадрах камеры или на фото.

    python tools/spike_recognize.py "rtsp://..." --frames 20 --interval 1
    python tools/spike_recognize.py photo1.jpg photo2.jpg
Кадры сохраняются в spike_out/ для просмотра.
"""
import argparse
import time
from pathlib import Path

import cv2
from fast_alpr import ALPR

DETECTOR = "yolo-v9-t-384-license-plate-end2end"
OCR = "cct-xs-v1-global-model"


def recognize(alpr: ALPR, frame) -> str:
    started = time.perf_counter()
    results = alpr.predict(frame)
    ms = (time.perf_counter() - started) * 1000
    texts = [f"{r.ocr.text} (conf={r.ocr.confidence})" for r in results if r.ocr]
    return f"{frame.shape[1]}x{frame.shape[0]}, {ms:.0f} мс -> {texts or 'номер не найден'}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sources", nargs="+", help="RTSP-адрес или файлы изображений")
    ap.add_argument("--frames", type=int, default=20)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--out", default="spike_out")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(exist_ok=True)
    alpr = ALPR(detector_model=DETECTOR, ocr_model=OCR)

    if args.sources[0].startswith("rtsp://"):
        cap = cv2.VideoCapture(args.sources[0], cv2.CAP_FFMPEG)
        if not cap.isOpened():
            raise SystemExit("Не удалось открыть поток")
        for i in range(args.frames):
            for _ in range(5):  # выкинуть накопившиеся в буфере кадры
                cap.grab()
            ok, frame = cap.read()
            if not ok:
                print(f"{i:02d}: кадр не получен")
                continue
            print(f"{i:02d}: {recognize(alpr, frame)}")
            cv2.imwrite(str(out / f"frame_{i:02d}.jpg"), frame)
            time.sleep(args.interval)
        cap.release()
    else:
        for src in args.sources:
            frame = cv2.imread(src)
            if frame is None:
                print(f"{src}: не удалось прочитать")
                continue
            print(f"{src}: {recognize(alpr, frame)}")


if __name__ == "__main__":
    main()
