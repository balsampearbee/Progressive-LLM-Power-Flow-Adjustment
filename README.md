# Task-Conditioned LLM–Simulation Power Flow Adjustment: IEEE 39-Bus Reproducibility Package

**Release candidate for author review.** This package was prepared from the archived V7 real-API closed-loop demonstration dated 2026-10-18. It reproduces a **single synthetic modified IEEE 39-bus case**, including source code and a recorded 14-call DeepSeek Flash demonstration. It is **not** a reproduction of all experiments reported in the manuscript.

## Contents

- `src/`: physical relation extraction, device/direction interface, candidate enumeration/selection, black-box AC solver adapter, and demonstration solver.
- `src/case39_local.m`: IEEE 39-bus reference case data (derived from MATPOWER case39; see `THIRD_PARTY_DATA_NOTICE.md`).
- `src/demo_scenario.json`: synthetic disturbed scenario, initial generator settings and shunt configuration.
- `fixtures/ieee39.dat`, `fixtures/ieee39.pf`: sample input/output parser fixtures used in regression tests; **not** real utility-grid records.
- `recorded_live_run/`: 14 recorded credential-free API exchanges, 14 round records, and accepted AC state transitions from the frozen V7 demonstration.
- `verify_v7.py`: structural replay, including source prompt, context, recorded model response, AC trials, and commit checks.
- `verify_physical_v7.py`: independent candidate-by-candidate AC replay that does not import the action-determination module.
- `tests/`: unit and regression tests.

## Installation

Tested historically on Python 3.12.14 with `numpy==2.3.5`, `scipy==1.17.0`, `networkx==3.7`; Python 3.13 also passes in the isolated build check with NetworkX 3.6.1. No proprietary commercial power-flow engine is required for this demonstration.

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python run_tests_v7.py
python verify_v7.py --run-dir recorded_live_run --out recorded_structural_check.json
python verify_physical_v7.py --run-dir recorded_live_run --out recorded_physical_check.json
python run_v7.py --mode offline --out-dir offline_demo
```

To reproduce a **new** paid real-LLM call (optional), provide your own credential and an empty output directory:

```bash
python run_v7.py --mode live --ask-key --out-dir my_live_demo
```

`--mode offline` uses a **scripted engineering selector**, not LLM inference. The recorded live trace uses DeepSeek Flash and is independently replayable without any API access. Never commit API keys or secrets.

## Scope and differences from the full paper benchmarks

1. This archive contains **one synthetic demonstration scenario**, not the 400-case IEEE 39-bus test partition underlying the paper's statistical comparisons. The original 400-case input files and MIQCP/DDQN/PPO/Eng-Exp/Direct LLM benchmark implementations are not contained in the archived V7 deliverable.
2. The demonstration monitors buses **1–29** via `src/schemas.py` (Config default); the manuscript's evaluation protocol states **all 39 buses** are monitored for the benchmark. These scopes should not be conflated.
3. This live demonstration uses **DeepSeek Flash**, whereas the manuscript reports comparisons using **Qwen3.5-9B and Qwen3.6-27B** through vLLM. The demonstration's outcomes do not establish those statistical claims.
4. The demonstration IEEE39 system has a single balancing area, so it does not exercise a physical inter-area HVDC connection; corresponding algebra and guards are tested with synthetic unit fixtures.
5. The historical real run has **14 live calls**, **11 committed actions**, **45 AC power-flow calls**, and terminal feasible status, as recorded in the original `outputs_v7/V6_V7_AGGREGATE_STATS.json`.
6. The recorded test suite originally had 38 tests: 37 passed, one obsolete V6 assertion was explicitly skipped after V7 changed the continuous-P policy. The test runner retains that skip notice.

## Physical-model and provenance notes

The demonstration includes a bundled Newton AC power-flow solver and MATPOWER-derived case39 matrix data, with a clear data provenance notice. Its solver is a research demonstration: it is not the paper's DSP production engine. The public-release rights of the case-data derivative should be reviewed before uploading the repository. The 15,517-bus CSG dataset is not included.

**Publication status:** staged reproducibility package. No public repository URL or open-source license is asserted by this archive. Assign an approved source license and verify third-party data reuse before publishing, then insert the verified repository URL into the final paper.
