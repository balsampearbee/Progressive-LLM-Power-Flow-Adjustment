# Running the reproducibility checks

Install the pinned packages from `requirements.txt` in a clean Python environment.

```bash
python run_tests_v7.py
python verify_v7.py --run-dir recorded_live_run --out recorded_structural_check.json
python verify_physical_v7.py --run-dir recorded_live_run --out recorded_physical_check.json
python run_v7.py --mode offline --out-dir offline_demo
```

The offline demonstration uses scripted selection; it does not call a live LLM.
The recorded historical live trace can be independently checked without credentials.
The paper's 400-case/Qwen evaluation and large-scale CSG benchmark are not contained here.
