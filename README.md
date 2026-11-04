# Локальный запуск Qwen в Kubernetes на CPU

Проект запускает Qwen2.5-0.5B-Instruct как локальный сервис. Клиент отправляет вопрос по HTTP и получает сгенерированный ответ через OpenAI-совместимый API vLLM. Вычисления выполняются на CPU.

## Как устроен запуск

1. Kubernetes запускает подготовительный контейнер `model-downloader`. При первом запуске он скачивает выбранную ревизию Qwen из Hugging Face и сохраняет файлы на постоянном томе `model-storage`.
2. При следующем запуске подготовительный контейнер проверяет отметку о завершении загрузки и повторно использует файлы на томе.
3. Контейнер `qwen-server` читает модель с этого тома и запускает vLLM. Сервис Kubernetes предоставляет ему постоянное имя `qwen-server` и порт `8000`.
4. Клиент обращается к API с ключом. Для доступа из Ubuntu порт сервиса пробрасывается на `127.0.0.1:18000`.

## 1. Подготовить окружение

Проверенное окружение: Windows 11, Ubuntu в WSL, Docker Desktop с Kubernetes, узел `desktop-control-plane`, Intel Core i7-1260P. Использованы образы `linux/amd64`, vLLM `0.28.0+cpu` и PyTorch `2.13.0+cpu`.

Все команды ниже выполняются в Bash внутри Ubuntu. На тестовой машине проект находится в `/mnt/d/kaspersky-llm`, Ubuntu — в `D:\WSL\Ubuntu`, данные Docker Desktop — в `D:\DockerDesktopWSL`. Перед сборкой нужно убедиться, что хранилище Docker размещено на D: именно туда Docker записывает образы независимо от текущего каталога терминала.

```bash
cd /mnt/d/kaspersky-llm
docker info --format '{{.ServerVersion}}'
kubectl config current-context
kubectl get nodes
```

Продолжать, когда выбран нужный локальный кластер и узел имеет статус `Ready`. Для первой сборки и первой загрузки весов нужен доступ в интернет.

## 2. Собрать образы и передать их узлу

```bash
docker build --platform linux/amd64 -t model-downloader:0.1 ./downloader
docker build --platform linux/amd64 -t qwen-server:0.1 ./server
```

В проверенной конфигурации узел Kubernetes работает в контейнере `desktop-control-plane`. Чтобы узел увидел локальные образы, их нужно импортировать в его хранилище. Команды передают данные потоком без промежуточного архива.

```bash
set -o pipefail
for image in model-downloader:0.1 qwen-server:0.1; do
  docker image save --platform linux/amd64 "$image" |
    docker exec -i desktop-control-plane ctr --namespace=k8s.io images import - || break
done
docker exec desktop-control-plane crictl images | grep -E 'model-downloader|qwen-server'
```

В выводе должны присутствовать оба образа с тегом `0.1`.

## 3. Создать ключ и запустить сервис

В новом окружении создать Secret `model-access`. Если он уже существует, команда сохранит действующий ключ. Значение ключа не выводится.

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

Ожидаемый результат: PVC `model-storage` имеет статус `Bound`, серверный Pod — `1/1 Running`. Для выбранной публичной модели токен Hugging Face не требуется. Ревизия модели и предел контекста в 1024 токена заданы в `k8s/model-config.yaml`. Отдельный `k8s/download-job.yaml` использовался для проверки загрузчика. При обычном запуске подготовку модели выполняет сам Deployment.

## 4. Задать первый вопрос

В первом окне Ubuntu запустить проброс порта и оставить команду работающей:

```bash
kubectl port-forward --address 127.0.0.1 service/qwen-server 18000:8000
```

Во втором окне Ubuntu выполнить:

```bash
cd /mnt/d/kaspersky-llm
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

Ожидается `health: HTTP 200`, затем `HTTP 200` и ответ модели `5`. При тестировании первый запрос после запуска иногда завершался по тайм-ауту, хотя `/health` уже возвращал `200`. Последующие запросы успешно генерировали ответ. Причина начальной задержки не установлена. Для диагностики:

```bash
kubectl get pods -l app=qwen-server
kubectl logs deployment/qwen-server -c server --since=10m --tail=60
```

## 5. Контрольные проверки

- Образ сервера запускался с UID `10001`.
- Запрос к `/v1/models` без ключа вернул `401`. С действующим ключом получены `200` и имя модели `qwen`.
- Для тестового запроса к `/v1/chat/completions` в журнале найдена запись `chat_request status=200`. Проверка по уникальной метке запроса и точному тексту ответа не обнаружила их в просмотренных логах. Для запроса без ключа зарегистрирован статус `401`.
- Повторная подготовка модели использовала уже скачанные файлы на постоянном томе.

## 6. Что показали измерения

Измерения сделаны со стороны клиента через локальный проброс порта. TTFT здесь — время до первого непустого фрагмента текста, E2E — время до завершения ответа. По три последовательных запроса каждого типа:

| Вход, токенов | Медиана TTFT, с | Медиана E2E, с |
|---:|---:|---:|
| 46 | 1,216 | 4,682 |
| 606 | 3,927 | 7,645 |

При двух почти одновременных коротких запросах оба клиента получили по 32 токена. Первый текст появился через 1,174 и 6,092 с соответственно. Результат согласуется с ожиданием в очереди при `--max-num-seqs 1`. Время нахождения в очереди отдельно не измерялось.

Исходные измерения: `bench/series_20261105_004331.jsonl` и `bench/parallel_20261105_004633.jsonl`. Скрипт `bench/measure_case.py` использовался для обеих серий. Для повторения одного короткого замера при работающем пробросе порта:

```bash
cd /mnt/d/kaspersky-llm
export SERIES_FILE="repeat_$(date +%Y%m%d_%H%M%S).jsonl"
python3 bench/measure_case.py short 1
```
