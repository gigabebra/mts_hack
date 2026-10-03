# Независимая приемка чистой Ubuntu — 3 октября 2026 года

**Результат: pass для чистой установки, MVP и назначенной области FINAL-01/05.**
Принятая ревизия исполняемого кода:
`c268c5fb47759db76a661a014ffd56a8974c8f51`.
[Машинный протокол](clean-ubuntu-2026-10-03.json) содержит результаты,
границы приемки и SHA-256 исходных evidence.

Независимый проверяющий изучил исходники, выполнил локальные отрицательные
fixtures и сверил подробные отчеты интегратора. Все операции на VM выполнял
интегратор; проверяющий не обращался к VM и не изменял ее. Достоверность
результата проверялась по дочерним отчетам, сохраненным samples, журналам
реальных процессов и соответствию проверенного кода Git, а не только
по итоговым полям `pass`.

## Исходное состояние и происхождение кода

Для проверки создана отдельная Ubuntu 24.04.5 LTS amd64: 4 vCPU, 8192 MiB RAM,
60 GiB диска. До переноса проекта отсутствовали `/etc/kubernetes`,
`/etc/mts-stack`, `/var/lib/kubelet`, `/var/lib/containerd` и checkout проекта.
Не было kubeadm, kubelet, kubectl, containerd, Helm, Ansible, make и Python venv.
Это зафиксировано в `pristine.json`; подготовка доступа ограничивалась
облачной VM с SSH, передачей исходников и явным локальным environment.

Все **143 отслеживаемых файла** исходного `source.tar.gz` независимо сравнены
с Git-коммитом и `tested-source.json`: совпадение побайтовое. В архиве нет
`.venv`, `.cache`, `.state`, artifacts, локальных environment-файлов или иных
посторонних файлов вне `.git`. Вложенный Git HEAD указывает на принятую ревизию.
SHA-256 архива исходников:
`4ff970a3c1b8fe9dadb1f41686b46c17bef31a691f6101a3cbeed05957cfe0c0`.

Первая команда установки — ровно `bash scripts/deploy.sh --env ubuntu`.
Предварительного запуска `make tools`, `make bootstrap` или ручных команд
Kubernetes не было. Полный драйвер завершился с exit 0; его итоговый
`working_tree_dirty: false`. Девять MVP и шесть release-отчетов также содержат
принятую ревизию и `working_tree_dirty: false`.

## Чистая установка и основной стек

| Реальная команда | Результат | Время |
| --- | --- | ---: |
| `bash scripts/deploy.sh --env ubuntu` | pass: подготовка инструментов, bootstrap и все компоненты | 312,30 с |
| `make lint` | pass: 180 tests, syntax, Helm lint/render, image lock | 15,67 с |
| `make health ENV=ubuntu` | pass: 23 задачи, DNS и сеть обеих семей | 46,35 с |
| `make verify-release ENV=ubuntu` | pass: MVP и Gateway/TLS | 26,69 с |
| `make verify-idempotency ENV=ubuntu` | pass: два полных bootstrap/deploy/verify | 267,83 с |
| `make demo ENV=ubuntu` | pass: один полный последовательный запуск | 918,13 с |
| `make evidence ENV=ubuntu` | pass: архив отчетов | 0,20 с |

Основной драйвер занял 1587,41 с; последующие reboot и внешняя проверка
в это время не включены. Bootstrap завершил 110 задач без failed/unreachable.
Журнал подтверждает установку runtime, kubeadm/Kubernetes и Calico, затем
восьми Helm releases: storage, monitoring, traefik, monitoring-extras, logging,
app, app-canary и gateway. Нет скрытого предварительно работающего кластера.

Независимо пересчитаны все девять групп unittest в `lint.log`: 180 tests,
девять `OK`. На машине проверяющего дополнительно выполнены 49 unit и
51 canary/cleanup fixture, `bash -n` двух новых shell-скриптов и шесть
CLI-проверок аргументов `deploy.sh`. Ошибочное имя, отсутствующий environment
и неполные/неизвестные аргументы отклоняются до установки и обращения к VM.

В девяти MVP-прогонах прошли **11 основных и 105 вложенных checks** каждый:
readiness, dual-stack, Gateway conditions, HTTP по IPv6/IPv4, Prometheus,
Fluentd/Loki access/error и runtime security. Сверены Request-ID и error marker
между HTTP и Loki; повторной генерации probes сборщиком логов нет.
Приросты HTTP counter пересчитаны из before/after, пороги выполнены.

Idempotency `20261003T161950Z-46a8bb03` действительно повторил bootstrap и
полную установку дважды, затем выполнил два успешных MVP verify. Сравнены
28 identities, bindings двух Bound PVC и содержимое пяти прикладных Secrets.
Хеши содержимого используются проверенным кодом локально и исключены из
публичного отчета; сохранение Secret UID не подменяет это сравнение.

## Gateway, TLS и canary

Все шесть Ubuntu Gateway-прогонов содержат **154/154 pass**. Проверены точные
host/path routes, текущие conditions, оба backend и HTTPS с проверкой CA/имени.
В каждом прогоне неверные hostname и CA отклонены с `curl_exit: 60` по обеим
семьям. `release-e86942e5af53` при 90/10 выполнил по 1000 запросов без retries:
canary получил 99 по IPv6 и 100 по IPv4, ошибок нет. После возврата 100/0
по 100 запросов каждой семьи обслужены только stable.

Набор `acceptance-20261003T162435Z-4dca503b` принят по шести дочерним отчетам:

| Сценарий | Фактический результат |
| --- | --- |
| healthy | accepted; 2400 HTTP за 60 с: 2160 stable, 240 canary; backend delta 241, 5xx 0% |
| http500 после 90/10 | rejected; ошибочные samples относятся к запрошенному canary; backend 241/242 ошибок, 99,5868% |
| wrong-body после 90/10 | rejected по HTTP 200 с неверным телом; backend 5xx 0% |
| wrong-body до split | rejected; пять неверных ответов нужного canary, событие включения 90/10 отсутствует |
| metrics-unavailable | inconclusive после фактического закрытия port-forward; запрос к Prometheus дал URLError |
| insufficient-sample | inconclusive; 20 HTTP, два canary, backend delta 2 при неизменном минимуме 100 |

По сырым samples независимо пересчитаны backend deltas и error rate,
проверены baseline anchors, монотонность, неизменность process identity и
`up=1`. Последние samples получены после HTTP-окна; возраст при снимке
0,239–0,958 с при пределе 45 с. Метрики включают convergence-пробы,
поэтому backend delta не обязан совпадать с числом HTTP в измеренном окне.
Все 29 сохраненных fault samples проверены по роли, revision, Request-ID,
статусу и hash тела.

Во всех пяти отрицательных сценариях подтверждены 100/0, **25/25 точных
ответов stable** и **5/5 ответов восстановленного здорового canary**.
Сам rollback занимал 13,408–17,123 с; это не время от первой ошибки до решения.
Cleanup набора — pass, после него `verify-release` — pass.
Ожидаемые отказы сохраняют `status: fail` в дочерних release-отчетах.
Поля exit code шести cases выведены из in-process `run_locked`; реальными
OS exit codes они не называются.

Прежнее замечание о пропущенной readiness stable закрыто. Новый gate проверяет
Helm `deployed`, текущее поколение, полный rollout, image и неизменные values
перед кандидатом, перед 90/10 и перед принятием. В здоровом live-сценарии
сохранены все три подтверждения двух ready replicas. Отказы pending/failed
Helm, устаревшего поколения, неполных replicas и изменения release проверены
локальными regression fixtures; намеренная деградация stable на VM — **not run**.

## Прерывания и восстановление

`interrupt-bcf567ff32d2` подтвердил настоящие `pending-upgrade` и непригодный
для Nginx закрепленный образ. В текущем запуске реальный SIGTERM привел к
exit 2, `interrupted` и успешному rollback за 7,682 с. В следующем запуске
writer завершился с 143 и оставил pending-upgrade; штатный restore восстановил
его за 7,822 с. Дочерние HTTP samples, 100/0, сохранение TLS-сертификата и
cleanup проверены. Таким образом, ненулевой CLI exit после восстановления
подтвержден отдельным реальным subprocess-сценарием.

`demo-c714ab74cc81` целиком завершился успешно. Замена stable pod дала
76 HTTP-запросов без ошибок. Config rollout и Helm rollback подтверждены
исчезновением старых UID, полной готовностью текущего поколения и HTTP revision;
convergence заняла 5,00 и 6,08 с, затем выполнены отдельные строгие probes.

`recovery-6fe16f3c1adc` подтвердил сохранение истории Prometheus и Loki при
пересоздании pod. При коротком отключении Loki и рестарте Fluentd доставлены
все пять заранее известных buffer markers, каждый найден один раз;
проверенный интервал — 51,48 с. В raw comparison независимо сверены все три
активных path/inode/размера/offset и границы marker-файлов. Удалены только
20 неактивных entries с unwatched offset; replay равен нулю.
Последующий MVP verify — pass.

Reboot выполнен отдельно внешним controller после завершения полного demo.
По raw Node snapshots подтверждена смена bootID, API готов через 34,85 с.
Реальные subprocess health и verify-release завершились с exit 0;
итоговый release — `release-84ea46799502`. Независимое сравнение пяти ресурсов
подтвердило сохранение Node/PV/PVC UID и bindings двух Bound PVC.
Внешний `fresh-external-20261003` также дал 154/154 checks и по 100 stable
ответов обеих семей. Последнее проверенное рабочее состояние: 100/0,
оба backend здоровы.

После приемки штатное выключение временной VM сначала превысило ожидание
90 с и вернуло exit 255, `VM quit/powerdown failed`. Этот исход сохранен.
Последующая отдельная проверка подтвердила, что временная VM остановилась,
а основная VM продолжает работать. По сообщению интегратора принудительное
отключение не выполнялось, диски сохранены. Задержка выключения отделена
от успешных результатов reboot и приемки.

## Evidence и границы результата

Сверены SHA-256 всех **38 report/gateway JSON** из машинного протокола,
дополнительных исходников приемки и двух архивов. В первом архиве 35 отчетов,
во втором 38; все вложенные файлы побайтово совпадают с собранными raw.
Помимо отчетов каждый архив содержит `index.json`.

| Архив | SHA-256 |
| --- | --- |
| `artifacts/evidence/20261003T163936Z.tar.gz` | `8526a037dd61c1ca07ce4bd4e6f842462e1839c7ccf1fee636774f68c2cc5c1f` |
| `artifacts/evidence/20261003T164309Z.tar.gz` | `dc063b8b73eb827f196f0e1dd36bc432134600ab7d32e8f31ee889e0a15ca5f8` |

Исходная локальная коллекция — `.state/fresh136/collected/`; отдельные reboot,
external и power-state evidence находятся в `.state/fresh136/`.
В этот публичный протокол не включены credentials, kubeconfig, реальные адреса
или сырые логи. Отрицательные отчеты не удалялись и не менялись на успешные.

Техническая приемка не подтверждает неназначенные FINAL-02/03/04/06:
Grafana/alerts, NetworkPolicy, hosted CI и дополнительные опции. Полный
`PROFILE=final` остается неподдерживаемым. Публикация репозитория с проверкой
анонимного clone, окончательный паспорт, архив сдачи и его отправка относятся
к отдельным условиям готовности поставки и этим протоколом не принимаются.

Проверена одна Ubuntu VM, ограниченные нагрузки и перечисленные отказы.
HA, production SLA, backup/restore и отсутствие потерь при произвольном
outage не заявляются. Дубли логов допустимы; reset между scrape может остаться
незамеченным. Существенных незакрытых замечаний в принятой области не осталось.
