# Мониторинг: Prometheus

При `network_mode: ipv4` установщик дополнительно применяет `ipv4-values.yaml`
после основных values. Services используют `[IPv4]` / `SingleStack`, процессы
слушают IPv4, ServiceMonitor выбирают IPv4 EndpointSlices. Для `extras/`
передается `networkMode: ipv4`, локальный port-forward использует `127.0.0.1`.
Остальные параметры и проверки метрик одинаковы. Далее — базовый `dual-stack`.

Команды выполняются из корня репозитория после установки или `make tools`.
Для прямого вызова закрепленных Helm/Ansible/Python настройте PATH:

```bash
export PATH="$PWD/.cache/tools:$PWD/.venv/bin:$PATH"
```

`values.yaml` применяется к архиву kube-prometheus-stack **80.6.0** из
`config/versions.yaml`; Helm release `monitoring`, namespace `monitoring`.
Общий deploy устанавливает CRD до release `monitoring-extras` из `extras/`. Namespace
`edge` и Service `traefik-metrics` создает общая установка Gateway.
Сторонние зависимости уже содержатся в закрепленном архиве; `helm dependency
update` здесь не используется.

Обычные `image` поля рендера переводит в digest общий post-renderer по
`config/images.lock.yaml`. Config-reloader создается Operator динамически:
его digest уже явно указан в
`prometheusOperator.prometheusConfigReloader.image.sha` **без префикса
`sha256:`**, который добавляет chart. При обновлении lock этот ключ нужно
обновить согласованно. Дополнительных environment overrides не требуется.

Prometheus: Service/CR `monitoring-prometheus`, порт 9090, интервал scrape
15s, timeout 10s, retention 24h/4GB, PVC 12Gi на PV `mts-prometheus`,
StorageClass `local-retain`, UID/GID 1000:2000. Лимиты памяти: Prometheus
1536Mi, Operator 256Mi, config-reloader 64Mi, node-exporter 128Mi,
kube-state-metrics 256Mi. Grafana, Alertmanager, defaultRules, admission
hooks и сбор недоступных control-plane/kubelet/DNS targets выключены.

ServiceMonitor Traefik находится в `edge`, имеет label `release=monitoring`
и выбирает Service по labels `app.kubernetes.io/name=traefik`,
`app.kubernetes.io/instance=traefik-edge`, endpoint `metrics`. Prometheus
выбирает ServiceMonitor с `release=monitoring` во всех namespaces.

## Проверка

`make verify ENV=ubuntu` открывает локальный port-forward к Service Prometheus
с kubeconfig выбранного окружения. До исходного снимка отправляется GET
через Gateway: счетчик без единого запроса может еще не существовать.
Ручные команды targets и PromQL приведены в
[README](../../README.md#проверка-мониторинга).

Ниже показан вызов скрипта внутри общего сценария. `RUN_ID` — идентификатор
проверки, `BASELINE` и `RESULT` — пути к JSON-отчетам; Prometheus уже доступен
через port-forward. В режиме `ipv4` используйте `http://127.0.0.1:9090`.

```bash
python3 scripts/check-metrics.py --mode snapshot \
  --prometheus-url 'http://[::1]:9090' --run-id "$RUN_ID" \
  --timeout 90 --output "$BASELINE"
# Здесь общий verify делает HTTP-пробы через Gateway.
python3 scripts/check-metrics.py --mode verify \
  --prometheus-url 'http://[::1]:9090' --run-id "$RUN_ID" \
  --baseline "$BASELINE" --min-increase 20 --timeout 90 --output "$RESULT"
```

Скрипт сам не генерирует HTTP-трафик. Проверяет `/api/v1/targets`, затем
`up=1` и реальные samples `node_memory_MemAvailable_bytes` и `kube_node_info`.
Обязательны services `edge/traefik-metrics`,
`monitoring/monitoring-prometheus-node-exporter`,
`monitoring/monitoring-kube-state-metrics`, `monitoring/monitoring-prometheus`.
Scrape discovery использует EndpointSlice. Helm Services имеют
`RequireDualStack` / `ipFamilies: [IPv6, IPv4]`. Создаваемый Operator
headless Service `prometheus-operated` может оставаться SingleStack IPv6.
Prometheus слушает `[::]:9090` — обычный wildcard обеих семей на Linux.
Локальный config-reloader использует штатный `127.0.0.1`; HTTP port-forward
может использовать любую loopback семью. kube-state-metrics слушает `::`,
общий post-renderer задает `[::]:8080` Operator и `HOST_IP=::` node-exporter,
чтобы primary IPv6 pod probes и scrape были доступны вместе с IPv4.

В каждом ServiceMonitor endpoint, включая reloader, задан native relabel
`keep` с `sourceLabels: [__meta_kubernetes_endpointslice_address_type]`
и `regex: IPv6`. Так один Pod/порт не попадает в scrape дважды через IPv6
и IPv4 EndpointSlices. Это фиксированный выбор IPv6 для мониторинга;
автоматическое переключение scrape на IPv4 при отказе IPv6 не реализовано.

У каждого обязательного service требуется основной endpoint; все
обнаруженные endpoints этих services должны быть здоровы и свежи, включая
Prometheus config-reloader. `check-metrics.py` принимает native IPv4 и
IPv6 в `scrapeUrl`/`labels.instance`; необязательный IPv4 target не является
ошибкой. Дополнительно все идентифицируемые Pod/порт/path должны встречаться
ровно один раз среди всех active targets, включая необязательные targets,
независимо от IP, job и Service.
Повторный scrape через другую семью либо другой ServiceMonitor дает fail.
URL API допускает IPv4 или IPv6 literal с квадратными скобками. Проверки
сохраняют прямое соединение без HTTP proxy и не следуют redirect.

Фиксированный scope HTTP:
`traefik_entrypoint_requests_total{namespace="edge",service="traefik-metrics",endpoint="metrics",entrypoint="web",code="200",method="GET"}`.
В `/api/v1/query` передается **range selector**, не `query_range` с
шагом и не обычный instant vector: matrix содержит timestamps сохраненных
scrape samples. Последний sample каждой серии должен быть не старше 45s;
timestamp этого sample более чем на 5s в будущем отклоняется. Исторические
samples для сравнения с baseline и проверки reset могут быть старше 45s.
Verify требует sample после времени baseline,
полное совпадение labels серий, наличие исходного sample и отсутствие
падения счетчика между любыми соседними сохраненными samples. Таким образом,
reset с последующим ростом выше baseline также дает fail. Reset, полностью
произошедший между двумя scrape и не видимый в samples, обнаружить нельзя.

JSON содержит query/raw_query, инфраструктурные samples, targets, исходные
серии, before/after/increase и временное окно. Пустой ответ, stale sample,
отсутствующий target, API warning/error и несовпадающий baseline — fail,
exit 1. Baseline должен быть успешным snapshot того же run ID не старше часа.
Фоновый HTTP-трафик может увеличить общий delta: это проверка scope, а не
трассировка Request-ID. Минимальное увеличение должно соответствовать числу
успешных GET/200 общей нагрузки. Labels подтверждены [проверкой на VM](../../docs/verification.md);
ни одного собственного Request-ID label не добавляется.

Локальные тесты входят в `make lint`; отдельный запуск:

```bash
python3 -m unittest discover -s deploy/monitoring/tests -v
```

Тесты используют HTTP-серверы на `::1` и `127.0.0.1` с синтетическими ответами.
Сбор реальных метрик и сохранение истории после перезапуска Prometheus
проверяются на выделенной VM.
