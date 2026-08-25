"""Изоляция тестов от живой системы (2026-08-24).

``logging_setup`` и ``metrics`` резолвят путь к файлам лениво — при каждом
вызове через ``config.HERMES_HOME``.  Тесты, которые не патчат ``HERMES_HOME``
(или зовут ``setup_logging()`` без аргументов), писали в ЖИВЫЕ файлы:
``~/hermes/wiki/logs/wiki_v2.log`` и ``~/hermes/wiki/wiki_metrics.jsonl``.
Из-за этого индикаторы дашборда (api_errors_24h / api_state) завышались
тестовыми «connection refused», а лог засорялся записями «Test Session».

Autouse-фикстура перенаправляет ОБА резолвера в ``tmp_path`` на время теста.
Порядок с автозадачами внутри тестовых модулей: фикстуры из conftest
инстанцируются раньше, поэтому модульные фикстуры видят уже изолированные
пути и могут их переопределять (например, через cfg.reload()).
"""
from pathlib import Path

import pytest

from wiki_v2 import logging_setup, metrics


@pytest.fixture(autouse=True)
def isolate_logs_and_metrics(tmp_path):
    orig_log_path = logging_setup._log_file_path
    orig_metrics_path = metrics._metrics_path

    def _tmp_metrics_path() -> Path:
        d = tmp_path / "wiki"
        d.mkdir(parents=True, exist_ok=True)
        return d / "wiki_metrics.jsonl"

    logging_setup._log_file_path = lambda: tmp_path / "logs" / "wiki_v2.log"
    metrics._metrics_path = _tmp_metrics_path

    # Пересобрать хендлеры, чтобы уже открытый живой файл-хендлер закрыть.
    logging_setup.reset_logging()
    logging_setup.setup_logging()

    yield

    fh = logging_setup._file_handler
    if fh is not None:
        fh.close()
    logging_setup.reset_logging()

    logging_setup._log_file_path = orig_log_path
    metrics._metrics_path = orig_metrics_path
