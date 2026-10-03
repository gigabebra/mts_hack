# Архитектура решения

Версии и контрольные суммы зафиксированы в `config/versions.yaml`, образы —
в `config/images.lock.yaml`. Архивы проверяются по SHA-256, контейнеры запускаются
по digest. Для записи `repository@digest` проверяется вся пара; известный
digest не разрешает использовать другой repository или tag. Это относится
и к образам, которые создает Prometheus Operator.

## Установка и выбор стенда

`scripts/deploy.sh` готовит инструменты управляющей машины и вызывает
`deploy.py`. Тот под одной блокировкой последовательно запускает Ansible
bootstrap и установку компонентов через Helm. Ошибка bootstrap прекращает
весь запуск. `make bootstrap` и `make health` доступны отдельно.

Локальный `config/environments/<ENV>.local.yaml` задает узел, SSH, kubeconfig,
адреса, подсети и DNS/NTP. Пути разрешаются относительно корня репозитория.
Команды передают kubeconfig явно; унаследованный `KUBECONFIG` и текущий
контекст не выбирают целевой кластер. API проверяется по доверенному CA
и настроенному `node_ip`, без HTTP proxy.

Bootstrap записывает идентичность кластера: имя и адреса узла, Pod/Service
CIDR, версии, DNS/NTP и каталог данных. Перед повторным запуском проверяются
этот файл и реальный узел. Чужой или частично созданный кластер не сбрасывается.
Смена сетевого режима и обновление Kubernetes этим установщиком не поддерживаются.

## Ресурсы

| Namespace | Helm release | Основные ресурсы |
| --- | --- | --- |
| default | storage | StorageClass `local-retain`, PV `mts-prometheus`, `mts-loki` |
| edge | traefik | Deployment/Service `traefik`, Service `traefik-metrics` |
| demo | app | Deployment/Service/ConfigMap `hello`, stable, две реплики |
| demo | app-canary | Deployment/Service/ConfigMap `hello-canary`, одна реплика |
| demo | gateway | GatewayClass `mts-traefik`, Gateway `gateway`, четыре HTTPRoute, Secret `hello-tls` |
| monitoring | monitoring | Prometheus Operator, Prometheus, node exporter, kube state metrics |
| monitoring | monitoring-extras | ServiceMonitor Traefik |
| logging | logging | Loki Deployment/Service/PVC и Fluentd DaemonSet |

При `network_mode: ipv4` весь стек использует IPv4: один Calico VXLAN/NAT pool,
Services с `SingleStack` / `[IPv4]`, API на IPv4 и port-forward на `127.0.0.1`.
Режим выбирается до первой установки, поддержка IPv6 в ядре не нужна.

В режиме `dual-stack` IPv6 является основным, IPv4 доступен параллельно. Calico использует два
VXLAN/NAT pool, kube-proxy работает в обычном режиме. Собственные Services:
`RequireDualStack`, `ipFamilies: [IPv6, IPv4]`. Системные исключения и
проверка EndpointSlices описаны в [сетевой инструкции](networking.md).

StorageClass использует no-provisioner, `WaitForFirstConsumer` и `Retain`.
PV привязаны к имени узла, PVC указывают конкретный `volumeName`.

| Данные | Путь по умолчанию | UID:GID / mode | Размер |
| --- | --- | --- | --- |
| Prometheus | `/var/lib/mts-stack/prometheus` | 1000:2000 / 0770 | PV 12 GiB |
| Loki | `/var/lib/mts-stack/loki` | 10001:10001 / 0770 | PV 12 GiB |
| Fluentd positions и buffer | `/var/lib/mts-stack/fluentd` | root:root / 0750 | Буфер 256 MiB |

Корень задается через `data_root`. Каталоги и права создает bootstrap.
Capacity PV не ограничивает размер каталога на файловой системе;
данные используют общий диск VM. Retain не заменяет резервную копию.

## HTTP, маршруты и TLS

Traefik: `web` 8000 → Service 80 → NodePort 30080;
`websecure` 8443 → Service 443 → NodePort 30443. Provider — Kubernetes Gateway;
Ingress и Traefik CRD providers выключены. `GatewayClass.controllerName` —
`traefik.io/gateway-controller`. Gateway status содержит `node_ip`.

| HTTPRoute | Запрос | Backend |
| --- | --- | --- |
| hello | Exact `/`, PathPrefix `/verify` | stable/canary по весам |
| hello-host | Те же пути, hostname `hello.test` | Те же веса |
| hello-stable | Exact `/backend/stable`, rewrite в `/` | Только stable |
| hello-canary | Exact `/backend/canary`, rewrite в `/` | Только canary |

Все маршруты принадлежат одному release `gateway`. Значения `tls_hostname`,
`https_node_port` и `node_port` задаются в конфигурации окружения. CA и сертификат
генерируются автоматически; локальные ключи хранятся в `.state/tls/<ENV>/`
с правами 0600, каталог — 0700. Secret `hello-tls` содержит `tls.crt`,
`tls.key` и публичный `ca.crt`. Повторный deploy сохраняет сертификаты;
новый checkout может получить их из существующего Secret. Просроченный
или несовместимый сертификат останавливает deploy. Автоматической ротации нет.

Nginx возвращает ровно 12 байт `Hello World!`, без перевода строки.
`/unrouted` дает 404 на Gateway; `/verify/missing/<marker>` дает настоящий
missing-file error Nginx. Заголовок `X-Request-ID` сохраняется, если соответствует
`[A-Za-z0-9][A-Za-z0-9._-]{0,63}`; иначе генерируется новый. `X-Release`
и `X-Revision` определяют обслуживший запрос backend.

Nginx работает с UID 101, read-only root filesystem, без capabilities,
privilege escalation и ServiceAccount token. Writable tmp/cache вынесены
в отдельные volumes. Stable использует RollingUpdate с `maxUnavailable: 0`;
перед остановкой Pod предусмотрено время на удаление endpoint из Gateway.
Canary обновляется через Recreate при нулевом весе.

## Метрики и логи

Prometheus собирает Traefik, node exporter, kube state metrics и себя.
Интервал scrape/evaluation — 15 секунд, timeout — 10 секунд. ServiceMonitor
Traefik выбирает Service `edge/traefik-metrics`, порт `metrics:9100`, и имеет
label `release: monitoring`. Для сквозной проверки используется счетчик:

```promql
traefik_entrypoint_requests_total{namespace="edge",service="traefik-metrics",endpoint="metrics",entrypoint="web",code="200",method="GET"}
```

Перед нагрузкой сохраняется исходное значение, после нее ожидается новый
scrape. Проверяются свежесть, прирост и наблюдаемое уменьшение счетчика.
Метрики агрегированы; Request-ID не является label и не связывает отдельный
запрос с отдельным sample. Двойной scrape одного Pod/port/path запрещен.
Сбор kubelet/cAdvisor/control-plane metrics и defaultRules не включен.

Fluentd читает `/var/log/containers/hello-*_demo_nginx-*.log`, включая canary.
CRI stdout разбирается как JSON access, stderr сохраняет Nginx error.
Labels Loki ограничены `app`, `namespace`, `container`, `stream`; Request-ID,
URI, pod и адреса остаются в записи. Positions и файловый буфер сохраняются
на hostPath. Доставка допускает дубли; при длительном сбое возможна потеря непрочитанных логов. [Настройки коллектора](../deploy/logging/README.md).

## Управление выпуском

`release_control.py` объединяет операции Helm, TLS и проверку целевого узла.
Deploy, canary и сценарии отказов используют общую блокировку. На одном
стенде изменения выполняются последовательно.

1. Скрипт подтверждает веса 100/0 и готовность stable: Helm status, актуальное поколение,
   все реплики, закрепленный образ и неизменность values.
2. Устанавливает кандидата с весом 0 и проверяет его отдельным маршрутом.
3. Снова проверяет stable и включает 90/10. Ожидает фактического
   `X-Traffic-Split`, заданного в той же HTTPRoute, что и веса.
4. Измеряет HTTP и метрики конкретного canary backend. Перед принятием
   еще раз подтверждает готовность stable и canary.
5. При отказе или недостатке данных возвращает 100/0, проверяет stable-трафик
   и восстанавливает здоровую конфигурацию кандидата.

Параметры окна и порогов — `config/canary.yaml`. HTTP-запросы в измеряемой выборке выполняются без повторных попыток. Ошибочный ответ, тело, роль или ревизия означают отказ.
Недоступные/устаревшие метрики или недостаточная выборка дают `inconclusive`.
Неуспешный выпуск возвращает ненулевой код даже при успешном откате;
ошибка отката фиксируется как `rollback_failed`.

При повторном запуске читаются реальные веса из кластера. Для прерванного
Helm release выбирается завершенная безопасная ревизия; для Gateway —
с весами 100/0. Если такой ревизии нет, выполнение прекращается с ошибкой.
Источником состояния не служит прежний локальный отчет.
