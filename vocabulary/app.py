import argparse
import logging
import os
from pathlib import Path
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from .storage import Repository
from .theme import apply_theme
from .window import MainWindow


def default_data_dir():
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "kkMemorizeVocabulary"


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线单词本")
    parser.add_argument("--data-dir", type=Path, default=None, help="数据库和日志目录")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("kkMemorizeVocabulary")
    app.setOrganizationName("kkMemorizeVocabulary")
    apply_theme(app)
    data_dir = args.data_dir or default_data_dir()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        from logging.handlers import RotatingFileHandler
        handler = RotatingFileHandler(data_dir / "application.log", maxBytes=1_000_000, backupCount=2,
                                      encoding="utf-8")
        logging.basicConfig(handlers=[handler], level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(message)s", force=True)
        repository = Repository(data_dir / "vocabulary.sqlite3")
        window = MainWindow(repository)
    except Exception as error:
        QMessageBox.critical(None, "无法启动单词本", f"无法读取程序数据：\n{error}\n\n可用 --data-dir 指定其他可写目录。")
        return 1

    def exception_hook(kind, value, traceback):
        logging.error("Unhandled application error", exc_info=(kind, value, traceback))
        QMessageBox.critical(window, "操作未完成", f"操作遇到问题，当前输入会保留。\n{value}")

    sys.excepthook = exception_hook
    window.show()
    if args.smoke_test:
        if args.data_dir is None:
            raise ValueError("冒烟测试必须指定隔离的 --data-dir。")
        from .smoke import run_smoke
        from PySide6.QtCore import QTimer
        QTimer.singleShot(200, lambda: run_smoke(app, window, data_dir))
    try:
        return app.exec()
    finally:
        repository.close()

