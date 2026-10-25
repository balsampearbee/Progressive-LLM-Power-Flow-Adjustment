# Progressive LLM-Based Power Flow Adjustment

Reference implementation of a progressive LLM–simulation framework for power flow adjustment, with an IEEE 39-bus demonstration. The method combines task-conditioned physical relation encoding, LLM device/direction selection, and AC-verified action magnitude determination.

## Installation

```bash
pip install -r requirements.txt
```

## Run

```bash
# Offline demonstration (scripted device selection; no LLM API required)
python run_demo.py --mode offline --out-dir demo_output

# Reproduce recorded LLM decisions and AC power-flow results
python verify_trace.py --run-dir example --out trace_check.json
python verify_ac.py --run-dir example --out ac_check.json

# Unit and regression tests
python -m unittest discover -s tests
```

For a new DeepSeek Flash run, use `python run_demo.py --mode live --ask-key --out-dir live_output` and enter your own API key.

## Contents

- `src/`: relation encoder, action selection, AC power-flow solver, and adapters.
- `example/`: recorded LLM responses and AC adjustment trajectory for a modified IEEE 39-bus case.
- `tests/` and `fixtures/`: unit tests and interface examples.

**Scope:** The provided run is a single synthetic IEEE39 demonstration using DeepSeek Flash and monitored buses 1–29; its offline mode uses scripted selection. It does not reproduce the paper's 400-case Qwen comparison or the confidential CSG system. See [Data provenance](DATA_PROVENANCE.md) for the IEEE39 case-data notice.
