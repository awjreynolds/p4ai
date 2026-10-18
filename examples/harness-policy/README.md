# Offline decision replay

Run from the repository root with Python 3.12 or later. No model service, credentials, scikit-learn, GPU or native compiler is needed to replay this recorded feature packet:

```sh
python3 -S -m scripts.harness_policy \
  --artifact examples/harness-policy/policy.json \
  --packet examples/harness-policy/packet.json \
  --compiler-identity examples/harness-policy/compiler-identity.json \
  --output /tmp/p4ai-decision.json
diff examples/harness-policy/expected-decision.json /tmp/p4ai-decision.json
```

Expected action: `ijk`. The record contains the six predicted log-loss scores, canonical input hashes and compiler identity. The forest was trained on this project's measured outcomes, not on Jev answers. Its inference implementation uses only the standard library and is checked against the training library in the test suite.

This packet is the original development case `discovery-small-ccr`, compiled by the pinned LLVM on Apple M3. This is a replay demonstration, not a fresh quality result. The supplied identity describes that recorded compilation; this command does not inspect a local compiler or rebuild a binary. The native harness demonstration verifies the actual compiler and build inputs separately.

The artifact carries weights learned by this project. It is not a pretrained language model or a signed artifact. The hashes support identity checks, not authenticity. Altering a compiler identity rejects replay; an authorized transfer to another compiler must create a separately identified artifact and be evaluated there.
