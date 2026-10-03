# Эксплуатация и диагностика

Все команды выполняются из корня checkout после установки. В примерах
выбран `ubuntu`; для другого environment замените его имя.

`make health`, `make verify-release`, canary и recovery автоматически используют
`network_mode` environment. В режиме `ipv4` в ручных командах ниже замените
`--address=::1` на `--address=127.0.0.1`, `http://[::1]` на `http://127.0.0.1`,
`curl -6` на `curl -4`, а адрес VM — на её IPv4 без квадратных скобок.

## Выбор кластера

Не используйте случайный текущий context. Получите путь из конфигурации:

```bash
KUBECONFIG="$(.venv/bin/python -c 'import sys; sys.path.insert(0, "scripts"); from stacklib import environment; print(environment("ubuntu")["kubeconfig"])')" || exit 1
test -s "$KUBECONFIG" || exit 1
export KUBECONFIG
export PATH="$PWD/.cache/tools:$PWD/.venv/bin:$PATH"
kubectl get nodes
kubectl get pods -A
kubectl get pv,pvc -A
kubectl -n demo get gateway,httproute
```

Обычные `make deploy`, `make verify` и другие команды сами передают явный
kubeconfig из environment. Пример выше нужен для ручной диагностики.

## Prometheus

В отдельном терминале откройте port-forward:

```bash
kubectl -n monitoring port-forward --address=::1 service/monitoring-prometheus 9090:9090
```

Получите targets и HTTP-счетчик:

```bash
curl -6 --noproxy '*' -gs 'http://[::1]:9090/api/v1/targets'
curl -6 --noproxy '*' -gsG 'http://[::1]:9090/api/v1/query' \
  --data-urlencode 'query=traefik_entrypoint_requests_total{namespace="edge",service="traefik-metrics",endpoint="metrics",entrypoint="web",code="200",method="GET"}'
```

Ожидаются работающие targets Traefik, node exporter, kube state metrics
и Prometheus. После запросов через HTTP NodePort счетчик должен вырасти;
следующий scrape происходит примерно через 15 секунд. Сам по себе пустой
ответ query не означает нулевое количество ошибок или запросов.
`make verify` выполняет сравнение до/после автоматически.

## Loki и логи Nginx

Отправьте запрос с известным Request-ID и запрос к отсутствующему файлу:

```bash
curl -6 --noproxy '*' -g -H 'X-Request-ID: manual-check-01' 'http://[<VM_IPv6>]:30080/'
curl -6 --noproxy '*' -g 'http://[<VM_IPv6>]:30080/verify/missing/manual-error-01'
```

В отдельном терминале:

```bash
kubectl -n logging port-forward --address=::1 service/loki 3100:3100
```

Найдите access и error в Loki:

```bash
curl -6 --noproxy '*' -gsG 'http://[::1]:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={app="hello",namespace="demo",stream="stdout"} |= "manual-check-01"'
curl -6 --noproxy '*' -gsG 'http://[::1]:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={app="hello",namespace="demo",stream="stderr"} |= "manual-error-01"'
```

Access должен содержать заданный Request-ID, метод GET, URI `/` и status 200.
Error должен относиться к отсутствующему файлу с заданным marker.
`kubectl logs` полезен для диагностики, но только поиск через Loki подтверждает
прохождение всей цепочки Fluentd → Loki.

## Выпуск и откат

```bash
make deploy-canary ENV=ubuntu CANARY_ARGS="--revision demo-v2"
make verify-release ENV=ubuntu
make restore-stable ENV=ubuntu
```

Успешный выпуск оставляет 90/10. `restore-stable` возвращает 100/0 и проверяет
трафик. Обычный `make deploy` также возвращает базовую здоровую конфигурацию;
отвергнутый кандидат не включается повторно. Для TLS используйте CA
из `.state/tls/<ENV>/ca.crt`; ключи и kubeconfig не публикуйте.

При ошибке прочитайте JSON в `artifacts/canary/`. `inconclusive` означает,
что данных для принятия кандидата недостаточно. Успешный rollback не меняет
отказ выпуска на успех. При `rollback_failed` проверьте маршруты, stable
и Helm history с тем же kubeconfig; после устранения причины выполните restore.

Состояние `pending-*` после прерывания обрабатывается следующим запуском.
Скрипт восстанавливает завершенную безопасную ревизию. Если ее нет,
потребуется разбор `helm history`; удалять Helm Secrets вручную не следует.

## Сценарии восстановления

Они временно меняют тестовый стенд, поэтому выполняются последовательно:

```bash
make verify-canary ENV=ubuntu
make verify-interruption ENV=ubuntu
make verify-recovery ENV=ubuntu
make demo ENV=ubuntu
```

`demo` объединяет выпуск/отказы, прерывание, замену app pod, изменение
конфигурации с Helm rollback и сохранение истории/буфера логов.
Отдельные части: `DEMO_ARGS="--section app"` или `"--section recovery"`.

Reboot проверяется с внешнего controller, чтобы процесс проверки пережил
перезагрузку: `make demo ENV=ubuntu DEMO_ARGS="--section reboot"`.
Для ручного reboot дождитесь загрузки ОС, затем выполните `make health`
и `make verify-release` с нужным `ENV`. Kubelet в состоянии `activating`
еще не готов: дождитесь запуска и повторите health.

## Частые неполадки

| Симптом | Что проверить |
| --- | --- |
| ImagePullBackOff | `kubectl describe pod -n <ns> <pod>`, доступ к registry, image lock |
| Недостаточно CPU/RAM | `kubectl describe node`, ресурсы VM; не убирать requests ради запуска |
| PVC Pending | `kubectl get pv,pvc -A`, node affinity, volumeName, storageClass |
| Ошибка Gateway conditions | `kubectl -n demo describe gateway,httproute`, версии CRD/controller |
| Target down или отсутствует | Prometheus `/api/v1/targets`, ServiceMonitor labels и Service `traefik-metrics` |
| В Loki нет записи | `kubectl -n logging logs ds/fluentd --tail=100`, CRI path, stream, buffer и готовность Loki |
| Bootstrap отказал на существующем кластере | Identity выбранного environment и причина failed task; автоматического reset нет |
| Verify завершился ошибкой | Конкретный check в JSON и приложенные events; после исправления повторить тот же verify |

Порт-forward завершайте через Ctrl+C. Штатные проверки открывают его
автоматически только на loopback и закрывают при завершении.
