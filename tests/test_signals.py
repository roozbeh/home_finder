from flip_finder.signals import classify_description


def test_signals_keep_observed_phrase_and_context_without_diagnosing_defect():
    signals = classify_description("Original condition with a reported foundation issue; bring your contractor.")
    foundation = next(signal for signal in signals if signal.phrase == "foundation")
    assert foundation.kind == "risk"
    assert foundation.hard_stop
    assert "reported foundation issue" in foundation.snippet
    assert any(signal.phrase == "original condition" for signal in signals)


def test_blank_description_has_no_invented_signals():
    assert classify_description(None) == ()
