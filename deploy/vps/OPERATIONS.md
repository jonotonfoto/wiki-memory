# VPS Operations Runbook (wiki v3)

> Операционный рецепт обновления и диагностики wiki v3 на VPS.
> Хост далее `<VPS_HOST>` (конкретный адрес не хранится в репо — см. локальные
> доки проекта / панель хостинга). Доступ: ключевой SSH (`root`), секреты в
> репо не заносятся.

## Карта сервера

| Что | Путь на хосте |
|---|---|
| Клон репо (обновление = git pull) | `/opt/hermes-data/wiki-memory` |
| Рантайм wiki_v2 | `/opt/hermes-data/scripts/wiki_v2` |
| Рантайм-плагины | `/opt/hermes-data/plugins/<имя>` |
| Маунт в контейнер | `/opt/hermes-data` => `/opt/data` (`HERMES_HOME=/opt/data`) |
| Контейнер hermes | compose-проект в `/opt/hermes-data` |

Важно: путь `/opt/data/...` существует только ВНУТРИ контейнера. Команды на
хосте всегда используют `/opt/hermes-data/...`.

## Обновление кода (стандартная процедура)

1. На десктопе: правка живого кода → зеркало → `tools/sync_to_repo.py --check`
   («расхождений нет») → `--apply` → коммит+пуш в main.
2. На VPS:
   ```bash
   cd /opt/hermes-data/wiki-memory && git pull --ff-only
   ```
3. Перенос дельты клон → рантайм: бэкап `.bak.<YYYYMMDD_HHMMSS>` рядом,
   копия из клона, `chown 10000:10000`, MD5-сверка клон↔рантайм.
4. Лимит бэкапов ≤2 на файл: `ls -1t dir/*.bak.* | tail -n +3 | xargs -r rm -f`.
5. Синтаксис: `docker exec hermes python -m py_compile <файл>`.
6. Рестарт: `docker restart hermes` (Telegram-сессии кратко оборвутся).
7. Верификация: `docker logs hermes --since 120s` — регистрация плагинов,
   отсутствие Traceback.

## Известные особенности (не баги)

- **Healthcheck `unhealthy` по :8642** — норма, состояние контейнера не
  отражает (наблюдается месяцами при рабочем Telegram/dashboard).
- **«Previous gateway life exited UNCLEANLY»** после рестарта — ожидаемо
  (SIGKILL при `docker restart`).
- **Нет numpy/requests в контейнере**: семантический канал session-плагинов
  уходит в legacy-fallback (`АР-6 failed (No module named 'numpy')`) —
  fail-open по дизайну, контекст всё равно вставляется. Установка numpy в
  контейнер — отдельное решение (пропадёт при recreate).
- **RAM 2 ГБ**: перед установкой тяжёлых сервисов мерить память
  (`free -h`, `docker stats --no-stream`) — риск OOM-killer.

## Питфоллы подключения (Windows-клиент)

- **CRLF**: скрипты, подготовленные в PowerShell, ломают bash
  (`$'\r': command not found`, `ambiguous redirect`). Лечение:
  `ssh root@<VPS_HOST> "tr -d '\r' | bash -s"` < script.sh.
- **Вложенные кавычки**: `sh -c "grep -c "x" f"` ломается; использовать
  `docker exec hermes grep -c 'паттерн' файл` без sh -c и heredoc `<<'EOF'`.
- Ключевая авторизация предпочтительнее парольной; при сбросе доступа через
  панель хостинга — восстановить пароль из локальных данных и СРАЗУ
  переустановить публичный ключ. Пароли в репо/логах не хранить.
