# MVP-02: приложение и Gateway API

Статус: `ready_for_integration`. База: `3874474e11f4812f8366b8f2a0464df7584f34ed`.
Итоговая ревизия передается интегратору как SHA коммита этого отчета и кода.
Полная live-приемка на Ubuntu VM остается за интегратором.

Изменены только `deploy/app/`, `deploy/gateway/`, `scripts/check-gateway.*`
и этот отчет. Общие файлы, VM и Kubernetes не изменялись.

## Реализация

- Собственный Helm chart `deploy/app` 0.1.0: ConfigMap, Deployment и
  ClusterIP Service `hello` в release namespace `demo`. ConfigMap хранит
  ровно 12 bytes `Hello World!`; Nginx слушает 8080, Service — 80.
- UID/GID 101, read-only root, drop ALL, no privilege escalation,
  RuntimeDefault seccomp, без service-account token. `/tmp` и
  `/var/cache/nginx` — отдельные emptyDir по 16Mi. Пробы `/healthz` идут
  прямо к pod, не попадают в access-log и счетчик внешнего web entrypoint.
- JSON stdout содержит `time`, `request_id`, `method`, `uri`, числовые
  `status`, `bytes_sent` (байты тела ответа), `request_time`. stderr получает
  настоящий `open() ... No such file or directory` для `/verify/missing/ID`.
  Валидный `X-Request-ID` сохраняется; отсутствующий, невалидный и длиннее
  64 символов заменяется Nginx `$request_id`. Header добавляется с `always`.
- Chart `deploy/gateway` 0.1.0 создает только GatewayClass `mts-traefik`,
  Gateway `gateway` с listener `web:8000`, HTTPRoute `hello`: Exact `/`,
  PathPrefix `/verify`, backend `hello:80`, namespace policy Same.
- `deploy/gateway/traefik-values.yaml` относится к закрепленному upstream
  chart 37.4.0. Только Gateway provider, Standard channel, штатный cluster
  RBAC; chart не создает дубли Gateway/GatewayClass/IngressClass. Websecure,
  dashboard и Ingress/CRD providers выключены. Service `traefik` публикует
  только `web` 80 → 8000 → NodePort 30080. `traefik-metrics` — ClusterIP
  9100, port name `metrics`, labels `traefik` / `traefik-edge` подтверждены
  render. Upstream ServiceMonitor выключен, его владелец MVP-03.
- `check-gateway.py` использует только Python stdlib и явный kubeconfig.
  Modes `conditions|traffic|all`, bounded total timeout, JSON даже при
  ошибке, exit 0 только при всех успешных checks. Conditions проверяются
  на GatewayClass, Gateway, listener и правильном HTTPRoute parent с
  совпадением observedGeneration. Проверяются непустой IPv4 status address
  и attachedRoutes. HTTP redirect не принимается за успешный GET, системные
  HTTP proxies выключены. Checks точного body/ID, `/verify`, безопасных
  fallback, настоящего missing-file URI и `/unrouted` включены обязательно.

## Overrides и порядок интеграции

1. Установить закрепленные Gateway API Standard CRDs общим deploy и
   дождаться Established до Helm release Traefik/Gateway. Собственные
   charts не устанавливают и не владеют общими CRDs.
2. Release `traefik`, namespace `edge`: upstream chart с нашим values и
   overrides `providers.kubernetesGateway.statusAddress.ip=environment.node_ip`,
   `ports.web.nodePort=environment.node_port`. Status service copy выключен:
   NodePort не требует внешнего LoadBalancer IP.
3. Release `app`, namespace `demo`, chart `deploy/app`; release `gateway`,
   namespace `demo`, chart `deploy/gateway`. Все image поля проходят общий
   lock/post-renderer. App override key `image`; Traefik —
   `image.registry`, `image.repository`, `image.tag`.
4. Дождаться rollout hello/traefik, затем выполнить check conditions.
   Traefik metrics Service готов к ServiceMonitor MVP-03.

Контрактные изменения не требуются. Для `--requests N` скрипт делает ровно
N основных GET `/`, включая основную marker-пробу, и еще четыре успешные
пробы (`/verify` и три Request-ID fallback). `data.request_count` считает
все реально полученные HTTP 200, при успехе это N+4. Общий metrics verify
может использовать N как нижнюю границу или `data.request_count`.
`data.http` — основная probe GET `/`; `data.error_http` — missing-file
probe; оба содержат method/uri/status/body/bytes/request_id.

## Выполненные проверки

Все команды ниже выполнялись из выданного worktree. `helm` в примерах —
`/home/Bebroid/mts_hack/.cache/tools/helm`; `TRAEFIK_CHART` — проверенный
локальный `/home/Bebroid/mts_hack/.cache/charts/traefik`.

| Статус | Проверка | Результат |
| --- | --- | --- |
| pass | `helm lint deploy/app deploy/gateway` | Оба собственных chart валидны |
| pass | `helm lint "$TRAEFIK_CHART" -f deploy/gateway/traefik-values.yaml` | Upstream schema/Helm lint |
| pass | `helm template app deploy/app -n demo` | Ровно ConfigMap/Service/Deployment; static body 12 bytes |
| pass | `helm template gateway deploy/gateway -n demo` | Три согласованных Gateway API ресурса |
| pass | `helm template traefik "$TRAEFIK_CHART" -n edge -f deploy/gateway/traefik-values.yaml --set providers.kubernetesGateway.statusAddress.ip=192.0.2.10` | Порты, labels, RBAC, statusAddress и provider args проверены; IP — documentation fixture |
| pass | `python3 -m py_compile scripts/check-gateway.py scripts/check-gateway.test.py scripts/check-gateway.container.py` | Синтаксис Python |
| pass | `python3 scripts/check-gateway.test.py` | 12 fixture tests; False/Unknown/missing/stale/duplicate conditions, неверный controller/parent, пустой address, wrong HTTP/body/ID/route, fallback, timeout, bad inputs, явный kubeconfig, JSON error report |
| pass | `python3 scripts/check-gateway.container.py --image docker.io/nginxinc/nginx-unprivileged:1.28.0-alpine@sha256:c97ff0bf7cbae369953c6da1232ec14ad9f971d66360c5698db0856a4cd657a0` | Реальный изолированный Docker Nginx: 8 HTTP checks, JSON stdout, настоящий missing-file stderr, UID101/CapEff0/NoNewPrivs1, root mount ro, token отсутствует; container удален |
| pass | `git diff --check` | Нет whitespace ошибок |
| not run | Gateway conditions/HTTP через IP VM:NodePort | Общая VM только у интегратора; standalone Nginx не заменяет Gateway live-приемку |
| not run | Live Traefik metrics scrape | Выполняется MVP-03/05 в общем стенде |
| not run | Kubernetes runtime security и readiness | Локальный container smoke не доказывает настройки Kubernetes runtime |

Первый container smoke дал `fail`: `alias` в exact root location приводил
к HTTP 500. Конфигурация исправлена на `try_files /index.html =404` только
для `/` и `/verify`; повторный реальный тест прошел. Missing-file location
по-прежнему открывает отсутствующий файл без `try_files` и формирует stderr.

## Команды live-приемки для интегратора

```sh
python3 scripts/check-gateway.py --mode conditions \
  --kubeconfig "$KUBECONFIG" --run-id "$RUN_ID" --timeout 120 \
  --output "$ARTIFACTS/gateway-conditions.json"
python3 scripts/check-gateway.py --mode traffic \
  --kubeconfig "$KUBECONFIG" --base-url "$BASE_URL" \
  --run-id "$RUN_ID" --request-id "$REQUEST_ID" --error-marker "$ERROR_MARKER" \
  --requests 20 --timeout 120 --output "$ARTIFACTS/gateway-http.json"
```

BASE_URL должен вести к IP выделенной VM и NodePort Traefik. Port-forward
к app не считается проверкой пути Gateway. Основной Request-ID и error
marker следует передать неизменными в общий check-logs. Conditions можно
запустить отдельно перед metrics warm-up/baseline, traffic — после baseline.

Проверены первичные источники: [Traefik 3.6 Gateway provider](https://doc.traefik.io/traefik/v3.6/reference/install-configuration/providers/kubernetes/kubernetes-gateway/),
[реализация conditions в Traefik v3.6.2](https://github.com/traefik/traefik/blob/v3.6.2/pkg/provider/kubernetes/gateway/kubernetes.go),
[Nginx map](https://nginx.org/en/docs/http/ngx_http_map_module.html) и
[Nginx JSON log escaping](https://nginx.org/en/docs/http/ngx_http_log_module.html).
