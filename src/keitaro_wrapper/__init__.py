"""Обёртка над Keitaro Admin API (тестовое задание ETOLEGKO)."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version

try:
    # Единственный источник версии — `pyproject.toml`: дублировать её здесь не нужно
    __version__ = package_version("keitaro-wrapper")
except PackageNotFoundError:  # запуск из исходников без установки пакета
    __version__ = "0.0.0"
