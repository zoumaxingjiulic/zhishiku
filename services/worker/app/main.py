import logging
import os
import time

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


def main() -> None:
    """Temporary long-running process; replaced by the ingestion queue consumer next."""
    logger.info("knowledge-base worker started; ingestion consumer is not implemented yet")
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()

