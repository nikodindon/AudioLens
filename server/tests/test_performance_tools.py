from tools.benchmark_whisper_ab import distribution, normalized_words, word_error


def test_benchmark_percentile_and_synthetic_wer():
    assert distribution([1, 4, 3, 2, 5])["p95"] == 5
    assert normalized_words("Hello, WORLD!") == ["hello", "world"]
    assert word_error("hello world", "hello world") == 0
    assert word_error("hello world", "hello") == 0.5


def test_experimental_vad_matches_silence_prefixes():
    import numpy as np
    from faster_whisper.vad import get_vad_model
    from tools.benchmark_vad_ab import run_trial

    result = run_trial(get_vad_model(), np.zeros(16000, dtype=np.float32), 100)
    assert result["threshold_mismatches"] == 0
    assert result["final_flags_equal"]
    assert result["max_probability_difference"] < 0.000001
