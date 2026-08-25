# tests/test_quality_s3_10.py
import re


def test_regex_cyrillic_capture():
    """S3.10: Verify regex captures Cyrillic and alphanumeric correctly."""
    # The actual implementation in search.py uses: r"[а-яА-ЯёЁa-zA-Z0-9]{3,}"
    pattern = r"[а-яА-ЯёЁa-zA-Z0-9]{3,}"
    
    assert re.findall(pattern, "сознание") == ['сознание']
    assert re.findall(pattern, "VPN-сервер") == ['VPN', 'сервер']
    assert re.findall(pattern, "12345") == ['12345']
    # Check that it doesn't capture 2 chars
    assert re.findall(pattern, "ab cd еф") == []

def test_no_word_regex_in_code():
    r"""S3.10: Verify \w{3,} is NOT used in search.py or pages.py."""
    import os
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files_to_check = [
        os.path.join(base, "search.py"),
        os.path.join(base, "pages.py")
    ]
    
    for file_path in files_to_check:
        assert os.path.exists(file_path), f"{file_path} not found"
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            assert r"\w{3," not in content, f"{file_path} contains forbidden \\w{{3,}}"
