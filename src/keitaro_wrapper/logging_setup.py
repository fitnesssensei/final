"""Настройка логирования приложения по `LOG_LEVEL`."""

import logging

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(level: str) -> None:
    """Применяет `LOG_LEVEL` к корневому логгеру и к логгерам пакета.

    `basicConfig` не перебивает обработчики uvicorn (`force=False`), поэтому
    уровень дополнительно выставляется на корневом логгере: без этого
    `LOG_LEVEL` из `.env` ни на что не влиял бы при запуске через uvicorn.
    """
    numeric_level = logging.getLevelNamesMapping()[level.upper()]
    logging.basicConfig(level=numeric_level, format=LOG_FORMAT)
    logging.getLogger().setLevel(numeric_level)
    logging.getLogger("keitaro_wrapper").setLevel(numeric_level)
