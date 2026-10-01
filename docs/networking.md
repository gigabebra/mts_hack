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
