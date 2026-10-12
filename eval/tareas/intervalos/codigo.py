def merge_intervals(intervals):
    """Une intervalos cerrados [a, b] que se solapan."""
    result = []
    for a, b in intervals:
        if result and a < result[-1][1]:
            result[-1][1] = max(result[-1][1], b)
        else:
            result.append([a, b])
    return result
