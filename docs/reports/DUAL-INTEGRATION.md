# Dual-stack — интеграция MVP

Кодовая ревизия: `cc3afc6cab06a4cb441c3f0c705e79cc04910a0f`.
Статус: **accepted** после независимой сверки чистой установки и финального возврата основного стенда.

Пользователь выбрал полноценный dual-stack Kubernetes с IPv6 основным и
доступным IPv4 для приложения и компонентов. Контракт v3, environment,
kubeadm/Calico, Services, слушающие sockets, HTTP/metrics/logs проверки и
документация приведены к этому режиму. Основной kubeconfig и Gateway status
используют IPv6. Наши Services — RequireDualStack [IPv6,IPv4]; точные
системные исключения перечислены в сетевой инструкции. Сбор метрик выбирает
IPv6 и отдельно исключает повторные endpoint независимо от Service/job/IP.

Интегратор объединял ветки и один выполнял операции VM. Изолированные агенты
подготовили infrastructure, components и независимое ревью; одновременно
работали не более трех исполнителей. Все прежние VM/диски сохранены.
Изменения домашнего router и соседних репозиториев не требовались.

## Проверки и исправления

Общий `make lint`: Python/Ansible syntax, YAML, Helm lint/render, image lock,
22 инфраструктурных tests, компонентные и общие negative fixtures — pass.
Nginx, Fluentd и Loki прошли реальные Docker smoke по обеим семьям;
подробности в [DUAL-COMPONENTS](DUAL-COMPONENTS.md).

Независимое ревью выявило два false-pass: EndpointSlice мог указывать на Pod
чужого namespace/selector, а optional scrape дубликаты не выявлялись.
Проверки привязаны к namespace/selector/targetRef и всем идентифицируемым
Pod/port/path. Raw network snapshot сохраняется отдельно. Kubeconfig с
proxy-url или отключенной TLS verification отклоняется.

Первый dual-stack прогон `09d3410` прошел bootstrap, HTTP/метрики/логи,
повторы, сохранение истории, но recovery остановился на гонке наблюдения
Fluentd: counter уже вырос, первое чтение position file было старым.
Диагностика воспроизвела последовательность; исходный fail сохранен.
Итоговая проверка находит byte boundaries всех пяти exact CRI Request-ID,
ждет persisted offset за каждой границей и прирост counter перед рестартом
collector. Regression отклоняет старые/частичные offsets и другой inode.

Усиленный recovery основного стенда на `cc3afc6` — pass: Prometheus/Loki
сохранили историю, все пять buffer markers доставлены, replay после
collector restart равен нулю. Последующий verify — 11/11 pass. Внешние
SSH/API/HTTP обеих семей и CA/SAN verification — pass; DNS A/AAAA всех
восьми собственных Services совпадают с ClusterIPs; четыре literal
Pod/Service HTTP пути — pass. Пять IPv6 scrape endpoints уникальны.
Фактические Calico/Loki telemetry и Traefik version/usage checks отключены.

## Приемка

На новой чистой Ubuntu 24.04.5 весь цикл из 11 стадий прошел на `cc3afc6`:
tools, lint, bootstrap, health, deploy, verify, bootstrap-repeat, health-repeat,
idempotency (два deploy), recovery и evidence. Все четыре вложенных verify
имеют 11 обязательных и 105 компонентных checks pass, checkout чистый.
Первый verify завершился через 7 минут 8 секунд, весь цикл — 12 минут 9 секунд.
Подтверждены identities/bindings пяти Node/PV/PVC и 25 ресурсов повторного deploy.

После штатного выключения и включения основного стенда boot ID изменился;
после API ready прошли health и полный verify с шестью неверными proxy-переменными,
пустыми NO_PROXY/no_proxy. Повторный внешний supplement обеих семей — pass.
Все восемь собственных Services отвечают по IPv6 и IPv4: 16 внутренних HTTP
probes pass. UID, creation time и bindings пяти Node/PV/PVC сохранены после boot.

Независимый агент подтвердил [протокол](../acceptance/dual-stack-acceptance.md)
и [JSON evidence](../acceptance/dual-stack-evidence.json). Чистый архив содержит
шесть отчетов этого прогона. Общий архив основного стенда также содержит историю;
его отчеты различаются по собственным revision/run-id, а не объявляются все
принадлежащими принятому SHA. Первоначальные отказы сохранены.

После чистой проверки доступно 5,86 GiB из 7,76 GiB RAM, диск занят на
6,13 GiB, свободно 50,94 GiB; CPU busy за 30 секунд — 3,59%. Это снимок
после проверки, не измерение пика или минимально необходимых ресурсов.

FINAL не назначен. Автоматическое переключение всех клиентов между семьями,
HA, резервное копирование и запрет любого IPv4 трафика не заявляются.
