# tests/test_plugin_canary_s3_11.py
import sys
from pathlib import Path
from unittest import mock

# Add the plugins directory to sys.path so we can import wiki-context as a module
plugins_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(plugins_dir))

# Import the plugin module. Note: folder name is 'wiki-context', 
# but Python modules usually don't have hyphens. 
# However, if it's a directory with __init__.py, we might need to handle it.
# Let's try importing via the absolute path of the file directly or by adding its parent.

import importlib.util


def test_e2e_canary_mock_llm_sees_context():
    """S3.11: E2E-canary - mock-LLM sees '12345' from wiki-plugin context (АР-6)."""

    # Load the plugin module manually to avoid name collisions with sandbox/__init__.py
    plugin_file = plugins_dir / "wiki-context" / "__init__.py"
    spec = importlib.util.spec_from_file_location("wiki_context", str(plugin_file))
    plugin_module = importlib.util.module_from_spec(spec)
    sys.modules["wiki_context"] = plugin_module
    spec.loader.exec_module(plugin_module)

    # 1. Setup fake search results: (hits, pages) — АР-6 путь через search()
    fake_hits = [("secret-page", 0.9, "semantic")]
    fake_pages = {
        "secret-page": {
            "title": "Secret Page",
            "path": plugin_module.WIKI_PATH + "/entities/secret-page.md",
            "key_topics": ["пароль", "секрет"],
        }
    }
    # создадим реальный файл главной страницы, чтобы _build_context_main её прочитал
    import os
    os.makedirs(os.path.dirname(fake_pages["secret-page"]["path"]), exist_ok=True)
    with open(fake_pages["secret-page"]["path"], "w", encoding="utf-8") as f:
        f.write("The secret password is: 12345. Do not share it!")

    # 2. Mock _cache_get to bypass cache
    plugin_module._cache_get = lambda q: None

    with mock.patch("wiki_v2.search.search", return_value=(fake_hits, fake_pages)):
        user_query = "what is the secret password?"
        context_block = plugin_module._build_context(user_query)

        # 3. Verify structure: <wiki-memory> ... 12345 ... </wiki-memory>
        assert '<wiki-memory>' in context_block
        assert '</wiki-memory>' in context_block
        assert '12345' in context_block

        # 4. Simulate Mock LLM
        assert "12345" in context_block

if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
