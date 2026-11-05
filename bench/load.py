import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from statistics import median
from time import perf_counter

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

for scenario in ("short", "long"):
    filename = f"load_{scenario}_{stamp}.jsonl"
    env = {**os.environ, "SERIES_FILE": filename}
    print(f"Running {scenario}: 6 requests, 2 clients", flush=True)

    def run(number):
        output = subprocess.check_output(
            ["python3", "bench/measure_case.py", scenario, str(number)],
            env=env,
            text=True,
        )
        print(output.strip(), flush=True)
        return json.loads(output)

    start = perf_counter()
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(run, range(101, 107)))
    elapsed = perf_counter() - start

    print(json.dumps({
        "scenario": scenario,
        "requests": len(rows),
        "median_ttft_s": round(median(r["ttft_s"] for r in rows), 3),
        "median_latency_s": round(median(r["e2e_s"] for r in rows), 3),
        "throughput_output_tokens_s": round(sum(r["output_tokens"] for r in rows) / elapsed, 2),
        "elapsed_s": round(elapsed, 2),
        "results": filename,
    }), flush=True)
