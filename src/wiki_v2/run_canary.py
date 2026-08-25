import sys, os, traceback
sys.path.insert(0, r'%LOCALAPPDATA%\hermes\scripts')
sys.path.insert(0, os.getcwd())
try:
    from wiki_v2.indexer import main
    n = main(session_id='20260812_014350_ceb9a3')
    print('CANARY: обработано сессий =', n, flush=True)
except Exception:
    traceback.print_exc()
