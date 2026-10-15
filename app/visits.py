"""Заезды: хранение, выборка за день, группировка по номеру, поиск."""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.db import Database, from_ts, to_ts


@dataclass(frozen=True)
class Visit:
    id: int
    plate: str
    plate_raw: str
    camera: str
    entered_at: datetime
    last_seen_at: datetime
    exited_at: datetime | None
    screenshot: str
    edited_by: str | None

    def duration(self, now: datetime) -> timedelta:
        return (self.exited_at or now) - self.entered_at


@dataclass(frozen=True)
class DayGroup:
    plate: str
    visits: list[Visit]
    first_entry: datetime
    last_exit: datetime | None  # None — машина сейчас в боксе
    total: timedelta
    from_prev_day: bool

    @property
    def count(self) -> int:
        return len(self.visits)

    @property
    def in_box(self) -> bool:
        return self.last_exit is None


def _visit(row) -> Visit:
    return Visit(
        id=row["id"], plate=row["plate"], plate_raw=row["plate_raw"], camera=row["camera"],
        entered_at=from_ts(row["entered_at"]), last_seen_at=from_ts(row["last_seen_at"]),
        exited_at=from_ts(row["exited_at"]), screenshot=row["screenshot"],
        edited_by=row["edited_by"],
    )


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time())


class VisitRepo:
    def __init__(self, db: Database):
        self._db = db

    def create(self, plate: str, plate_raw: str, camera: str, entered_at: datetime,
               screenshot: str) -> int:
        return self._db.insert(
            "INSERT INTO visits (plate, plate_raw, camera, entered_at, last_seen_at, screenshot)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (plate, plate_raw, camera, to_ts(entered_at), to_ts(entered_at), screenshot),
        )

    def touch(self, visit_id: int, at: datetime) -> None:
        self._db.execute("UPDATE visits SET last_seen_at = ? WHERE id = ?", (to_ts(at), visit_id))

    def close(self, visit_id: int, at: datetime) -> None:
        self._db.execute(
            "UPDATE visits SET last_seen_at = ?, exited_at = ? WHERE id = ?",
            (to_ts(at), to_ts(at), visit_id),
        )

    def get(self, visit_id: int) -> Visit | None:
        rows = self._db.query("SELECT * FROM visits WHERE id = ?", (visit_id,))
        return _visit(rows[0]) if rows else None

    def open_visits(self, camera: str) -> list[Visit]:
        rows = self._db.query(
            "SELECT * FROM visits WHERE camera = ? AND exited_at IS NULL ORDER BY entered_at",
            (camera,),
        )
        return [_visit(r) for r in rows]

    def for_day(self, day: date) -> list[Visit]:
        start = _day_start(day)
        end = start + timedelta(days=1)
        rows = self._db.query(
            "SELECT * FROM visits WHERE entered_at < ?"
            " AND (entered_at >= ? OR exited_at IS NULL OR exited_at >= ?)"
            " ORDER BY entered_at",
            (to_ts(end), to_ts(start), to_ts(start)),
        )
        return [_visit(r) for r in rows]

    def search(self, query: str, date_from: date | None = None, date_to: date | None = None,
               limit: int = 500) -> list[Visit]:
        sql = "SELECT * FROM visits WHERE plate LIKE ?"
        params: list = [f"%{query}%"]
        if date_from:
            sql += " AND entered_at >= ?"
            params.append(to_ts(_day_start(date_from)))
        if date_to:
            sql += " AND entered_at < ?"
            params.append(to_ts(_day_start(date_to + timedelta(days=1))))
        sql += " ORDER BY entered_at DESC LIMIT ?"
        params.append(limit)
        return [_visit(r) for r in self._db.query(sql, params)]

    def edit_plate(self, visit_id: int, plate: str, user_login: str, at: datetime) -> None:
        self._db.execute(
            "UPDATE visits SET plate = ?, edited_by = ?, edited_at = ? WHERE id = ?",
            (plate, user_login, to_ts(at), visit_id),
        )

    def delete(self, visit_id: int) -> None:
        self._db.execute("DELETE FROM visits WHERE id = ?", (visit_id,))


def group_by_plate(visits: list[Visit], day: date, now: datetime) -> list[DayGroup]:
    by_plate: dict[str, list[Visit]] = {}
    for v in visits:
        by_plate.setdefault(v.plate, []).append(v)
    groups = []
    for plate, items in by_plate.items():
        items.sort(key=lambda v: v.entered_at)
        exits = [v.exited_at for v in items]
        groups.append(DayGroup(
            plate=plate,
            visits=items,
            first_entry=items[0].entered_at,
            last_exit=None if any(e is None for e in exits) else max(exits),
            total=sum((v.duration(now) for v in items), timedelta()),
            from_prev_day=items[0].entered_at < _day_start(day),
        ))
    return groups


_SORT_KEYS = {
    "entry": lambda g: g.first_entry,
    "exit": lambda g: g.last_exit or datetime.max,
    "duration": lambda g: g.total,
}


def sort_groups(groups: list[DayGroup], key: str = "entry", desc: bool = False) -> list[DayGroup]:
    return sorted(groups, key=_SORT_KEYS.get(key, _SORT_KEYS["entry"]), reverse=desc)
