import json
import os


def load_queries() -> list[dict[str, str]]:
    """
    Load associative queries from benchmarks/associative_queries.json
    relative to the current file's directory (sandbox/wiki_v2).
    """
    # Get the directory of this test file
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # Go up one level to sandbox/wiki_v2, then into benchmarks
    json_path = os.path.join(current_dir, '..', 'benchmarks', 'associative_queries.json')
    # Normalize path
    json_path = os.path.normpath(json_path)
    with open(json_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def expected_in(results: list[str], expected_slug: str) -> bool:
    """
    Check if expected_slug matches any result by partial (root-match) and case-insensitive.
    A match if:
        expected_slug in result OR result in expected_slug
    ignoring case and allowing for hyphen-separated parts.
    Guard: only consider matches when the expected slug is long enough (>= 8 chars)
    to avoid false-positives on tiny slugs like 'ок' or 'привет'.
    """
    MIN_MATCH_LEN = 8
    expected_lower = expected_slug.strip().lower()
    if len(expected_lower) < MIN_MATCH_LEN:
        # Too short to trust a substring match — require exact equality.
        for res in results:
            if res.strip().lower() == expected_lower:
                return True
        return False
    for res in results:
        res_lower = res.lower()
        if expected_lower in res_lower or res_lower in expected_lower:
            return True
    return False

def run_benchmark(queries: list[dict[str, str]], search_fn, top_k: int = 5) -> dict[str, float]:
    """
    Run benchmark on a list of queries using the provided search_fn.
    Returns a dictionary with keys 'recall@5' and 'mrr'.
    """
    if not queries:
        return {"recall@5": 0.0, "mrr": 0.0}
    
    total_recall = 0
    total_mrr = 0.0
    
    for item in queries:
        query = item["query"]
        expected = item["expected_slug"]
        # Call the search function
        results = search_fn(query, k=top_k)  # Expected to return list of slugs/results
        # Check if expected is in results
        found = expected_in(results, expected)
        if found:
            total_recall += 1
            # Find the rank (first occurrence) - 1-indexed for MRR
            rank = None
            for i, res in enumerate(results):
                if expected_in([res], expected):  # Use same matching for rank
                    rank = i + 1
                    break
            # If rank found, add reciprocal rank
            if rank is not None:
                total_mrr += 1.0 / rank
            else:
                # Should not happen if found is True, but fallback
                total_mrr += 0.0
        else:
            # Not found: recall 0, MRR 0 for this query
            pass
    
    recall_at_5 = total_recall / len(queries)
    mrr = total_mrr / len(queries) if len(queries) > 0 else 0.0
    
    return {"recall@5": recall_at_5, "mrr": mrr}

# ---------- Test cases ----------
def test_load_queries_exists_and_has_min_20():
    queries = load_queries()
    assert isinstance(queries, list), "load_queries should return a list"
    assert len(queries) >= 20, f"Expected at least 20 queries, got {len(queries)}"
    for q in queries:
        assert "query" in q and "expected_slug" in q, "Each query must have 'query' and 'expected_slug'"

def test_run_benchmark_single_query_rank3():
    # Mock search function that returns a fixed list where expected is at rank 3 (0-indexed 2)
    def mock_search(query, k=5):
        # Ignore query, return a list of 5 slugs, expected at position 2 (rank 3)
        return ["slug1", "slug2", "expected-slug", "slug4", "slug5"]
    
    queries = [{"query": "test", "expected_slug": "expected-slug", "note": ""}]
    result = run_benchmark(queries, mock_search, top_k=5)
    assert result["recall@5"] == 1.0, "Recall should be 1.0 when found"
    # MRR = 1/3 ≈ 0.333...
    assert abs(result["mrr"] - (1.0/3.0)) < 1e-9, f"MRR should be 1/3, got {result['mrr']}"

def test_run_benchmark_not_found():
    def mock_search(query, k=5):
        return ["slug1", "slug2", "slug3", "slug4", "slug5"]
    
    queries = [{"query": "test", "expected_slug": "never-going-to-match", "note": ""}]
    result = run_benchmark(queries, mock_search, top_k=5)
    assert result["recall@5"] == 0.0, "Recall should be 0 when not found"
    assert result["mrr"] == 0.0, "MRR should be 0 when not found"

def test_run_benchmark_empty_queries():
    def mock_search(query, k=5):
        return ["slug1", "slug2"]
    
    result = run_benchmark([], mock_search, top_k=5)
    assert result["recall@5"] == 0.0, "Recall should be 0 for empty queries"
    assert result["mrr"] == 0.0, "MRR should be 0 for empty queries"

def test_run_benchmark_duplicates_in_top_k():
    # Expected slug appears twice in top-k, rank should be the first occurrence
    def mock_search(query, k=5):
        return ["expected-slug", "slug2", "expected-slug", "slug4", "slug5"]
    
    queries = [{"query": "test", "expected_slug": "expected-slug", "note": ""}]
    result = run_benchmark(queries, mock_search, top_k=5)
    assert result["recall@5"] == 1.0
    # Rank of first occurrence is 1 -> MRR = 1/1 = 1.0
    assert abs(result["mrr"] - 1.0) < 1e-9, f"MRR should be 1.0 for first rank, got {result['mrr']}"

def test_expected_in_partial_match():
    # Test the expected_in function directly
    assert expected_in(["some-prefix-аудит-wiki-memory-v3-стратегия-и-детали-реализации-с suffix"], 
                       "аудит-wiki-memory-v3-стратегия-и-детали-реализации") == True
    assert expected_in(["аудит-wiki-memory-v3-стратегия-и-детали-реализации"], 
                       "some-prefix-аудит-wiki-memory-v3-стратегия-и-детали-реализации-с suffix") == True
    assert expected_in(["АУДИТ-WIKI-MEMORY-V3-СТРАТЕГИЯ-И-ДЕТАЛИ-РЕАЛИЗАЦИИ"], 
                       "аудит-wiki-memory-v3-стратегия-и-детали-реализации") == True  # case-insensitive
    assert expected_in(["different-slug"], "аудит-wiki-memory-v3-стратегия-и-детали-реализации") == False