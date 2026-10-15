"""Точка входа службы: python -m app.main --config config.yaml"""
import argparse
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn

from app.archive_service import ArchiveService
from app.backup import BackupScheduler
from app.config import load_config
from app.db import Database
from app.engine import Engine
from app.go2rtc import Go2rtcProcess
from app.users import UserRepo
from app.visits import VisitRepo
from app.web.app import create_app

log = logging.getLogger("app")


def setup_logging(logs_dir: Path) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(logs_dir / "app.log", maxBytes=10 * 1024 * 1024,
                                       backupCount=10, encoding="utf-8")
    console = logging.StreamHandler()
    for handler in (file_handler, console):
        handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[file_handler, console], force=True)


def stop_all(archive, backups, engine, go2rtc) -> None:
    """Остановить всё; сбой одной части не мешает остальным. go2rtc — последним (держит порты)."""
    for name, part in (("архив", archive), ("резервные копии", backups), ("распознавание", engine),
                       ("go2rtc", go2rtc)):
        if part is None:
            continue
        try:
            part.stop()
        except Exception:
            log.exception("Не удалось остановить: %s", name)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.main")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    cfg.screenshots_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(cfg.logs_dir)
    db = Database(cfg.db_path)
    if UserRepo(db).count() == 0:
        log.warning("Нет пользователей. Создайте админа: "
                    "python -m app.manage --config %s create-user admin --role admin", args.config)

    go2rtc = Go2rtcProcess(cfg)
    go2rtc.start()
    engine = None
    backups = None
    archive = None

    def shutdown() -> None:
        stop_all(archive, backups, engine, go2rtc)

    def restart() -> None:
        # остановить go2rtc до выхода, иначе дочерний процесс удержит порты
        try:
            shutdown()
        finally:
            os._exit(3)  # служба (NSSM) перезапустит программу

    try:
        try:
            from app.recognizer import FastAlprRecognizer  # тяжёлый импорт, грузит модели
            candidate = Engine(cfg, VisitRepo(db), FastAlprRecognizer())
            try:
                candidate.start()
            except Exception:
                candidate.stop()
                raise
            engine = candidate
        except Exception:
            log.exception("Распознавание не запущено, видео и журнал работают без него")
        if any(c.record for c in cfg.cameras):
            try:
                candidate_archive = ArchiveService(cfg, db)
                try:
                    candidate_archive.start()
                except Exception:
                    candidate_archive.stop()
                    raise
                archive = candidate_archive
            except Exception:
                log.exception("Архив не запущен, остальная АП работает")
        backups = BackupScheduler(db, cfg.backups_dir)
        backups.start()
        app = create_app(cfg, args.config, db, engine=engine, restart=restart, archive=archive)
        uvicorn.run(app, host=cfg.host, port=cfg.port, log_config=None)
    finally:
        shutdown()


if __name__ == "__main__":
    main()
