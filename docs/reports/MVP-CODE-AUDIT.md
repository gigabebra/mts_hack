# Независимый аудит кода MVP

Дата: 2026-10-01. База аудита: `ac3abaf36c704a1fc3d08ae395dc7e439a14338d`.
Это отчет компонентного аудитора о локальной проверке и передаче исправления.
Статус передачи: `ready_for_integration`; live-проверки выполняет интегратор.

## Область и результат

Сопоставлены текущие сетевые и эксплуатационные гарантии `docs/contracts.md`,
`docs/networking.md`, README и компонентных инструкций с `scripts/`, Ansible,
Helm values/templates и `tests/e2e/mvp-recovery.py`.

Найдены два воспроизводимых пробела строгости проверок. Они не устанавливают
отказ работающего стенда: проверенные шаблоны используют предусмотренные адреса
и образы. Для окончательного вывода о текущем runtime нужны проверки интегратора.

1. **Gateway status мог содержать посторонний или непригодный IPv6.** В базовой
   ревизии `scripts/check-gateway.py:71–82` проверял только семейство и отсутствие
   IPv4-mapped адреса. Замена fixture `snapshot()['gateway']['status']['addresses'][0]['value']`
   на `::`, `::1`, `ff02::1`, `fe80::1` или другой unicast IPv6 оставляла все
   `conditions_checks()` успешными. При изменении только рекламируемого адреса
   запросы на явно заданный NodePort продолжали бы проверять другой адрес.
   Исправлено в этой передаче: проверка usable native unicast IPv6 и аргумент
   `--expected-address`, сравнивающий status с основным адресом узла. Каноническая
   запись IPv6 учитывается; успешный JSON не содержит адрес. Интегратору передано
   требование передавать `environment.node_ip` общим `scripts/verify.py`.

2. **Готовый digest проверялся без связи с repository.** В базовой ревизии
   `scripts/stacklib.py:128–132` принимал
   `unreviewed.invalid/arbitrary/repository@<digest из принятого lock>`.
   Для воспроизведения достаточно передать такой reference в `locked_image()`:
   он возвращался без ошибки. Это расходилось с контрактом отказа от неизвестного
   image reference; контроль содержимого digest оставался. Общий `stacklib.py`
   принадлежит интегратору: ему переданы воспроизведение и исправление проверки
   связи repository/digest, включая допустимые закрепленные формы reference.

Прочие рассмотренные gates соответствуют заявленной области: все обязательные
checks должны выполниться; отсутствующий check дает `fail`; `PROFILE=final`
не поддерживается. Проверяются точное HTTP-тело, Request-ID, настоящий missing-file
stderr, свежие сырые Prometheus samples, наблюдаемые resets и дубли scrape.
Сетевой snapshot связывает EndpointSlice с UID/namespace/selector действительного
Pod и требует обе семьи у каждого собственного Service. Bootstrap сверяет
immutable identity до изменения уже созданного кластера и не выполняет reset.

Повторный deploy сравнивает UIDs, PVC/PV bindings и локально содержимое Secrets.
Recovery отдельно заменяет Prometheus/Loki Pods и сопоставляет исторические
данные. Для перезапуска Fluentd он ждет позиции за точными byte boundaries
всех пяти CRI markers с тем же inode, а затем подтверждает доставку после
ограниченного outage. Эти проверки не обещают отсутствие потерь при любом
отказе, автоматический failover семей или обнаружение reset между scrape,
если счетчик успел восстановиться; текущая документация этих гарантий не дает.

## Выполненные проверки

Все команды запускались из выделенного worktree. `PYTHONDONTWRITEBYTECODE=1`;
Python — существующий `.venv/bin/python` основного checkout. Только локальные
fixtures, без SSH, kubectl или доступа к VM.

| Команда | Результат |
| --- | --- |
| `python -m unittest discover -s tests/unit -v` | `pass`, 8 тестов |
| `python -m unittest discover -s ansible/tests -v` | `pass`, 22 теста |
| `python scripts/check-gateway.test.py` | `pass`, 18 тестов |
| `python deploy/monitoring/tests/test_check_metrics.py` | `pass`, 8 тестов |
| `python deploy/logging/tests/test_check_logs.py` | `pass`, 7 тестов |
| `git diff --check` | `pass` |
| Полный lint/render в этой сессии аудитора | `not run`: выполняет интегратор |
| Живой verify/idempotency/recovery | `not run`: общая VM только у интегратора |

Итого 63 локальных теста. Первые запуски Gateway/metrics fixtures были остановлены
запретом socket в sandbox; после разрешенного повторного запуска временного
listener на `::1` обе группы полностью прошли. Это ограничение среды проверок,
а не скрытая ошибка теста.

Три новые Gateway regression-проверки охватывают непригодные IPv6 status addresses,
совпадение с ожидаемым узлом с учетом нормализации IPv6 и CLI с неправильным
ожидаемым адресом/несовпадающим status. Измененные пути этой передачи:
`scripts/check-gateway.py`, `scripts/check-gateway.test.py` и этот отчет.
Изменения общих файлов и итоговую live-приемку объединяет интегратор.
