"""wiki_v3_sweep_loader.py — cron-обход wiki-памяти v3.

Запускает фоновый индексатор v3 (без --session → до MAX_SESSIONS_PER_RUN=5
завершённых сессий за запуск). Env-переменные заданы здесь, чтобы
индексация шла локально через LM Studio (не NVIDIA).

Cron резолвит скрипты из ~/AppData/Local/hermes/scripts/.
"""
import os
import subprocess
import sys

# Рабочие скрипты v3 живут в scripts/wiki_v2 (родитель пакета wiki_v2).
# Приоритет: HERMES_HOME/scripts, иначе — директория, где лежит этот скрипт.
_here = os.path.abspath(os.path.dirname(__file__))
SCRIPTS = os.path.join(os.environ.get("HERMES_HOME", ""), "scripts") if os.environ.get("HERMES_HOME") else _here
if not os.path.isdir(os.path.join(SCRIPTS, "wiki_v2")):
    SCRIPTS = _here  # cron мог вызвать без HERMES_HOME — берём реальную папку файла

PYTHON = sys.executable

# v3: индексация локально через LM Studio. Наследуемся от окружения Hermes,
# ВСЕ эндпоинты берём из единого конфига endpoints.yaml (wiki_v2.endpoints.apply),
# а не хардкодим здесь (не перезаписываем существующее).
env = os.environ.copy()
try:
    if SCRIPTS not in sys.path:
        sys.path.insert(0, SCRIPTS)
    from wiki_v2.endpoints import apply as _endpoints_apply
    _endpoints_apply(env)
except Exception as exc:
    print(f"wiki_v3_sweep: не удалось применить endpoints.yaml (продолжаем): {exc}")

# v3: chat/extract идёт по единому конфигу endpoints.yaml (сейчас — облако
# NVIDIA nemotron-3-super-120b-a12b, без параллелизма и с паузой между вызовами).
# Перед запуском индексатора проверяем доступность chat-модели: если она в
# rate-limit/блокировке/недоступна — НЕ запускаем прогон (иначе весь прогон уйдёт
# в fallback и продолжит долбить заблокированную модель). Fail-open: probe сбоит → пропуск.
try:
    sys.path.insert(0, SCRIPTS)
    from wiki_v2.gateway import chat_available
    if not chat_available():
        print("wiki_v3_sweep: chat-модель недоступна/в rate-limit — пропускаю запуск")
        sys.exit(0)
    print("wiki_v3_sweep: chat-модель доступна")
except SystemExit:
    raise
except Exception as exc:
    print(f"wiki_v3_sweep: не удалось проверить chat-модель (пропускаю запуск): {exc}")
    sys.exit(0)

cmd = [PYTHON, "-m", "wiki_v2.indexer"]

# Фоновый режим: без session_id → индексатор сам берёт до 5 завершённых сессий.
# runpy'ом не запускаем (у subprocess чище env и не блокирует cron).
proc = subprocess.Popen(
    cmd,
    cwd=SCRIPTS,
    env=env,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    close_fds=True,
)
print(f"wiki_v3_sweep: запущен индексатор (pid={proc.pid}) из {SCRIPTS}")
