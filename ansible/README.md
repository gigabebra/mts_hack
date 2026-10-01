# Ubuntu bootstrap MVP-01

Интегратор запускает bootstrap только на выделенной Ubuntu 24.04 amd64 VM:
4 vCPU, nominal 8 GiB RAM, диск 60 GiB. Guard учитывает накладные расходы ОС:
минимум 7000 MiB MemTotal, 50 GiB root filesystem, 35 GiB свободного места
для первой установки и 10 GiB для повторной. Требуются Python3, SSH с sudo
без интерактивного пароля и синхронизация времени. Cloud image должен
закончить cloud-init до запуска; установка из Server ISO также поддерживается.

Скопируйте `config/environments/ubuntu.example.yaml` в игнорируемый
`<ENV>.local.yaml`. Inventory должен содержать ровно один явный хост в группе
`mvp`; общий wrapper интегратора строит inventory из environment. Например:

```ini
[mvp]
dedicated ansible_host=2001:db8:20::10 ansible_user=ubuntu ansible_port=22 ansible_python_interpreter=/usr/bin/python3
```

```sh
ansible-playbook -i .state/ubuntu/inventory.yaml ansible/bootstrap.yaml \
  -e @config/environments/ubuntu.local.yaml
ansible-playbook -i .state/ubuntu/inventory.yaml ansible/health.yaml \
  -e @config/environments/ubuntu.local.yaml
```

Пути kubeconfig разрешаются относительно корня checkout; итоговый файл
имеет mode 0600, его каталог — 0700. Playbook нигде не обращается к локальному
current-context. `health.yaml` обращается только к явному
`/etc/kubernetes/admin.conf` на выбранной VM. Он пригоден и после
контролируемого reboot, который запускает интегратор отдельно.

Bootstrap сверяет ОС, IP, ресурсы, CIDR и существующую идентичность до
изменения VM. Чужой кластер, несовпадение versions/environment, частичный
kubeadm init или недоступный существующий API завершаются ошибкой. Reset
и upgrade не выполняются. При повторном запуске kubeadm init пропускается;
настройки ОС, сеть и image digests сверяются повторно. Временные health Pod,
Service и Job создаются с уникальными именами и удаляются даже после сбоя.
Они проверяют реальные AAAA Kubernetes и тестового Service, единственную
IPv6-семью Pod/Service, HTTP между двумя pod через IPv6 ClusterIP и напрямую
по `[PodIPv6]:8080`. Ошибки DNS/IPv6 не считаются успешным пропуском.

Containerd, runc, CNI plugins, Helm и Calico скачиваются с SHA256 из
`versions.yaml`; Kubernetes deb packages имеют точную версию и apt hold.
Все kubeadm images проверяются по фактическому `kubeadm config images list`,
pull выполняется по digest. Локальные tag aliases нужны kubeadm preflight;
`imagePullPolicy: Never` запрещает ему заново разрешать tag. Static Pods и
CoreDNS получают patches с digest до запуска; kube-proxy генерируется
`kubeadm init phase addon kube-proxy --print-manifest`, затем pin/apply.
Calico YAML структурно переводится в IPv6 VXLAN без BGP; все images закрепляются.
CNI назначает только IPv6 (`assign_ipv4=false`, `assign_ipv6=true`), Calico
не ищет IPv4 узла (`IP=none`, `IP6=<node_ip>`). `NO_DEFAULT_POOLS=true`
предотвращает неявное создание IPv4 pool. После Established CRD отдельно
применяется `mts-ipv6-pool`: CIDR environment, VXLAN Always, IPIP Never,
blockSize 122, NAT outgoing и отключенный BGP export. Это также обходит
особенность закрепленного [startup Calico 3.31.2](https://github.com/projectcalico/calico/blob/v3.31.2/node/pkg/lifecycle/startup/startup.go):
чтение `CALICO_IPV6POOL_VXLAN` вложено в ветку IPv4 pool, поэтому одного env
для явно отключенного IPv4 pool недостаточно. Felix health слушает `::1`;
Kubelet проверяет HTTP по IPv6, без exec-helper с конкатенацией IPv6 без скобок.

Bootstrap готовит directories, но PV принадлежат Helm release `storage`
в namespace `default`, который применяет общий deploy интегратора:

```sh
helm upgrade --install storage deploy/storage --namespace default \
  --kubeconfig .state/ubuntu/admin.kubeconfig \
  --set nodeName=mts-mvp --set dataRoot=/var/lib/mts-stack
```

Chart создает `local-retain`, `mts-prometheus` и `mts-loki` по 12Gi с
WaitForFirstConsumer, Retain и node affinity. Никакого dynamic provisioner
нет: Prometheus volumeClaimTemplate и Loki PVC должны использовать
`volumeName` из контракта. Каталоги: prometheus 1000:2000/0770,
loki 10001:10001/0770, fluentd root:root/0750. Retain и Helm keep предотвращают
автоматическое удаление PV, но не являются backup. Емкость PV — запрос
Kubernetes, а не отдельная filesystem quota; данные делят диск VM.

Сеть стенда — IPv6-only: пример node `2001:db8:20::10`, Pod
`fd4d:5453:1000:100::/56`, Service `fd4d:5453:1000:200::/112`. Пример node —
документальный адрес; для установки нужен реальный доступный IPv6 из вашей
сети. Поддерживаются Pod prefix /48../64 и Service /108../120. Node /64
выделяет kube-controller-manager; Calico использует блоки /122 внутри pool.
Bootstrap до изменений требует назначенный стабильный unicast IPv6 на UP
интерфейсе, IPv6 default route и отсутствие пересечений с маршрутами. При
повторе разрешены только маршруты подтвержденного Calico внутри Pod CIDR.
Идентичность включает семейство сети, CIDR, node и DNS/NTP servers. Старый
IPv4 кластер не преобразуется на месте: нужна новая чистая VM; reset не
выполняется и старые PVC автоматически не мигрируют.

API и kubelet слушают только node IPv6; scheduler/controller-manager и
health endpoints — `::1`. Kubeadm выбирает IPv6 также для etcd и соединения
API → etcd. Containerd CRI stream server использует `::1`. Kube-proxy
обрабатывает единственный IPv6 Service CIDR и публикует NodePort только на
`node_ip/128`. SSH ограничен AddressFamily inet6; Ubuntu `ssh.socket`
получает отдельный IPv6-only listener с BindIPv6Only, существующие IPv6 SSH
сессии сохраняются при перезапуске master. Router advertisements остаются
разрешены (`accept_ra=2`) для all/default/реального uplink до включения IPv6
forwarding, чтобы SLAAC и default route не потерялись после bootstrap.

`dns_servers` — 1–3 доступных рекурсивных IPv6 DNS; kubelet использует
`/etc/mts-stack/resolv.conf`, поэтому CoreDNS не наследует DHCP IPv4 DNS.
Для ОС systemd-resolved получает те же IPv6 upstreams, пустой FallbackDNS
и route domain `~.`. Локальный stub ОС `127.0.0.53` может обслуживать
процессы загрузки; кластерный DNS обращается к IPv6 servers напрямую.
`ntp_servers` — 1–3 IPv6 NTP, по умолчанию `2606:4700:f1::123` из
[документации Cloudflare Time Services](https://developers.cloudflare.com/time-services/ntp/usage/),
FallbackNTP выключен. IPv4 адрес ОС допустим только для внешних загрузок
пакетов и образов из Internet; runtime Node/API/Pod/Service/SSH работает по
IPv6. Bootstrap не отключает DHCP IPv4 и не меняет netplan.

Доступ извне по IPv6: TCP SSH (environment.ssh_port), TCP 6443 для интегратора,
TCP NodePort 30080 для HTTP-проб. Метрики/Loki не публикуются: проверка через
loopback port-forward с явным kubeconfig. Между node/pods допускаются
Kubernetes/CNI потоки; VXLAN UDP4789 нужен при будущих нескольких узлах,
в MVP узел один. Наружу VM нужны DNS, NTP и HTTPS к Ubuntu archive/security,
pkgs.k8s.io, GitHub/raw.githubusercontent.com, get.helm.sh, registry.k8s.io,
quay.io и Docker Hub (включая registry redirect/CDN). Host firewall должен
допускать маршрутизацию pod и локальные Kubernetes порты. Playbook не меняет
router, Proxmox firewall или домашние сервисы.

Локальные проверки без VM:

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
helm template storage deploy/storage --set nodeName=mts-acceptance
```

`render.yaml` проверяет SHA256 Calico и создает только локальные файлы
в `/tmp/mts-bootstrap-render`. Эти проверки не заменяют чистый bootstrap,
повторный bootstrap, PVC binding, reboot и health на живой VM.

Принятые defaults etcd/CoreDNS/pause сверены с [исходником kubeadm v1.34.3](https://github.com/kubernetes/kubernetes/blob/v1.34.3/cmd/kubeadm/app/constants/constants.go).
Формат patches соответствует [Kubeadm API customization](https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/control-plane-flags/).

Сетевые параметры сверены с [Calico IPv6-only](https://docs.tigera.io/calico/latest/networking/ipam/ipv6),
[Calico IPv6 VXLAN](https://docs.tigera.io/calico/latest/networking/configuring/vxlan-ipip),
[официальным kubeadm networking](https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/dual-stack-support/)
и исходниками закрепленного [kubeadm 1.34.3](https://github.com/kubernetes/kubernetes/blob/v1.34.3/cmd/kubeadm/app/phases/controlplane/manifests.go).
Наличие IPv6 default route не доказывает достижимость выбранных внешних
DNS/NTP; их работоспособность, сохранение маршрута после reboot и полный
IPv6 verify должен подтвердить интегратор на чистом стенде.
