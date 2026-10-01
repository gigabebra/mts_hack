# MVP-01 — Ubuntu и kubeadm

Статус: `ready_for_integration`, не `done`; live-приемку выполняет интегратор.
База: `3874474e11f4812f8366b8f2a0464df7584f34ed`. Ветка: `mvp-01`.
Итоговый SHA — commit, содержащий этот отчет; передан интегратору отдельно.

Изменены только `ansible/`, `deploy/storage/`,
`config/environments/ubuntu.example.yaml`, `docs/reports/MVP-01.md`.

Результат: builtin-only Ansible bootstrap Ubuntu24.04 amd64 с preflight
ресурсов/IP/CIDR/identity; pinned containerd/runc/CNI/Helm/Kubernetes;
системные cgroups, отключенный swap, modules/sysctl, NTP и systemd enable;
kubeadm без reset и без повторного init; digest patches static Pods/CoreDNS,
render/pin/apply kube-proxy; checksum-verified Calico VXLAN без BGP;
control-plane workload scheduling; DNS/HTTP health Job; приватный kubeconfig;
local directories с UID/GID. Storage Helm chart создает два static PV
12Gi с node affinity, Retain и no-provisioner StorageClass.

Интерфейс согласован с интегратором: `ansible-playbook -i <inventory>
ansible/bootstrap.yaml -e @<environment.local.yaml>`; group `mvp` содержит
ровно один explicit host. `ansible/health.yaml` — проверка после reboot.
Storage применяет общий deploy, release `storage`/`default`, overrides
`nodeName`, `dataRoot`. Ни общие файлы, ни VM, ни Server-node агент не менял.

## Проверки

`pass`: Ansible 2.19.3 syntax для bootstrap и health; настоящий offline
render шаблонов и закрепленного upstream Calico; Helm3.19.0 storage lint
и render с другим node name; семантический разбор Calico images/VXLAN/probes,
containerd TOML/systemd cgroups, health objects и PV affinity/Retain.
`pass`: 7 unittest negative/positive guards: неизвестный image, испорченный
digest, вложенные init/regular images, пересечение node/pod/service/route,
разрешение только известных Calico routes у existing cluster.
`pass`: Helm отвергает `nodeName=BAD_NODE` с exit1 — ожидаемый negative case.

Команды, выполненные из worktree (tool paths интегратора):

```sh
export ANSIBLE_LOCAL_TEMP=/tmp/mvp01-ansible
/home/Bebroid/mts_hack/.venv/bin/ansible-playbook -i mvp, \
  ansible/bootstrap.yaml --syntax-check -e @config/environments/ubuntu.example.yaml
/home/Bebroid/mts_hack/.venv/bin/ansible-playbook -i mvp, \
  ansible/health.yaml --syntax-check -e @config/environments/ubuntu.example.yaml
/home/Bebroid/mts_hack/.venv/bin/ansible-playbook -i localhost, \
  ansible/render.yaml -e calico_source_file=/home/Bebroid/mts_hack/.cache/calico.yaml
/home/Bebroid/mts_hack/.venv/bin/python -m unittest discover -s ansible/tests -v
/home/Bebroid/mts_hack/.cache/tools/helm lint deploy/storage
/home/Bebroid/mts_hack/.cache/tools/helm template storage deploy/storage \
  --namespace default --set nodeName=mts-acceptance --set dataRoot=/var/lib/mts-stack
# Expected exit 1:
/home/Bebroid/mts_hack/.cache/tools/helm template storage deploy/storage --set nodeName=BAD_NODE
```

`fail`: нет незакрытых ошибок локальных проверок. Первоначальный системный
Ansible без writable local tmp не запускался; проверки выполнены закрепленным
Ansible2.19.3 с `ANSIBLE_LOCAL_TEMP=/tmp/mvp01-ansible`.

`not run`: live bootstrap чистой VM, live повтор, реальная работоспособность
CRI/DNS/CNI, PVC Bound, controlled reboot/recovery. Причина: по правилам
общим стендом управляет только интегратор. Ни syntax, ни offline render,
ни fixtures не объявляются runtime-приемкой.

## Проверить при интеграции

1. Дождаться cloud-init, выполнить bootstrap, проверить Node Ready и результат
   `MTS_DNS_AND_POD_NETWORK_OK`; сохранить не credentials, а sanitized evidence.
2. Применить storage и остальных владельцев PVC, проверить обе PVC Bound.
3. Повторить bootstrap и deployment; сравнить UID node/kube-system/PV/PVC,
   убедиться в сохранении данных и пропуске kubeadm init.
4. Сделать controlled reboot выделенной VM, дождаться SSH и запустить
   `ansible/health.yaml`, общий verify после восстановления workload.

Версии kubeadm etcd3.6.5-0/CoreDNSv1.12.1/pause3.10.1 подтверждены
[upstream v1.34.3 constants](https://github.com/kubernetes/kubernetes/blob/v1.34.3/cmd/kubeadm/app/constants/constants.go).
Сам bootstrap дополнительно проверяет реальные `kubeadm config images list`
против lock. Изменений общего контракта не требуется.

Ограничения: единственный node, локальный диск без HA/backup/filesystem quotas.
PV Retain требует ручной процедуры reclaim после удаления PVC. Bootstrap
не выполняет version migration/cluster repair/reset. Внешний firewall и
сетевой доступ — предпосылки среды; порты и endpoints описаны в
`ansible/README.md`.
