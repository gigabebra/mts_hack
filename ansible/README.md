# Ubuntu bootstrap MVP-01: dual-stack, IPv6 основной

Контракт — `docs/contracts.md` версии 3. Интегратор запускает bootstrap на
новой выделенной Ubuntu 24.04 amd64 VM: 4 vCPU, nominal 8 GiB RAM, диск
60 GiB. Guard требует минимум 7000 MiB MemTotal, 50 GiB root filesystem,
35 GiB свободного места для установки и 10 GiB для повторного запуска.
Нужны Python3, SSH с sudo без пароля, завершенный cloud-init и доступные
DNS/NTP. Установка из Ubuntu Server ISO также поддерживается.

Скопируйте `config/environments/ubuntu.example.yaml` в игнорируемый
`<ENV>.local.yaml`. `node_ip` — основной стабильный IPv6, `node_ipv4` — IPv4
той же VM. Документальные адреса `2001:db8:20::10` и `192.0.2.10` заменяются
реальными. SSH и kubeconfig используют IPv6 по умолчанию. Inventory содержит
ровно один явный host в группе `mvp`; wrapper строит его из environment.

```sh
make bootstrap ENV=ubuntu
make health ENV=ubuntu
```

Прямой запуск с уже подготовленным inventory:

```sh
ansible-playbook -i .state/ubuntu/inventory.yaml ansible/bootstrap.yaml \
  -e @config/environments/ubuntu.local.yaml
ansible-playbook -i .state/ubuntu/inventory.yaml ansible/health.yaml \
  -e @config/environments/ubuntu.local.yaml
```

Пути kubeconfig разрешаются относительно checkout. Файл имеет mode 0600,
каталог — 0700. Playbooks не используют local current-context; `health.yaml`
обращается только к `/etc/kubernetes/admin.conf` выбранной VM и пригоден после
контролируемого reboot интегратором.

## Сеть и предварительные проверки

Kubeadm получает сети в порядке IPv6,IPv4:

| Назначение | IPv6 | IPv4 |
| --- | --- | --- |
| Pod CIDR | `fd4d:5453:1000:100::/56` | `10.244.0.0/16` |
| Service CIDR | `fd4d:5453:1000:200::/112` | `10.96.0.0/16` |
| Per-node Pod CIDR | /64 | /24 |
| Calico allocation block | /122 | /26 |

Поддерживаемые размеры: IPv6 Pod /48../64 и Service /108../120;
IPv4 Pod /8../24 и Service /12../24. До изменений проверяются обе назначенные
node IP на UP интерфейсах, default routes обеих семей и пересечения Pod,
Service, node IP и уже существующих маршрутов каждой семьи. При повторе
исключаются только Calico routes внутри Pod CIDR подтвержденного кластера.

Immutable identity включает `dual-stack-ipv6-primary`, оба адреса, четыре
CIDR, версии, DNS/NTP и data_root. Чужой кластер, частичный kubeadm init,
изменение identity или недоступный existing API вызывают отказ. Старый
single-stack стенд нельзя преобразовать этим bootstrap: нужна новая чистая
VM; автоматических reset, upgrade и переноса старых PVC нет. Даже удаление
устаревших ограничивающих конфигов разрешено только после identity guard.

Kubelet объявляет `node-ip=IPv6,IPv4`. Выбор основного PodIP подтвержден
[исходником kubelet 1.34.3](https://github.com/kubernetes/kubernetes/blob/v1.34.3/pkg/kubelet/kubelet_pods.go#L2015):
он сортирует PodIPs по семье первого node IP, в том числе для downward API.
API и kubelet слушают `::` с `net.ipv6.bindv6only=0`, принимая обе семьи.
API advertises IPv6, сертификат включает оба адреса; kubeconfig остается
основным IPv6 путем. Это не автоматическое переключение control plane
при отказе сети и не multi-node HA. Kube-proxy NodePort ограничен двумя
адресами VM (`node_ip/128`, `node_ipv4/32`).

Calico CNI включает обе IPAM семьи, `IP` и `IP6` задаются явно. После
Established CRD применяются два явных IPPool: VXLAN Always, IPIP Never,
NAT outgoing, BGP export off. `NO_DEFAULT_POOLS=true` предотвращает
неявные сторонние pools. BGP выключен; health оставляет штатные Felix exec
пробы без BIRD. IP forwarding включен в обеих семьях; `accept_ra=2` для
all/default/uplink устанавливается до IPv6 forwarding и сохраняет SLAAC.

SSH принимает обе семьи: AddressFamily any, IPv4/IPv6 ListenAddress. Для
Ubuntu socket activation используется `[::]:port` с BindIPv6Only=both.
Сначала проверяется `sshd -t`; master/socket перезапускаются одним systemd
job с проверкой KillMode=process, сохраняющей установленные SSH сессии.
Специальные SSH IPv6-only overrides удаляются на идентифицированной dual VM.

`dns_servers` и `ntp_servers` содержат 1–3 IP обеих семей, доступные из VM;
IPv6 задается первым при наличии маршрута. Для kubelet/CoreDNS создается
явный `/etc/mts-stack/resolv.conf`, systemd-resolved получает тот же список.
NTP fallback задается явными адресами; прежний timesyncd BPF-фильтр удаляется.
DHCP IPv4 NTP, обычный resolver stub, IPv4 loopback и внешние загрузки
разрешены. Bootstrap не изменяет netplan, router или гипервизор.

CoreDNS использует штатные wildcard DNS/health/ready/metrics, без PodIP bind
и специальных self-health overrides. Containerd CRI streaming и локальные
control-plane health probes используют штатный loopback. Образы остаются
закреплены digest. Управляемый Corefile применяется и при repeat; стабильный
checksum перезапускает CoreDNS только при изменении конфигурации.

Необязательные usage/version checks Felix остаются выключены отдельным
`FELIX_USAGEREPORTINGENABLED=false`. [Default](https://github.com/projectcalico/calico/blob/v3.31.2/felix/config/config_params.go#L424),
[условие запуска](https://github.com/projectcalico/calico/blob/v3.31.2/felix/daemon/daemon.go#L625)
и [внешний endpoint](https://github.com/projectcalico/calico/blob/v3.31.2/felix/usagerep/usagerep.go#L34)
сверены с Calico 3.31.2. Это решение независимо от допустимости IPv4 трафика.

## Health и сохранение данных

Health создает отдельные server/client Pods и собственный Service
`ipFamilies: [IPv6, IPv4]`, `ipFamilyPolicy: RequireDualStack`. Он требует
два реальных адреса у обоих Pods, проверяет AAAA/A Service, HTTP по обоим
Service IP и напрямую по обоим server Pod IP. Single-stack или неверный
primary порядок вызывает fail. Ресурсы получают уникальные имена и удаляются
даже при сбое. Системные Services `kubernetes` и `kube-dns` могут оставаться
SingleStack IPv6: health проверяет основной Kubernetes AAAA и не пытается
насильно преобразовать эти ресурсы. Это совместимо с dual-stack приложением.

Containerd, runc, CNI, Helm и Calico скачиваются с SHA256 из `versions.yaml`;
Kubernetes packages имеют точные версии и apt hold. Все kubeadm images
проверяются по реальному списку установленного kubeadm и скачиваются по
digest. Локальные tag aliases и imagePullPolicy Never служат kubeadm
preflight; static Pod/CoreDNS patches и сгенерированный kube-proxy закреплены.

Bootstrap готовит директории хранения; PV принадлежат Helm release `storage`:
`local-retain`, `mts-prometheus`, `mts-loki`, по 12Gi, WaitForFirstConsumer,
Retain, node affinity. Prometheus: 1000:2000/0770, Loki: 10001:10001/0770,
Fluentd: root:root/0750. Retain/Helm keep не являются backup; 12Gi — capacity
Kubernetes, не отдельная filesystem quota. Данные делят диск VM.

Снаружи по обеим семьям нужны SSH, API 6443 и HTTP NodePort 30080. Метрики
и Loki не публикуются; диагностика использует loopback port-forward с явным
kubeconfig. Host firewall должен пропускать маршрутизацию Pod. Для будущих
нескольких узлов VXLAN требует UDP4789 обеих семей, но MVP — один узел.
Наружу нужны DNS, NTP и HTTPS для Ubuntu, pkgs.k8s.io, GitHub, get.helm.sh,
registry.k8s.io, quay.io и Docker Hub с их redirect/CDN.

## Проверки без VM

```sh
export ANSIBLE_LOCAL_TEMP=/tmp/mvp01-ansible
export ANSIBLE_REMOTE_TEMP=/tmp/mvp01-ansible-remote
ansible-playbook -i mvp, ansible/bootstrap.yaml --syntax-check \
  -e @config/environments/ubuntu.example.yaml
ansible-playbook -i mvp, ansible/health.yaml --syntax-check
ansible-playbook -i localhost, ansible/render.yaml \
  -e calico_source_file=/absolute/path/to/checksum-verified/calico.yaml
python -m unittest discover -s ansible/tests -v
helm lint deploy/storage
```

`render.yaml` проверяет SHA256 исходного Calico и пишет только локальные
файлы. Fixtures/render не заменяют чистый bootstrap, повтор, reboot, реальные
AAAA/A и HTTP/SSH/API по обеим семьям. Сетевые параметры соответствуют
[официальному kubeadm dual-stack](https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/dual-stack-support/)
и [Calico dual-stack](https://docs.tigera.io/calico/latest/networking/ipam/ipv6).
