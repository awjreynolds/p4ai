# Development-data policy diagnostic

This diagnostic reuses the original 36 compiler-feedback cases, including their former test partition. It is development evidence, not a fresh performance claim. No native benchmarks or provider calls were made.

Nine distinct recipes, each with four layouts and six action labels, are separated by leave-one-recipe-out folds. Within each fold, deterministic nested subsets contain two, four or eight other recipes. Feature scaling and fitting use only that fold’s training rows. Each size uses one fixed subset, so these curves do not average over subset variability. No hyperparameters were tuned.

| Features | Model | Training recipes per fold | Runtime / stock | Runtime / measured oracle |
| --- | --- | ---: | ---: | ---: |
| source30 | ridge | 2 | 0.4482 | 1.4746 |
| source30 | random_forest | 2 | 0.4175 | 1.3736 |
| source30 | ridge | 4 | 0.4160 | 1.3687 |
| source30 | random_forest | 4 | 0.3445 | 1.1334 |
| source30 | ridge | 8 | 0.4190 | 1.3785 |
| source30 | random_forest | 8 | 0.3656 | 1.2026 |
| compiler42 | ridge | 2 | 0.3486 | 1.1468 |
| compiler42 | random_forest | 2 | 0.4304 | 1.4159 |
| compiler42 | ridge | 4 | 0.3531 | 1.1617 |
| compiler42 | random_forest | 4 | 0.3927 | 1.2919 |
| compiler42 | ridge | 8 | 0.3526 | 1.1600 |
| compiler42 | random_forest | 8 | 0.3330 | 1.0953 |

The measured oracle is approximately 0.3040 times stock runtime; the unchanged stride selector is approximately 0.4066. Stock is canonical source order, not a production compiler scheduling policy. Ratios are geometric means balanced by recipe.

Compiler evidence helped ridge at all three sizes. The forest’s results were non-monotonic: its strongest source-only result used four recipes, while compiler-informed forest was strongest with eight. This does not identify a universally superior model or establish sample efficiency. Missing representation, sparse recipes and workload heterogeneity remain plausible explanations.

The compiler-informed input requires compiling candidate alternatives before choosing; its runtime advantage does not establish lower end-to-end compilation cost. Fit/inference timings are retained in the JSON but do not include historical label acquisition.

Reproduce with installed project development dependencies:

```sh
uv run --offline python -m scripts.policy_learning_diagnostic --output artifacts/policy-learning-diagnostic-v1/report.json
```

The [complete JSON](policy-learning-diagnostic-v1.json) records grouping, choices, recipe counts, fixed parameters, evidence hashes and costs. The follow-on [compiler-shift design](../policy-shift-design.md) was fixed independently of these development results and keeps its final outcomes separate.
