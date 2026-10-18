"""Gunicorn entry point. Run with ONE worker so the scheduler starts exactly once."""

import logging
import os

from mesabot.app import create_app

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = create_app()
