# Контракт MVP, версия 1

Владельцы и границы — `AGENTS.md`. Версии и проверенные SHA256 публичных
артефактов — `config/versions.yaml`; OCI index/manifest digests —
`config/images.lock.yaml`. Deployment не разрешает теги по сети: применяет
принятый lock, отказывается от неизвестного image. Обновление lock — отдельная
операция интегратора. Transitive images включены, в том числе создаваемый
Operator config-reloader. Неиспользуемые Grafana/Alertmanager/admission hooks
выключены. Запрещены `latest`, `main`, пустые image tags и Helm dependencies
без принятого архива с checksum.

Матрица: Kubernetes 1.34.3, containerd 1.7.28, Calico 3.31.2, Gateway API
1.4.0 Standard, Traefik 3.6.2/chart 37.4.0. Traefik 3.6 документирует
[поддержку Gateway 1.4](https://doc.traefik.io/traefik/v3.6/reference/install-configuration/providers/kubernetes/kubernetes-gateway/).
Calico 3.31 — [требования](https://docs.tigera.io/calico/3.31/getting-started/kubernetes/requirements).
Runtime-совместимость подтверждается MVP-05/06, а не этим документом.

Fluentd image `grafana/fluent-plugin-loki:3.6.2` содержит Fluentd 1.19.0
и plugin `/fluentd/plugins/out_loki.rb` из релиза Loki 3.6.2. Загрузка plugin
и встроенных regexp/JSON parsers проверена в контейнере; запуск collector
должен передавать `-p /fluentd/plugins`. Отдельного установленного gem
`fluent-plugin-grafana-loki` в этом image нет; состав закреплен image digest.

## Среда и хранение

Цель: отдельная Ubuntu 24.04 amd64, 4 vCPU, 8 GiB RAM, диск 60 GiB,
SSH с sudo и публичный Internet. Текущий стенд создается интегратором на
Proxmox через существующий SSH alias; адреса, VMID и ключи только в
игнорируемом `.state/` и локальном environment. Основной deploy не зависит
от Proxmox или homelab. Для независимой приемки — вторая чистая VM, fresh
checkout; ресурсоемкие прогоны по очереди. Способ автоматического получения
чистой ОС — официальный Ubuntu 24.04 cloud image с проверкой SHA256 и
cloud-init; обычная установка Ubuntu Server ISO также допустима.

Pod CIDR `10.244.0.0/16`, service CIDR `10.96.0.0/12`, IPv4, Calico VXLAN,
BGP выключен. Проверены отличия от известных homelab/VPN сетей 10.42/16,
10.43/16, 10.0.0/24, 10.10.11/24 и 192.168.X/24. Bootstrap дополнительно
проверяет локальные маршруты и не выполняет kubeadm reset автоматически.

Environment — `config/environments/<ENV>.local.yaml`, пример
`config/environments/ubuntu.example.yaml`. Верхние ключи: `node_name`,
`node_ip`, `ssh_host`, `ssh_user`, `ssh_private_key_file`, `ssh_port`,
`pod_cidr`, `service_cidr`, `node_port`, `kubeconfig`, `data_root`.
Пути относительно корня проекта. Общие команды берут kubeconfig только из
environment; унаследованный KUBECONFIG не меняет выбранный стенд. Компонентные
checks принимают явный `--kubeconfig`.
Имя узла по умолчанию `mts-mvp`; IP не берется из домашнего current-context.

StorageClass `local-retain`: no-provisioner, WaitForFirstConsumer, Retain.
PV `mts-prometheus`: 12Gi, `/var/lib/mts-stack/prometheus`, UID/GID 1000:2000.
PV `mts-loki`: 12Gi, `/var/lib/mts-stack/loki`, UID/GID 10001:10001.
Node affinity — environment.node_name. Prometheus volumeClaimTemplate
volumeName `mts-prometheus`, Loki PVC `loki` в logging с volumeName `mts-loki`.
Fluentd hostPath `/var/lib/mts-stack/fluentd`, root:root 0750; позиции и
ограниченный файловый буфер не ephemeral. Корень путей — environment.data_root.

## Ресурсы и порты

| Namespace | Release | Ресурс / контракт |
| --- | --- | --- |
| edge | traefik | upstream chart; Deployment/Service `traefik`; web container 8000 → Service 80 → NodePort 30080 |
| demo | app | собственный chart `deploy/app`; Deployment/Service `hello`; container 8080 → Service 80, только ClusterIP |
| demo | gateway | собственный chart `deploy/gateway`; GatewayClass `mts-traefik`, Gateway `gateway`, HTTPRoute `hello` |
| monitoring | monitoring | kube-prometheus-stack; fullnameOverride `monitoring`; Service `monitoring-prometheus`:9090 |
| monitoring | monitoring-extras | chart `deploy/monitoring/extras`, ServiceMonitor Traefik |
| logging | logging | собственный chart `deploy/logging`; Loki Deployment/Service/PVC `loki`:3100; Fluentd DaemonSet `fluentd` |
| default | storage | chart `deploy/storage`, StorageClass и два PV |

GatewayClass controllerName `traefik.io/gateway-controller`. Gateway listener
`web`, HTTP, port **8000** (порт entrypoint), same namespace allowedRoutes.
HTTPRoute: Exact `/` и PathPrefix `/verify`; `/unrouted` обязан дать 404.
Chart Traefik не создает собственные GatewayClass/Gateway, Ingress/CRD
providers выключены, Gateway provider включен, experimentalChannel false.
Gateway status IP задается через provider statusAddress.ip из environment,
не ожидает LoadBalancer IP у NodePort. Conditions проверяются с generation.

Traefik metrics: отдельный ClusterIP Service `traefik-metrics` в edge,
port name `metrics`, 9100; labels `app.kubernetes.io/name: traefik`,
`app.kubernetes.io/instance: traefik-edge` (подтвердить chart render).
ServiceMonitor в edge, label `release: monitoring`, selector соответствует
Service; Prometheus выбирает release=monitoring во всех namespaces.
Scrape/evaluation 15s, timeout 10s. Фиксированный HTTP scope:
`traefik_entrypoint_requests_total{entrypoint="web",code="200",method="GET"}`.
Фактические labels подтверждаются в live. Job/service фильтр добавляется
после проверки labels. До baseline выполнить warm-up и дождаться sample.
App probes идут на pod:8080/healthz и не увеличивают web counter.
Required targets: Traefik, node-exporter, kube-state-metrics, Prometheus.
Kubelet/cAdvisor/etcd/controller-manager/scheduler/kube-proxy и defaultRules
в MVP выключены; их сбор не заявляется. Retention 24h/4GB для Prometheus,
24h для Loki; PVC 12Gi каждого.

## HTTP, логи и runtime

Тело HTTP 200 — ровно 12 bytes `Hello World!`, без перевода строки. `/` и
`/verify` обслуживают эту статику. `GET /verify/missing/<error-marker>`
пытается открыть отсутствующий файл (настоящий Nginx error), дает 404.
Nginx user 101, readOnlyRootFilesystem, drop ALL, allowPrivilegeEscalation
false, automountServiceAccountToken false, отдельные writable tmp/cache.

`X-Request-ID`: `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`; корректный сохраняется,
отсутствующий/невалидный заменяется `$request_id`. Возвращается всегда,
включая 404. JSON access в stdout: `time`, `request_id`, `method`, `uri`,
`status` (число), `bytes_sent` и `request_time`. Nginx error в stderr.
Fluentd читает только `/var/log/containers/hello-*_demo_nginx-*.log`,
mount containers + pods read-only, разбирает CRI regexp, JSON stdout и
не теряет stderr. Loki labels только `app="hello"`, `namespace="demo"`,
`container="nginx"`, `stream="stdout|stderr"`. Record включает `message`
и распарсенные поля. Request-ID/URI/IP/pod не labels. Доставка at-least-once,
дубли допустимы, гарантии отсутствия потерь при любом outage не заявляются.

## Интерфейс сборки и проверок

Собственные Helm charts версии 0.1.0. Static values не содержат адресов
конкретного стенда; интегратор генерирует overrides versions/environment.
Все image поля рендера переводит в digest общий post-renderer по lock;
неподдерживаемые динамические images задаются отдельно в values.

Вход: `make tools`, `make lint`, `make bootstrap ENV=ubuntu`, `make deploy
ENV=ubuntu`, `make verify ENV=ubuntu PROFILE=mvp`, `make verify-idempotency
ENV=ubuntu`, `make evidence ENV=ubuntu`. Обязательные инструменты ставятся
локально, Nix не требуется. Компонентные checks — Python stdlib, запускаются
как процессы и возвращают JSON в `--output`, exit 0 только при успехе.
Скрипт пишет отчет и при ошибке; ограниченные timeout обязательны.

Общий результат компонента: `{ "status": "pass|fail", "checks": [
{"name":"...", "status":"pass|fail", "expected":..., "actual":...}],
"data": {...} }`. Общие параметры `--timeout` seconds, `--output` path,
`--run-id`; Gateway принимает также `--kubeconfig` (обязателен).

- `check-gateway.py --mode conditions|traffic|all --kubeconfig PATH
  --base-url URL --run-id ID --request-id ID --error-marker ID
  --requests 20 --output PATH`. Traffic сохраняет основную access-пробу
  (GET `/`, request-id), делает еще нагрузку с производными ID и error-пробу;
  data: `request_id`, `error_marker`, `request_count`, `http`, `error_http`.
- `check-metrics.py --mode snapshot|verify --prometheus-url URL --run-id ID
  --baseline PATH --min-increase 20 --output PATH`. Snapshot сохраняет
  серии со значениями/timestamps, targets, query и время; verify читает
  этот JSON, ждет свежий scrape, проверяет increment, reset и targets.
  Отсутствие vector/свежего scrape не равно нулю и не pass.
- `check-logs.py --loki-url URL --run-id ID --request-id ID --error-marker ID
  --start-time UNIX_SECONDS --output PATH`. При переданных ID не создает
  новые HTTP-пробы. Дополнительный `--base-url` позволяет самостоятельный
  запуск без ID. Проверить access exact request_id/method/uri/status,
  отдельный stderr missing-file marker и источник.

Общий verify: readiness/conditions → warm-up → baseline → общие HTTP
пробы → counter delta → Loki access/error → runtime UID/capabilities/
read-only root mount/token. Port-forward Prometheus/Loki только loopback,
с cleanup. `artifacts/verify/<run-id>/report.json`: schema_version, run_id,
request_id, revision, profile, started_at, finished_at, environment (без
credentials), versions, checks, status. Ошибка любого обязательного check
дает ненулевой exit; следующие невозможные checks отмечаются fail с причиной.
Profile `final` пока завершается ошибкой unsupported, а не успешным skip.

MVP gate проверяет все шесть групп: readiness, gateway/http, metrics,
access/error logs, runtime security, сохранение identities/PVC при deploy.
NetworkPolicy/canary/TLS/CI относятся только к FINAL. Хостинг по умолчанию
GitHub; публикация и hosted CI сейчас не выполняются. Локальная проверка:
lint/render → bootstrap/deploy/verify → повтор → fresh Ubuntu приемка.
