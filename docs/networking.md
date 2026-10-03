# Сеть: IPv6 и IPv4

IPv6 — основной адрес Kubernetes API и Gateway, а также первое семейство
Services. IPv4 параллельно доступен приложению и компонентам. DNS/NTP,
локальные проверки и внешние загрузки могут использовать обе семьи.

## Настройка

В локальном environment задаются адреса одной VM: `node_ip` (IPv6),
`node_ipv4` (IPv4), а также `ssh_host`. Оба node IP должны быть стабильными
и принадлежать активным интерфейсам. Нужны default routes обеих семей.

| Назначение | Поле | Пример |
| --- | --- | --- |
| Pod IPv6 | pod_cidr | `fd4d:5453:1000:100::/56` |
| Pod IPv4 | pod_cidr_ipv4 | `10.244.0.0/16` |
| Service IPv6 | service_cidr | `fd4d:5453:1000:200::/112` |
| Service IPv4 | service_cidr_ipv4 | `10.96.0.0/16` |

Эти примеры нужно сверить со своими сетями и VPN. Bootstrap проверяет
пересечения до изменения VM. Он не преобразует уже созданный single-stack
кластер в dual-stack; для смены семейства сети нужна чистая VM.

Kubeadm получает CIDR в порядке IPv6,IPv4, kubelet объявляет оба InternalIP.
Calico использует два явных VXLAN/NAT pool, BGP выключен. Для сохранения
SLAAC при включенном IPv6 forwarding на uplink задается `accept_ra=2`.
Bootstrap не меняет netplan, router или настройки гипервизора.

Собственные Services имеют `ipFamilyPolicy: RequireDualStack` и
`ipFamilies: [IPv6, IPv4]`. Автоматически созданные `default/kubernetes`,
`kube-system/kube-dns` и `monitoring/prometheus-operated` могут оставаться
SingleStack IPv6; исключение проверяется по точному имени ресурса.

## Проверка и диагностика

`make health ENV=ubuntu` создает временные server/client Pod и Service,
проверяет DNS A/AAAA, HTTP к обоим Service IP и напрямую к обоим Pod IP.
После проверки временные ресурсы удаляются, в том числе при ошибке.

`make verify ENV=ubuntu` дополнительно сверяет Node/Pod IP, Calico pools,
Services и Ready EndpointSlices обоих семейств. Адреса EndpointSlices
должны соответствовать тем же Pod UID. HTTP и access/error-логи проверяются
по IPv6 и IPv4 раздельно.

При неполадках проверьте Node InternalIP, Pod `status.podIPs`, Service
`clusterIPs`, EndpointSlice `addressType` и маршруты узла. Сравните
`curl -6` и `curl -4` с `--noproxy '*'`. IPv6 в URL записывается в скобках.
В сертификат Kubernetes API включены оба адреса узла; отключать TLS
verification для доступа по IPv4 не требуется.

С управляющей машины нужны SSH, API 6443, HTTP 30080 и HTTPS 30443 по обеим
семьям. Host firewall должен пропускать маршрутизацию Pod. Метрики и Loki
доступны через loopback port-forward; для него необходим рабочий `::1`.

ServiceMonitor выбирает IPv6 EndpointSlices, чтобы не собирать один endpoint
дважды через разные адреса. Общий verify выявляет дубликаты Pod/port/path
независимо от job и Service. Автоматического переключения scrape на IPv4
при отказе IPv6 нет. Dual-stack также не делает одноузловой control plane
отказоустойчивым.

Подробная реализация — [Ansible](../ansible/README.md);
результаты — [протокол проверки](verification.md).
