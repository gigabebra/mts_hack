# IPv6: доступ, внутренняя сеть и переход с IPv4

Назначение пользователя: весь стек работает по IPv6 снаружи и внутри
кластера. IPv4 у ОС разрешен только для получения публичных пакетов и
образов. Это single-stack IPv6 Kubernetes, не dual-stack.

| Соединение | Адресация |
| --- | --- |
| SSH, Kubernetes API, kubelet | Явный маршрутизируемый IPv6 узла |
| Внешний HTTP | `http://[<node_ip>]:30080/`, IPv6 NodePort |
| Pod, Service, EndpointSlice | Только IPv6, отдельные Pod/Service CIDR |
| Calico | Явный IPv6 IPPool, VXLAN, IPv4 IPAM выключен |
| Cluster DNS | Service IPv6, записи AAAA, upstream из `dns_servers` по IPv6 |
| Метрики и Fluentd → Loki | IPv6 Pod/Service endpoints; IPv4 target дает ошибку verify |
| Prometheus/Loki diagnostics | Локальный `::1`, API и CRI streaming по IPv6 |
| Config reloader и внутренний Loki ring | IPv6 loopback `::1` |
| Синхронизация времени | IPv6 из `ntp_servers` |

В `config/environments/ubuntu.local.yaml` IPv6 literals задаются **без**
квадратных скобок. Скобки требуются только в URL и составном `host:port`.
Пример узла в репозитории — `2001:db8:20::10`; это документационный адрес,
его нужно заменить. Примеры Pod/Service сетей — `fd4d:5453:1000:100::/56`
и `fd4d:5453:1000:200::/112`; они должны отличаться от LAN/VPN и друг от друга.
IPv6 ULA доступен только из сетей, где есть маршрут к нему. Наличие GUA
само по себе не является разрешением публиковать стенд в Internet.

ОС сохраняет IPv4/DHCPv4 для загрузок из источников, не имеющих IPv6.
Локальный resolver stub ОС `127.0.0.53` может обслуживать загрузчики;
его upstream задается IPv6. Pod/CoreDNS используют отдельный управляемый
resolv.conf с IPv6-серверами, а не этот stub. Работа приложения, сбор
метрик/логов и управляющие подключения не зависят от IPv4 адреса ОС.

Bootstrap включает IPv6 forwarding и `accept_ra=2` на uplink, сохраняя SLAAC
и default route. SSH socket слушает только IPv6; kube-proxy разрешает
NodePort только на IPv6 узла. Смена семейства или CIDR прежней identity
останавливает bootstrap до изменений. Чужие кластеры не сбрасываются.

## Почему недостаточно заменить URL

В закрепленном containerd 1.7.28 CRI port-forward сначала пробует IPv4
loopback внутри Pod. Поэтому один `kubectl --address=::1` не доказывает
IPv6 на последнем участке. Loki использует HTTP/gRPC `tcp6`; Prometheus
слушает два конкретных IPv6 адреса: Pod IP и `::1`. IPv4 loopback к этим
API не принимается, и CRI переходит к IPv6. Config reloader использует
`http://[::1]:9090`. Внутренние HTTP checkers обходят HTTP proxy окружения
и запрещают смену транспорта через redirect.

Wildcard listener некоторых остальных компонентов может принимать IPv4
loopback в своем namespace; это не IPv4 Pod/Service сеть. Фактические
соединения проверяются отдельно: все targets, DNS, sockets и внешний HTTP.
Утверждение о сетях IPv6-only не означает удаление IPv4 из ядра Linux.

Источники реализации: [Kubernetes single-stack/dual-stack](https://kubernetes.io/docs/concepts/services-networking/dual-stack/),
[CRI port-forward containerd 1.7.28](https://github.com/containerd/containerd/blob/v1.7.28/pkg/cri/server/sandbox_portforward_linux.go),
[Prometheus 3.8.1 listen addresses](https://github.com/prometheus/prometheus/blob/v3.8.1/cmd/prometheus/main.go),
[параметры серверов Loki](https://grafana.com/docs/loki/latest/configure/).

## Переход и проверка

Изменение сетевых CIDR выполняется новым bootstrap на чистой выделенной
VM. Предыдущая VM выключается, ее диск, kubeconfig и локальный environment
сохраняются для возврата. Она не является IPv6-кластером после такого
перехода. Исторические Prometheus/Loki данные остаются на прежнем диске;
новый кластер начинает собственную историю. Перенос TSDB не заявляется.
Реальные адреса и параметры доступа хранятся только в игнорируемом `.state/`.

Порядок установки совпадает с README. `make verify` дополнительно проверяет
IPv6 API/InternalIP/Pod/Service/EndpointSlice/IPPool и адреса всех active
Prometheus targets. После deploy следует выполнить повторные bootstrap,
deploy и `make verify-recovery`, затем reboot и `make health`/`make verify`.
Внешний `curl -6 --noproxy '*'` должен вернуть HTTP 200, ровно 12 bytes и
совпадающий Request-ID. IPv4 подключения к 22/6443/30080 должны отклоняться.

Проверка самого транспорта дополняется наблюдением runtime sockets,
manifest/argv управляющих компонентов, AAAA без A для Kubernetes Services
и отрицательными пробами IPv4 loopback к Prometheus/Loki. Исторический
[IPv4-протокол MVP](acceptance/mvp-acceptance.md) сохраняется как отдельное
доказательство прежней ревизии, а не как приемка новых IPv6 изменений.
