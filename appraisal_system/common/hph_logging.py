"""Process-wide logging setup; import as ``from common.hph_logging import logging``."""

import logging
import os


if not logging.getLogger().handlers:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

__all__ = ["logging"]
