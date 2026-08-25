"""Watchdog: проверить, что llm-extractor реально сработал на новой NVIDIA-модели.

Проверяет в agent.log записи экстракции ('Extracted fact') и текущую модель плагина.
Выводит отчёт в stdout — доставляется пользователю через cron (no_agent).
"""
import os
import re
import time

HOME = os.environ.get("HERMES_HOME", os.path.expanduser("~/AppData/Local/hermes"))
LOG = os.path.join(HOME, "logs", "agent.log")
PLUGIN = os.path.join(HOME, "plugins", "llm-extractor", "__init__.py")
# момент после фикса таймаута (extract_timeout 10 -> 120) 2026-08-08 18:40
SINCE_TS = 1786203600

def ts_of(line):
    m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
    if m:
        try:
            return time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
        except Exception:
            return 0
    return 0

print("=== Watchdog: llm-extractor на NVIDIA ===")
print(f"Время проверки: {time.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"Лог: {LOG}")

# 1) Текущая модель в плагине
model = "не найдено"
if os.path.exists(PLUGIN):
    m = re.search(r'nvidia/nemotron[^\s"]+', open(PLUGIN, encoding="utf-8").read())
    if m:
        model = m.group(0)
print(f"Модель в плагине: {model}")

# 1a) extract_timeout из config.yaml (фикс 10 -> 120)
timeout = "не найден"
try:
    import yaml
    cfg = yaml.safe_load(open(os.path.join(HOME, "config.yaml"), encoding="utf-8"))
    timeout = cfg.get("plugins", {}).get("llm-extractor", {}).get("extract_timeout", "не задан")
except Exception as e:
    timeout = f"ошибка чтения: {e}"
print(f"extract_timeout в config.yaml: {timeout}")

# 2) Записи экстракции после перезапуска
hits = []
if os.path.exists(LOG):
    for line in open(LOG, encoding="utf-8", errors="replace"):
        if "Extracted fact" in line and ts_of(line) >= SINCE_TS:
            hits.append(line.strip())
print(f"Записей 'Extracted fact' после перезапуска: {len(hits)}")
for h in hits[-8:]:
    print("  •", h[-120:])

# 3) Ошибки именно llm-extractor (НЕ любые 429/402 в логе)
errs = []
if os.path.exists(LOG):
    for line in open(LOG, encoding="utf-8", errors="replace"):
        t = ts_of(line)
        low = line.lower()
        if t >= SINCE_TS and ("llm.extractor" in low or "llm extraction" in low):
            errs.append(line.strip())
print(f"Ошибок/событий llm-extractor: {len(errs)}")
for e in errs[-8:]:
    print("  !", e[-150:])

# 4) Итог
if hits:
    verdict = "✅ Экстракция СРАБОТАЛА на новой модели (NVIDIA)."
elif errs:
    verdict = "⚠️ Экстракция НЕ сработала — есть ошибки вызова. См. выше."
else:
    verdict = "⚠️ Записей экстракции пока нет (возможно, не накопилось ~10 сообщений) ИЛИ плагин не сработал."
print("\nВЕРДИКТ:", verdict)

# 5) Сохранить отчёт в файл (для чтения после срабатывания watchdog)
report = [
    "=== Watchdog llm-extractor ===",
    "time: " + time.strftime('%Y-%m-%d %H:%M:%S'),
    "model: " + model,
    "extract_timeout: " + str(timeout),
    "extracted_facts: %d" % len(hits),
    "llm_events: %d" % len(errs),
    "verdict: " + verdict,
]
report_path = os.path.join(HOME, "logs", "llm_extractor_watchdog_report.txt")
try:
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print("Отчёт сохранён:", report_path)
except Exception as e:
    print("Не удалось сохранить отчёт:", e)
