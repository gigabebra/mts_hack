# Self-Verifying Kubernetes Stack — черновик паспорта MVP

Рабочий материал по структуре исходного задания. Финальный PDF, проверка
лимитов четырех страниц/15 МБ и архив относятся к отдельно назначаемой
сдаче. **MVP accepted** на кодовой ревизии
`337d0c671617929ebf162e1216099524fe8d611d`: независимый аудит и чистая установка
на второй Ubuntu подтверждены в [протоколе](../acceptance/mvp-acceptance.md).

## Страница 1. Архитектура и состав

Одноузловой Kubernetes публикует Nginx с ответом `Hello World!` через
Gateway API. Установка рассчитана на отдельную Ubuntu 24.04 amd64;
чистая приемка — Ubuntu 24.04.5, 4 vCPU, 8 GiB RAM, диск 60 GiB.
Proxmox использовался для создания тестовой VM, но не является зависимостью
развертывания. Коммерческие облачные сервисы и инфраструктура автора не нужны.

```mermaid
flowchart LR
    U[Проверяющий] -->|HTTP NodePort 30080| T[Traefik 3.6.2]
    T --> G[Gateway / HTTPRoute]
    G --> S[Service hello]
    S --> N[Nginx 1.28.0]
    P[Prometheus 3.8.1] -->|scrape| T
    P --> E[node exporter / kube state metrics]
    N -->|CRI stdout / stderr| F[Fluentd 1.19.0]
    F --> L[Loki 3.6.2]
    P --- V1[local PV]
    L --- V2[local PV]
```

| Состав | Реализация |
| --- | --- |
| ОС и кластер | Ubuntu 24.04; kubeadm/Kubernetes 1.34.3; containerd 1.7.28; Calico 3.31.2 VXLAN |
| Публикация | Gateway API Standard 1.4.0; Traefik 3.6.2 / chart 37.4.0; GatewayClass, Gateway, HTTPRoute |
| Приложение | Публичный Nginx unprivileged 1.28.0-alpine, ConfigMap и собственный Helm chart |
| Мониторинг | kube-prometheus-stack 80.6.0; Operator 0.87.1 создает Prometheus 3.8.1; node exporter 1.10.2, kube state metrics 2.17.0 |
| Логирование | Fluentd DaemonSet 1.19.0 с Loki plugin из image 3.6.2; Loki 3.6.2, filesystem TSDB/WAL |
| Автоматизация | Make, Python, Ansible core 2.19.3, Helm 3.19.0; версии/SHA256/image digests в репозитории |
| Хранение | Два local PV по 12Gi, Retain; persistent positions и buffer Fluentd на hostPath |

## Страница 2. Обязательные функции и проверка

| Пункт задания | Реализация и обоснование | Как проверить |
| --- | --- | --- |
| Kubernetes на Ubuntu 24.04 | Ansible готовит ОС/containerd, kubeadm создает кластер; отсутствие зависимости от облачного вендора | `make bootstrap ENV=ubuntu`, `make health ENV=ubuntu`: Node Ready, DNS и HTTP между pod |
| HTTP-приложение | Nginx возвращает ровно 12 bytes, пишет JSON access в stdout и настоящие error в stderr; собственной бизнес-логики не требуется | `make verify ENV=ubuntu`; curl NodePort из README |
| Gateway API | Traefik, GatewayClass `mts-traefik`, Gateway `gateway`, HTTPRoute `hello`; backend — ClusterIP Service | Verify проверяет актуальные conditions, HTTP 200, `/verify`, 404 на unmatched route |
| Prometheus | ServiceMonitor собирают Traefik, node exporter, kube state metrics и Prometheus; видны HTTP и состояние узла | Verify проверяет targets, свежие samples и прирост counter; ручные API queries в README |
| Fluentd и поиск логов | Collector читает CRI только Nginx, сохраняет stream/source, передает в Loki; централизованный поиск воспроизводим через API | Verify находит exact access Request-ID и отдельный missing-file stderr marker |
| Автоматизация | `make tools/lint/bootstrap/deploy/verify`, локальный environment, SHA256 и image lock; ручная правка Kubernetes-ресурсов не нужна | Повтор bootstrap и `make verify-idempotency ENV=ubuntu` |
| Документация | README содержит схему, версии, сеть/ресурсы, команды и диагностику | Независимая установка только по README; матрица требований в `docs/acceptance/` |

Для развертывания нужны публичные repositories/registries, SSH с sudo,
непересекающиеся CIDR и контролируемый доступ к портам из README. Единственный
environment явно выбирает kubeconfig. Проверяемые команды и сводки связаны
с SHA ревизии; локальные адреса, private keys и kubeconfig не входят в Git.

На чистой VM первый полный verify получен за 5 min 54 s; весь прогон с
повторами — 8 min 56 s без подготовки ОС и apt-предпосылок. После прогона
доступно 5.87 GiB RAM, занято 6.12 GiB диска; CPU busy 3.89% за 30 s.
Это измерение одного стенда, не минимальные требования и не пиковая нагрузка.

## Страница 3. Дополнения, ревью и развитие

Дополнительные возможности текущего MVP:

- Один verify связывает HTTP-ответ и access-log по Request-ID, отдельно
  проверяет увеличение HTTP counter и сохраняет JSON даже при отказе.
  Это сокращает ручную диагностику; `make verify` возвращает ненулевой код
  при ошибке любого обязательного компонента.
- Nginx работает без root/API token/capabilities с read-only root,
  probes и resources. Проверяется фактический PID1 и mount через verify.
- `make verify-recovery` проверяет историю Prometheus/Loki после замены pod,
  offsets collector и доставку пяти маркеров после короткого outage Loki
  с рестартом Fluentd. На первом стенде сценарий pass; это не backup/HA.

**Главная особенность.** Работоспособность подтверждается единым сценарием:
реальный запрос, актуальный маршрут, свежая метрика и точная запись Loki.
Отчет хранит ревизию, ожидаемый и фактический результат и не считает
отсутствие данных успешной проверкой.

**Сложный выбор.** Для воспроизводимости на обычной VM выбран NodePort с
Traefik/Gateway API вместо зависимости от внешнего LoadBalancer.
Local PV позволяют проверить сохранение истории при замене pod без NAS,
но сознательно ограничивают отказоустойчивость одним узлом и диском.

**Потенциальное развитие.** Добавить TLS/NetworkPolicy и canary с проверкой
метрик; потребуются сертификаты и отдельные сценарии отказов.
Для телеком-нагрузки полезны несколько edge-площадок, SLO доступности/latency
и испытания деградации каналов; нужны дополнительные узлы и эмуляция сети.
HA и backup/restore потребуют независимого хранилища и проверки восстановления.

Границы: HTTP без TLS, single-node без HA/backup, local PV не ограничивают
занятый каталог квотой. Prometheus retention 24h/4GB, Loki 24h; buffer
Fluentd 256MiB допускает дубли и не гарантирует сохранность при произвольном
outage. HTTP counter агрегирован, reset между scrape может остаться
невидимым; Request-ID коррелирует только HTTP и log. CI, TLS, canary,
dashboards/alerts и сетевые политики пока не реализованы.
