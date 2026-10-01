# Состояние MVP

| ID | Статус | Владелец | Результат |
| --- | --- | --- | --- |
| MVP-00 | done | интегратор | Контракт, 20 image digests, checksum downloads, доступ к отдельным VM определен |
| MVP-01 | done | инфраструктура | Live integration pass; см. docs/reports/MVP-01.md |
| MVP-02 | done | Gateway | Live integration pass; см. docs/reports/MVP-02.md |
| MVP-03 | done | метрики | Live integration pass; см. docs/reports/MVP-03.md |
| MVP-04 | done | логирование | Live integration pass; см. docs/reports/MVP-04.md |
| MVP-05 | done | интегратор | Live integration pass; см. docs/reports/MVP-05.md |
| MVP-06 | done | независимая приемка | Историческая IPv4-приемка `337d0c6`; текущая IPv6-приемка приведена ниже |
| IPv6-INFRA | done | инфраструктура + интегратор | SSH/API/Calico/Pod/Service/DNS/NTP по IPv6; CoreDNS self-check и NTP filter; docs/reports/IPv6-INFRA.md |
| IPv6-COMPONENTS | done | компоненты + интегратор | HTTP, scrape targets, Loki/Fluentd и port-forward IPv6; proxy guards; docs/reports/IPv6-components.md |
| IPv6-INTEGRATION | done | интегратор | Чистый bootstrap, повтор, idempotency, recovery, reboot и обязательные network/transport checks pass |
| IPv6-REVIEW | done | независимая приемка | Accepted `232964f`; docs/acceptance/ipv6-acceptance.md и очищенный ipv6-evidence.json |

FINAL не назначен. Live-проверки и изменения общей VM выполняет интегратор.

Принятая кодовая ревизия IPv6: `232964fc1cc6642f563225e4768f5964cde94e5f`.
Чистая Ubuntu 24.04.5 установлена на `fd67893`: все 10 стадий, три полных
verify и сохранение Node/PV/PVC при повторном bootstrap подтверждены.
Последующая поправка `232964f` касается только клиентских проверок proxy;
дерево установки не менялось. Lint и полный verify с нерабочим IPv4 proxy
повторены на обоих стендах. Эти ревизии и прогоны разделены в
[независимом протоколе](acceptance/ipv6-acceptance.md).

Основной выделенный IPv6-стенд работает. После возврата выполнены health,
полный verify `20261001T131132Z-7554b9be` (10 checks pass), внешний HTTP6,
отказ IPv4 на SSH/API/NodePort, DNS AAAA без A и проверка IPv6 NTP/BPF.
VM чистой приемки штатно остановлена, evidence сохранен. Прежние IPv4 VM
остаются выключенными с сохраненными дисками; их история Prometheus/Loki
не переносилась в новый кластер. IPv4 ОС разрешен только для внешних загрузок.

[Прежняя IPv4-приемка](acceptance/mvp-acceptance.md) относится к
`337d0c671617929ebf162e1216099524fe8d611d` и сохранена без переписывания.
Изменения после принятой кодовой ревизии IPv6 относятся только к документации.
