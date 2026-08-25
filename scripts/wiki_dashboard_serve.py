"""Wiki 3 Dashboard вЂ” Р°РІС‚Рѕ-РїРѕРґРґРµСЂР¶Р°РЅРёРµ СЃРµСЂРІРµСЂР° --serve РЅР° 9120.

РЎРєСЂРёРїС‚ РґР»СЏ cron-Р·Р°РґР°С‡Рё Hermes (no_agent). РљР°Р¶РґС‹Р№ С‚РёРє РїСЂРѕРІРµСЂСЏРµС‚, Р¶РёРІ Р»Рё
СЃРµСЂРІРµСЂ РґР°С€Р±РѕСЂРґР° РЅР° 127.0.0.1:9120. Р•СЃР»Рё РјС‘СЂС‚РІ вЂ” Р·Р°РїСѓСЃРєР°РµС‚ РµРіРѕ С„РѕРЅРѕРј.
Р’С‹С…РѕРґ: РїСѓСЃС‚РѕР№ stdout (С‚РёС…Рѕ), Р»РёР±Рѕ РѕРґРЅР° СЃС‚СЂРѕРєР° СЃ СЃРѕРѕР±С‰РµРЅРёРµРј РєРѕРіРґР° С‡С‚Рѕ-С‚Рѕ СЃРґРµР»Р°РЅРѕ.
"""
import os
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

URL = "http://127.0.0.1:9120/api/status"
PORT = "9120"
PORT_INT = 9120
SCRIPTS_ROOT = Path(__file__).resolve().parent


def _choose_python_no_window() -> str:
    """Return a python executable that won't spawn a console window.

    Prefer pythonw.exe (Windows GUI subsystem вЂ” never opens a console) next to
    the current interpreter. Fall back to sys.executable (python.exe) вЂ” the
    CREATE_NO_WINDOW flag still suppresses the console in most cases.
    """
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    if pythonw.exists():
        return str(pythonw)
    return sys.executable


def _port_is_listening() -> bool:
    """True if something is already listening on the dashboard port.

    TCP-connect check is a stronger guard than HTTP 200 alone: even if a
    server process is mid-startup (not yet answering HTTP), the port is
    already bound вЂ” so a second planner must NOT spawn another copy. This
    is the anti-duplicate guard against the two `hermes serve` schedulers.
    """
    try:
        with socket.create_connection(("127.0.0.1", PORT_INT), timeout=1.0):
            return True
    except OSError:
        return False


def _is_running() -> bool:
    # Server is alive if the port is bound (avoids a startup race where a
    # second scheduler sees no HTTP response yet and spawns a duplicate).
    if _port_is_listening():
        return True
    try:
        with urllib.request.urlopen(URL, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def main() -> None:
    if _is_running():
        return  # СЃРµСЂРІРµСЂ Р¶РёРІ вЂ” С‚РёС…Рѕ
    # СЃРµСЂРІРµСЂ РјС‘СЂС‚РІ вЂ” Р·Р°РїСѓСЃРєР°РµРј
    try:
        env = dict(os.environ)
        # ВСЕ эндпоинты — из единого конфига endpoints.yaml (LM Studio).
        try:
            if str(SCRIPTS_ROOT) not in sys.path:
                sys.path.insert(0, str(SCRIPTS_ROOT))
            from wiki_v2.endpoints import apply as _endpoints_apply
            _endpoints_apply(env)
        except Exception as exc:
            print(f"dashboard_serve: не удалось применить endpoints.yaml (продолжаем): {exc}")
        env["HERMES_HOME"] = os.environ.get("HERMES_HOME", str(SCRIPTS_ROOT.parent))
        env["DASHBOARD_PORT"] = PORT
        # РСЃРїРѕР»СЊР·СѓРµРј pythonw.exe (Р±РµР·РѕРєРѕРЅРЅС‹Р№) РµСЃР»Рё РґРѕСЃС‚СѓРїРµРЅ вЂ” РёРЅР°С‡Рµ РјРµР»СЊРєР°РµС‚
        # РєРѕРЅСЃРѕР»СЊРЅРѕРµ РѕРєРЅРѕ python РїСЂРё СЃС‚Р°СЂС‚Рµ. pythonw РЅРµ СЃРѕР·РґР°С‘С‚ РѕРєРЅР° РІРѕРѕР±С‰Рµ.
        exe = _choose_python_no_window()
        # venv-трамплин (uv) перезапускает консольный базовый python.exe БЕЗ
        # наших флагов DETACHED_PROCESS/CREATE_NO_WINDOW → появляется видимое
        # чёрное окно. Поэтому запускаем напрямую настоящий GUI-subsystem
        # pythonw.exe базового интерпретатора, а пакеты венва отдаём через
        # PYTHONPATH (numpy и остальное живут в site-packages венва).
        try:
            if getattr(sys, "base_prefix", sys.prefix) != sys.prefix:
                base_pw = Path(sys.base_prefix) / "pythonw.exe"
                site_pkgs = Path(sys.prefix) / "Lib" / "site-packages"
                if base_pw.exists() and site_pkgs.exists():
                    exe = str(base_pw)
                    extra = str(site_pkgs)
                    if env.get("PYTHONPATH"):
                        extra = extra + os.pathsep + env["PYTHONPATH"]
                    env["PYTHONPATH"] = extra
        except Exception as exc:
            print(f"dashboard_serve: windowless resolve failed (fallback to venv exe): {exc}")
        subprocess.Popen(
            [exe, "-m", "wiki_v2.dashboard", "--serve"],
            cwd=str(SCRIPTS_ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
        )
        print("Wiki dashboard server started on 9120")
    except Exception as exc:
        print(f"ERROR starting wiki dashboard server: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()

