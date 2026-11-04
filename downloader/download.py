import os
from pathlib import Path

from huggingface_hub import snapshot_download

model_id = os.environ["MODEL_ID"]
revision = os.environ["MODEL_REVISION"]
folder = Path("/models") / revision
ready = folder / ".download-complete"

if ready.is_file() and ready.read_text() == model_id:
    print("Модель уже на диске. Скачивание не требуется.")
else:
    snapshot_download(
        repo_id=model_id,
        revision=revision,
        local_dir=folder,
        token=os.getenv("HF_TOKEN") or False,
        allow_patterns=["*.json", "*.safetensors", "*.txt"],
    )
    required = ["model.safetensors", "config.json", "tokenizer.json"]
    for name in required:
        file = folder / name
        if not file.is_file() or file.stat().st_size == 0:
            raise RuntimeError(f"Отсутствует или пуст файл {name}")

    ready.write_text(model_id)
    print("Модель сохранена на диске.")
