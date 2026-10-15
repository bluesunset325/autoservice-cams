"""Логика заезд / пребывание / выезд по каждому номеру. Без ввода-вывода."""
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.config import TrackerConfig


@dataclass(frozen=True)
class Entered:
    plate: str
    at: datetime


@dataclass(frozen=True)
class Seen:
    plate: str
    at: datetime


@dataclass(frozen=True)
class Exited:
    plate: str
    at: datetime


Event = Entered | Seen | Exited


@dataclass
class _Track:
    last_seen: datetime
    absence_from: datetime  # с какого момента считаем, что номер не виден
    last_written: datetime  # когда последний раз отдавали Seen


class Tracker:
    def __init__(self, cfg: TrackerConfig, now: datetime,
                 open_visits: dict[str, datetime] | None = None):
        self._window = timedelta(seconds=cfg.confirm_window_s)
        self._exit_after = timedelta(seconds=cfg.exit_after_s)
        self._seen_every = timedelta(seconds=cfg.seen_write_interval_s)
        self._confirm_reads = cfg.confirm_reads
        self._candidates: dict[str, deque[datetime]] = {}
        self._tracks: dict[str, _Track] = {
            plate: _Track(last_seen, max(last_seen, now), last_seen)
            for plate, last_seen in (open_visits or {}).items()
        }
        self._online = True

    @property
    def in_box(self) -> set[str]:
        return set(self._tracks)

    def update(self, plates: Iterable[str], at: datetime, online: bool = True) -> list[Event]:
        events: list[Event] = []
        if online and not self._online:
            for track in self._tracks.values():
                track.absence_from = max(track.absence_from, at)
        self._online = online

        for plate in set(plates):
            track = self._tracks.get(plate)
            if track is not None:
                track.last_seen = at
                track.absence_from = at
                if at - track.last_written >= self._seen_every:
                    track.last_written = at
                    events.append(Seen(plate, at))
                continue
            reads = self._candidates.setdefault(plate, deque())
            reads.append(at)
            self._drop_old(reads, at)
            if len(reads) >= self._confirm_reads:
                del self._candidates[plate]
                self._tracks[plate] = _Track(at, at, at)
                events.append(Entered(plate, reads[0]))

        for plate in list(self._candidates):
            self._drop_old(self._candidates[plate], at)
            if not self._candidates[plate]:
                del self._candidates[plate]

        if online:
            for plate, track in list(self._tracks.items()):
                if at - track.absence_from >= self._exit_after:
                    del self._tracks[plate]
                    events.append(Exited(plate, track.last_seen))
        return events

    def _drop_old(self, reads: deque[datetime], at: datetime) -> None:
        while reads and at - reads[0] > self._window:
            reads.popleft()
