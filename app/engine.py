"""Связывает камеры, распознавание, трекеры и базу."""
import logging
import threading
import time
from datetime import datetime

from app.capture import CameraStatus, CameraStream
from app.config import AppConfig
from app.go2rtc import go2rtc_rtsp_url
from app.plate_fmt import normalize_plate
from app.recognizer import Recognizer
from app.screenshots import save_screenshot
from app.tracker import Entered, Exited, Seen, Tracker
from app.visits import VisitRepo

log = logging.getLogger(__name__)


class Engine:
    def __init__(self, cfg: AppConfig, visits: VisitRepo, recognizer: Recognizer,
                 stream_factory=None, clock=datetime.now):
        stream_factory = stream_factory or (lambda cid, url: CameraStream(cid, url))
        self._cfg, self._visits, self._recognizer, self._clock = cfg, visits, recognizer, clock
        cfg.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self._streams = {c.id: stream_factory(c.id, go2rtc_rtsp_url(c.id)) for c in cfg.cameras}
        now = clock()
        self._trackers: dict[str, Tracker] = {}
        self._open_ids: dict[str, dict[str, int]] = {}
        self._last_frame_at: dict[str, datetime | None] = {}
        self._recognizer_errors: dict[str, bool] = {}
        for cam in cfg.cameras:
            if not cam.recognize:
                continue
            self._last_frame_at[cam.id] = None
            self._recognizer_errors[cam.id] = False
            open_visits = self._dedupe_open(cam.id, visits)
            self._open_ids[cam.id] = {v.plate: v.id for v in open_visits}
            self._trackers[cam.id] = Tracker(cfg.tracker, now, {v.plate: v.last_seen_at for v in open_visits})
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _dedupe_open(camera_id: str, visits: VisitRepo) -> list:
        """Два открытых заезда с одним номером (после ручной правки): старые закрываем
        по их последнему появлению, остаётся только новейший."""
        newest: dict[str, object] = {}
        for v in visits.open_visits(camera_id):  # отсортированы по entered_at
            if (prev := newest.get(v.plate)) is not None:
                log.warning("Дубль открытого заезда %s (%s): закрыт заезд #%s", v.plate, camera_id, prev.id)
                visits.close(prev.id, prev.last_seen_at)
            newest[v.plate] = v
        return list(newest.values())

    def start(self) -> None:
        for stream in self._streams.values():
            stream.start()
        self._thread = threading.Thread(target=self._loop, name="engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        for stream in self._streams.values():
            stream.stop()

    def statuses(self) -> dict[str, CameraStatus]:
        return {cid: s.status() for cid, s in self._streams.items()}

    def process(self, camera_id: str, frame, at: datetime, online: bool) -> None:
        reads = []
        if online and frame is not None:
            try:
                reads = self._recognizer.read(frame)
                self._recognizer_errors[camera_id] = False
            except Exception:
                if not self._recognizer_errors[camera_id]:
                    log.exception("Ошибка распознавания на камере %s", camera_id)
                    self._recognizer_errors[camera_id] = True
                return
        raw_by_plate: dict[str, str] = {}
        for r in reads:
            plate = normalize_plate(r.text)
            if plate:
                raw_by_plate.setdefault(plate, r.text)
        for event in self._trackers[camera_id].update(raw_by_plate.keys(), at, online):
            try:
                self._handle(camera_id, event, frame, raw_by_plate)
            except Exception:
                log.exception("Не удалось сохранить событие %s", event)

    def _handle(self, camera_id: str, event, frame, raw_by_plate: dict[str, str]) -> None:
        ids = self._open_ids[camera_id]
        if isinstance(event, Entered):
            name = ""
            try:
                name = save_screenshot(self._cfg.screenshots_dir, event.at, frame)
            except Exception:
                log.exception("Не удалось сохранить скриншот для %s", event.plate)
            ids[event.plate] = self._visits.create(
                event.plate, raw_by_plate.get(event.plate, event.plate), camera_id, event.at, name)
            log.info("Заезд %s (%s), скриншот %s", event.plate, camera_id, name)
        elif isinstance(event, Seen):
            if (vid := ids.get(event.plate)) is not None:
                self._visits.touch(vid, event.at)
        elif isinstance(event, Exited):
            if (vid := ids.pop(event.plate, None)) is not None:
                self._visits.close(vid, event.at)
                log.info("Выезд %s (%s) в %s", event.plate, camera_id, event.at)

    def _tick(self, camera_id: str) -> None:
        stream = self._streams[camera_id]
        frame, frame_at = stream.latest()
        online = stream.status().online
        if online and (frame_at is None or frame_at == self._last_frame_at[camera_id]):
            return  # no new frame yet
        if online:
            self._last_frame_at[camera_id] = frame_at
        try:
            self.process(camera_id, frame if online else None, self._clock(), online)
        except Exception:
            log.exception("Ошибка обработки камеры %s", camera_id)

    def _loop(self) -> None:
        interval = 1.0 / self._cfg.analyze_fps
        while not self._stop.is_set():
            started = time.monotonic()
            for camera_id in self._trackers:
                self._tick(camera_id)
            self._stop.wait(max(0.0, interval - (time.monotonic() - started)))
