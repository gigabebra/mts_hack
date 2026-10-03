# Проверка проекта по заданию — 3 октября 2026 года

**Вердикт: к сдаче по всем требованиям проект пока не готов.** Основные
компоненты работают на выделенном стенде; остаются обязательные артефакты
сдачи, неподтвержденный публичный clone и один воспроизводимый пробел
проверки готовности stable в FINAL-05.

Проверенная ветка — `main`, SHA
`5937f9f38a555684f398e7425b20b9403c12a970`. До аудита checkout был чистым.
Исходное задание прочитано непосредственно из
`Кейс DevOps (MTC ENGINEER HACK).docx`, включая требования к сдаче;
дополнительно сверены пользовательский список функций, AGENTS.md,
implementation-plan, agent-sessions и contracts. Учтены 139 tracked files,
конфигурации всех компонентов, автоматизация, проверки и приемочные отчеты.
Независимое ревью FINAL-01/05 выполнено в отдельном worktree той же ревизии.

Реализация и конфигурация стенда не изменялись. Новые live-проверки использовали
только kubeconfig из `ENV=ubuntu`: чтение ресурсов, HTTP/TLS, диагностический
exec и временные loopback port-forward. Deploy, fault injection, restart и
reboot в этом аудите не выполнялись. Исторические результаты не выдаются за
повторный прогон.

| Приоритет | Что препятствует полной приемке | Доказательство и необходимое действие |
| --- | --- | --- |
| P1, обязательная сдача | Анонимная доступность `main` не подтверждена | HTTPS `git ls-remote` для текущего origin без credential helper и пользовательской Git-конфигурации завершился exit 128 с запросом Username. Отдельный anonymous clone не завершился за 45 s. Нужен публичный URL и успешный clone без авторизации с проверкой принятого SHA. Эти результаты сами по себе не доказывают точную настройку visibility на сервере. |
| P1, обязательная сдача | Нет окончательного паспорта | Найден только [исторический Markdown-черновик MVP](../passport/mvp-draft.md); PDF/DOC/DOCX в проекте отсутствует. Нужен паспорт до четырех страниц и 15 МБ с актуальными TLS/canary, архитектурой, проверками и разделом ревью/развития. |
| P1, обязательная сдача | Нет комплекта для загрузки | Не найдены `Ссылка.txt` и ZIP/RAR сдачи. По заданию архив до 18 МБ, имя — фамилия участника, внутри `Ссылка.txt` только с URL актуальной ветки и `Паспорт.pdf` либо допустимый документ. Готовность архива и загрузка на площадку — разные факты. |
| P2, FINAL-05 | Canary допускает `accepted`, не проверив готовность stable Deployment | [canary.py](../../scripts/canary.py): `ready()` проверяет только `hello-canary` (строки 142–159); stable проходит HTTP smoke, а его Helm status только сохраняется (339–370). Перед `accepted` снова проверяется только кандидат (417–422). Требование готовности stable явно задано в [FINAL-05](../agent-sessions.md), строка 240. Добавить проверку актуального поколения и всех требуемых ready/available replicas stable; незавершенный release должен обрабатываться явно. |

Последнее замечание воспроизведено на существующем fixture с production
`run_locked`: `ops.info("app")` возвращает `pending-upgrade`, HTTP и остальные
fixtures остаются штатными. Результат:

```text
previous_stable_status: pending-upgrade
readiness_calls: [canary, canary]
status: pass
decision: accepted
weights_after: {stable: 90, canary: 10}
```

Практический сценарий — незавершенный rollout stable, при котором оставшийся
готовый pod отвечает на HTTP, но требуемые две replicas еще не готовы.
Это подтвержденный пробел gate, а не утверждение о текущей деградации VM.
Исправление кода в задачу аудита не вносилось.

| Требование исходного задания | Результат | Проверенная реализация и доказательство |
| --- | --- | --- |
| Kubernetes; предпочтительно kubeadm; версии указаны | pass | Ansible, kubeadm 1.34.3, containerd, Calico; README содержит матрицу. Новый live readiness и dual-stack check прошли. |
| HTTP-приложение с однозначным ответом и access-логом | pass | Nginx, ровно `Hello World!`, Request-ID, access/error в stdout/stderr. Новые HTTP-пробы по IPv6 и IPv4 прошли, их записи найдены через Loki. |
| Настоящий Gateway API | pass | Traefik, GatewayClass, Gateway, HTTPRoute и ClusterIP Service. Актуальные conditions и реальные маршруты прошли; отдельно 154/154 TLS/Gateway checks. |
| Prometheus: target и реальный query | pass | Пять здоровых targets; проверены свежие samples и scoped HTTP counter: 518 → 567, прирост 49 при требовании не менее 48 тестовых запросов. |
| Fluentd/Filebeat: доставка access/error | pass | Fluentd → Loki; обе семьи дали exact access Request-ID и независимый missing-file stderr marker. |
| Работа на Ubuntu 24.04 | pass по сохраненной приемке | Чистая установка Ubuntu 24.04.5 на `cc3afc6`; последующие FINAL live-сценарии на `9ce5f99`. Исходные доказательства сверены. Новая чистая установка текущего состава в этом аудите — not run. |
| Автоматизация и корректный повтор deploy | pass по коду и сохраненной приемке | Make/Python/Ansible/Helm, точные версии, SHA256 и image lock; текущий код соответствует принятому `9ce5f99`. Последний idempotency сравнивает UIDs, PVC bindings и содержимое Secrets. Новый повтор deploy — not run. |
| README: архитектура, версии, запуск, диагностика, ограничения | pass | Обязательные разделы и команды присутствуют; битых локальных Markdown-ссылок не найдено. Обязательный окончательный паспорт оценивается отдельно выше. |
| Публичный репозиторий, основная ветка | fail / не подтверждено полностью | Локальная ветка `main` есть; анонимное клонирование удаленной сдаваемой ветки не подтверждено. |
| Нет секретов в Git | pass в пределах проведенного сканирования | Проверены все 434 уникальных blob, достижимых из HEAD, около 3,9 МБ. PEM private keys, характерные GitHub/GitLab/AWS tokens, встроенные kubeconfig credentials и реальные credential URLs не обнаружены. Найденные URL с паролем — отрицательные fixtures `user:secret@example.invalid`. Чувствительные локальные пути не tracked. Это не доказательство отсутствия любого возможного секрета. |

**Новые проверки и ограничения их среды.**

| Проверка | Статус | Результат |
| --- | --- | --- |
| Python/Ansible syntax, YAML, Helm lint/render семи charts, image lock | pass | Выполнены штатной последовательностью `make lint` до остановки на HTTP fixtures; monitoring render также проверен отдельно. |
| Отдельно завершенные локальные suites | pass | 158 тестов: app 3, gateway chart 22, logging 7, Ansible 22, unit 46, canary 41, Gateway ConditionTests/ReportTests 17. Пересекающиеся прогоны независимого агента повторно не суммируются. |
| Полный `make lint` на управляющей машине | fail | Первая попытка уперлась в запрет сокетов sandbox; разрешенный повтор — в timeout IPv6 loopback у metrics fixtures. Полностью зеленого lint этого прогона нет. |
| `make verify-release ENV=ubuntu` | fail | MVP readiness/network/conditions/HTTP обеих семей/runtime — pass. Метрики и логи через штатный `::1` port-forward — fail; delta не выполнен из-за baseline. До вложенного TLS checker команда не дошла. |
| Независимая диагностика localhost | pass как установление причины | Простой локальный HTTP server: `127.0.0.1` → 200/OK, `::1` → timeout. Настройки сети машины не менялись. |
| Дополнительный live observability | pass | Те же production component checkers через port-forward на `127.0.0.1`, с исходными критериями; 6/6: baseline, HTTP v6/v4, delta, access/error v6/v4. Это отдельная диагностика, не успешный штатный `make verify-release`. |
| Отдельный `check-gateway.final.py` | pass | 154/154 checks: TLS с проверкой CA/hostname, отрицательные CA/hostname, host/path, оба backend, по 100 запросов каждой семьи при 100/0. Новый эксперимент 90/10 не выполнялся. |
| Canary precondition regression probe | fail относительно требования | `pending-upgrade` stable не препятствует `accepted`; воспроизведение приведено выше. |
| Новая clean Ubuntu установка, idempotency, fault/recovery, reboot | not run | В этом аудите стенд не переразворачивался и не перезапускался. Проверены сохраненные live-доказательства соответствующих ревизий. |

Локальные источники нового аудита:

- `artifacts/verify/release-0b2d645d704c/report.json` — штатный неуспешный MVP прогон.
- `artifacts/audit/audit-71da812fb344/report.json` — дополнительный live observability.
- `artifacts/audit/gateway-20261003.json` — 154 успешные Gateway/TLS проверки.
- `artifacts/audit/2026-10-03/summary.json` — сводка и копии диагностических скриптов/логов рядом.

Основные точные команды новых проверок:

```bash
make lint
make verify-release ENV=ubuntu
.venv/bin/python -m unittest discover -s ansible/tests -v
.venv/bin/python -m unittest discover -s tests/unit -v
.venv/bin/python -m unittest discover -s tests/canary -p 'test_*.py' -v
.venv/bin/python scripts/check-gateway.test.py ConditionTests ReportTests
.venv/bin/python deploy/monitoring/tests/check_render.py \
  --helm .cache/tools/helm --chart .cache/downloads/monitoring.tgz
.venv/bin/python scripts/check-gateway.final.py --env ubuntu --requests 100 \
  --run-id audit-20261003 --output artifacts/audit/gateway-20261003.json
```

Сохраненные evidence проверены по содержимому и SHA256. Для
`final-01-05.json` совпали все 18 исходных JSON и архив. Для dual-stack
и MVP-аудита проверены соответственно 213 и 189 записей с хешами:
исторический код сопоставлялся с его Git-ревизией, raw evidence — с локальными
файлами. После принятого `9ce5f99` до проверенного HEAD менялись только
README и документация. Новую чистую установку полного состава FINAL это
сравнение не заменяет: чистый прогон относится к более раннему MVP.

CI, Grafana/Alertmanager, NetworkPolicy и backup отсутствуют, но исходное
задание относит такие улучшения к дополнительным возможностям. Неназначенные
FINAL-02/03/04/06 не считаются нарушением обязательной части. Утверждать,
что реализованы все шесть идей из пользовательского наброска, нельзя:
сетевые security invariants и ephemeral Kubernetes CI пока не выполнены.
README честно ограничивает область и отклоняет `PROFILE=final`.

На момент аудита перед сдачей нужно было закрыть три обязательных пункта комплекта, исправить
stable readiness gate и выполнить итоговый чистый прогон сдаваемой ревизии
на Ubuntu с работающим IPv6 loopback. Затем зафиксировать публичную `main`
и проверить архив. В исходном задании указан дедлайн **4 октября 23:59**
и запрет изменять сдаваемую ветку после него; часовой пояс документ не
уточняет. Публикация и загрузка на площадку в рамках этого аудита не выполнялись.

После аудита замечание о stable readiness исправлено; изменения и проверки
описаны в [FINAL-05](FINAL-05.md). Текущее состояние последующей чистой
установки отражается в [статусе проекта](../status.md). Результаты таблиц выше
относятся к исходной ревизии аудита.
