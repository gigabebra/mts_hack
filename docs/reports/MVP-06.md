# MVP-06 — независимая приемка и документация

> Исторический отчет первоначального MVP на IPv4; итоговая приемка этого
> этапа относится к `337d0c6`. Приведенные ниже статусы, команды и числа
> описывают ту ревизию, а не текущий dual-stack. Актуальный результат:
> [интеграция dual-stack](DUAL-INTEGRATION.md) и
> [независимая приемка](../acceptance/dual-stack-acceptance.md).

Статус: **done — MVP accepted**.
Дата: 2026-10-01. База аудита: `7a9b305f417b6e509ef57dc20ea57aa9394f9605`.
Исправленная интегратором ревизия: `337d0c6`, повторный независимый lint pass.
Принятая кодовая ревизия: `337d0c671617929ebf162e1216099524fe8d611d`.
Ветка: `mvp-06`; изменяются только `docs/acceptance/`, `docs/passport/`
и этот отчет. SSH/kubectl и изменения стендов выполняет только интегратор.

Прочитаны AGENTS, README, карточка/план, контракт, исходный DOCX, код
bootstrap/deploy/verify/component checks, manifests и отчеты MVP-01–05.
Подготовлены [матрица и протокол](../acceptance/mvp-acceptance.md) и
[черновик паспорта](../passport/mvp-draft.md). FINAL не выполнялся.

Независимые проверки из worktree:

```sh
make lint
.venv/bin/python deploy/monitoring/tests/check_render.py \
  --helm .cache/tools/helm --chart .cache/downloads/monitoring.tgz
# Ожидается ненулевой exit и fail JSON без обращения к кластеру:
make verify PROFILE=final
make verify ENV=missing-mvp06
```

| Проверка | Результат | Граница |
| --- | --- | --- |
| Python/YAML/Ansible syntax, Helm lint/render, image lock | pass | Локально, закрепленные инструменты; не Ubuntu live |
| 7 logging, 4 metrics, 7 bootstrap, 12 gateway tests | pass | Synthetic protocol/negative fixtures |
| Monitoring semantic render | pass | Отдельный запуск; затем включен интегратором в общий lint и повторен |
| Unsupported FINAL и отсутствующий environment | pass | Ожидаемый отказ с fail JSON и ненулевым кодом |
| Чистая Ubuntu установка/HTTP/metrics/logs/repeat | pass | Десять стадий с exit 0, четыре полных verify, два повторных deploy |
| Идентичность после repeat bootstrap | pass | Сохранены Node, два PV и два PVC: UID, created_at и bindings |
| Внешний curl | pass | Контроллер вне VM → NodePort, 200/12 bytes/exact Request-ID |
| Evidence tar | pass | Список members и содержимое пяти JSON побайтно сверены с исходниками |

Первый lint завершился fail из-за sandbox socket PermissionError тестового
loopback HTTP-сервера; полный повтор с escalation pass. Код/проверки ради
прохождения не ослаблялись. SHA256 журналов и отрицательных JSON —
[mvp-static-audit.json](../acceptance/mvp-static-audit.json).

Переданы интегратору замечания: curl и публичные источники в prerequisites;
повтор bootstrap после deploy с health/verify; включение semantic render
в lint; точное описание наблюдаемого reset; уточнение source selectors
counter после подтверждения live labels. Изменения общих файлов приемщик
не вносит. Все изменения проверены в `e920b1d`/`337d0c6`; повторный локальный
lint pass. Pristine evidence второй Ubuntu проверен: Kubernetes/containerd
отсутствуют до установки, cloud-init завершен. Все блокирующие замечания закрыты.

## Независимая оценка чистого evidence

Исполнение команд на второй VM делегировано единственному оператору —
интегратору; критерии и проверку полных отчетов выполнил MVP-06. Fresh
checkout main был чистым до/после, без ранее установленных проектных
инструментов. Ubuntu 24.04.5, Python 3.12.3, 4 vCPU, 8 GiB nominal RAM,
60 GiB disk. Сводка и SHA256 источников:
[mvp-clean-vm.json](../acceptance/mvp-clean-vm.json).

Live-команды выполнены строго из README:

```sh
make tools
make lint
make bootstrap ENV=ubuntu
make deploy ENV=ubuntu
make verify ENV=ubuntu
make bootstrap ENV=ubuntu
make health ENV=ubuntu
make verify ENV=ubuntu
make verify-idempotency ENV=ubuntu
make evidence ENV=ubuntu
```

В каждом из четырех verify проверены все восемь обязательных групп и
вложенные checks, совпадение HTTP/access Request-ID, отдельный error-marker,
рост source-scoped counter минимум на 24, UID 101/capabilities=0/root ro/token
absent. Первый counter 1→25; исходные JSON действительно относятся к
принятой ревизии. Idempotency содержит оба pass и оба вложенных verify.

Первый verify получен за 353.91 s, все стадии — 536.11 s; создание VM и
apt-предпосылки не учитывались. RAM available 5.87 GiB, total-minus-available
1.88 GiB; root disk used/free 6.12/50.94 GiB. CPU busy/iowait 3.89/0.19%
за 30 s после проверки — снимок, не пиковая или минимальная потребность.

`not run`: recovery повторно на второй VM (неизмененная реализация уже
подтверждена первым live recovery); публичный anonymous clone, окончательный
PDF/архив и публикация (FINAL-07 не назначен). После чистой приемки
интегратор вернул первый стенд: ранний health до завершения загрузки ОС
дал отказ; повтор после готовности ОС и полный verify
`20261001T115337Z-ec095221` прошли. Этот эксплуатационный эпизод не входит
в десять успешных стадий чистой VM и не скрывается за их результатом.

Итог: **MVP accepted**, все обязательные пункты gate подтверждены.
Измененные пути приемщика: `docs/acceptance/mvp-acceptance.md`,
`docs/acceptance/mvp-clean-vm.json`, `docs/acceptance/mvp-static-audit.json`,
`docs/passport/mvp-draft.md`, `docs/reports/MVP-06.md`.
SHA документационного коммита передан интегратору отдельно.
