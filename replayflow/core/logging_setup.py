from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

_RESERVED = set(vars(logging.makeLogRecord({})))


class JsonFormatter(logging.Formatter):
    """Uma linha JSON por evento; campos de `extra=` viram chaves."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            **{k: v for k, v in vars(record).items() if k not in _RESERVED},
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def attach_session_log(log_file: Path) -> logging.Handler:
    """Liga o log da sessão ao logger raiz do pacote. Retorna o handler para remoção."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("replayflow")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return handler


def detach(handler: logging.Handler) -> None:
    logging.getLogger("replayflow").removeHandler(handler)
    handler.close()
