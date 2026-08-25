# session_status.py
"""Определение «завершённости» сессии по простою (нет новых сообщений > порог).

Сессия считается завершённой, если с последнего сообщения прошло больше
`idle_minutes`. Это НАША метрика, не зависящая от того, как Hermes закрыл сессию.
"""
import os
import sqlite3
import time

DEFAULT_IDLE_MINUTES = 32


def last_message_ts(state_db: str, session_id: str):
    """Вернуть timestamp последнего сообщения сессии (user/assistant) или None."""
    if not os.path.exists(state_db):
        return None
    conn = sqlite3.connect(state_db)
    try:
        # Проверяем тип колонки timestamp через PRAGMA table_info
        cursor = conn.execute("PRAGMA table_info(messages)")
        columns = {row[1]: row[2].upper() for row in cursor.fetchall()}
        ts_type = columns.get('timestamp', '')

        if 'TEXT' in ts_type:
            # Если TEXT (ISO 8601), используем strftime для получения epoch
            query = (
                "SELECT MAX(strftime('%s', timestamp)) AS ts FROM messages "
                "WHERE session_id=? AND role IN ('user','assistant')"
            )
        else:
            # Если REAL/INTEGER или неизвестно, используем текущий подход
            query = (
                "SELECT MAX(timestamp) AS ts FROM messages "
                "WHERE session_id=? AND role IN ('user','assistant')"
            )

        row = conn.execute(query, (session_id,)).fetchone()
        if row and row[0] is not None:
            try:
                return float(row[0])
            except (ValueError, TypeError):
                return None
        return None
    except Exception:
        # Fail-open: при любой ошибке возвращаем None
        return None
    finally:
        conn.close()


def is_session_finished(last_msg_ts, now: float = None,
                        idle_minutes: int = DEFAULT_IDLE_MINUTES) -> bool:
    """True, если сессия «завершена» (простой >= idle_minutes) или метки нет."""
    if last_msg_ts is None:
        return True
    now = time.time() if now is None else now
    idle_seconds = (now - last_msg_ts)
    return idle_seconds >= idle_minutes * 60
