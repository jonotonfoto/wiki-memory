import pytest
import requests
from unittest.mock import MagicMock, patch
import wiki_v2.nvidia_client as nc

@pytest.fixture(autouse=True)
def _reset_breaker():
    nc._errors_consecutive = 0
    nc._state = "normal"
    yield
    nc._errors_consecutive = 0
    nc._state = "normal"

def test_nvidia_client_session_is_requests_session():
    assert isinstance(nc._SESSION, requests.Session)

def test_nvidia_client_has_https_adapter():
    # Verify that the session has an adapter for https://
    adapter = nc._SESSION.get_adapter("https://")
    from requests.adapters import HTTPAdapter
    assert isinstance(adapter, HTTPAdapter)

def test_nvidia_client_pool_config_check():
    # We check if the adapter is present and has a poolmanager (which it should)
    adapter = nc._SESSION.get_adapter("https://")
    assert hasattr(adapter, 'poolmanager')
