from pathlib import Path

import pytest
from tools.benchmark_real_ws import benchmark, metrics, summary


def test_summary_nearest_rank_and_empty():
    assert summary([]) is None
    assert summary([1, 4, 2, 3, 5]) == {"median": 3, "p95": 5, "samples": 5}


def test_metrics_only_extracts_stage_timings(tmp_path: Path):
    log = tmp_path / "metrics.log"
    log.write_text(
        "private payload ignored\nstage=vad duration_ms=1.500\nstage=vad duration_ms=2.500\n"
    )
    assert metrics(log) == {"vad": {"median": 2.0, "p95": 2.5, "samples": 2}}


async def test_missing_cached_model_rejected_without_starting_server(tmp_path: Path):
    with pytest.raises(ValueError, match="cached local model"):
        await benchmark(tmp_path / "missing", tmp_path / "output.json", 1)
