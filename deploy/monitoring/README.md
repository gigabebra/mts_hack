# Мониторинг MVP

`values.yaml` применяется к архиву kube-prometheus-stack **80.6.0** из
`config/versions.yaml`; Helm release `monitoring`, namespace `monitoring`.
Устанавливать CRDs до release `monitoring-extras` из `extras/`. Namespace
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

Интегратор открывает loopback port-forward к Service Prometheus, используя
только явно переданный kubeconfig. До baseline нужен успешный warm-up GET
через Gateway: счетчик без единого запроса может еще не существовать.

```sh
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
Scrape discovery использует EndpointSlice. Service имеют `SingleStack` /
`ipFamilies: [IPv6]`; Operator-created `prometheus-operated` получает IPv6
из single-stack сети кластера. Prometheus слушает конкретные `[Pod_IPv6]:9090`
и `[::1]:9090`: IPv4 loopback не принимается даже внутри Pod, поэтому
CRI port-forward использует IPv6 после отказа начального tcp4 dial.
kube-state-metrics — `::`. Общий post-renderer задает `[::]:8080` Operator
и `HOST_IP=::` node-exporter: upstream charts явно задают IPv4 wildcard.
Operator `--localhost=[::1]` направляет config-reloader к Prometheus по IPv6.

У каждого требуется основной endpoint; все обнаруженные endpoints этих
services должны быть здоровы, включая Prometheus config-reloader.
`check-metrics.py` требует native IPv6 literal в обоих `scrapeUrl` и
`labels.instance` каждого endpoint; IPv4, IPv4-mapped IPv6 и отсутствие URL
дают fail. Дополнительно проверяются IPv6-адреса всех active targets,
включая targets вне четырех обязательных groups.
URL API принимается как IPv6 literal с квадратными скобками.

Фиксированный scope HTTP:
`traefik_entrypoint_requests_total{namespace="edge",service="traefik-metrics",endpoint="metrics",entrypoint="web",code="200",method="GET"}`.
В `/api/v1/query` передается **range selector**, не `query_range` с
шагом и не обычный instant vector: matrix содержит timestamps сохраненных
scrape samples. Все samples должны быть не старше 45s; будущие timestamps
более чем на 5s отклоняются. Verify требует sample после времени baseline,
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
успешных GET/200 общей нагрузки. Labels подтвердит live-приемка MVP-05/06;
ни одного собственного Request-ID label не добавляется.

Fixtures запускаются командой:

```sh
python3 -m unittest discover -s deploy/monitoring/tests -v
```

Это HTTP сервер на `::1` с `IPV6_V6ONLY=1` и синтетическими ответами. Его pass не подтверждает
живые scrape, совместимость CRDs/Operator или сохранение истории после
рестарта Prometheus. Эти проверки выполняет интегратор на выделенной VM.
