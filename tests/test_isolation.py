"""Проверка изоляции тестов от машины разработчика."""

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_config_tests_ignore_environment() -> None:
    """`LOG_LEVEL`/`DATABASE_URL` из shell не должны ронять тесты настроек.

    Регрессия блокера Б1: эти переменные протекали в `Settings`, потому что
    conftest гасил только `KEITARO_*`. Прогон во вложенном процессе повторяет
    именно тот сценарий, который ломал ревью, — «грязный» shell.
    """
    env = {
        **os.environ,
        "LOG_LEVEL": "verbose",  # невалидный уровень: до правки валил весь файл
        "DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/other",
    }
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "tests/test_config.py",
    ]

    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
