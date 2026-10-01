# DUAL-REVIEW — независимая приемка

**Статус: done; вердикт pass.** Принята кодовая ревизия
`cc3afc6cab06a4cb441c3f0c705e79cc04910a0f`, 2026-10-01, контракт v3.
Все 14 критериев [протокола](../acceptance/dual-stack-acceptance.md)
выполнены. [JSON-сводка](../acceptance/dual-stack-evidence.json) содержит
санитизированные результаты, run IDs и SHA256 исходных доказательств.
[Паспорт](../passport/mvp-draft.md) обновлен по фактическим измерениям.
FINAL, PDF и архив сдачи не назначены.

## Независимость и область работы

Приемщик изменил только четыре файла: протокол, JSON-сводку, этот отчет
и черновик паспорта. Код проверялся read-only в checkout интегратора;
локальные тесты не обращались к VM. Все SSH/kubectl, развертывания, остановки
и запуски стендов выполнял один интегратор. Credentials/kubeconfig приемщик
не читал. Исходные чувствительные данные остались в ignored каталогах;
публичные документы не содержат реальные адреса или идентификаторы VM.

Документы создавались в ветке `dual-review` от контрактной базы `f2eabb7`.
Итоговая ревизия приложения выше подтверждена отдельными живыми отчетами;
doc-only коммит не выдается за повторно развернутый код. Старые strict IPv6
черновики сохранены отдельно, без merge и без accepted.

## Результат независимой сверки

| Проверка | Результат и доказательство |
| --- | --- |
| Контракт и реализация | pass: native dual-stack, основной IPv6, точные SingleStack исключения, отсутствие обещания автоматического fallback |
| Чистый окончательный SHA | pass: Ubuntu 24.04.5 без Kubernetes/containerd, 11 стадий exit 0, clean checkout до/после |
| Полные verify | pass: четыре на чистом стенде, один после recovery основного, один после его нового запуска; каждый 11 обязательных + 105 вложенных checks |
| Граф сети | pass: raw kubeadm/Calico/kubelet/CNI и network-snapshot; 17 Pods, 11 Services, 19 EndpointSlices, 20 endpoints; все 11 обычных Running Pod имеют обе семьи |
| Реальный трафик | pass: SSH/API/HTTP обеих семей, TLS CA/SAN, точные DNS A/AAAA восьми Services; четыре literal Pod/Service пути и 16 проб всех собственных Services |
| Метрики и логи | pass: пять уникальных свежих IPv6 targets, counter минимум +48 от двух нагрузок; точные разные access/error markers обеих семей в Loki |
| Repeat/idempotency | pass: пять Node/PV/PVC UID/creation/bindings совпали; два deploy сохранили 25 identities, PVC и Secret contents, каждый с полным verify |
| Recovery | pass на двух стендах: история Prometheus/Loki, точные boundaries пяти CRI markers, replay 0, доставленный buffer |
| Новый запуск основного стенда | pass: boot ID изменился, API ready до health; полный verify с шестью недоступными proxy и пустым NO_PROXY; внешние probes обеих семей |
| Архивы и измерения | pass: все члены архивов побайтово сопоставлены с исходниками; новый ресурсный снимок и длительности, без заимствования прежних IPv4 цифр |

Сырые network snapshots повторно проверены относительно runtime config;
ответы component JSON сопоставлены с вложенными checks отчетов. DNS проверен
как точное множество Answer адресов, а не совпадение с IP сервера DNS.
Полные activeTargets проверены на свежесть, IPv6 и уникальность логического
endpoint. SSH двух семей сверены по host key и machine ID. Для telemetry
сверены runtime Calico env, Traefik argv и фактический Loki `/config`.

Итоговый verify после нового запуска — `20261001T142659Z-28071d54`;
чистый первый verify — `20261001T141748Z-5c04e477`.
Recovery — `recovery-012a6562931a` на чистом и
`recovery-fdf767e5834f` на основном стенде. Полный чистый цикл занял
728.58833 s; первый verify — 427.90 s от начала `make tools`.

Чистый архив содержит index и шесть отчетов. Архив основного стенда содержит
27 отчетов разных исторических ревизий, включая прежние failures. SHA его
index означает только ревизию упаковки. Каждый из шести verify, принятых
как итоговое доказательство, отдельно имеет `cc3afc6` и clean checkout.
Raw snapshots, component JSON и дополнительные журналы не включаются
`make evidence` в tar; их пути и хеши записаны отдельно в JSON-сводке.

## Исправленные замечания

1. EndpointSlice мог ссылаться на известный Pod чужого namespace/selector.
   Добавлены проверки namespace, selector, kind/name/namespace targetRef
   и UID/IP backend. Raw graph сохраняется для независимого пересчета.
2. Уникальность scrape первоначально проверялась только для REQUIRED
   targets. Исправлено сравнение всех идентифицируемых endpoints независимо
   от семейства адреса и monitor/job; исправлен discovery metadata fallback
   на `__meta_kubernetes_endpointslice_address_target_name`.
3. Kubeconfig с `insecure-skip-tls-verify` или `proxy-url` теперь отклоняется.
   Live API обеих семей проверялся с обычной CA/SAN верификацией.
4. Health с substring DNS-проверкой не доказывал точный ответ; supplement
   дополнен exact Answer set. Добавлено чтение эффективного Loki analytics
   наряду с Calico/Traefik и отдельные HTTP-пробы всех собственных Services.
5. Recovery `09d3410` мог завершить ожидание по новому emit counter при
   ранее прочитанном stale position file. Первый fail сохранен. Промежуточное
   условие «offset изменился» тоже пропускало частичный offset. В `cc3afc6`
   ожидаются все точные byte boundaries пяти CRI JSON request_id, тот же
   канонический inode и counter+5; negative fixtures проверяют stale/partial
   offset и неправильный inode. Оба окончательных живых recovery прошли.

Все замечания закрыты без ослабления обязательных проверок. Требование
полного запрета IPv4 sockets отменено пользователем при переходе к текущему
контракту; его удаление не является обходом dual-stack приемки.

## Локальная проверка и проверка интегратора

Приемщик самостоятельно выполнил следующие команды в корне checkout
интегратора. Metrics fixture servers использовали локальные sockets.

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s deploy/monitoring/tests -p 'test_check_metrics.py' -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/unit -p 'test_network.py' -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/unit -p 'test_recovery.py' -v
```

Результаты: 8 metrics и 7 network tests на `09d3410`, 1 regression recovery
на `cc3afc6`, все pass. Metrics/network файлы между этими SHA не менялись.
Полный `make lint` на чистой Ubuntu окончательного SHA выполнил интегратор:
62 tests (группы 2+7+8+22+15+8), Python/Ansible syntax, Helm lint/render,
image lock и component negative checks — pass. Приемщик независимо
просмотрел сохраненный журнал; это не повторное выполнение всего lint им.

Отдельный локальный audit скрипт приемщика повторно разобрал reports,
network snapshots, markers, DNS, targets, identities и tar; все проверки
прошли. JSON parse, соответствие DS-01–DS-14, относительные Markdown links,
актуальность SHA256 источников, отсутствие чувствительных значений в четырех
документах и `git diff --check` проверены перед коммитом. Хеши трех локальных
test logs и исходных live-доказательств сохранены в JSON-сводке.

## История и практические границы

Первоначальная IPv4-приемка `337d0c6` остается исторической.
Strict IPv6-only попытка — **superseded**, accepted отсутствует. Независимо
найденный `calico-node` IPv4 HTTPS ошибочно проходил старое исключение
checker по public port 443; предварительный вердикт был отозван. Смена
пользователем контракта не переписывает тот finding.

Первый dual-stack recovery `recovery-b4e4e87e0b92` на `09d3410` сохранил
status fail; Prometheus/Loki history checks перед отказом были pass.
Гонка наблюдения подтверждена отдельной диагностикой и исправлена в checker.
Она не дает основания заявлять доказанную потерю логов. Все окончательные
чистые стадии `cc3afc6` прошли с первой попытки.

Приемка ограничена single-node, HTTP, local PV и проверенным коротким outage.
Нет HA/backup, гарантии exactly-once, произвольного outage или автоматического
переключения всей системы между семьями. IPv4 runtime разрешен, packet
capture не проводился. Снимок CPU/RAM/disk не является пиком или минимальным
размером VM. Установка из Git bundle не проверяет anonymous public clone.
