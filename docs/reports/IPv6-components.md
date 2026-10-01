# IPv6: Gateway, application, monitoring, logging

Дата: 2026-10-01. Статус: `ready_for_integration`. Live Kubernetes acceptance
выполняет интегратор; этот отчет не подменяет ее.

Изменения:

- Nginx `[::]:8080 ipv6only=on`, Service hello и Loki IPv6/SingleStack;
  Traefik и monitoring chart values требуют IPv6 Services.
- Prometheus `[::]:9090`, EndpointSlice discovery; kube-state-metrics
  HTTP и telemetry на `::`; Operator `--localhost=[::1]` для config-reloader.
- Loki HTTP/gRPC `::`, ring `::1` с `instance_enable_ipv6: true`.
  Fluentd monitor `bind "[::]"`; Loki service DNS дает AAAA.
- Три checkers принимают только native IPv6 literal URL; proxies и redirects
  отключены. Gateway status IPv4/IPv4-mapped отклоняется; metrics проверяет
  IPv6 одновременно в scrapeUrl и instance всех обязательных endpoints.
- Component READMEs и Docker fixtures переведены на IPv6; Docker networks
  создаются с `--ipv6 --ipv4=false`, порты публикуются только на `[::1]`.

Проверки в worktree с закрепленными tools из общего cache:

| Проверка | Результат |
|---|---|
| `python3 scripts/check-gateway.test.py` | pass, 13 тестов; настоящий HTTP `::1`, IPV6_V6ONLY=1; отрицательные IPv4/mapped status и URL |
| `python3 deploy/monitoring/tests/test_check_metrics.py` | pass, 4 теста с многочисленными отрицательными fixtures; HTTP сервер IPv6-only; IPv4/mapped/missing scrapeUrl и IPv4 instance дают fail |
| `python3 deploy/logging/tests/test_check_logs.py` | pass, 7 тестов; native IPv6 CLI и IPv6 client в CRI fixtures |
| `.venv/bin/python deploy/monitoring/tests/check_render.py --helm .cache/tools/helm --chart .cache/charts/kube-prometheus-stack` | pass, Services IPv6/SingleStack, EndpointSlice, IPv6 args; проверка, что CLI args остаются строками |
| `yamllint` измененных values/configs, `py_compile`, `git diff --check` | pass |
| `python3 scripts/check-gateway.container.py --image "$LOCKED_NGINX_IMAGE"` | pass, реальный Nginx в IPv6-only Docker: HTTP/Request-ID, stdout/stderr, security |
| `.venv/bin/python deploy/logging/tests/docker_smoke.py --helm .cache/tools/helm` | pass, реальный Loki config, AAAA-only DNS из Fluentd, CRI→Fluentd→Loki, restart/file buffer при outage, Loki history |
| Live Ubuntu/Kubernetes | not run агентом: только интегратор работает с VM |

Найденная реальная несовместимость и исправление: Fluentd 1.19 HTTP helper
собирает URI из bind. Голое `::` превращалось в invalid `http://:::24220`;
некавыченный `[::]` воспринимался Fluent config parser как JSON array.
Итоговое `bind "[::]"` прошло настоящий Loki/Fluentd smoke.

Зависимости интеграции: общий post-renderer должен преобразовать Traefik
entrypoint arguments `:PORT/tcp` в `[::]:PORT/tcp`, Operator web listener
в `[::]:8080`, node-exporter env HOST_IP в `::`; upstream charts сами
задают IPv4 wildcard либо не умеют корректно рендерить IPv6 hostIP.
Root подтвердил эту реализацию. Operator-created headless Service
`prometheus-operated` получает IPv6 из single-stack кластера и проверяется
по live API интегратором.

Подтвержденные upstream interfaces: pinned chart templates 37.4.0/80.6.0,
[Prometheus Operator v0.87.1 localhost flag](https://github.com/prometheus-operator/prometheus-operator/blob/v0.87.1/cmd/operator/main.go),
[Prometheus argument generation](https://github.com/prometheus-operator/prometheus-operator/blob/v0.87.1/pkg/prometheus/promcfg.go),
[Loki v3.6.2 common ring propagation](https://github.com/grafana/loki/blob/v3.6.2/pkg/loki/config_wrapper.go)
и Fluentd 1.19 HTTP helper из закрепленного image.

Дополнение после независимого ревью: standalone Gateway checker передает
каждому `kubectl` subprocess отдельный env без HTTP/HTTPS/ALL/NO_PROXY
независимо от регистра имени. Environment родительского процесса и прочие
переменные сохраняются. Fixture с унаследованным IPv4 proxy проверяет env
всех трех вызовов kubectl; 14 Gateway tests — pass. Metrics/logs checkers
не запускают kubectl/subprocess и уже используют ProxyHandler({}).
