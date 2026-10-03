# Сеть: IPv6 и IPv4

Сетевой режим задается в локальной конфигурации окружения до первой установки:

| `network_mode` | Адреса VM | Kubernetes / Services | Локальный port-forward |
| --- | --- | --- | --- |
| `dual-stack` (по умолчанию) | IPv6 `node_ip` и IPv4 `node_ipv4` | IPv6 первый, `RequireDualStack` | `::1` |
| `ipv4` | IPv4 `node_ip`; `node_ipv4` можно опустить | Только IPv4, `SingleStack` | `127.0.0.1` |

Для VM без IPv6 возьмите [пример конфигурации IPv4](../config/environments/ubuntu-ipv4.example.yaml).
Достаточно IPv4 default route, стабильного IPv4 на активном интерфейсе и
IPv4 DNS/NTP. Используются `pod_cidr_ipv4` и `service_cidr_ipv4`; IPv6 CIDR
не нужны. Bootstrap не включает IPv6. API, kubelet, SSH, Traefik, Nginx,
Loki, Fluentd и мониторинг используют IPv4; Calico создает один IPv4 pool.
Health проверяет DNS A, Service и прямой Pod IPv4. Verify, TLS, canary и
восстановление проверяют тот же набор функций через IPv4. Метрики собираются
с IPv4 EndpointSlices. На управляющей машине также не требуется IPv6.

Автоматического определения или переключения режима нет. Режим входит в
неизменяемую конфигурацию кластера: для смены нужна чистая VM.

Далее описан режим `dual-stack`. IPv6 — основной адрес Kubernetes API и Gateway, а также первое семейство
Services. IPv4 параллельно доступен приложению и компонентам. DNS/NTP,
локальные проверки и внешние загрузки могут использовать обе семьи.

## Настройка

В локальной конфигурации задаются адреса одной VM: `node_ip` (IPv6),
`node_ipv4` (IPv4), а также `ssh_host`. Оба node IP должны быть стабильными
и принадлежать активным интерфейсам. Нужны маршруты по умолчанию обеих семей.

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
Bootstrap не меняет netplan, маршрутизатор или настройки гипервизора.

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
В сертификат Kubernetes API включены оба адреса узла; отключать проверку TLS для доступа по IPv4 не требуется.

С управляющей машины нужны SSH, API 6443, HTTP 30080 и HTTPS 30443 по обеим
семьям. Межсетевой экран узла должен пропускать маршрутизацию Pod. Метрики и Loki
доступны через loopback port-forward; для него необходим рабочий `::1`.

ServiceMonitor выбирает IPv6 EndpointSlices, чтобы не собирать один endpoint
дважды через разные адреса. Общий verify выявляет дубликаты Pod/port/path
независимо от job и Service. Автоматического переключения scrape на IPv4
при отказе IPv6 нет. Один узел control plane не обеспечивает отказоустойчивость.

Подробная реализация — [Ansible](../ansible/README.md);
результаты — [протокол проверки](verification.md).
