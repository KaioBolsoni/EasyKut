"""
Configuração centralizada de logging estruturado com rotação de arquivos (RotatingFileHandler)
e rastreabilidade por Correlation ID (request_id / job_id) via contextvars.
"""

from contextvars import ContextVar
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from core.config import LOGS_DIR

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_LOG_FORMAT = "%(asctime)s [%(levelname)s] [req=%(request_id)s] %(name)s: %(message)s"


class RequestIdFilter(logging.Filter):
    """Injeta o correlation ID ativo do contexto atual em cada registro de log."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def configure_logging(
    logs_dir: Optional[Path] = None,
    level: int = logging.INFO,
    log_filename: str = "easykut.log",
) -> Path:
    """
    Configura logging para console e arquivo rotativo em data/logs/easykut.log (5 MB x 5 backups).
    Idempotente: evita duplicar handlers se chamado múltiplas vezes.
    """
    target_dir = Path(logs_dir).resolve() if logs_dir is not None else LOGS_DIR.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    log_file = target_dir / log_filename

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    formatter = logging.Formatter(_LOG_FORMAT)
    req_filter = RequestIdFilter()

    has_console = any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)
        for h in root_logger.handlers
    )
    if not has_console:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        stream_handler.addFilter(req_filter)
        root_logger.addHandler(stream_handler)

    resolved_file_str = str(log_file)
    has_rotating = any(
        isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", None) == resolved_file_str
        for h in root_logger.handlers
    )
    if not has_rotating:
        file_handler = RotatingFileHandler(
            filename=resolved_file_str,
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(req_filter)
        root_logger.addHandler(file_handler)

    for handler in root_logger.handlers:
        if not any(isinstance(f, RequestIdFilter) for f in handler.filters):
            handler.addFilter(req_filter)

    return log_file
