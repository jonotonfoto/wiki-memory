"""Wiki 3 — догонка эмбеддингов для страниц с vecs=0 (cron, no_agent).

Скрипт-обёртка для cron-задачи Hermes. Каждый тик находит страницы без
core-эмбеддингов (title/summary) в .index_v2.db и пересчитывает для них
векторы через единый эндпоинт embed из endpoints.yaml (сейчас — llamaserver/CPU,
догонка после ``embed connection refused``).

Поведение вывода (watchdog-паттерн):
  - страниц к догонке нет  -> пустой stdout (тихо, ничего не сообщаем)
  - есть страницы          -> список + сколько догнано/пропущено,
                              так что сообщение доставляется только когда реально что-то сделано
  - ошибка запуска         -> в stderr (alert)

Использование напрямую (из живого каталога):
    python wiki_embed_backfill_loader.py            # реальная догонка
    python wiki_embed_backfill_loader.py --dry-run  # только показать, ничего не писать

Создан: 2026-08-17.
"""
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parent


def _choose_python_no_window() -> str:
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    if pythonw.exists():
        return str(pythonw)
    return sys.executable


def main() -> int:
    dry = "--dry-run" in sys.argv
    cmd = [str(_choose_python_no_window()), "-m", "wiki_v2.embed_backfill"]
    if dry:
        cmd.append("--dry-run")
    env = dict(os.environ)
    env["HERMES_HOME"] = os.environ.get("HERMES_HOME", str(SCRIPTS_ROOT.parent))
    # Все эндпоинты (в т.ч. эмбеддинги) — из единого конфига endpoints.yaml (llamaserver)
    try:
        if str(SCRIPTS_ROOT) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_ROOT))
        from wiki_v2.endpoints import apply as _endpoints_apply
        _endpoints_apply(env)
    except Exception as exc:
        print(f"embed_backfill_loader: не удалось применить endpoints.yaml (продолжаем): {exc}")
    # Накапливаем stdout/stderr, чтобы понять результат.
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(SCRIPTS_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    except subprocess.TimeoutExpired:
        print("ERROR: embed_backfill timeout (>600s)", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR running embed_backfill: {exc}", file=sys.stderr)
        return 1

    out = (proc.stdout or "")
    err = (proc.stderr or "")

    # Вывод embed_backfill содержит либо "Нет страниц без эмбеддингов",
    # либо список "[OK]"/"[ПРОПУСК]" + "Итог:".
    if "Нет страниц без эмбеддингов" in out:
        # нечего догонять — тихо (watchdog: пустой stdout = silent)
        return 0

    # Фильтруем шумные миграции sqlite.
    clean_lines = [
        l for l in out.splitlines()
        if "migrate_to" not in l and "Skipping migration" not in l and "current=" not in l
    ]
    cleaned = "\n".join(clean_lines).strip()

    if dry:
        print(f"[embed_backfill DRY-RUN]\n{cleaned}" if cleaned else "[embed_backfill DRY-RUN] ничего к догонке")
        return 0

    if cleaned:
        print(f"[embed_backfill]{os.linesep}{cleaned}")
    if err:
        # успех мог быть частичным; ошибки пригодится увидеть
        print(err.strip(), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
