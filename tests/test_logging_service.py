import logging

from app.services.logging_service import setup_logging


def test_setup_logging_writes_to_file(tmp_path):
    log_file = setup_logging(tmp_path / "logs", "DEBUG")
    logging.getLogger("test").info("hello")
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert log_file.exists()
    assert "hello" in log_file.read_text(encoding="utf-8")

    root = logging.getLogger()
    for handler in list(root.handlers):
        handler.close()
        root.removeHandler(handler)
