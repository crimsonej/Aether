try:
    import structlog
except ImportError:
    structlog = None

import logging
import sys
from pathlib import Path

def setup_logging(log_level="INFO", log_file="aether/logs/system.json"):
    """
    Configures structured JSON logging for the Aether platform.

    Args:
        log_level (str): The logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_file (str): Path to the JSON log file.
    """
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if structlog:
        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.JSONRenderer()
            ],
            logger_factory=structlog.PrintLoggerFactory(),
        )
        logger_instance = structlog.get_logger()
    else:
        # Fallback to standard logging
        logging.basicConfig(
            format="%(asctime)s %(levelname)s %(message)s",
            level=log_level,
            stream=sys.stdout
        )
        logger_instance = logging.getLogger("aether")

    return logger_instance

# Default logger instance
if structlog:
    logger = structlog.get_logger()
else:
    class SimpleLogger:
        def __init__(self, name="aether"):
            self._log = logging.getLogger(name)
        def _log_with_kwargs(self, level, msg, *args, **kwargs):
            if kwargs:
                extra = " ".join(f"{k}={v}" for k, v in kwargs.items())
                msg = f"{msg} | {extra}"
            self._log.log(level, msg, *args)
        def info(self, msg, *args, **kwargs):
            self._log_with_kwargs(logging.INFO, msg, *args, **kwargs)
        def warning(self, msg, *args, **kwargs):
            self._log_with_kwargs(logging.WARNING, msg, *args, **kwargs)
        def warn(self, msg, *args, **kwargs):
            self.warning(msg, *args, **kwargs)
        def error(self, msg, *args, **kwargs):
            self._log_with_kwargs(logging.ERROR, msg, *args, **kwargs)
        def debug(self, msg, *args, **kwargs):
            self._log_with_kwargs(logging.DEBUG, msg, *args, **kwargs)
    logger = SimpleLogger()
