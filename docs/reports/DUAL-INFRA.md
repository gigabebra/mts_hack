# DUAL-INFRA — ready_for_integration

> Ниже сохранен отчет компонентного агента на момент передачи реализации.
> Последующая live-интеграция завершена; текущий статус — в
> [реестре](../status.md) и [независимом протоколе](../acceptance/dual-stack-acceptance.md).

Основание: `docs/contracts.md` v3, пользователь выбрал полноценный dual-stack
с основным IPv6. Изменены только `ansible/`, пример environment и этот отчет.
Агент не подключался к VM и не изменял общий Kubernetes.

## Реализация

- Environment: обязательные `node_ipv4`, `pod_cidr_ipv4`,
  `service_cidr_ipv4` дополняют IPv6 поля; примеры IPv4 Pod 10.244/16,
  Service 10.96/16. DNS/NTP принимают обе семьи.
- Kubeadm Pod/Service CIDR и kubelet node-ip — IPv6,IPv4. API advertise и
  kubeconfig остаются IPv6; API SAN включает оба IP, API/kubelet принимают
  обе семьи. NodePortAddresses ограничен обоими адресами VM.
- Два явных Calico VXLAN/NAT pool, IPAM обеих семей, BGP/default pools off.
  Необязательные usage/version checks остаются выключены.
- Forwarding обеих семей; accept_ra=2 сохраняет IPv6 SLAAC. SSH принимает
  IPv6/IPv4. Удалены специальные ограничения NTP/BPF; CoreDNS, Felix health,
  containerd streaming и control-plane loopback используют штатное поведение.
- Identity `dual-stack-ipv6-primary` проверяется до любых изменений. Старый
  single-stack кластер отклоняется; reset/upgrade/миграция дисков не выполняются.
- Health требует оба PodIP у server и client в primary порядке, собственный
  RequireDualStack Service IPv6,IPv4, AAAA/A, HTTP по обоим Service IP и
  напрямую по обоим server Pod IP. Системные SingleStack IPv6 Services
  допускаются и не патчатся для искусственного dual-stack.

## Проверки

**pass**, без VM:

```sh
python -m unittest discover -s ansible/tests -v
yamllint -c .yamllint ansible config/environments/ubuntu.example.yaml
ANSIBLE_LOCAL_TEMP=/tmp/dual-infra-ansible ansible-playbook -i mvp, \
  ansible/bootstrap.yaml --syntax-check -e @config/environments/ubuntu.example.yaml
ANSIBLE_LOCAL_TEMP=/tmp/dual-infra-ansible ansible-playbook -i mvp, \
  ansible/health.yaml --syntax-check
ANSIBLE_LOCAL_TEMP=/tmp/dual-infra-render ANSIBLE_REMOTE_TEMP=/tmp/dual-infra-remote \
  ansible-playbook -i localhost, ansible/render.yaml \
  -e calico_source_file=/absolute/path/to/checksum-verified/calico.yaml \
  -e output_dir=/tmp/mts-dual-render
git diff --check
```

22 unit/render cases: IPv4/IPv6 overlaps/default routes/node assignment,
ошибочная семья/размер сетей, repeat Calico routes только после identity,
оба Pod/Service IP в правильном порядке, primary kubeadm CIDR/SAN,
оба Calico pools и IPAM, digest pinning, отключение telemetry, штатный
CoreDNS, SSH/NTP без прежнего запрета IPv4. Calico render использует
исходный 3.31.2 с принятым SHA256; Ansible render: ok=6, failed=0.

**not run**: clean live bootstrap, repeat, reboot, SSH/API/HTTP по обоим
адресам и полноценная независимая приемка — выполняет интегратор. Ни
fixtures, ни render не доказывают работоспособность dual-stack dataplane.

## Источники и ограничения

Порядок primary PodIP проверен по
[kubelet v1.34.3 sortPodIPs](https://github.com/kubernetes/kubernetes/blob/v1.34.3/pkg/kubelet/kubelet_pods.go#L2015).
Кластерные параметры соответствуют
[kubeadm dual-stack](https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/dual-stack-support/)
и [Calico dual-stack IPAM](https://docs.tigera.io/calico/latest/networking/ipam/ipv6).
Дополнительные ссылки на pinned Calico и команды описаны в `ansible/README.md`.

Две семьи не означают автоматическое переключение control plane, отказоустойчивый
кластер или перенос старой истории. Loopback IPv4 и системные DNS/NTP разрешены
контрактом; проверка абсолютного отсутствия IPv4 sockets больше неприменима.
