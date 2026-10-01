# Dual-stack компоненты — контракт v3

> Ниже сохранен отчет компонентного агента на момент передачи реализации.
> Последующая live-интеграция завершена; текущий статус — в
> [реестре](../status.md) и [независимом протоколе](../acceptance/dual-stack-acceptance.md).

Статус: `ready_for_integration`. Live Ubuntu/Kubernetes не выполнялся
компонентным агентом; стендом управляет только интегратор.

Реализация:

- Собственные Services hello/Loki и values upstream Traefik/monitoring:
  `RequireDualStack`, `ipFamilies: [IPv6, IPv4]`. Nginx слушает IPv4 и IPv6.
- Prometheus использует один dual-family wildcard `[::]:9090`, без PodIP
  env и отдельных IPv6 listener. Operator возвращен к штатному localhost
  127.0.0.1 для config-reloader. KSM `::` и общий post-render wildcard
  Operator/node-exporter/Traefik нужны для доступности primary IPv6 вместе
  с IPv4; IPv4 loopback разрешен.
- Loki HTTP/gRPC: `tcp` вместо `tcp6`, `::` wildcard обеих семей; ring
  127.0.0.1. Analytics остается выключен. Fluentd `bind "[::]"` сохранен
  для IPv6 kubelet probes и принимает обе семьи на Linux, что проверено
  настоящим monitor endpoint по `::1` и 127.0.0.1.
- Gateway HTTP и Prometheus/Loki CLI URL принимают native IPv4 и IPv6.
  Gateway status остается IPv6 primary. Proxy environment guards и запрет
  redirects сохранены. IPv4-mapped/scoped IPv6 по-прежнему отклоняются.
- Все четыре ServiceMonitor используют EndpointSlice discovery и native
  keep relabel по `__meta_kubernetes_endpointslice_address_type=IPv6` на
  каждом endpoint, включая config-reloader. Это фиксированный выбор IPv6,
  а не автоматическое переключение scrape на IPv4 при отказе IPv6.
- Metrics checker принимает обе семьи required targets и не запрещает
  optional IPv4 targets. Он отклоняет повторные scrape обязательного
  Pod/port/path среди всех jobs и IP-семей: alias ServiceMonitor/Service
  не должен удвоить счетчики. Freshness/up/counter reset проверки сохранены.
  В target evidence `address_family: IPv4|IPv6` заменяет старое поле `ipv6`.

Проверки (tools закрепленных версий из общего cache; paths ниже относительно
корня checkout):

| Команда / область | Результат |
|---|---|
| `python3 scripts/check-gateway.test.py` | pass, 15 tests: IPv6 HTTP, разрешенный IPv4 origin, generation/conditions, отрицательные ответы и proxy guard |
| `python3 deploy/monitoring/tests/test_check_metrics.py` | pass, 6 tests: настоящие HTTP API на `::1` и 127.0.0.1, required/optional IPv4, отрицательные duplicate families и alias monitor, stale/reset/ошибки |
| `python3 deploy/logging/tests/test_check_logs.py` | pass, 7 tests, включая IPv4 CLI origin и CRI validation |
| `.venv/bin/python deploy/monitoring/tests/check_render.py --helm .cache/tools/helm --chart .cache/charts/kube-prometheus-stack` | pass: dual-stack Service policy, все native relabels, Prometheus wildcard и отсутствие PodIP override |
| `.venv/bin/python deploy/gateway/tests/test_render.py` | pass, 2 tests: version/usage telemetry effective false, отрицательные renders |
| `python3 scripts/check-gateway.container.py --image "$LOCKED_NGINX_IMAGE"` | pass: реальный Nginx в dual-stack Docker, полный HTTP checker по обеим семьям, stdout/stderr/runtime security |
| `.venv/bin/python deploy/logging/tests/docker_smoke.py --helm .cache/tools/helm` | pass: Loki config, DNS A+AAAA из Fluentd, monitor по обеим loopback семьям, Loki query по IPv6/IPv4, буфер/restart/history |
| `yamllint` измененных values/config, `py_compile`, `git diff --check` | pass |
| Kubernetes full verify/fresh acceptance | not run агентом; выполняет интегратор |

Docker smoke создают только уникальные временные сети/контейнеры, публикуют
порты на loopback и выполняют cleanup. Они не используют VM или kubeconfig.

Интеграционные зависимости общих файлов: root post-renderer задает
RequireDualStack/[IPv6,IPv4] всем Helm Services, включая upstream
`traefik-metrics`, где chart не предоставляет IP policy values. Убрать
переписывание Operator `--localhost=127.0.0.1` в IPv6-only loopback.
Сохранить dual-family `[::]` listeners и node-exporter HOST_IP=::.
Recovery Fluentd URI может быть `http://127.0.0.1:24220/api/plugins.json`
или `http://[::1]:24220/api/plugins.json`; обе реально проверены.

Метаданные семейства подтверждены pinned
[Prometheus 3.8.1 EndpointSlice discovery](https://github.com/prometheus/prometheus/blob/v3.8.1/discovery/kubernetes/endpointslice.go#L281).
Historical IPv6-only reports не являются приемкой нового dual-stack профиля.

Дополнение независимого ревью: `all_targets_unique` теперь отклоняет
дубли любых идентифицируемых active Pod/port/path, включая два optional
endpoints, не относящихся к REQUIRED. Отрицательный fixture воспроизводит
optional duplicate по разным IP-семьям и через другой ServiceMonitor;
required checks при этом остаются pass, а глобальная проверка дает fail.
`python3 deploy/monitoring/tests/test_check_metrics.py` — pass, 7 tests.

Уточнен fallback metadata: используется реальное поле Prometheus
`__meta_kubernetes_endpointslice_address_target_name`. Fixture проверяет
успешную идентификацию required Pod без публичного pod label и отклонение
optional duplicates по этому же targetRef metadata. Metrics fixtures:
pass, 8 tests.
