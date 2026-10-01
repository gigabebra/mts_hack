# Независимая приемка dual-stack MVP

**Вердикт: pass. Принята кодовая ревизия `cc3afc6cab06a4cb441c3f0c705e79cc04910a0f`.**
Дата: 2026-10-01. Все 14 критериев [контракта v3](../contracts.md) выполнены.
[Машинная сводка](dual-stack-evidence.json) содержит результаты, точные
run IDs, относительные пути и SHA256 доказательств. Методика и исправленные
замечания описаны в [отчете приемщика](../reports/DUAL-REVIEW.md).

Полноценный dual-stack с основным IPv6 подтвержден на новой чистой Ubuntu,
а затем на основном стенде после выключения и нового запуска. Все действия
с VM выполнял интегратор. Независимый приемщик проверил код, локальные
fixtures, сырые отчеты, снимки сети и архивы, не выполняя SSH/kubectl и не
изменяя стенд. Чистая установка использовала Git bundle окончательного SHA;
публичный anonymous clone этим прогоном не проверен.

## Проверенный сетевой режим

Основные SSH-автоматизация, kubeconfig/API transport и Gateway status
используют IPv6. Kubeadm получает CIDR в порядке IPv6,IPv4, kubelet
объявляет оба адреса. Собственные Services имеют `RequireDualStack`,
`ipFamilies: [IPv6, IPv4]` и основной IPv6 clusterIP. Доступ по IPv4
подтвержден отдельными внешними SSH/API/HTTP запросами, а также внутренними
HTTP-пробами всех восьми собственных Services.

IPv4 разрешен компонентам, loopback, DNS/NTP и загрузкам. Абсолютный запрет
IPv4 sockets отменен пользователем и не является критерием этой приемки.
Порядок семей Service определяет основной IP, а не порядок выбора DNS
адресов клиентом. `RequireDualStack` не обещает переключение соединений;
у kubeadm один API advertiseAddress. Для EndpointSlice scrape явно выбран
IPv6; автоматического scrape fallback на IPv4 нет.
[Services](https://kubernetes.io/docs/concepts/services-networking/dual-stack/),
[kubeadm 1.34](https://v1-34.docs.kubernetes.io/docs/setup/production-environment/tools/kubeadm/dual-stack-support/),
[Prometheus discovery](https://prometheus.io/docs/prometheus/latest/configuration/configuration/).

Точные допустимые SingleStack IPv6 исключения:
`default/kubernetes`, `kube-system/kube-dns`,
`monitoring/prometheus-operated` (автоматический headless Service оператора).
Другие Services не получают исключение по namespace или признаку headless.
Targets уникальны по логическому Pod/port/metrics path: другая семья адреса
или другой monitor не создают право повторно собирать тот же endpoint.

## Матрица результатов

`pass` ниже относится к живым доказательствам окончательного SHA.
Fixtures проверены отдельно и не заменяют live. Подробные исходные критерии
и соответствующие фактические результаты сохранены в JSON-сводке.

| ID | Требование | Статус | Подтвержденный результат |
| --- | --- | --- | --- |
| DS-01 | Чистая установка | pass | Ubuntu 24.04.5 до установки без Kubernetes/containerd; 11 штатных стадий, окончательный SHA, clean checkout в начале и конце. |
| DS-02 | Две сети и основной IPv6 | pass | Runtime kubeadm/kubelet: порядок IPv6,IPv4; Node InternalIP и Pod CIDR обеих семей; два Calico VXLAN/NAT pools, IPAM обеих семей. |
| DS-03 | Pod адреса | pass | Все 11 Running non-hostNetwork Pod имеют native IPv6 и IPv4; фактический podIP основной IPv6. Всего в снимке 17 Pods. |
| DS-04 | Services | pass | Восемь собственных Services RequireDualStack, IPv6 первым и основной clusterIP IPv6; три точных системных исключения ниже. |
| DS-05 | EndpointSlices | pass | 19 EndpointSlices, 20 endpoints; обе семьи проверены для каждого собственного Service по Pod IP/UID, namespace, selector и targetRef. |
| DS-06 | DNS и внутренний HTTP | pass | Точные DNS A/AAAA восьми Services; четыре literal HTTP-пути Service/Pod; 16 HTTP-проб всех восьми собственных Services по обеим семьям. |
| DS-07 | SSH и API | pass | SSH -6/-4: один host key и machine ID. API обеих семей с аутентификацией и CA/SAN проверкой; основной API transport IPv6. |
| DS-08 | Gateway/HTTP | pass | Gateway conditions актуальны; обе семьи дают 200, ровно 12 bytes `Hello World!`, ожидаемый Request-ID; route/error cases проходят. |
| DS-09 | Метрики | pass | Пять свежих up IPv6 targets, повторов/неопознанных endpoints нет. Нагрузка 24+24 запроса, counter первого чистого verify 1→49 (+48). |
| DS-10 | Логи | pass | Для каждой семьи разные access Request-ID и missing-file markers найдены в Loki; проверены stdout/stderr и источник записи. |
| DS-11 | Runtime и прямые диагностики | pass | Nginx non-root, CapEff=0, root ro, token отсутствует; verify проходит с недоступным proxy; необязательная telemetry/version checking выключена. |
| DS-12 | Повтор bootstrap/deploy | pass | Повтор bootstrap сохранил пять Node/PV/PVC UID, creation time и bindings; два deploy сохранили 25 identities, PVC и Secret contents, оба verify прошли. |
| DS-13 | Recovery и reboot | pass | Два final-SHA recovery сохранили историю; пять CRI markers дочитаны до точных offsets, replay 0, buffer доставлен. После нового boot ID — API ready, health/verify/supplement pass. |
| DS-14 | Evidence и измерения | pass | Шесть итоговых verify сверены с raw; архивы побайтово равны исходникам; длительности и ресурсный снимок относятся к чистому dual-stack прогону. |

## Чистая установка и повторные операции

До начала установки подтверждены Ubuntu 24.04.5 amd64, завершенный
cloud-init, отсутствие kubeadm/kubelet/kubectl/containerd и `/etc/kubernetes`.
Проверенная конфигурация VM: 4 vCPU, 8 GiB RAM, диск 60 GiB. Checkout чистый
на одном окончательном SHA до и после цикла; установка не использовала
готовый Kubernetes или кэш инструментов управляющей машины.

| Стадия | Результат | Секунды |
| --- | --- | ---: |
| `make tools` | pass | 24.59 |
| `make lint` | pass, 62 tests плюс syntax/render/lock checks | 12.98 |
| `make bootstrap ENV=ubuntu` | pass | 208.74 |
| `make health ENV=ubuntu` | pass | 43.43 |
| `make deploy ENV=ubuntu` | pass | 98.09 |
| `make verify ENV=ubuntu` | pass | 40.07 |
| Повтор bootstrap | pass | 104.20 |
| Повтор health | pass | 43.99 |
| `make verify-idempotency ENV=ubuntu` | pass, два deploy/verify | 47.71 |
| `make verify-recovery ENV=ubuntu` | pass | 104.58 |
| `make evidence ENV=ubuntu` | pass | 0.09 |

Первый verify получен за **427.90 s (7 min 7.90 s)** от начала `make tools`;
весь цикл занял **728.58833 s (12 min 8.59 s)**, UTC
14:11:20.839233–14:23:29.427563. Создание VM, cloud-init и исходные
OS prerequisites в это время не входят. Все стадии завершились с exit 0.

Полные verify чистого стенда:
`20261001T141748Z-5c04e477`, `20261001T142057Z-24e43244-1`,
`20261001T142057Z-24e43244-2`, `recovery-012a6562931a-after`.
Каждый содержит 11 обязательных и 105 вложенных checks pass.
Повтор bootstrap сопоставлен по UID/creation timestamp/bindings пяти
Node/PV/PVC; два deploy дополнительно сравнивают 25 resource identities,
PVC bindings и содержимое Secrets. Секретные данные и их content hashes
в отчеты не выводятся.

Recovery `recovery-012a6562931a` на чистом стенде и
`recovery-fdf767e5834f` на основном прошли на окончательном SHA. Старые
samples Prometheus и записи Loki сохранились после замены Pod. Перед
рестартом Fluentd проверено достижение точной byte boundary каждого из пяти
CRI JSON request_id в persisted positions с тем же inode. После рестарта
зафиксировано ноль повторно прочитанных записей, все пять buffered markers
найдены в Loki по одному разу. Измеренные окна сценария outage/restart —
44.17 s и 48.05 s соответственно; это ограниченный сценарий, без гарантии
произвольной длительности outage или exactly-once доставки.

## Новый запуск основного стенда

После штатного выключения и запуска изменился boot ID; до health ожидалась
готовность API `/readyz`. Health прошел за 53.75 s, полный verify
`20261001T142659Z-28071d54` — за 29.73 s (11+105 checks).
Для этого verify шесть переменных HTTP/HTTPS/ALL_PROXY в обоих регистрах
указывали на недоступный IPv4 loopback port 9, NO_PROXY/no_proxy были пусты.
Успех подтверждает прямые диагностики при таком наследованном окружении.

Внешний supplement после нового запуска занял 4.24 s: SSH/API/HTTP обеих
семей, точные DNS Answers восьми Services, четыре literal HTTP пути,
пять уникальных IPv6 scrape targets и эффективные настройки выключенной
Calico/Loki telemetry и Traefik version/usage checks — pass.
Содержимое сырых DNS/targets/SSH/telemetry сверено независимо.
Отдельные 16 HTTP-проб подтвердили реальный ответ всех восьми собственных
Services через обе семьи; одной выдачи двух Service IP для этого недостаточно.

## Измерения и сохранность доказательств

Ресурсный снимок чистого стенда после полного цикла, UTC 14:24:43.755702:

| Метрика | Значение |
| --- | ---: |
| CPU | 4 vCPU; средний busy 3.59%, iowait 0.06% за 30 s |
| MemTotal / MemAvailable | 8,326,914,048 / 6,288,429,056 bytes (7.76 / 5.86 GiB) |
| Root filesystem used / free | 6,576,820,224 / 54,691,729,408 bytes (6.13 / 50.94 GiB) |

Это один снимок после проверок, не пик, benchmark или минимальный размер.
Объем filesystem отличается от номинального размера виртуального диска.

Чистый evidence archive содержит index и шесть report.json. Все члены
побайтово совпали с исходными файлами. Итоговый архив основного стенда
содержит index и 27 отчетов, включая исторические результаты: SHA в index
обозначает ревизию упаковки, а не всех вложенных прогонов. Текущую приемку
подтверждают только явно перечисленные final-SHA reports. Raw network,
component JSON, DNS/targets/telemetry, журналы стадий и измерения хешируются
отдельно; `make evidence` не включает их в tar автоматически.

Credentials, kubeconfig, реальные адреса и сырые чувствительные журналы
остаются в игнорируемых каталогах. В Git включены санитизированная сводка,
относительные пути и SHA256; они не заменяют локальное хранение исходников.

## Исправления, история и границы

Проверенные negative fixtures отклоняют IPv4 primary, отсутствующую семью
Pod/Service/конкретного EndpointSlice, mapped IPv4, неразрешенный SingleStack,
чужой backend/targetRef, повтор одного scrape endpoint через другую семью
или monitor, insecure TLS/proxy-url и недостаточный Fluentd offset/inode.

На `09d3410` recovery `recovery-b4e4e87e0b92` дал fail:
`Tail positions did not advance during Loki outage`. Истории Prometheus/Loki
перед этим прошли, Loki был восстановлен. Диагностика показала гонку чтения:
emit counter уже 170 вместо 165, а ранее прочитанный offset еще 34200;
следующая выборка увидела 35170. Простое изменение offset недостаточно:
приемщик выявил случай частичного 34394. Итоговый `cc3afc6` ожидает точные
границы всех пяти CRI markers и counter вместе; partial/wrong-inode fixtures
и оба живых recovery прошли. Исходный отказ сохранен и не выдается за
доказанную потерю логов.

[Первоначальная IPv4-приемка](mvp-acceptance.md) относится к `337d0c6`.
Strict IPv6-only попытка имеет статус **superseded**, без принятой ревизии:
в ней `calico-node` IPv4 HTTPS ошибочно проходил старое исключение checker
для public port 443. Предварительный вердикт был отозван; finding сохранен.
Пользователь затем выбрал dual-stack. Прежние прогоны не заменяют текущую
приемку; старые VM/диски сохранены, перенос их TSDB не заявлен.

Single-node/local PV не дают HA, backup или квоту каталога. HTTP публичного
приложения без TLS; NetworkPolicy, CI, canary, dashboards/alerts и FINAL не
назначены. Counter агрегирован, reset между scrape может остаться незаметным;
Request-ID связывает HTTP и log, не отдельный Prometheus sample. Packet
capture и автоматическое переключение всего стека при отказе семьи не
проверялись. Финальный PDF, его лимиты и архив сдачи не входят в этот вердикт.
