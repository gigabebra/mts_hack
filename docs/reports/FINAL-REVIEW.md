# FINAL-REVIEW — независимая проверка FINAL-01 и назначенной части FINAL-05

Дата: 2026-10-02. Результат: `accepted` в указанном ниже объеме.
Принятая ревизия исполняемого кода: `9ce5f99d92aca36cf2399469962a710f9b444def`.
Блокирующих замечаний после исправлений и повторной live-проверки не осталось.

Рецензент проверял исходники, локальные fixtures и JSON evidence интегратора;
сам не изменял VM и не запускал live-сценарии. Этот отчет не означает приемку
всего этапа FINAL. Машиночитаемая сводка и SHA-256 исходных отчетов находятся в
[final-01-05.json](../acceptance/final-01-05.json).

## Ревизии и происхождение результатов

| Ревизия | Принятые сценарии |
| --- | --- |
| `da0a0affe9ac4ef7eb9980ede92f5444656676a5` | Шесть canary-сценариев, MVP и Gateway/TLS при 90/10 и после возврата 100/0 |
| `b45f6a60d49f105c952661eadec5f2ab9f10982c` | Реальные прерывания Helm и восстановление в текущем и следующем запуске |
| `9ce5f99d92aca36cf2399469962a710f9b444def` | Повтор app/recovery после исправления проверки Fluentd, idempotency, реальный CLI exit, внешний reboot и итоговый verify |

Независимое сравнение `git diff --exit-code da0a0af 9ce5f99` подтвердило
неизменность `ansible/`, `config/`, `deploy/`, `scripts/canary.py`,
`scripts/canary_metrics.py`, `scripts/release_control.py`, `scripts/deploy.py`,
`scripts/stacklib.py`, `scripts/verify.py`, `scripts/verify-release.py`,
`scripts/check-gateway.py`, `scripts/check-gateway.final.py`,
`scripts/check-metrics.py`, `scripts/check-logs.py`, `tests/canary/verify.py`.
Промежуточные изменения затрагивали harness восстановления/демонстрации,
его fixtures, очистку процессов, упаковку evidence и документацию. Поэтому
результаты gate применимы к принятому коду без повторения только ради нового SHA.
Отчеты gate, release, interruption и отдельного CLI, содержащие
`working_tree_dirty`, имеют значение `false`.

## Выпуск и отказ кандидата — pass

Основной набор: `acceptance-20261002T134950Z-bbaa0fbb`.
Проверены aggregate, все шесть дочерних отчетов и cleanup; ожидаемые отказы
в дочерних отчетах сохраняют `status: fail`.

| Сценарий | Фактическое наблюдение | Решение |
| --- | --- | --- |
| healthy | 2400 HTTP за 60 с: 2160 stable, 240 canary; backend delta 240, 5xx 0% | `accepted`, 90/10 |
| http500 после split | 240 canary-ответов; все 12 сохраненных ошибочных samples имеют нужную revision и Request-ID, HTTP 500; backend delta 241, 5xx 100% | `rejected` |
| wrong-body после split | 240 canary-ответов; 12 сохраненных samples нужной revision имеют HTTP 200 и неверный hash тела; метрики 5xx 0% | `rejected` по HTTP |
| wrong-body до split | 5/5 ответов нужной revision с неверным телом; событие включения 90/10 отсутствует | `rejected` до пользовательского трафика |
| metrics-unavailable | Реально закрыт port-forward gate к Prometheus; запрос завершился `URLError`, HTTP оставался здоровым | `inconclusive` |
| insufficient-sample | 20 HTTP: 18 stable, 2 canary; backend delta 3 при неизменном минимуме 100 | `inconclusive` |

Во всех пяти отказах независимо проверены сохраненные samples:
25/25 новых ответов stable и 5/5 восстановленного canary с точным телом,
ролью, revision и Request-ID; фактические веса возвращены к 100/0.
Сам rollback занял 12,426–14,525 с. Это время восстановления после решения,
а не обещание мгновенного обнаружения ошибки: измерительное окно составляет
60 с, при недоступности метрик дополнительно ожидается bounded scrape timeout.

По исходным samples пересчитаны приросты, наличие baseline anchor,
монотонность счетчиков, неизменность process identity и `up=1`.
Все итоговые samples относятся к canary backend и получены после конца
HTTP-окна; их возраст при снимке — 0,048–0,679 с при пределе 45 с.
Метрики backend включают также convergence-пробы: 241/242/3 в отрицательных
сценариях нельзя называть числом только запросов 60-секундной нагрузки.

Поля `release_exit_code` шести сценариев вычислены harness из результата
`run_locked`, выполняемого в том же процессе. Они не являются шестью
измеренными OS exit codes. Отдельный реальный CLI-прогон
`cli-negative-72fb320310fc` зафиксировал `subprocess.returncode=2`;
дочерний `cli-negative-72fb320310fc-release` имеет `rejected`, пять точных
wrong-body samples, успешный rollback и 25/25 stable при 100/0.

## Gateway/TLS и основной MVP — pass

`release-ecba92a7dbc0`: 154/154 Gateway/TLS checks при 90/10.
Из 1000 HTTP-запросов каждой семьи canary обслужил 99 по IPv6 и 100 по IPv4;
ошибок нет. `release-0aced3d7fd54`: 154/154 после cleanup; здесь по 100
запросов на семью, все stable, canary 0. Это разные размеры выборок.

В обоих состояниях подтверждены направленные stable/canary пути,
hostname routing, HTTPS с проверкой CA и имени и отрицательные пути.
Неверные hostname и посторонняя CA отклонены `curl` с кодом 60 по обеим
семьям; проверка TLS не отключалась. Сопутствующий MVP в каждом запуске
содержит 11 основных и 105 вложенных успешных checks.

## Прерывание, восстановление и повторное развертывание — pass

`interrupt-b3baa92710e2`: действительно наблюдались `pending-upgrade`
кандидата и запрошенный непригодный для Nginx закрепленный образ.
В текущем запуске SIGTERM дал настоящий exit 2, `interrupted` и успешный
rollback за 6,318 с. В сценарии следующего запуска writer завершился с 143,
после его выхода сохранился `pending-upgrade`; обычный restore восстановил
его за 6,379 с. Дочерние отчеты подтверждают 25/25 stable, 5/5 здорового
canary и 100/0; сертификат TLS сохранен. Cleanup набора — pass.

`demo-6e013052049b`: замена stable pod дала 86 HTTP-запросов без ошибок.
Config rollout и Helm rollback подтверждены отдельным ожиданием исчезновения
старых UID, готовности текущего поколения, нужной HTTP revision и последующими
20 строгими пробами в каждую сторону. Convergence заняла 4,39 и 4,92 с.
Итоговый `release-251fb714a2b5` — pass.

`recovery-c432ca0abfe0`, после настоящего churn приложения: история Prometheus
и Loki сохранена; все пять записей, созданных при недоступном Loki, доставлены
после рестарта Fluentd, каждая найдена один раз. Проверенный интервал — 49,23 с.
Из восьми position entries после рестарта осталось три: удалены только пять
неактивных, помеченных `0xffffffffffffffff`. Три активных path/inode/size и
их offsets неизменны, обе границы файлов с маркерами достигнуты,
новых entries нет, счетчик replay равен нулю. Проверены исходные comparison
и boundaries, а не только итоговый `pass`. Последующие MVP и
`demo-22919528eab7` / `release-2e33b5516b4c` — pass.

Первоначальные recovery failures, включая `recovery-65b2209e8f3b` и
`recovery-1318bd66645e`, не переименованы в успешные. Точная причина второго —
некорректное требование равенства всего position file при штатной компактации.
[Fluentd 1.19.0](https://raw.githubusercontent.com/fluent/fluentd/v1.19.0/lib/fluent/plugin/in_tail/position_file.rb)
удаляет unwatched и отсутствующие источники при загрузке position file.
Новая проверка сохраняет строгие требования ко всем активным источникам,
offsets, boundaries и нулевому replay. Ее отрицательные fixtures и новый
успешный live-прогон проверены отдельно; это исправление проверочного harness.

`20261002T141608Z-8b4ea027`: два повторных deploy и verify — pass,
сопоставлены identities 28 ресурсов, PVC bindings и Secret UID.
Эта проверка metadata не заявляется сравнением всех байтов Secret.

## Reboot и итоговое состояние — pass

`external-reboot-a999e891566b`: внешний supervisor наблюдал смену bootID и
восстановление API за 33,31 с, затем получил реальные exit 0 для health
и полного `verify-release`. Процесс наблюдения не выполнялся на
перезагружаемой VM. `release-b59150144063` на принятой ревизии и чистом
checkout: MVP pass, Gateway/TLS 154/154, 100/0. Внешняя проверка
`external-final-01-05` также содержит 154/154 checks, по 100 запросов каждой
семьи без ошибок, все stable. Конечный кандидат здоров, `faultMode: none`.

## Проверки рецензента и передача

Локально на принятом коде повторены:

```sh
.venv/bin/python -m unittest discover -s tests/unit -p 'test_*.py'
.venv/bin/python -m unittest discover -s tests/canary -p 'test_*.py'
```

Результат: 46 unit и 41 canary/cleanup fixtures — pass. Это локальные
проверки, отдельно от live evidence. Интегратор дополнительно сообщил
успешный полный `make lint` на VM, 167 тестов.

Независимо сверены SHA-256 всех 18 исходных файлов из acceptance-сводки.
Архив `artifacts/evidence/20261002T142057Z.tar.gz` содержит 107 JSON-файлов,
все эти 18 файлов побайтово совпадают с источниками; семь вложенных отчетов
canary включены. SHA-256 архива:
`4e8961fd8f4cd613cd0d216c1140f90acb1b2a2f9bd2d5b2e0ddb0dcd496c006`.
Архив сохраняет также историю неуспешных запусков; актуальную приемку задают
перечисленные run IDs и acceptance-сводка, а не отсутствие `fail` во всем архиве.

Изменение этого рецензента: только `docs/reports/FINAL-REVIEW.md`, ветка
`final-review`, worktree `.worktrees/final-review`, база `9ce5f99`.
Проверка отчета: `git diff --check`; без credentials, адресов стенда и сырых логов.

## Ограничения и not run

- Приняты FINAL-01 и назначенные release/recovery-сценарии FINAL-05.
  Grafana/alerts и их восстановление, NetworkPolicy, hosted CI и полный
  `PROFILE=final` не приняты: соответствующие карточки не назначены.
- Чистая установка этого расширения на новой Ubuntu — `not run`;
  прежняя чистая приемка MVP является отдельным результатом.
- Один непрерывный `make demo --reboot` — `not run`: сценарии проверены
  отдельными секциями, reboot выполнен внешним supervisor.
- Это один узел, bounded нагрузка и проверенные fault-сценарии, без обещания
  HA, production SLA, бесконечного outage или отсутствия потерь при любом сбое.
  Между scrape необнаруженный reset возможен; доставка логов допускает дубли.
