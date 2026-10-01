# IPv6: Gateway, application, monitoring, logging

> Историческая попытка IPv6-only, замененная требованием dual-stack.
> Ограничения IPv4 и конфигурации ниже не действуют; принятой strict
> IPv6-only ревизии нет. Текущая реализация: [DUAL-COMPONENTS.md](DUAL-COMPONENTS.md)
> и [сетевой контракт](../networking.md). Результаты попытки сохранены без изменений.

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

Дополнение по outbound-аудиту: Traefik chart 37.4.0 включает проверку
обновлений по умолчанию. Явно заданы `global.checkNewVersion: false` и
`global.sendAnonymousUsage: false`. Semantic render test вычисляет оба
эффективных флага с учетом pinned runtime defaults и ловит включение
каждого из них отдельным отрицательным render — pass, 2 tests.
Команда: `.venv/bin/python deploy/gateway/tests/test_render.py` (закрепленные
Helm и chart берутся из `.cache`; отдельный worktree может указать
`MTS_TEST_HELM` и `MTS_TEST_TRAEFIK_CHART`).

Источники: [runtime defaults Traefik 3.6.2](https://github.com/traefik/traefik/blob/v3.6.2/cmd/configuration.go#L17),
[периодический запуск после 10 минут и каждые 24 часа](https://github.com/traefik/traefik/blob/v3.6.2/cmd/traefik/traefik.go#L617),
[внешний update endpoint](https://github.com/traefik/traefik/blob/v3.6.2/pkg/version/version.go#L59).
Loki reporting уже отключен: [reporter не создается при false](https://github.com/grafana/loki/blob/v3.6.2/pkg/analytics/reporter.go#L81).
В проверенных startup/config путях Prometheus/Fluentd других автоматических
version/usage reporters не найдено; Loki plugin использует заданный
внутренний URL, а не [внешний default](https://github.com/grafana/loki/blob/v3.6.2/clients/cmd/fluentd/lib/fluent/plugin/out_loki.rb#L99).
Source-аудит не заменяет live-проверку пакетов интегратором.
