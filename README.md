# Qwen на CPU в Kubernetes

## Описание проекта

Проект разворачивает языковую модель Qwen2.5-0.5B-Instruct локально на CPU. Сервер vLLM принимает запросы через OpenAI-совместимый HTTP API. Для обращения к модели нужен ключ. При первом запуске загрузчик скачивает веса модели с Hugging Face на постоянный том Kubernetes. При пересоздании Pod сохранённые веса используются повторно. Нагрузочный тест сравнивает задержку и пропускную способность для коротких и длинных промптов.

## Технологический стек

vLLM запускает Qwen2.5-0.5B-Instruct на CPU и обрабатывает запросы к API. Docker собирает два образа: для загрузчика весов и сервера. Kubernetes управляет запуском контейнеров и хранением весов, а Kustomize объединяет манифесты для развёртывания одной командой. Скрипты загрузки весов и нагрузочного тестирования написаны на Python.

Проект проверен в Kubernetes от Docker Desktop под Windows 11 с Ubuntu в WSL. Узел назывался `desktop-control-plane`. Процессор — Intel Core i7-1260P, архитектура образов — `linux/amd64`, версия vLLM — `0.28.0+cpu`.

## Запуск

**1.** Открыть Bash в корне репозитория и проверить, что узел имеет статус `Ready`.

```bash
docker info --format '{{.ServerVersion}}'
kubectl config current-context
kubectl get nodes
```

Docker выводит версию сервера, `kubectl` показывает контекст кластера, а узел `desktop-control-plane` имеет статус `Ready`.

**2.** Собрать два образа и импортировать их в узел Docker Desktop.

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

После запуска PVC должен иметь статус `Bound`, а Pod — `1/1 Running`. Модель, её ревизия и лимит контекста заданы в `k8s/model-config.yaml`.

Публичная модель Qwen скачивается без токена Hugging Face. При переходе на модель, доступ к которой требует авторизации, загрузчик может получить токен из Secret `huggingface-access`, поле `HF_TOKEN`. Такой Secret можно создать из локального файла с токеном:

```bash
kubectl create secret generic huggingface-access --from-file=HF_TOKEN=/путь/к/файлу_с_токеном
```

**4.** Запустить проброс порта и оставить его работать в отдельном терминале.

```bash
kubectl port-forward --address 127.0.0.1 service/qwen-server 18000:8000
```

Во втором терминале проверить доступность API и отправить запрос. Скрипт читает ключ из Kubernetes Secret.

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

Ожидаемый результат — `health: HTTP 200`, `HTTP 200` и `Ответ Qwen: 5`. При повторной локальной проверке `/health` вернул `HTTP 200`, а запрос на генерацию завершился по тайм-ауту. После перезапуска Pod и повторного проброса порта контрольный запрос вернул `HTTP 200`, модель ответила `5`, а в журнале появилась запись `chat_request status=200`.

Состояние Pod и свежие логи сервера можно посмотреть следующими командами:

```bash
kubectl get pods -l app=qwen-server
kubectl logs deployment/qwen-server -c server --since=10m --tail=60
```

### Проверка API и журнала

Запрос к `/v1/models` без ключа вернул `401`, с ключом — `200`. Тестовые запросы к `/v1/chat/completions` и `/v1/completions` вернули `200`. В журнале появились записи `chat_request status=200` и `completion_request status=200`. При проверке журналов по тестовым меткам и фрагментам ответов совпадений не было.

## Бонус 1. Развёртывание через Kustomize

Команда `kubectl apply -k k8s` применяет ресурсы из `k8s/kustomization.yaml`: Deployment, Service, PVC и два ConfigMap. Перед развёртыванием манифесты можно проверить командой `kubectl apply -k k8s --dry-run=server`.

## Бонус 2. Загрузка модели через init-контейнер

Kubernetes запускает `prepare-model` как init-контейнер перед vLLM. Его образ собирается из `downloader/Dockerfile` на базе `python:3.12-slim` и запускается под UID `10001`.

Скрипт скачивает закреплённую ревизию модели с Hugging Face на PVC `model-storage`. После проверки файлов он записывает отметку о завершении. При пересоздании Pod загрузчик нашёл эту отметку и использовал веса с диска. Сервер монтирует том в режиме только для чтения и загружает модель по локальному пути.

Сервер также запускается под UID `10001`. Его базовый образ закреплён по digest. Версии 154 установленных Python-пакетов сверены с `server/requirements.txt`.

## Бонус 3. Нагрузочное тестирование

При работающем пробросе порта выполнить из корня репозитория:

```bash
SERIES_FILE="warmup_$(date +%Y%m%d_%H%M%S).jsonl" python3 bench/measure_case.py short 100
python3 bench/load.py
```

На каждый сценарий отправлено шесть запросов, до двух одновременно со стороны клиента. На сервере было установлено `--max-num-seqs 1`. Каждый запрос сгенерировал 32 токена. Прогрев после перезапуска измерялся отдельно и занял 150,073 с.

| Промпт | Входных токенов на запрос | Запросов | Медиана TTFT, с | Медиана Latency, с | Throughput, выходных токенов/с |
|---|---:|---:|---:|---:|---:|
| Короткий | 48 | 6 | 2,151 | 6,364 | 5,97 |
| Длинный | 608 | 6 | 6,742 | 9,930 | 4,35 |

TTFT оценивался по времени от отправки запроса до первого непустого фрагмента текста. Latency измерялась от отправки запроса до завершения потока. Для расчёта throughput 192 выходных токена делились на время всей серии с учётом работы клиента. Короткая серия заняла 32,19 с, длинная — 44,15 с. В этом прогоне медиана TTFT для длинного промпта оказалась примерно в 3,1 раза выше.

Шесть запросов на сценарий дают предварительную оценку для этой конфигурации. При двух клиентах и `--max-num-seqs 1` измеренный на стороне клиента TTFT мог включать ожидание в очереди.

Исходные данные: [короткие промпты](bench/load_short_20261105_205224.jsonl), [длинные промпты](bench/load_long_20261105_205224.jsonl).

### Настройки для следующих замеров

- Для потока коротких запросов стоит сравнить `--max-num-seqs 1` и `2` при двух клиентах. Это покажет, повышает ли совместная обработка запросов пропускную способность и как при этом меняется задержка.
- Для длинных промптов стоит сравнить `--max-num-batched-tokens 512` и `1024` при включённом chunked prefill. Этот параметр задаёт бюджет токенов на шаг планировщика. При каждом значении стоит измерить throughput, TTFT и расход памяти.
