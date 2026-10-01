# Сеть MVP: dual-stack, IPv6 основной

Статус: реализация согласованного пользователем режима. Приемка еще не выполнена.
Текущий контракт — [версия 3](contracts.md). Строгая IPv6-only приемка остановлена
по изменению требования; ее промежуточные отчеты не являются приемкой dual-stack.

IPv6 — основной адрес SSH/API/Gateway и первое семейство Services. IPv4 также
доступен приложению и компонентам. IPv4 loopback, DNS/NTP и загрузки разрешены.
Не добавляем переводчики протоколов, дополнительные прокси и фильтры для
запрета IPv4. Для нового семейства Pod/Service создается отдельный кластер.
Прежние VM/данные сохраняются; TSDB между кластерами автоматически не переносится.

Основа: [kubeadm dual-stack](https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/dual-stack-support/),
[Kubernetes Services](https://kubernetes.io/docs/concepts/services-networking/dual-stack/),
[Calico dual-stack](https://docs.tigera.io/calico/3.31/networking/ipam/ipv6).

## Установка и проверка

В environment задаются `node_ip` (IPv6), `node_ipv4`, IPv6-first Pod CIDR
`pod_cidr,pod_cidr_ipv4` и Service CIDR `service_cidr,service_cidr_ipv4`.
Все четыре сети должны быть свободны от пересечений с физической сетью/VPN.
`ssh_host` по умолчанию — IPv6 узла; явный IPv4 тоже разрешен. URL IPv6 содержит
скобки. Bootstrap не преобразует существующий single-stack кластер: изменение
семейства, адресов или CIDR требует новой выделенной VM.

Kubeadm получает CIDR в порядке IPv6,IPv4, kubelet объявляет оба InternalIP.
Calico использует два VXLAN/NAT pool. Собственные Helm Services имеют
`ipFamilyPolicy: RequireDualStack`, `ipFamilies: [IPv6, IPv4]`. Исключения допускаются
только для автоматически созданных `default/kubernetes`, `kube-system/kube-dns`
и `monitoring/prometheus-operated`; они могут быть SingleStack IPv6.

`make verify` проверяет 11 обязательных стадий: readiness, dual-stack ресурсы,
Gateway conditions, warmup, baseline метрик, HTTP по IPv6, HTTP по IPv4,
рост счетчика по суммарному трафику, логи IPv6, логи IPv4 и runtime security.
У каждого собственного Service проверяются обе семьи Ready EndpointSlices и
соответствие адресов фактическим Pod UID. Проверка SSH/API обеих семей, DNS A/AAAA,
прямого Pod/Service HTTP, чистой установки, повторов и восстановления дополнительно
входит в независимую [приемку](acceptance/dual-stack-acceptance.md).

## Что означает «IPv6 основной»

Главный kubeconfig, Gateway status и первый Service IP используют IPv6.
Порядок `ipFamilies` определяет основной ClusterIP, но не порядок DNS/getaddrinfo
у всех клиентов. IPv4 loopback и DNS/NTP разрешены, системные проверки не требуют
отдельных BPF-фильтров или запрета IPv4-сокетов. Внешние загрузки могут идти по любой семье.

ServiceMonitor выбирает IPv6 EndpointSlices. Это исключает двойной сбор метрик
через IPv4/IPv6 одного Pod; verifier отдельно выявляет дубликаты Pod/port/path
независимо от job, Service и IP. Этот выбор не обеспечивает автоматическое
переключение scrape на IPv4 при отказе IPv6. У single-node control plane также
нет HA. Dual-stack означает проверенную доступность обоих семейств, а не
автоматический failover всех клиентов.

## Диагностика

Проверяйте маршруты, обе Node InternalIP, Pod `status.podIPs`, Service `clusterIPs`
и EndpointSlice `addressType`. Для HTTP сравните `curl -6` и `curl -4` с
`--noproxy '*'`. API по IPv4 проверяется с тем же доверенным CA и сертификатом;
обе node IP входят в SAN, отключать TLS verification не требуется.
Опциональные отчеты Calico/Loki и проверки версии/usage Traefik отключены.
