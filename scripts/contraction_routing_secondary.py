"""Exploratory calibration-confidence refinement; preserve the primary result."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from . import contraction_experiment as trial
from .quality_analysis import select_route


def fit_router(rows: list[dict], budget: float = 0.25) -> dict:
    """Fit one threshold from calibration rows only; no test data is accepted."""
    if not rows or not 0 <= budget <= 1:
        raise ValueError("nonempty calibration rows and valid budget required")
    for row in rows:
        if not 0 <= row['confidence'] <= 1 or any(
            not math.isfinite(row[key]) or row[key] <= 0
            for key in ('jev_ns', 'codex_ns')
        ):
            raise ValueError('invalid calibration observation')
    confidences = sorted({row['confidence'] for row in rows})
    thresholds = [(left + right) / 2 for left, right in zip(confidences, confidences[1:])]
    rules = [{'mode': 'never', 'threshold': None}] + [
        {'mode': 'threshold', 'threshold': value} for value in thresholds
    ]
    candidates = []
    for rule in rules:
        routes = [select_route(rule, row['confidence']) for row in rows]
        raw = [math.log(row['codex_ns'] / row['jev_ns']) if route else 0.0
               for row, route in zip(rows, routes, strict=True)]
        scored = [0.0 if math.log(0.98) <= value < 0 else value for value in raw]
        count = sum(routes)
        candidates.append({
            **rule, 'escalations': count, 'escalation_fraction': count / len(rows),
            'within_budget': count <= int(len(rows) * budget + 1e-12),
            'average_log_ratio': sum(scored) / len(rows),
            'raw_geometric_mean_ratio': math.exp(sum(raw) / len(rows)),
        })
    chosen = min((row for row in candidates if row['within_budget']), key=lambda row: (
        row['average_log_ratio'], row['escalations'], row['mode'] != 'never',
        row['threshold'] if row['threshold'] is not None else -1,
    ))
    return {'chosen': chosen, 'candidates': candidates}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def analyze(output: Path, amendment: Path) -> dict:
    primary_path = output / 'models.json'
    primary = json.loads(primary_path.read_text())
    choices_path = Path(primary['model_choices']['path'])
    if digest(choices_path) != primary['model_choices']['sha256']:
        raise ValueError('frozen model choices changed')
    choices = json.loads(choices_path.read_text())['choices']
    discovery_path = output / 'discovery.json'
    discovery = json.loads(discovery_path.read_text())
    if digest(discovery_path) != primary['discovery_sha256']:
        raise ValueError('frozen discovery changed')
    design_path = Path(discovery['design'])
    if digest(design_path) != discovery['provenance']['design_sha256']:
        raise ValueError('frozen design changed')
    design = json.loads(design_path.read_text())
    for cases in (primary['calibration_cases'], primary['test_cases']):
        trial._verify_cache_refs(cases)
    calibration_rows = [{
        'case_id': case['case_id'],
        'confidence': choices[case['case_id']]['jev_confidence'],
        'jev_ns': trial._block_median(case, choices[case['case_id']]['jev'], 1),
        'codex_ns': trial._block_median(case, choices[case['case_id']]['codex'], 1),
    } for case in primary['calibration_cases']]
    routing = fit_router(calibration_rows, design['calibration_escalation_budget'])
    selected = routing['chosen']
    policies = {}
    for case in primary['calibration_cases'] + primary['test_cases']:
        name = case['case_id']
        choice = choices[name]
        policies[name] = {
            **primary['policy_actions'][name],
            'primary_cascade': primary['policy_actions'][name]['cascade'],
            'cascade': choice['codex'] if select_route(selected, choice['jev_confidence']) else choice['jev'],
        }
    test_rate = sum(select_route(selected, choices[case['case_id']]['jev_confidence'])
                    for case in primary['test_cases']) / len(primary['test_cases'])
    comparisons = trial._policy_comparisons(primary['test_cases'], policies, 1)
    gate = trial._success_gate(comparisons, design, selected['escalation_fraction'], test_rate)
    result = {
        'schema_version': 1,
        'status': 'exploratory_secondary_analysis',
        'amendment': json.loads(amendment.read_text()),
        'provenance': {
            'amendment_sha256': digest(amendment),
            'analysis_sha256': digest(Path(__file__)),
            'primary_report_sha256': digest(primary_path),
            'model_choices_sha256': digest(choices_path),
            'discovery_sha256': digest(discovery_path),
        },
        'primary_routing': primary['routing'],
        'calibration_rows': calibration_rows,
        'routing': routing,
        'test_escalation_fraction': test_rate,
        'policy_actions': policies,
        'test_comparisons': comparisons,
        'success_gate': gate,
        'interpretation': 'Post-registration amendment prompted by calibration confidences; not a primary preregistered success. No rules changed after inspecting outcomes.',
    }
    target = output / 'routing-secondary.json'
    if target.exists() and json.loads(target.read_text()) != result:
        raise ValueError('secondary result differs from prior analysis')
    trial._save(target, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--amendment', type=Path, default=Path('experiments/contraction-routing-secondary-v1.json'))
    args = parser.parse_args()
    result = analyze(args.output.resolve(), args.amendment.resolve())
    print(json.dumps({'secondary_rule': result['routing']['chosen'], 'secondary_gate_passed': result['success_gate']['passed']}))


if __name__ == '__main__':
    main()
