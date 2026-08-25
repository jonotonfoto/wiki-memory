"""Wiki graph: BFS expansion of search candidates via links."""


def bfs(start_slugs, links_dict, depth=2):
    """Расширить набор кандидатов по рёбрам графа (неориентированного).

    start_slugs: список стартовых slug.
    links_dict: {slug: set(to_slugs)} (неориентированный граф).
    depth: на сколько шапов расширять.
    Возвращает упорядоченный список НОВЫХ slug (без start), дедуп, порядок BFS.
    fail-open: на любой ошибке возвращает [].
    """
    try:
        seen = set(start_slugs)
        result = []
        frontier = list(start_slugs)
        for _ in range(depth):
            if not frontier:
                break
            next_frontier = []
            for node in frontier:
                for neighbor in (links_dict.get(node) or set()):
                    if neighbor not in seen:
                        seen.add(neighbor)
                        result.append(neighbor)
                        next_frontier.append(neighbor)
            frontier = next_frontier
        return result
    except Exception:
        return []


def bfs_edges(start_slugs, edges_dict, depth=2):
    """Расширить набор кандидатов по направленным рёбрам (edges).

    start_slugs: список стартовых slug.
    edges_dict: {slug: [(rel, to), ...]} или {slug: set(to)} — поддерживаются оба формата.
    depth: на сколько шагов расширять.
    Возвращает упорядоченный список НОВЫХ slug (без start), дедуп, порядок BFS.
    fail-open: на любой ошибке возвращает [].
    """
    try:
        seen = set(start_slugs)
        result = []
        frontier = list(start_slugs)
        for _ in range(depth):
            if not frontier:
                break
            next_frontier = []
            for node in frontier:
                targets = edges_dict.get(node, [])
                # Поддержка обоих форматов: [(rel, to), ...] или set(to)
                if isinstance(targets, set):
                    neighbors = list(targets)  # order non-deterministic but same as bfs()
                else:
                    # Preserve insertion order from the tuple list; dedup within this level.
                    seen_in_level = set()
                    neighbors = []
                    for t in targets:
                        if isinstance(t, (list, tuple)) and len(t) >= 2:
                            nb = t[1]
                        else:
                            nb = t
                        if nb not in seen_in_level:
                            seen_in_level.add(nb)
                            neighbors.append(nb)
                for neighbor in neighbors:
                    if neighbor not in seen:
                        seen.add(neighbor)
                        result.append(neighbor)
                        next_frontier.append(neighbor)
            frontier = next_frontier
        return result
    except Exception:
        return []
