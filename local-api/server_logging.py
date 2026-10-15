from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading
from pathlib import Path
from typing import TextIO

from maintenance import trim_file_to_tail

DEFAULT_SERVER_LOG_MAX_BYTES = 2 * 1024 * 1024
DEFAULT_SERVER_LOG_BACKUP_COUNT = 2


class _LineLogger(TextIO):
    def __init__(self, logger: logging.Logger, level: int) -> None:
        self._logger = logger
        self._level = level
        self._buffer = ""
        self._lock = threading.Lock()

    @property
    def encoding(self) -> str:
        return "utf-8"

    def writable(self) -> bool:
        return True

    def write(self, value: str) -> int:
        text = str(value or "")
        if not text:
            return 0
        with self._lock:
            self._buffer += text
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                line = line.rstrip("\r")
                if line:
                    self._logger.log(self._level, "%s", line)
        return len(text)

    def flush(self) -> None:
        with self._lock:
            line = self._buffer.rstrip("\r\n")
            self._buffer = ""
            if line:
                self._logger.log(self._level, "%s", line)

    def isatty(self) -> bool:
        return False


def configure_server_process_logging() -> logging.Logger | None:
    raw_path = str(os.environ.get("LOCAL_VOICE_SERVER_LOG") or "").strip()
    if not raw_path:
        return None

    path = Path(raw_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    max_bytes = DEFAULT_SERVER_LOG_MAX_BYTES
    backup_count = DEFAULT_SERVER_LOG_BACKUP_COUNT
    trim_file_to_tail(path, max_bytes)
    for index in range(1, backup_count + 1):
        trim_file_to_tail(path.with_name(f"{path.name}.{index}"), max_bytes)

    logger = logging.getLogger("local-voice-bridge-server")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.handlers.RotatingFileHandler(
        path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s [server] %(levelname)s %(message)s"))
    logger.addHandler(handler)
    sys.stdout = _LineLogger(logger, logging.INFO)
    sys.stderr = _LineLogger(logger, logging.ERROR)
    return logger
