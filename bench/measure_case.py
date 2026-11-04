import os
import sys
import base64
import json
import subprocess
import time
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

encoded_key = subprocess.check_output([
    "kubectl", "get", "secret", "model-access",
    "-o", "jsonpath={.data.API_KEY}",
])
key = base64.b64decode(encoded_key).decode()

scenario, number = sys.argv[1:3]
question = f"Опыт {number}. "
if scenario == "long":
    question += "Датчик измеряет температуру воздуха. " * 40
question += "Назови три цвета радуги."
request = Request(
    "http://127.0.0.1:18000/v1/chat/completions",
    headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
    data=json.dumps({
        "model": "qwen",
        "messages": [{"role": "user", "content": question}],
        "temperature": 0,
        "max_tokens": 32,
        "stream": True,
        "stream_options": {"include_usage": True},
    }).encode(),
)

client = build_opener(ProxyHandler({}))
first_text = None
usage = {}
finished = False
start = time.perf_counter()

with client.open(request, timeout=180) as response:
    for line in response:
        if not line.startswith(b"data:"):
            continue
        data = line[5:].strip()
        if data == b"[DONE]":
            finished = True
            break
        event = json.loads(data)
        if event.get("error"):
            raise RuntimeError(event["error"])
        if event.get("usage"):
            usage = event["usage"]
        for choice in event.get("choices", []):
            if first_text is None and choice["delta"].get("content"):
                first_text = time.perf_counter()

elapsed = time.perf_counter() - start
if not finished or first_text is None:
    raise RuntimeError("Полный текстовый ответ не получен")

result = {
    "scenario": scenario, "run": int(number),
    "ttft_s": round(first_text - start, 3),
    "e2e_s": round(elapsed, 3),
    "input_tokens": usage.get("prompt_tokens"),
    "output_tokens": usage.get("completion_tokens"),
}
with Path(__file__).with_name(os.environ["SERIES_FILE"]).open("a", encoding="utf-8") as file:
    file.write(json.dumps(result) + "\n")
print(json.dumps(result))
