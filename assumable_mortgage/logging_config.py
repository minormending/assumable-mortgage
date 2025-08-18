import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Optional


def setup_logging(level: Optional[str] = None, json_logs: bool | None = None) -> None:
    """Configure application logging.

    - Level can be provided via arg or `APP_LOG_LEVEL` env (default INFO).
    - JSON logging controlled by arg or `APP_JSON_LOGS` env (default False).
    """
    level_name = (level or os.getenv("APP_LOG_LEVEL", "INFO")).upper()
    log_level = getattr(logging, level_name, logging.INFO)

    use_json = (
        json_logs
        if json_logs is not None
        else os.getenv("APP_JSON_LOGS", "false").lower() in {"1", "true", "yes"}
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(log_level)

    if use_json:
        fmt = "{" + ", ".join(
            [
                '"level": "%(levelname)s"',
                '"time": "%(asctime)s"',
                '"name": "%(name)s"',
                '"message": "%(message)s"',
            ]
        ) + "}"
    else:
        fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"

    # Console handler
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(fmt))
    root.addHandler(handler)

    # Optional file handler for longer retention if APP_LOG_FILE is set
    log_file = os.getenv("APP_LOG_FILE")
    if log_file:
        file_handler = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=2)
        file_handler.setFormatter(logging.Formatter(fmt))
        root.addHandler(file_handler)
