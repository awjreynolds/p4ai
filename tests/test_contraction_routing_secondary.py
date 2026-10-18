import hashlib
import json
from pathlib import Path

from scripts import contraction_routing_secondary as secondary
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


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mock_case(case_id: str, family: str, confidence: float) -> tuple[dict, dict]:
    actions = ['ijk', 'ikj', 'jik', 'jki', 'kij', 'kji']
    measured = {
        'case_id': case_id,
        'family': family,
        'benchmark': {
            'round_records': [{
                'round': 1,
                'block': 1,
                'measurements': {action: {'elapsed_ns': 100} for action in actions},
            }],
        },
    }
    choice = {
        'best_constant': 'ijk',
        'stride_selector': 'ijk',
        'nearest_neighbor': 'ijk',
        'jev': 'ijk',
        'codex': 'ikj',
        'jev_confidence': confidence,
    }
    policy = {name: choice[name] for name in (
        'best_constant', 'stride_selector', 'nearest_neighbor', 'jev', 'codex'
    )}
    policy.update({'canonical_ijk': 'ijk', 'cascade': 'ijk'})
    return measured, {'choice': choice, 'policy': policy}


def test_analyze_consumes_frozen_runner_schema_without_mutating_primary(tmp_path, monkeypatch):
    output = tmp_path / 'run'
    output.mkdir()
    design_path = tmp_path / 'design.json'
    amendment_path = tmp_path / 'amendment.json'
    design_path.write_text(json.dumps({
        'calibration_escalation_budget': 0.25,
        'noise_floor_fraction': 0.02,
    }))
    amendment_path.write_text(json.dumps({'name': 'mock-amendment'}))

    calibration_cases = []
    test_cases = []
    choices = {}
    primary_actions = {}
    confidence_values = [0.05, 0.11, 0.15, 0.15, 0.21, 0.22]
    for index, confidence in enumerate(confidence_values):
        case_id = f'cal-{index}'
        case, values = _mock_case(case_id, 'cal-layout', confidence)
        calibration_cases.append(case)
        choices[case_id] = values['choice']
        primary_actions[case_id] = values['policy']
    for index, (family, confidence) in enumerate((('test-a', .3), ('test-b', .5), ('test-c', .9))):
        case_id = f'test-{index}'
        case, values = _mock_case(case_id, family, confidence)
        test_cases.append(case)
        choices[case_id] = values['choice']
        primary_actions[case_id] = values['policy']

    choices_path = output / 'model-choices.json'
    choices_path.write_text(json.dumps({'choices': choices}))
    discovery_path = output / 'discovery.json'
    discovery_path.write_text(json.dumps({
        'design': str(design_path),
        'provenance': {'design_sha256': _digest(design_path)},
    }))
    primary_path = output / 'models.json'
    primary_path.write_text(json.dumps({
        'model_choices': {'path': str(choices_path), 'sha256': _digest(choices_path)},
        'discovery_sha256': _digest(discovery_path),
        'calibration_cases': calibration_cases,
        'test_cases': test_cases,
        'policy_actions': primary_actions,
        'routing': {'chosen': {'mode': 'never', 'threshold': None}},
    }))
    original_primary_hash = _digest(primary_path)

    monkeypatch.setattr(secondary.trial, '_verify_cache_refs', lambda _cases: None)
    result = secondary.analyze(output, amendment_path)

    assert result['status'] == 'exploratory_secondary_analysis'
    assert result['routing']['chosen']['mode'] == 'never'
    assert result['test_comparisons']['cascade_vs_jev']['geometric_mean_ratio'] == 1
    assert all(
        row['primary_cascade'] == 'ijk'
        for row in result['policy_actions'].values()
    )
    assert _digest(primary_path) == original_primary_hash
