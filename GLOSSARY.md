# Bounded compiler decisions

Language for the compiler decision-policy experiment.

## Language

**Harness compiler**:
The compiler architecture formed by a decision harness and an existing compiler engine. The harness governs bounded internal choices, escalation, validation and recorded evidence; the engine supplies compilation mechanisms and target constraints. In this experiment LLVM is the engine and register eviction is the first decision boundary.
_Avoid_: A replacement for LLVM, a command-line flag tuner

**Selection policy**:
The rules or learned judgement that choose a compiler action from the permitted alternatives for a program and target profile.
_Avoid_: Validator, transformation

**Action mechanism**:
A defined compiler operation that implements a selected action; its existence does not determine when that action should be chosen.
_Avoid_: Selection policy

**Acceptance check**:
A mechanical check of a candidate against a stated requirement. Target loadability and application behaviour are separate requirements.
_Avoid_: Model approval

**Target profile**:
The versioned target facts, permitted actions, optimisation objective and relevant guidance supplied to a selector.
_Avoid_: Hidden target assumptions

**Production reference**:
The unchanged compiler configuration used to measure what the experiment preserves, loses or recovers when a selected policy is simplified or replaced.
_Avoid_: Simplified baseline

**Decision oracle**:
An offline record of measured outcomes for the declared action set, used to score decisions without revealing held-out answers to selectors.
_Avoid_: Global optimum
