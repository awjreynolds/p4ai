from scripts.contraction_routing_secondary import fit_router


def test_confidence_midpoints_allow_one_calibration_escalation():
    rows = [{'confidence': confidence, 'jev_ns': 100, 'codex_ns': 50 if index == 0 else 120}
            for index, confidence in enumerate([.05, .11, .15, .15, .21, .22])]
    result = fit_router(rows)
    assert result['chosen']['threshold'] == .08
    assert result['chosen']['escalations'] == 1
    assert all(c['threshold'] != .15 for c in result['candidates'])


def test_no_improvement_retains_never_and_groups_ties():
    rows = [{'confidence': confidence, 'jev_ns': 100, 'codex_ns': 99}
            for confidence in [.05, .05, .15, .15, .21, .22]]
    result = fit_router(rows)
    assert result['chosen']['mode'] == 'never'
    assert all(c['escalations'] != 1 for c in result['candidates'])


def test_all_equal_confidences_cannot_create_selective_routing():
    rows = [{'confidence': .2, 'jev_ns': 100, 'codex_ns': 20} for _ in range(6)]
    assert fit_router(rows)['chosen']['mode'] == 'never'
