# MVP-03 — Prometheus и реальные метрики

Статус: **ready_for_integration**, не `done`. Работа на общей VM не выполнялась.

- База: `3874474e11f4812f8366b8f2a0464df7584f34ed`.
- Ветка: `mvp-03`, отдельный worktree `.worktrees/mvp-03`.
- Ревизия реализации: `ba3ef8ca1320a616e828d215efb91dfa2b400e42`.
- Итоговый SHA, включающий этот отчет, передается интегратору отдельно.
- Пути: `deploy/monitoring/`, `scripts/check-metrics.py`, этот отчет.
- Общие файлы, соседние репозитории, VM и current-context не менялись.

## Результат

Values для принятого kube-prometheus-stack 80.6.0: Operator, Prometheus,
node-exporter и kube-state-metrics; остальные collectors, Grafana,
Alertmanager, rules и admission hooks отключены. У всех контейнеров
requests/limits. Retention 24h/4GB, PVC 12Gi с точной привязкой
`local-retain`/`mts-prometheus`, UID/GID 1000:2000. Имена объектов и
интервалы соответствуют `docs/contracts.md`.

Все обычные images явно имеют принятые tags и покрыты image lock; общий
post-renderer должен заменить их digest. Динамический image reloader
в аргументах Operator уже закреплен digest из lock через
`prometheusOperator.prometheusConfigReloader.image.sha` (без `sha256:`).

Chart `deploy/monitoring/extras`, version 0.1.0, устанавливает ServiceMonitor
`edge/traefik` с `release=monitoring`, namespace и labels Traefik по контракту.
Порядок: CRDs и основной monitoring release → extras. Нужных адресов VM или
environment overrides в мониторинге нет. Подробности и CLI —
[`deploy/monitoring/README.md`](../../deploy/monitoring/README.md).

`check-metrics.py` использует только Python stdlib. Snapshot сохраняет
targets и raw samples; verify требует baseline того же run ID, точный набор
серий и sample после baseline. Проверяет freshness ≤45s, `up=1`, реальные
`node_memory_MemAvailable_bytes` и `kube_node_info`, прирост GET/200 web scope
и отсутствие наблюдаемого reset между scrape. Baseline sample должен
остаться в TSDB; промежуточный reset 100→2→120 не принимается за +20.
Отсутствие данных, предупреждения API, stale samples, неисправные targets и
таймаут дают JSON `fail` и exit 1. Основной и дополнительные endpoints
обязательных services проверяются, включая reloader endpoint Prometheus.

Формат raw matrix и API проверялся по официальным
[HTTP API](https://prometheus.io/docs/prometheus/latest/querying/api/) и
[PromQL basics](https://prometheus.io/docs/prometheus/latest/querying/basics/).
Настройки chart проверены по локальному архиву версии 80.6.0, а не по latest
values. Versioned веб-страницы Prometheus 3.8 через browsing были недоступны;
совместимость закрепленного бинарника и фактические labels подтверждает live.

## Проверки

Команды из корня worktree; локальные инструменты подготовлены интегратором.

```sh
.cache/tools/helm lint \
  .cache/charts/kube-prometheus-stack \
  -f deploy/monitoring/values.yaml
.cache/tools/helm lint deploy/monitoring/extras
python3 deploy/monitoring/tests/check_render.py \
  --helm .cache/tools/helm \
  --chart .cache/charts/kube-prometheus-stack
python3 -m py_compile scripts/check-metrics.py
python3 -m unittest discover -s deploy/monitoring/tests -v
git diff --check
```

| Проверка | Результат | Что подтверждено |
| --- | --- | --- |
| Helm lint обоих charts | pass | Values/templates валидны; только необязательная рекомендация icon extras |
| Render assertions | pass | 4 обычных images входят в lock; digest reloader совпадает; нужные workloads/ServiceMonitors, selectors, storage, resources; нет hooks/rules/Alertmanager |
| Python compile | pass | Синтаксис checker |
| HTTP API fixtures | pass | 4 test methods, положительный snapshot→verify, 22 отрицательных сценария |
| Whitespace | pass | `git diff --check` |
| Живые targets/labels/counter | not run | Выполняет интегратор после общего deploy |
| История после рестарта Prometheus | not run | Требует живого PVC и рестарта pod на выделенной VM |
| Реальное отсутствие scrape после остановки компонента | not run | Fault check только интегратор на выделенной VM |

В первом запуске fixtures sandbox запрещал loopback socket. Запуск с
разрешенным локальным bind прошел. Fixtures обнаружили потерю конкретной
диагностики при финальном timeout; исправлено, итоговый прогон зеленый.
Это не live-приемка Prometheus и Kubernetes.

Отрицательные сценарии: HTTP 503, down target, down reloader, отсутствующий
target, старый target scrape, пустой matrix, неожиданный vector, старый
sample, NaN, up=0, отсутствующие инфраструктурные метрики, API warning,
невалидный JSON, промежуточный reset, потеря baseline sample, замена серии,
отсутствие нового scrape, прирост 19 вместо 20, другой run ID, нулевой
min-increase, отрицательный timeout, невалидный baseline.

## Следующий шаг интегратора

Объединить ветку, применить общий post-renderer и выполнить последовательный
deploy на выделенной Ubuntu. Через loopback port-forward к
`monitoring/monitoring-prometheus:9090` выполнить warm-up → snapshot → общие
HTTP-пробы → verify с `--min-increase` равным `gateway.data.request_count`.
Сохранить фактические series labels и targets в evidence.

Для проверки persistence сохранить query и evaluation time с историческими
samples, затем перезапустить созданный Operator StatefulSet
`prometheus-monitoring-prometheus` и повторить **тот же исторический query
с тем же time**. UID PV/PVC и исторические значения должны сохраниться.
Команда рестарта выполняется только интегратором с явным kubeconfig:

```sh
kubectl --kubeconfig "$KUBECONFIG" -n monitoring rollout restart \
  statefulset/prometheus-monitoring-prometheus
kubectl --kubeconfig "$KUBECONFIG" -n monitoring rollout status \
  statefulset/prometheus-monitoring-prometheus --timeout=180s
```

Изменений общих контрактов не требуется. Ограничения: counter delta отражает
общий фиксированный scope, включая возможный фоновый трафик; это не
трассировка Request-ID. Reset, который полностью произошел между scrape и
не отразился в samples, обнаружить нельзя. Требуется синхронизация времени
машины проверки и Prometheus. Сетевые проверки, dashboards и alerting не
входят в эту карточку.
