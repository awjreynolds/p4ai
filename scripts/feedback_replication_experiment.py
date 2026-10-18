"""Replicate compiler-informed choices against fixed cheap policies.

Preparation builds and validates every fresh case, fits controls exclusively
from the frozen prior block-one corpus, and freezes three Jev choices per
case. Measurement is a separate phase and accepts only that prepared state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import re
import sys
import time
from pathlib import Path
from typing import Any

try:
    from . import compiler_feedback_experiment as feedback
    from . import (
        contraction_build,
        contraction_corpus,
        feedback_policy,
        loop_experiment,
        quality_analysis,
    )
    from . import contraction_experiment as contraction
except ImportError:  # Running as ``python scripts/feedback_replication_experiment.py``.
    import compiler_feedback_experiment as feedback
    import contraction_build
    import contraction_corpus
    import contraction_experiment as contraction
    import feedback_policy
    import loop_experiment
    import quality_analysis


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DESIGN = ROOT / "experiments" / "feedback-replication-v1.json"
DEFAULT_SOURCE_OUTPUT = ROOT / "docs" / "results" / "compiler-feedback-v1.json"
SCHEMA_VERSION = 1
REPLICATES = 3
BLOCKS = (1, 2)
CONTROLS = ("best_constant", "stride_selector", "nearest_neighbor", "ridge", "random_forest")
EXPECTED_SOURCE_BUNDLE_SHA256 = "c918ecb366a9a14219e98a644acd4929ea74cbd60b5ed8477278cae560333fa8"
EXPECTED_FOREST = {
    "n_estimators": 64,
    "max_depth": 6,
    "min_samples_leaf": 2,
    "random_state": 20261020,
    "n_jobs": 1,
}
CASE_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class FeedbackReplicationError(RuntimeError):
    """The frozen feedback-replication experiment cannot safely proceed."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FeedbackReplicationError(f"cannot read {path}: {error}") from error


def _normalize_repo_paths(value: Any) -> Any:
    """Apply the public bundle's $REPO path normalization to raw JSON."""
    if isinstance(value, str):
        return value.replace(str(ROOT), "$REPO")
    if isinstance(value, list):
        return [_normalize_repo_paths(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_repo_paths(item) for key, item in value.items()}
    return value


def _same_public_evidence(raw: Any, published: Any) -> bool:
    return _normalize_repo_paths(raw) == published


def _load_raw_or_published(
    path: Path, published: Any, expected_sha256: Any, label: str
) -> tuple[Any, str]:
    """Prefer original evidence; use its bundle-anchored snapshot in clean checkouts."""
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise FeedbackReplicationError(f"published {label} has an invalid SHA-256")
    if path.is_file():
        if _sha256(path) != expected_sha256:
            raise FeedbackReplicationError(f"raw {label} hash differs from the public bundle")
        raw = _load(path)
        if not _same_public_evidence(raw, published):
            raise FeedbackReplicationError(f"raw {label} differs from published evidence")
        return raw, "raw_and_public"
    if published is None:
        raise FeedbackReplicationError(f"neither raw nor published {label} is available")
    return published, "public_bundle_only"


def _design(path: Path, source_design_path: Path) -> dict[str, Any]:
    design = _load(path)
    if not isinstance(design, dict) or design.get("schema_version") != SCHEMA_VERSION:
        raise FeedbackReplicationError("unsupported feedback-replication design")
    source_design = feedback._design(source_design_path)
    if design.get("source_design") != str(source_design_path.resolve().relative_to(ROOT)):
        raise FeedbackReplicationError("replication source design path changed")
    if design.get("actions") != list(feedback.ACTION_NAMES):
        raise FeedbackReplicationError("replication action menu differs from the frozen builder")
    if design.get("target_cpu") != contraction_build.TARGET_CPU:
        raise FeedbackReplicationError("replication target CPU differs from the builder")
    if (
        design.get("source_features") != source_design.get("source_features")
        or design.get("compiler_features") != source_design.get("compiler_features")
    ):
        raise FeedbackReplicationError("replication feature schema differs from its source experiment")
    if design.get("training_source") != {
        "design": str(source_design_path.resolve().relative_to(ROOT)),
        "public_bundle": "docs/results/compiler-feedback-v1.json",
        "public_bundle_sha256": EXPECTED_SOURCE_BUNDLE_SHA256,
        "raw_root": "artifacts/compiler-feedback-v1",
        "block": 1,
    }:
        raise FeedbackReplicationError("replication training source changed")
    if design.get("forest") != EXPECTED_FOREST:
        raise FeedbackReplicationError("random-forest parameters differ from the frozen design")
    if type(design.get("sklearn_version")) is not str or not design["sklearn_version"]:
        raise FeedbackReplicationError("design must pin the scikit-learn version")
    if type(design.get("provider_replicates")) is not int or design["provider_replicates"] != REPLICATES:
        raise FeedbackReplicationError("design must request exactly three provider replicates")
    max_attempts = design.get("provider_max_attempts", 1)
    if type(max_attempts) is not int or max_attempts not in (1, 3):
        raise FeedbackReplicationError("provider_max_attempts must be 1 or 3")
    if max_attempts == 3:
        amendment = design.get("pre_timing_amendment")
        if not isinstance(amendment, dict) or amendment.get("fresh_timing_observed") is not False:
            raise FeedbackReplicationError("bounded retry policy requires its pre-timing amendment evidence")
        for path_key, hash_key in (
            ("original_design", "original_design_sha256"),
            ("initial_abort_evidence", "initial_abort_evidence_sha256"),
        ):
            relative = amendment.get(path_key)
            expected_hash = amendment.get(hash_key)
            try:
                evidence_path = (ROOT / relative).resolve()
                evidence_path.relative_to(ROOT)
            except (TypeError, ValueError):
                raise FeedbackReplicationError(f"retry amendment {path_key} path is invalid") from None
            if (
                not isinstance(expected_hash, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
                or not evidence_path.is_file()
                or _sha256(evidence_path) != expected_hash
            ):
                raise FeedbackReplicationError(f"retry amendment {path_key} hash changed or is missing")
    if type(design.get("benchmark_rounds")) is not int or design["benchmark_rounds"] != 11:
        raise FeedbackReplicationError("design must use eleven rounds per block")
    if type(design.get("benchmark_blocks")) is not int or design["benchmark_blocks"] != 2:
        raise FeedbackReplicationError("design must use two independent timing blocks")
    if type(design.get("target_sample_ms")) is not int or design["target_sample_ms"] <= 0:
        raise FeedbackReplicationError("design target sample duration is invalid")
    if type(design.get("seed")) is not int or design["seed"] <= 0:
        raise FeedbackReplicationError("design seed is invalid")
    floor = design.get("noise_floor_fraction")
    if isinstance(floor, bool) or not isinstance(floor, (int, float)) or not math.isfinite(floor) or floor < 0:
        raise FeedbackReplicationError("design noise floor must be finite and non-negative")

    rows = design.get("cases")
    if not isinstance(rows, list) or len(rows) != 24:
        raise FeedbackReplicationError("design must contain the frozen 24-case replication corpus")
    source_ids = {row["case_id"] for row in source_design["cases"]}
    seen: set[str] = set()
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("partition") != "test":
            raise FeedbackReplicationError("all replication cases must use the test partition")
        case_id, group, spec = row.get("case_id"), row.get("group"), row.get("spec")
        if not isinstance(case_id, str) or not CASE_ID.fullmatch(case_id) or case_id in seen:
            raise FeedbackReplicationError("case IDs must be unique safe path components")
        if case_id in source_ids:
            raise FeedbackReplicationError("replication case IDs overlap prior training cases")
        if not isinstance(group, str) or not CASE_ID.fullmatch(group):
            raise FeedbackReplicationError(f"case {case_id} has an invalid group")
        if not isinstance(spec, dict):
            raise FeedbackReplicationError(f"case {case_id} has no valid contraction specification")
        try:
            contraction_corpus._validate(spec, "ijk")
        except (TypeError, ValueError) as error:
            raise FeedbackReplicationError(f"case {case_id} has an invalid specification: {error}") from error
        seen.add(case_id)
        groups.setdefault(group, []).append(row)
    if len(groups) != 6 or any(len(group_rows) != 4 for group_rows in groups.values()):
        raise FeedbackReplicationError("replication corpus must have six groups of four layouts")
    for group, group_rows in groups.items():
        specs = [row["spec"] for row in group_rows]
        shared_keys = ("m", "n", "k", *contraction_corpus.PADDING_KEYS)
        if any(any(spec[key] != specs[0][key] for key in shared_keys) for spec in specs):
            raise FeedbackReplicationError(f"group {group} mixes shapes or padding")
        layouts = {
            tuple(spec[name] for name in ("a_column_major", "b_column_major", "c_column_major"))
            for spec in specs
        }
        if len(layouts) != 4:
            raise FeedbackReplicationError(f"group {group} repeats a layout")
    return design


def _source_training(
    design: dict[str, Any], source_bundle_path: Path, source_design_path: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Verify published/raw evidence hashes and return first-block training rows."""
    if _sha256(source_bundle_path) != EXPECTED_SOURCE_BUNDLE_SHA256:
        raise FeedbackReplicationError("published source bundle hash differs from the frozen design")
    bundle = _load(source_bundle_path)
    if bundle.get("provenance", {}).get("design_sha256") != _sha256(source_design_path):
        raise FeedbackReplicationError("published bundle was not produced from the pinned source design")
    raw_root = (ROOT / design["training_source"]["raw_root"]).resolve()
    source_manifest_path = raw_root / "builds.json"
    build_ref = bundle.get("build_evidence", {}).get("manifest", {})
    build_sha = build_ref.get("sha256")
    build_manifest, audit_mode = _load_raw_or_published(
        source_manifest_path, build_ref.get("evidence"), build_sha, "build manifest"
    )
    built_cases = build_manifest.get("cases")
    if not isinstance(built_cases, list) or len(built_cases) != 36:
        raise FeedbackReplicationError("prior build manifest does not contain all 36 cases")

    reports_ref = bundle.get("build_evidence", {}).get("reports_by_case", {})
    if set(reports_ref) != {case.get("case_id") for case in built_cases}:
        raise FeedbackReplicationError("published build-report index differs from the source manifest")
    raw_hashes: dict[str, str] = {"builds.json": build_sha}
    raw_evidence_used = audit_mode == "raw_and_public"
    for case in built_cases:
        relative = Path("cases") / case["partition"] / case["case_id"] / "build" / "report.json"
        raw_path = raw_root / relative
        ref = reports_ref[case["case_id"]]
        report, mode = _load_raw_or_published(
            raw_path, ref.get("report"), ref.get("sha256"), f"build report for {case['case_id']}"
        )
        raw_evidence_used |= mode == "raw_and_public"
        if _normalize_repo_paths(case.get("build")) != ref.get("report"):
            raise FeedbackReplicationError(f"published report and manifest differ for {case['case_id']}")
        if case.get("build_report_sha256") != ref.get("sha256"):
            raise FeedbackReplicationError(f"manifest build-report hash differs for {case['case_id']}")
        raw_hashes[relative.as_posix()] = ref["sha256"]

    checkpoint_refs = (
        ("discovery-block-1.json", bundle.get("discovery_rounds", {}).get("block_1", {})),
        ("calibration-cases.json", bundle.get("calibration_cases", {})),
        ("test-cases.json", bundle.get("test_cases", {})),
    )
    by_id: dict[str, dict[str, Any]] = {}
    for filename, ref in checkpoint_refs:
        raw_path = raw_root / filename
        checkpoint, mode = _load_raw_or_published(
            raw_path, ref.get("evidence"), ref.get("sha256"), f"first-block checkpoint {filename}"
        )
        raw_evidence_used |= mode == "raw_and_public"
        raw_hashes[filename] = ref["sha256"]
        rows = checkpoint.get("cases")
        if not isinstance(rows, list):
            raise FeedbackReplicationError(f"raw checkpoint has no cases: {filename}")
        for case in rows:
            if case.get("case_id") in by_id:
                raise FeedbackReplicationError(f"duplicate source training case {case.get('case_id')}")
            by_id[case.get("case_id")] = case
    if len(by_id) != 36 or set(by_id) != set(reports_ref):
        raise FeedbackReplicationError("first-block source checkpoints do not cover all 36 built cases")

    raw_timing = bundle.get("raw_timing_evidence", {})
    block_one_keys = {
        name for name in raw_timing if name.startswith("cases/") and name.endswith("/timings/block-1.json")
    }
    expected_timing_keys: set[str] = set()
    for case_id, case in by_id.items():
        partition = case.get("partition")
        if partition not in ("discovery", "calibrate", "test"):
            raise FeedbackReplicationError(f"source case {case_id} has an invalid partition")
        if not isinstance(case.get("benchmark"), dict):
            raise FeedbackReplicationError(f"source case {case_id} has no frozen timing evidence")
        records = case["benchmark"].get("round_records")
        if not isinstance(records, list) or len(records) != 11 or any(row.get("block") != 1 for row in records):
            raise FeedbackReplicationError(f"source training case {case_id} is not first-block-only")
        relative_key = f"cases/{partition}/{case_id}/timings/block-1.json"
        expected_timing_keys.add(relative_key)
        ref = raw_timing.get(relative_key)
        relative_file = Path(relative_key)
        raw_path = raw_root / relative_file
        if not isinstance(ref, dict):
            raise FeedbackReplicationError(f"published raw-timing entry is missing for {case_id}")
        raw_evidence, mode = _load_raw_or_published(
            raw_path, ref.get("evidence"), ref.get("sha256"), f"block-one timing for {case_id}"
        )
        raw_evidence_used |= mode == "raw_and_public"
        raw_benchmark = raw_evidence.get("benchmark", {})
        aliases = case.get("code_aliases", {})
        raw_records = raw_benchmark.get("round_records")
        expected_records = [
            {
                "round": row.get("round"),
                "block": 1,
                "block_round": row.get("round"),
                "canonical_order": row.get("order"),
                "measurements": {
                    action: row.get("measurements", {}).get(aliases.get(action))
                    for action in feedback.ACTION_NAMES
                },
            }
            for row in raw_records or []
        ]
        saved_records = case["benchmark"].get("round_records")
        timing_caches = case.get("timing_caches")
        raw_identity = raw_evidence.get("identity")
        if (
            not isinstance(raw_records, list)
            or len(raw_records) != 11
            or saved_records != expected_records
            or not isinstance(timing_caches, list)
            or len(timing_caches) != 1
            or timing_caches[0].get("sha256") != ref.get("sha256")
            or _normalize_repo_paths(timing_caches[0].get("path")) != f"$REPO/artifacts/compiler-feedback-v1/{relative_key}"
            or timing_caches[0].get("identity_sha256") != raw_evidence.get("identity_sha256")
            or not isinstance(raw_identity, dict)
            or raw_identity.get("partition") != partition
            or raw_identity.get("case_id") != case_id
            or raw_identity.get("block") != 1
        ):
            raise FeedbackReplicationError(f"checkpoint and raw block-one timing disagree for {case_id}")
        raw_hashes[relative_key] = ref["sha256"]
    if block_one_keys != expected_timing_keys:
        raise FeedbackReplicationError("public raw-timing index does not match the 36 training cases")

    source_design = feedback._design(source_design_path)
    source_cases = {row["case_id"]: row for row in feedback._cases(source_design)}
    if set(source_cases) != set(by_id):
        raise FeedbackReplicationError("source design case IDs differ from the published training corpus")
    ordered_cases = []
    for source_case in feedback._cases(source_design):
        case = by_id[source_case["case_id"]]
        if (
            case.get("partition") != source_case["partition"]
            or case.get("family") != source_case["family"]
            or case.get("spec") != source_case["spec"]
        ):
            raise FeedbackReplicationError(f"source training identity changed for {source_case['case_id']}")
        ordered_cases.append(case)
    ordered_cases = feedback._attach_compiler_evidence(ordered_cases, built_cases)
    training_rows = feedback._training_rows(ordered_cases, block=1, informed=True)
    if len(training_rows) != 216:
        raise FeedbackReplicationError("source training must contain 216 unique loop-order labels")
    for row in training_rows:
        if not math.isfinite(row["loss"]) or row["loss"] <= 0:
            raise FeedbackReplicationError("source training contains an invalid normalized runtime")
    evidence = {
        "source_design_sha256": _sha256(source_design_path),
        "public_bundle_sha256": _sha256(source_bundle_path),
        "raw_root": str(raw_root),
        "audit_mode": "raw_and_public" if raw_evidence_used and source_manifest_path.is_file() else "public_bundle_only",
        "raw_file_sha256": raw_hashes,
        "case_count": len(ordered_cases),
        "training_block": 1,
        "action_rows": len(training_rows),
        "training_rows_sha256": _fingerprint(training_rows),
    }
    return ordered_cases, training_rows, evidence


def _implementation_hashes() -> dict[str, str]:
    paths = (
        Path(__file__),
        Path(feedback.__file__),
        Path(contraction.__file__),
        Path(contraction_build.__file__),
        Path(contraction_corpus.__file__),
        Path(feedback.compiler_evidence.__file__),
        Path(feedback.benchmark.__file__),
        Path(feedback.jev.__file__),
        Path(feedback.codex_advisor.__file__),
        Path(feedback.experiment.__file__),
        Path(feedback_policy.__file__),
        Path(quality_analysis.__file__),
        Path(loop_experiment.__file__),
        Path(loop_experiment.loop_build.__file__),
        Path(loop_experiment.quality_experiment.__file__),
        Path(contraction.counterfactual.__file__),
        Path(contraction.validate.__file__),
    )
    return {str(path.resolve().relative_to(ROOT)): _sha256(path) for path in paths}


def _provenance(
    design_path: Path,
    source_design_path: Path,
    source_bundle_path: Path,
    validation_path: Path,
    training_evidence: dict[str, Any],
    sklearn_version: str,
) -> dict[str, Any]:
    return {
        "design_sha256": _sha256(design_path),
        "source_design_sha256": _sha256(source_design_path),
        "source_bundle_sha256": _sha256(source_bundle_path),
        "validation_report_sha256": _sha256(validation_path),
        "training_rows_sha256": training_evidence["training_rows_sha256"],
        "raw_file_sha256": training_evidence["raw_file_sha256"],
        "implementation_sha256": _implementation_hashes(),
        "sklearn_version": sklearn_version,
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
    }


def _cases(design: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "partition": row["partition"],
            "family": row["group"],
            "group": row["group"],
            "case_id": row["case_id"],
            "spec": dict(row["spec"]),
        }
        for row in design["cases"]
    ]


def _build_all(
    design: dict[str, Any], validation_path: Path, output: Path
) -> list[dict[str, Any]]:
    return [feedback._build_case(case, design, validation_path, output) for case in _cases(design)]


def _validate_builds(
    design: dict[str, Any], validation_path: Path, output: Path, recorded: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    expected = _cases(design)
    if len(recorded) != len(expected):
        raise FeedbackReplicationError("prepared build manifest is missing cases")
    refreshed = []
    for want, have in zip(expected, recorded, strict=True):
        if any(have.get(key) != want[key] for key in ("partition", "group", "family", "case_id", "spec")):
            raise FeedbackReplicationError("prepared build case identity changed")
        case = feedback._build_case(want, design, validation_path, output)
        for key in (
            "source_features", "feature_kernel_sha256", "feature_driver_sha256",
            "build_report_path", "build_report_sha256", "distinct_code_identities",
        ):
            if case.get(key) != have.get(key):
                raise FeedbackReplicationError(f"prepared build evidence changed for {want['case_id']}")
        refreshed.append(case)
    return refreshed


def _fit_forest(rows: list[dict[str, Any]], design: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    try:
        import sklearn
        from sklearn.ensemble import RandomForestRegressor
    except ImportError as error:
        raise FeedbackReplicationError("scikit-learn is required for the frozen random-forest control") from error
    if sklearn.__version__ != design["sklearn_version"]:
        raise FeedbackReplicationError(
            f"scikit-learn version {sklearn.__version__} differs from pinned {design['sklearn_version']}"
        )
    names = sorted(rows[0]["features"])
    params = dict(design["forest"])
    forest = RandomForestRegressor(**params)
    features = [[float(row["features"][name]) for name in names] for row in rows]
    targets = [math.log(row["loss"]) for row in rows]
    forest.fit(features, targets)
    exported = {
        "schema_version": 1,
        "library": "scikit-learn",
        "library_version": sklearn.__version__,
        "parameters": params,
        "features": names,
        "training_rows": len(rows),
        "training_targets": "log(runtime/canonical stock)",
        "estimators": [
            {
                "children_left": tree.tree_.children_left.tolist(),
                "children_right": tree.tree_.children_right.tolist(),
                "feature": tree.tree_.feature.tolist(),
                "threshold": tree.tree_.threshold.tolist(),
                "value": tree.tree_.value[:, 0, 0].tolist(),
            }
            for tree in forest.estimators_
        ],
    }
    return forest, exported


def _fit_controls(
    source_cases: list[dict[str, Any]], rows: list[dict[str, Any]], design: dict[str, Any]
) -> tuple[dict[str, Any], Any]:
    constant = contraction._fit_discovery(source_cases)
    nn = quality_analysis.fit_nearest_neighbor(rows)
    ridge = feedback_policy.fit_ridge(rows, alpha=1.0)
    forest, forest_export = _fit_forest(rows, design)
    model_set = {
        "schema_version": 1,
        "training_case_ids": [case["case_id"] for case in source_cases],
        "training_block": 1,
        "training_rows": len(rows),
        "unique_action_rows_per_case": list(feedback.ORDERS),
        "best_constant": constant["best_constant"],
        "best_constant_scores": constant["first_block_gm_ratio_to_ijk"],
        "nearest_neighbor": nn,
        "ridge": ridge,
        "random_forest": forest_export,
    }
    return model_set, forest


def _predict_controls(
    models: dict[str, Any], forest: Any, case: dict[str, Any], design: dict[str, Any]
) -> dict[str, Any]:
    packet = feedback._provider_packet(case, design, informed=True)
    nn = quality_analysis.score_actions(models["nearest_neighbor"], packet["features"], packet["actions"])
    ridge = feedback_policy.score_packet(models["ridge"], packet)
    columns = packet["features"]
    names = models["random_forest"]["features"]
    matrix = [
        [float(columns[name][index]) for name in names]
        for index in range(len(packet["actions"]))
    ]
    forest_scores = forest.predict(matrix).tolist()
    forest_by_order = {order: float(score) for order, score in zip(feedback.ORDERS, forest_scores, strict=True)}
    forest_choice = min(feedback.ORDERS, key=lambda order: (forest_by_order[order], feedback.ORDERS.index(order)))
    return {
        "best_constant": models["best_constant"],
        "stride_selector": contraction.stride_choice(case["source_features"]),
        "nearest_neighbor": feedback.ORDERS[int(nn["action_id"])],
        "ridge": feedback.ORDERS[int(ridge["action_id"])],
        "random_forest": forest_choice,
        "nearest_neighbor_scores": nn,
        "ridge_predicted_log_losses": {
            feedback.ORDERS[int(action_id)]: value
            for action_id, value in ridge["predicted_log_losses"].items()
        },
        "random_forest_predicted_log_losses": forest_by_order,
    }


def _cached_provider_entry(
    packet: dict[str, Any], cache_path: Path, case_id: str, replicate: int
) -> tuple[dict[str, Any] | None, int]:
    """Validate an existing slot through the frozen provider-cache reader."""
    if not cache_path.exists():
        return None, 0
    packet_hash = loop_experiment._packet_fingerprint(packet)
    try:
        entry = feedback._query_provider(packet, "jev", cache_path, allow_call=False)
    except feedback.CompilerFeedbackError as error:
        message = str(error)
        if message not in {
            "frozen model choice has no matching provider cache entry",
            "frozen jev response is missing from provider cache",
        }:
            raise
        cache = feedback._load(cache_path)
        packets = cache["packets"]
        if message == "frozen model choice has no matching provider cache entry":
            raise FeedbackReplicationError(
                f"provider cache is incomplete or changed for {case_id} replicate {replicate}"
            ) from error
        entry = packets[packet_hash]
        if entry.get("responses") != {} or entry.get("response_sha256") != {}:
            raise FeedbackReplicationError(
                f"provider slot contains an unexpected response for {case_id} replicate {replicate}"
            ) from error
        if not entry["attempts"]:
            raise FeedbackReplicationError(
                f"provider slot has no retained attempt for {case_id} replicate {replicate}"
            ) from error
        if any(
            not isinstance(attempt, dict)
            or attempt.get("provider") != "jev"
            or attempt.get("status") not in {"unavailable", "rejected"}
            or not isinstance(attempt.get("reason"), str)
            or not attempt["reason"]
            for attempt in entry["attempts"]
        ):
            raise FeedbackReplicationError(
                f"missing response has no valid failure history for {case_id} replicate {replicate}"
            ) from error
        return None, len(entry["attempts"])
    if set(entry.get("responses", {})) != {"jev"}:
        raise FeedbackReplicationError(
            f"provider slot is not a single valid Jev response for {case_id} replicate {replicate}"
        )
    return entry, len(entry["attempts"])


def _slot_record(
    case_id: str,
    packet: dict[str, Any],
    cache_path: Path,
    replicate: int,
    entry: dict[str, Any],
) -> dict[str, Any]:
    packet_hash = loop_experiment._packet_fingerprint(packet)
    if entry.get("packet_sha256") != packet_hash or entry.get("packet") != packet:
        raise FeedbackReplicationError(f"provider packet changed for {case_id} replicate {replicate}")
    return {
        "replicate": replicate,
        "packet_sha256": packet_hash,
        "action": feedback._provider_action(entry, "jev"),
        "response": entry["responses"]["jev"],
        "attempts": entry["attempts"],
        "cache_path": str(cache_path.resolve()),
        "cache_sha256": _sha256(cache_path),
    }


def _verify_frozen_slots(
    case: dict[str, Any], packet: dict[str, Any], output: Path, recorded: list[dict[str, Any]]
) -> None:
    if not isinstance(recorded, list) or len(recorded) != REPLICATES:
        raise FeedbackReplicationError(f"frozen provider choices are incomplete for {case['case_id']}")
    for replicate, slot in enumerate(recorded, 1):
        cache_path = output / "provider-cache" / case["case_id"] / f"replicate-{replicate}.json"
        if (
            not isinstance(slot, dict)
            or slot.get("replicate") != replicate
            or slot.get("cache_path") != str(cache_path.resolve())
            or not cache_path.is_file()
            or slot.get("cache_sha256") != _sha256(cache_path)
        ):
            raise FeedbackReplicationError(f"frozen provider cache changed for {case['case_id']}")
        entry, _ = _cached_provider_entry(packet, cache_path, case["case_id"], replicate)
        if entry is None or _slot_record(case["case_id"], packet, cache_path, replicate, entry) != slot:
            raise FeedbackReplicationError(f"frozen provider choice changed for {case['case_id']}")


def _query_replicates(
    case: dict[str, Any],
    packet: dict[str, Any],
    output: Path,
    count: int = REPLICATES,
    max_attempts: int = 1,
) -> list[dict[str, Any]]:
    if count != REPLICATES:
        raise FeedbackReplicationError("provider replicate count changed")
    if type(max_attempts) is not int or max_attempts not in (1, 3):
        raise FeedbackReplicationError("provider max attempts must be 1 or 3")
    slots = []
    for replicate in range(1, count + 1):
        cache_path = output / "provider-cache" / case["case_id"] / f"replicate-{replicate}.json"
        while True:
            entry, attempts_before = _cached_provider_entry(
                packet, cache_path, case["case_id"], replicate
            )
            if entry is not None:
                break
            if attempts_before >= max_attempts:
                raise feedback.CompilerFeedbackError(
                    "jev returned no valid decision; cached provider attempts are exhausted"
                )
            try:
                feedback._query_provider(packet, "jev", cache_path, allow_call=True)
            except feedback.CompilerFeedbackError as error:
                if str(error) != "jev returned no valid decision; failed attempt is cached":
                    raise
                _, attempts_after = _cached_provider_entry(
                    packet, cache_path, case["case_id"], replicate
                )
                if attempts_after <= attempts_before:
                    raise FeedbackReplicationError(
                        f"failed Jev attempt was not retained for {case['case_id']} replicate {replicate}"
                    ) from error
                if attempts_after >= max_attempts:
                    raise
                continue
            # Re-read through the frozen helper to verify newly written response hashes.
            entry, _ = _cached_provider_entry(packet, cache_path, case["case_id"], replicate)
            if entry is None:
                raise FeedbackReplicationError(
                    f"Jev response was not retained for {case['case_id']} replicate {replicate}"
                )
            break
        slots.append(_slot_record(case["case_id"], packet, cache_path, replicate, entry))
    if len({slot["packet_sha256"] for slot in slots}) != 1:
        raise FeedbackReplicationError("replicate packets are not identical")
    if len({slot["cache_path"] for slot in slots}) != REPLICATES:
        raise FeedbackReplicationError("provider replicate caches are not independent slots")
    return slots


def run_prepare(
    design_path: Path,
    validation_path: Path,
    output: Path,
    source_output: Path = DEFAULT_SOURCE_OUTPUT,
) -> dict[str, Any]:
    design_path, validation_path, output, source_output = (
        path.expanduser().resolve() for path in (design_path, validation_path, output, source_output)
    )
    source_design_path = (ROOT / "experiments" / "compiler-feedback-v1.json").resolve()
    design = _design(design_path, source_design_path)
    if not validation_path.is_file():
        raise FeedbackReplicationError("validation report is missing")
    source_cases, training_rows, training_evidence = _source_training(
        design, source_output, source_design_path
    )
    try:
        import sklearn
    except ImportError as error:
        raise FeedbackReplicationError("scikit-learn is required for the frozen random-forest control") from error
    provenance = _provenance(
        design_path, source_design_path, source_output, validation_path, training_evidence, sklearn.__version__
    )
    model_path = output / "training-models.json"
    builds_path = output / "builds.json"
    choices_path = output / "choices.json"
    cost_path = output / "cost-inventory.json"
    prepared_path = output / "prepared.json"
    output.mkdir(parents=True, exist_ok=True)

    if builds_path.exists():
        build_manifest = _load(builds_path)
        if build_manifest.get("provenance") != provenance:
            raise FeedbackReplicationError("prepared build provenance changed")
        built = _validate_builds(design, validation_path, output, build_manifest.get("cases", []))
    else:
        built = _build_all(design, validation_path, output)
        build_manifest = {"schema_version": 1, "provenance": provenance, "cases": built}
        _save(builds_path, build_manifest)

    fit_started = time.perf_counter()
    models, forest = _fit_controls(source_cases, training_rows, design)
    fit_seconds = time.perf_counter() - fit_started
    model_checkpoint = {"schema_version": 1, "provenance": provenance, "models": models}
    if model_path.exists():
        if _load(model_path) != model_checkpoint:
            raise FeedbackReplicationError("fitted control checkpoint changed")
    else:
        _save(model_path, model_checkpoint)

    if cost_path.exists():
        cost_inventory = _load(cost_path)
        if not isinstance(cost_inventory, dict) or cost_inventory.get("provenance") != provenance:
            raise FeedbackReplicationError("cost inventory provenance changed")
        fit_seconds = cost_inventory.get("fit_seconds")
        prediction_cost = cost_inventory.get("predictions_seconds")
        if (
            isinstance(fit_seconds, bool)
            or not isinstance(fit_seconds, (int, float))
            or not math.isfinite(fit_seconds)
            or fit_seconds < 0
            or prediction_cost is not None and (
                isinstance(prediction_cost, bool)
                or not isinstance(prediction_cost, (int, float))
                or not math.isfinite(prediction_cost)
                or prediction_cost < 0
            )
        ):
            raise FeedbackReplicationError("saved preparation costs are invalid")
    else:
        cost_inventory = {
            "schema_version": 1,
            "provenance": provenance,
            "fit_seconds": fit_seconds,
            "predictions_seconds": None,
        }
        _save(cost_path, cost_inventory)

    existing_choices = _load(choices_path) if choices_path.exists() else None
    if existing_choices is not None:
        if existing_choices.get("provenance") != provenance:
            raise FeedbackReplicationError("frozen choice provenance changed")
        choices = existing_choices.get("choices")
        if not isinstance(choices, dict) or set(choices) != {case["case_id"] for case in built}:
            raise FeedbackReplicationError("frozen choice checkpoint has a different case set")
        for case in built:
            packet = feedback._provider_packet(case, design, informed=True)
            row = choices[case["case_id"]]
            if row.get("packet_sha256") != loop_experiment._packet_fingerprint(packet):
                raise FeedbackReplicationError(f"frozen packet changed for {case['case_id']}")
            _verify_frozen_slots(case, packet, output, row.get("provider_replicates", []))
    else:
        prediction_started = time.perf_counter()
        predictions = {
            case["case_id"]: _predict_controls(models, forest, case, design)
            for case in built
        }
        measured_prediction_seconds = time.perf_counter() - prediction_started
        if cost_inventory["predictions_seconds"] is None:
            cost_inventory["predictions_seconds"] = measured_prediction_seconds
            _save(cost_path, cost_inventory)
        choices = {}
        for case in built:
            controls = predictions[case["case_id"]]
            packet = feedback._provider_packet(case, design, informed=True)
            slots = _query_replicates(
                case,
                packet,
                output,
                design["provider_replicates"],
                design.get("provider_max_attempts", 1),
            )
            choices[case["case_id"]] = {
                **controls,
                "packet_sha256": loop_experiment._packet_fingerprint(packet),
                "provider_replicates": slots,
            }
        _save(choices_path, {
            "schema_version": 1,
            "provenance": provenance,
            "choices": choices,
        })

    if cost_inventory["predictions_seconds"] is None:
        raise FeedbackReplicationError("selector prediction cost was not frozen")

    if len(built) != 24 or len(choices) != 24 or any(
        len(row.get("provider_replicates", [])) != REPLICATES for row in choices.values()
    ):
        raise FeedbackReplicationError("preparation did not freeze all 24 cases and 72 provider slots")
    prepared = {
        "schema_version": 1,
        "status": "prepared",
        "provenance": provenance,
        "design": str(design_path),
        "source_design": str(source_design_path),
        "source_output": str(source_output),
        "validation_report": str(validation_path),
        "training_source": training_evidence,
        "builds": {"path": str(builds_path), "sha256": _sha256(builds_path), "case_count": len(built)},
        "training_models": {"path": str(model_path), "sha256": _sha256(model_path)},
        "choices": {"path": str(choices_path), "sha256": _sha256(choices_path)},
        "cost_inventory": {"path": str(cost_path), "sha256": _sha256(cost_path)},
        "cost_inventory_seconds": {
            "fit_seconds": cost_inventory["fit_seconds"],
            "predictions_seconds": cost_inventory["predictions_seconds"],
        },
        "provider_slots": sum(len(row["provider_replicates"]) for row in choices.values()),
        "provider_choice_agreement": _choice_agreement(choices),
        "fresh_timing_started": False,
    }
    if prepared_path.exists() and _load(prepared_path) != prepared:
        raise FeedbackReplicationError("prepared checkpoint changed on resume")
    if not prepared_path.exists():
        _save(prepared_path, prepared)
    return prepared


def _choice_agreement(choices: dict[str, dict[str, Any]]) -> dict[str, int]:
    unanimous = 0
    pairwise = 0
    pair_count = 0
    for row in choices.values():
        actions = [slot["action"] for slot in row["provider_replicates"]]
        unanimous += len(set(actions)) == 1
        for left in range(len(actions)):
            for right in range(left + 1, len(actions)):
                pair_count += 1
                pairwise += actions[left] == actions[right]
    return {
        "cases": len(choices),
        "unanimous_cases": unanimous,
        "pairwise_agreements": pairwise,
        "pairwise_comparisons": pair_count,
    }


def _expected_runtime_cases(
    cases: list[dict[str, Any]], choices: dict[str, dict[str, Any]], block: int
) -> list[dict[str, Any]]:
    """Add the arithmetic mean of the three Jev-selected runtimes per round."""
    out = []
    for case in cases:
        case_choices = choices[case["case_id"]]["provider_replicates"]
        action_names = [slot["action"] for slot in case_choices]
        records = []
        for row in case["benchmark"]["round_records"]:
            if row["block"] != block:
                continue
            values = [row["measurements"][action]["elapsed_ns"] for action in action_names]
            records.append({
                **row,
                "measurements": {
                    **row["measurements"],
                    "jev_expected": {"elapsed_ns": sum(values) / REPLICATES},
                },
            })
        if len(records) != 11:
            raise FeedbackReplicationError(f"case {case['case_id']} does not have eleven rounds in block {block}")
        out.append({**case, "benchmark": {**case["benchmark"], "round_records": records}})
    return out


def _comparison(
    cases: list[dict[str, Any]], choices: dict[str, dict[str, str]], candidate: str, reference: str, block: int
) -> dict[str, Any]:
    return contraction._comparison(cases, choices, candidate, reference, block)


def _analyze_block(
    cases: list[dict[str, Any]], choices: dict[str, dict[str, Any]], design: dict[str, Any], block: int
) -> dict[str, Any]:
    expected_cases = _expected_runtime_cases(cases, choices, block)
    expected_choices = {
        case["case_id"]: {"expected_jev": "jev_expected", **{
            name: choices[case["case_id"]][name] for name in CONTROLS
        }}
        for case in cases
    }
    expected_comparisons = {
        control: _comparison(expected_cases, expected_choices, "expected_jev", control, block)
        for control in CONTROLS
    }
    noise = {
        control: max(
            math.log1p(design["noise_floor_fraction"]),
            3 * result["maximum_state_mad_log_ratio"],
        )
        for control, result in expected_comparisons.items()
    }
    group_direction = {
        control: {
            group: result["geometric_mean_ratio"] < 1
            for group, result in result["families"].items()
        }
        for control, result in expected_comparisons.items()
    }
    requirements = {
        f"expected_jev_beyond_noise_vs_{control}":
            math.log(expected_comparisons[control]["geometric_mean_ratio"]) < -noise[control]
        for control in CONTROLS
    }
    requirements.update({
        f"expected_jev_positive_in_four_groups_vs_{control}": sum(group_direction[control].values()) >= 4
        for control in CONTROLS
    })

    individual: dict[str, Any] = {}
    for replicate in range(1, REPLICATES + 1):
        rep_choices = {
            case["case_id"]: {
                f"jev_replicate_{replicate}": choices[case["case_id"]]["provider_replicates"][replicate - 1]["action"],
                **{name: choices[case["case_id"]][name] for name in CONTROLS},
            }
            for case in cases
        }
        individual[f"replicate_{replicate}"] = {
            f"jev_vs_{control}": _comparison(cases, rep_choices, f"jev_replicate_{replicate}", control, block)
            for control in CONTROLS
        }
    return {
        "block": block,
        "rounds_per_case": 11,
        "expected_jev_vs_controls": expected_comparisons,
        "noise_floor_log_ratio": noise,
        "group_positive_direction": group_direction,
        "requirements": requirements,
        "passed": all(requirements.values()),
        "individual_provider_replicates": individual,
        "measurement_partition_adapter": "Fresh test cases are represented internally as discovery only to reuse the frozen two-block measurement helper; published cases remain test.",
    }


def _prepared_inputs(
    design_path: Path,
    validation_path: Path,
    output: Path,
    source_output: Path,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    design_path, validation_path, output, source_output = (
        path.expanduser().resolve() for path in (design_path, validation_path, output, source_output)
    )
    source_design_path = (ROOT / "experiments" / "compiler-feedback-v1.json").resolve()
    design = _design(design_path, source_design_path)
    source_cases, training_rows, training_evidence = _source_training(design, source_output, source_design_path)
    try:
        import sklearn
    except ImportError as error:
        raise FeedbackReplicationError("scikit-learn is required for the frozen random-forest control") from error
    provenance = _provenance(
        design_path, source_design_path, source_output, validation_path, training_evidence, sklearn.__version__
    )
    prepared_path = output / "prepared.json"
    prepared = _load(prepared_path)
    if prepared.get("status") != "prepared" or prepared.get("fresh_timing_started") is not False:
        raise FeedbackReplicationError("no valid pre-timing preparation checkpoint exists")
    if prepared.get("provenance") != provenance:
        raise FeedbackReplicationError("prepared provenance does not match current frozen inputs")
    for name in ("builds", "training_models", "choices", "cost_inventory"):
        ref = prepared.get(name, {})
        path = Path(ref.get("path", ""))
        if not path.is_file() or _sha256(path) != ref.get("sha256"):
            raise FeedbackReplicationError(f"prepared {name} checkpoint is missing or changed")
    build_manifest = _load(Path(prepared["builds"]["path"]))
    if build_manifest.get("provenance") != provenance:
        raise FeedbackReplicationError("prepared build manifest provenance changed")
    built = _validate_builds(design, validation_path, output, build_manifest.get("cases", []))
    choices_file = _load(Path(prepared["choices"]["path"]))
    if choices_file.get("provenance") != provenance:
        raise FeedbackReplicationError("prepared choices provenance changed")
    choices = choices_file.get("choices")
    if not isinstance(choices, dict) or set(choices) != {case["case_id"] for case in built}:
        raise FeedbackReplicationError("prepared choices have a different case set")
    if any(len(row.get("provider_replicates", [])) != REPLICATES for row in choices.values()):
        raise FeedbackReplicationError("prepared choices do not contain three provider slots per case")
    cost_inventory = _load(Path(prepared["cost_inventory"]["path"]))
    if (
        not isinstance(cost_inventory, dict)
        or cost_inventory.get("provenance") != provenance
        or any(
            isinstance(cost_inventory.get(name), bool)
            or not isinstance(cost_inventory.get(name), (int, float))
            or not math.isfinite(cost_inventory[name])
            or cost_inventory[name] < 0
            for name in ("fit_seconds", "predictions_seconds")
        )
    ):
        raise FeedbackReplicationError("prepared fit/prediction costs are missing or invalid")
    for case in built:
        packet = feedback._provider_packet(case, design, informed=True)
        row = choices[case["case_id"]]
        packet_hash = loop_experiment._packet_fingerprint(packet)
        if row.get("packet_sha256") != packet_hash:
            raise FeedbackReplicationError(f"prepared packet changed for {case['case_id']}")
        _verify_frozen_slots(case, packet, output, row["provider_replicates"])
    return design, prepared, built, choices, {
        "provenance": provenance,
        "source_cases": source_cases,
        "training_rows": training_rows,
        "training_evidence": training_evidence,
        "cost_inventory": cost_inventory,
    }


def _preflight_build_reports(design: dict[str, Any], output: Path) -> None:
    """Fail before build validation can enter its build-if-report-is-missing path."""
    manifest_path = output / "builds.json"
    if not manifest_path.is_file():
        raise FeedbackReplicationError("measurement refuses to rebuild: prepared build manifest is missing")
    manifest = _load(manifest_path)
    recorded = manifest.get("cases") if isinstance(manifest, dict) else None
    expected = _cases(design)
    if not isinstance(recorded, list) or len(recorded) != len(expected):
        raise FeedbackReplicationError("measurement refuses to rebuild: prepared build manifest is incomplete")

    problems = []
    for want, have in zip(expected, recorded, strict=True):
        if not isinstance(have, dict) or any(
            have.get(key) != want[key] for key in ("partition", "group", "family", "case_id", "spec")
        ):
            raise FeedbackReplicationError("measurement refuses to rebuild: prepared build case identity changed")
        expected_path = (
            output / "cases" / want["partition"] / want["case_id"] / "build" / "report.json"
        ).resolve()
        try:
            report_path = Path(have["build_report_path"]).resolve()
            expected_hash = have["build_report_sha256"]
        except (KeyError, TypeError, ValueError):
            problems.append(f"{want['case_id']}: missing report reference")
            continue
        if report_path != expected_path:
            problems.append(f"{want['case_id']}: unexpected report path")
        elif not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            problems.append(f"{want['case_id']}: invalid report hash")
        elif not report_path.is_file():
            problems.append(f"{want['case_id']}: report is missing")
        elif _sha256(report_path) != expected_hash:
            problems.append(f"{want['case_id']}: report hash changed")
    if problems:
        raise FeedbackReplicationError(
            "measurement refuses to rebuild; build report preflight failed: " + "; ".join(problems)
        )


def run_measure(
    design_path: Path,
    validation_path: Path,
    output: Path,
    source_output: Path = DEFAULT_SOURCE_OUTPUT,
) -> dict[str, Any]:
    design_path, validation_path, output, source_output = (
        path.expanduser().resolve() for path in (design_path, validation_path, output, source_output)
    )
    source_design_path = (ROOT / "experiments" / "compiler-feedback-v1.json").resolve()
    design = _design(design_path, source_design_path)
    _preflight_build_reports(design, output)
    design, prepared, built, choices, context = _prepared_inputs(
        design_path, validation_path, output, source_output
    )
    if not validation_path.is_file():
        raise FeedbackReplicationError("validation report is missing")
    measurement_cases = [{**case, "partition": "discovery"} for case in built]
    # This internal adapter bypasses the legacy helper's block-two discovery-only
    # guard. The output report restores the experiment's actual test partition.
    measured = contraction._measure_cases(measurement_cases, design, output, blocks=BLOCKS)
    attached = feedback._attach_compiler_evidence(
        measured,
        [{**case, "partition": "discovery"} for case in built],
    )
    for case in attached:
        case["partition"] = "test"
    if {case["case_id"] for case in attached} != {case["case_id"] for case in built}:
        raise FeedbackReplicationError("measurement omitted or changed fresh cases")
    report_blocks = {
        str(block): _analyze_block(attached, choices, design, block) for block in BLOCKS
    }
    report = {
        "schema_version": 1,
        "status": "passed" if all(block["passed"] for block in report_blocks.values()) else "completed_no_success_gate",
        "provenance": context["provenance"],
        "prepared_sha256": _sha256(output / "prepared.json"),
        "build_manifest_sha256": prepared["builds"]["sha256"],
        "training_model_sha256": prepared["training_models"]["sha256"],
        "choice_checkpoint_sha256": prepared["choices"]["sha256"],
        "cost_inventory": {
            "path": prepared["cost_inventory"]["path"],
            "sha256": prepared["cost_inventory"]["sha256"],
            "fit_seconds": context["cost_inventory"]["fit_seconds"],
            "predictions_seconds": context["cost_inventory"]["predictions_seconds"],
        },
        "provider_choice_agreement": _choice_agreement(choices),
        "provider_calls": {
            "case_count": len(choices),
            "attempt_slots": sum(len(row["provider_replicates"]) for row in choices.values()),
            "provider": "Jev",
            "distinct_cache_files": len({slot["cache_path"] for row in choices.values() for slot in row["provider_replicates"]}),
            "statistical_independence_established": False,
        },
        "training_source": context["training_evidence"],
        "candidate_controls": list(CONTROLS),
        "expected_jev_definition": "Arithmetic mean of elapsed_ns for the three frozen Jev-selected actions within each case and benchmark round; no best-of-three selection.",
        "block_results": report_blocks,
        "cases": [
            {
                **{key: case[key] for key in ("partition", "family", "group", "case_id", "spec", "iterations", "timing_caches")},
                "choices": choices[case["case_id"]],
                "benchmark": case["benchmark"],
            }
            for case in attached
        ],
        "internal_measurement_partition": "discovery",
        "combined_blocks": "not aggregated; each block is a primary result",
    }
    report_path = output / "report.json"
    if report_path.exists() and _load(report_path) != report:
        raise FeedbackReplicationError("measurement report changed on resume")
    if not report_path.exists():
        _save(report_path, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("prepare", "measure"), required=True)
    parser.add_argument("--design", type=Path, default=DEFAULT_DESIGN)
    parser.add_argument("--source-output", type=Path, default=DEFAULT_SOURCE_OUTPUT)
    parser.add_argument("--validation-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.phase == "prepare":
            result = run_prepare(args.design, args.validation_report, args.output, args.source_output)
        else:
            result = run_measure(args.design, args.validation_report, args.output, args.source_output)
    except (
        FeedbackReplicationError,
        feedback.CompilerFeedbackError,
        contraction.ContractionExperimentError,
        contraction_build.ContractionBuildError,
        OSError,
        ValueError,
    ) as error:
        print(f"feedback-replication experiment failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"status": result["status"], "output": str(args.output.resolve())}, sort_keys=True))
    return 0 if result["status"] in ("prepared", "passed", "completed_no_success_gate") else 1


if __name__ == "__main__":
    raise SystemExit(main())
