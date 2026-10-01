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
dedicated ansible_host=192.0.2.10 ansible_user=ubuntu ansible_port=22 ansible_python_interpreter=/usr/bin/python3
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
Они проверяют DNS Kubernetes и HTTP между двумя pod через ClusterIP.

Containerd, runc, CNI plugins, Helm и Calico скачиваются с SHA256 из
`versions.yaml`; Kubernetes deb packages имеют точную версию и apt hold.
Все kubeadm images проверяются по фактическому `kubeadm config images list`,
pull выполняется по digest. Локальные tag aliases нужны kubeadm preflight;
`imagePullPolicy: Never` запрещает ему заново разрешать tag. Static Pods и
CoreDNS получают patches с digest до запуска; kube-proxy генерируется
`kubeadm init phase addon kube-proxy --print-manifest`, затем pin/apply.
Calico YAML структурно переводится в VXLAN без BGP и все images закрепляются.

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

Доступ извне: TCP SSH (environment.ssh_port), TCP 6443 для интегратора,
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
