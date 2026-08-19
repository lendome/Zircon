import logging
import logging.handlers
from pathlib import Path

from zirconAgent.core.logging_config import setup_logging


class TestSetupLogging:
    def test_creates_log_dir(self, tmp_path):
        log_file = setup_logging(tmp_path)
        assert log_file.parent.exists()
        assert log_file.name == "agent.log"

    def test_log_file_writable(self, tmp_path):
        log_file = setup_logging(tmp_path)
        logger = logging.getLogger("agent.test_sub")
        logger.info("test message")
        for h in logger.handlers:
            h.flush()
        for h in logging.getLogger("agent").handlers:
            h.flush()
        assert log_file.name == "agent.log"
        assert ".zircon-code" in str(log_file) or ".zircon-code" in str(log_file).replace("\\", "/")

    def test_console_handler(self, tmp_path):
        root = logging.getLogger("agent")
        root.handlers.clear()
        setup_logging(tmp_path, console=True)
        assert any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.handlers.RotatingFileHandler)
                    for h in root.handlers)
        root.handlers.clear()

    def test_no_duplicate_handlers(self, tmp_path):
        root = logging.getLogger("agent")
        root.handlers.clear()
        setup_logging(tmp_path)
        setup_logging(tmp_path)
        count = len(root.handlers)
        root.handlers.clear()
        assert count == 1
