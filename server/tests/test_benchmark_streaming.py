from tools.benchmark_streaming import stats


def test_percentiles_use_nearest_rank_and_handle_empty():
    assert stats([]) is None
    assert stats([5, 1, 4, 2, 3]) == {"median": 3, "p95": 5}
