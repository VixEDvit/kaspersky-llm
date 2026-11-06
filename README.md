# Qwen

Локальный сервис на базе Qwen2.5-0.5B-Instruct и vLLM. Запросы отправляются через OpenAI-совместимый API. Сервер работает на CPU.

## Запуск

Проверено на Windows 11 с Ubuntu в WSL и Kubernetes в Docker Desktop. Узел назывался `desktop-control-plane`. Процессор — Intel Core i7-1260P, образы — `linux/amd64`, vLLM — `0.28.0+cpu`.

**1.** Открыть Bash в корне репозитория и проверить, что узел имеет статус `Ready`.

```bash
docker info --format '{{.ServerVersion}}'
kubectl config current-context
kubectl get nodes
```

**2.** Собрать два образа и передай их узлу Docker Desktop.

```bash
docker build --platform linux/amd64 -t model-downloader:0.1 ./downloader
docker build --platform linux/amd64 -t qwen-server:0.1 ./server
```

```bash
set -o pipefail
for image in model-downloader:0.1 qwen-server:0.1; do
  docker image save --platform linux/amd64 "$image" |
    docker exec -i desktop-control-plane ctr --namespace=k8s.io images import - || break
done
docker exec desktop-control-plane crictl images | grep -E 'model-downloader|qwen-server'
```

В списке образов узла должны быть `model-downloader:0.1` и `qwen-server:0.1`. Команда импорта рассчитана на конфигурацию Docker Desktop, в которой узел доступен через `docker exec desktop-control-plane`.

**3.** Создать ключ API и развернуть сервис.

```bash
if ! kubectl get secret model-access >/dev/null; then
  python3 -c 'import secrets, sys; sys.stdout.write(secrets.token_hex(32))' |
    kubectl create secret generic model-access --from-file=API_KEY=/dev/stdin
fi
kubectl apply -k k8s
kubectl rollout status deployment/qwen-server --timeout=10m
kubectl get pvc model-storage
kubectl get pods -l app=qwen-server
```

После запуска ожидаются PVC `Bound` и Pod `1/1 Running`. Модель, ревизия и лимит контекста заданы в `k8s/model-config.yaml`.

**4.** Оставить проброс порта работать в отдельном терминале.

```bash
kubectl port-forward --address 127.0.0.1 service/qwen-server 18000:8000
```

Во втором терминале проверить доступность API и отправить запрос.

```bash
curl --noproxy '*' --max-time 10 -sS -o /dev/null \
  -w 'health: HTTP %{http_code}\n' http://127.0.0.1:18000/health

python3 - <<'PY'
import base64
import json
import subprocess
from urllib.request import ProxyHandler, Request, build_opener

encoded = subprocess.check_output([
    "kubectl", "get", "secret", "model-access",
    "-o", "jsonpath={.data.API_KEY}",
])
key = base64.b64decode(encoded).decode()
payload = {
    "model": "qwen",
    "messages": [{"role": "user", "content": "Сколько будет 2 + 3? Ответь только числом."}],
    "temperature": 0,
    "max_tokens": 16,
}
req = Request(
    "http://127.0.0.1:18000/v1/chat/completions",
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
)
with build_opener(ProxyHandler({})).open(req, timeout=180) as response:
    print("HTTP", response.status)
    print("Ответ Qwen:", json.load(response)["choices"][0]["message"]["content"])
PY
```

В проверенном запуске оба запроса вернули `200`, а модель ответила `5`. Первый запрос после запуска иногда завершался по тайм-ауту при успешном `/health`. Состояние Pod и логи сервера можно посмотреть так:

```bash
kubectl get pods -l app=qwen-server
kubectl logs deployment/qwen-server -c server --since=10m --tail=60
```

## Бонус 1.

Команда `kubectl apply -k k8s` применяет ресурсы из `k8s/kustomization.yaml`. В него входят Deployment, Service, PVC и два ConfigMap. Перед применением манифесты можно проверить командой `kubectl apply -k k8s --dry-run=server`.

## Бонус 2.

Kubernetes запускает `prepare-model` как init-контейнер перед vLLM. Его образ собирается из `downloader/Dockerfile` на базе `python:3.12-slim` и запускается под UID `10001`.

Скрипт скачивает выбранную проверку с Hugging Face на PVC `model-storage`. После проверки файлов он записывает отметку о завершении. При пересоздании Pod загрузчик нашёл эту отметку и использовал веса с диска. Сервер монтирует том для чтения и загружает модель по локальному пути.

Сервер также запускается под UID `10001`. Его базовый образ закреплён по digest. Все 154 версии установленных Python-пакетов сверены с `server/requirements.txt`.

### Проверка API и журнала

Запрос к `/v1/models` без ключа вернул `401`, с ключом — `200`. Тестовые запросы к `/v1/chat/completions` и `/v1/completions` вернули `200`. В журнале появились `chat_request status=200` и `completion_request status=200`. Поиск по тестовым меткам и фрагментам ответов совпадений не дал.

## Бонус 3.

При работающем пробросе порта выполнить из корня репозитория:

```bash
SERIES_FILE="warmup_$(date +%Y%m%d_%H%M%S).jsonl" python3 bench/measure_case.py short 100
python3 bench/load.py
```

По шесть запросов на сценарий, до двух одновременно со стороны клиента. На сервере стоял `--max-num-seqs 1`. Каждый запрос сгенерировал 32 токена. Прогрев после перезапуска занял 150,073 с и измерялся отдельно.

| Промпт | Входных токенов на запрос | Запросов | Медиана TTFT, с | Медиана Latency, с | Throughput, выходных токенов/с |
|---|---:|---:|---:|---:|---:|
| Короткий | 48 | 6 | 2,151 | 6,364 | 5,97 |
| Длинный | 608 | 6 | 6,742 | 9,930 | 4,35 |

TTFT оценивался по времени до первого непустого фрагмента текста. Latency измерялось до конца потока. Для расчёта throughput 192 выходных токена делились на время всей серии с учётом работы клиента. Короткая серия заняла 32,19 с, длинная — 44,15 с. В этом прогоне медиана TTFT для длинного промпта оказалась примерно в 3,1 раза выше.

Исходные данные: [короткие промпты](bench/load_short_20261105_205224.jsonl), [длинные промпты](bench/load_long_20261105_205224.jsonl).

### Настройки для следующих замеров

- Для потока коротких запросов стоит сравнить `--max-num-seqs 1` и `2` при двух клиентах. Так можно проверить, повышает ли совместная обработка запросов пропускную способность и как меняется задержка.
- Для длинных промптов стоит сравнить `--max-num-batched-tokens 512` и `1024` при включённом chunked prefill. Это бюджет токенов на шаг планировщика. При каждом бюджете стоит измерить throughput, TTFT и расход памяти.
