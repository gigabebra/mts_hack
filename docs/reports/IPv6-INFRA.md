# IPv6 infrastructure — ready_for_integration

Scope: `ansible/`, `config/environments/ubuntu.example.yaml`. Живые VM и
общие файлы не изменялись; bootstrap/reset/SSH автором отчета не запускались.

Реализован IPv6-only kubeadm, API/kubelet/etcd/control-plane и CRI stream,
IPv6 NodePort filter, Calico CNI IPv6 IPAM и явный IPv6 VXLAN IPPool без
BGP/default IPv4 pool. IPv6 uplink получает accept_ra=2 до forwarding.
SSH ограничивается IPv6 в sshd и Ubuntu socket activation. DNS/NTP servers
явно задаются IPv6; kubelet/CoreDNS не наследуют DHCP IPv4 DNS.

Guards до изменений: stable assigned IPv6, IPv6 default route, CIDR family,
размеры и overlap, прежняя immutable identity. Изменение семейства сети
существующего IPv4 стенда отклоняется, автоматического reset нет.

Health создает раздельные server/client Pods, проверяет AAAA двух Service,
единственную IPv6 Pod/Service семью, HTTP через Service DNS и напрямую
`http://[PodIPv6]:8080/`. Проверяемые workload удаляются даже при ошибке.

Проверки без VM — **pass**:

```sh
python -m unittest discover -s ansible/tests -v
yamllint -c .yamllint ansible config/environments/ubuntu.example.yaml
ANSIBLE_LOCAL_TEMP=/tmp/ipv6-infra-ansible ansible-playbook -i mvp, \
  ansible/bootstrap.yaml --syntax-check -e @config/environments/ubuntu.example.yaml
ANSIBLE_LOCAL_TEMP=/tmp/ipv6-infra-ansible ansible-playbook -i mvp, \
  ansible/health.yaml --syntax-check
ANSIBLE_LOCAL_TEMP=/tmp/ipv6-infra-render ANSIBLE_REMOTE_TEMP=/tmp/ipv6-infra-remote \
  ansible-playbook -i localhost, ansible/render.yaml \
  -e calico_source_file=/absolute/path/to/checksum-verified/calico.yaml \
  -e output_dir=/tmp/mts-ipv6-render
git diff --check
```

15 unit/render cases, включая отказ IPv4/mapped/link-local/overlap/no-default
route/temporary node, подтвержденные repeat routes, CNI assign_ipv4=false,
явные IPv6 слушатели и direct Pod URL. Ansible render использовал исходный
Calico 3.31.2 с проверкой принятого SHA256; результат ok=6, failed=0.
Первый локальный render уперся в sandbox запрет записи `~/.ansible/tmp`;
повтор с явным ANSIBLE_REMOTE_TEMP=/tmp завершился успешно.

Чистый live bootstrap, повтор, health после reboot, внешний IPv6 HTTP,
IPv4 SSH/NodePort rejection и полный verify — **not run**, выполняет
интегратор. Нужно подтвердить достижимость IPv6 DNS/NTP конкретной VM,
сохранение default route и отсутствие DHCP IPv4 NTP peer. IPv4 ОС остается
только для внешних package/image downloads, локальный resolver stub ОС
может быть 127.0.0.53; Pod/CoreDNS upstream и runtime кластера — IPv6.

Особенности закрепленного Calico и официальные ссылки описаны в
`ansible/README.md`: явный pool обходит IPv6 VXLAN startup conditional,
HTTP health проба обходит некорректное соединение IPv6 host:port в exec-helper.

## Уточнение после live IPv6 аудита интегратора

На baseline `46b7ca4` интегратор обнаружил IPv4 self-health CoreDNS в
netns и неявный hostname NTP fallback. Исправление в пределах infrastructure:

- CoreDNS DNS bind и health/ready/metrics используют downward API `POD_IP`.
  Corefile и digest/env Deployment patch согласуются и при повторном
  bootstrap. Стабильный checksum выполняет rollout при изменении Corefile.
- NTP fallback теперь явные IPv6 адреса. Только timesyncd получает cgroup
  BPF IPAddressDeny=any/IPAddressAllow=::/0 с reset прежних значений;
  DHCP IPv4 NTP metadata не может разрешить IPv4 пакеты сервиса.

**pass**: 17 unit/render tests, yamllint, Ansible syntax, реальный render
закрепленного Calico и нового CoreDNS patch, `git diff --check`.
CoreDNS tests проверяют IPv6 endpoints, downward env, pinned image и
стабильность/изменение rollout checksum. NTP tests проверяют явный fallback,
отказ IPv4 server и отсутствие наследуемого IPv4 allow в service filter.
**not run**: применение на VM132, отсутствие IPv4 self-health после
перезапуска CoreDNS, фактическое прикрепление BPF и активный IPv6 NTP peer —
проверяет интегратор. Агент не подключался к VM.
