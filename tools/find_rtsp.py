"""Перебирает типовые RTSP-адреса китайских камер и показывает, какой отвечает.

Запуск:
    python tools/find_rtsp.py 192.168.1.50 --user admin --password 123456
"""
import argparse

import cv2

PATHS = [
    "/live/ch00_0", "/live/ch00_1",          # V380
    "/onvif1", "/onvif2",                    # Yoosee
    "/11", "/12",                            # CamHi
    "/user={user}&password={password}&channel=1&stream=0.sdp",  # XMEye / iCSee
    "/stream1", "/stream2",                  # Tapo и др.
    "/h264", "/live", "/ch0_0.h264", "/live/main",
    "/cam/realmonitor?channel=1&subtype=0",  # Dahua-совместимые
    "/Streaming/Channels/101",               # Hikvision-совместимые
    "/",
]
PORTS = [554, 8554, 10554]


def try_url(url: str) -> tuple[int, int] | None:
    cap = cv2.VideoCapture(
        url, cv2.CAP_FFMPEG,
        [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 4000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 4000],
    )
    try:
        if not cap.isOpened():
            return None
        ok, frame = cap.read()
        return (frame.shape[1], frame.shape[0]) if ok else None
    finally:
        cap.release()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ip")
    ap.add_argument("--user", default="")
    ap.add_argument("--password", default="")
    args = ap.parse_args()
    auth = f"{args.user}:{args.password}@" if args.user else ""
    found = []
    for port in PORTS:
        for path in PATHS:
            url = f"rtsp://{auth}{args.ip}:{port}{path.format(user=args.user, password=args.password)}"
            print(f"пробую {url} ...", end=" ", flush=True)
            size = try_url(url)
            print(f"OK {size[0]}x{size[1]}" if size else "нет")
            if size:
                found.append((url, size))
    print("\nРабочие адреса:" if found else "\nНичего не нашлось.")
    for url, (w, h) in found:
        print(f"  {url}  ({w}x{h})")


if __name__ == "__main__":
    main()
