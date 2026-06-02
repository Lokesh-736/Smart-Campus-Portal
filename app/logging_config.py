import logging
import os


def setup_logging():
    log_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_path),
            logging.StreamHandler(),
        ],
        force=True,
    )
    return logging.getLogger("smart_campus")
