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
        row = conn.execute(
            "SELECT MAX(timestamp) AS ts FROM messages "
            "WHERE session_id=? AND role IN ('user','assistant')",
            (session_id,)).fetchone()
        return row[0] if row and row[0] is not None else None
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
