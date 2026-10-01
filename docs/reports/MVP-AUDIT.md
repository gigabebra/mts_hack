# Повторный аудит MVP: код, рабочий стенд и документация

Дата: 2026-10-01. **Результат: pass на кодовой ревизии
`8ed0e70c97516a57c5541b21b173c3bf6686d309`.**
[Машинная сводка и SHA256 источников](../acceptance/mvp-audit.json).
Интегратор выполнял все действия на Ubuntu; отдельные агенты проверяли
документацию, код и исходные доказательства без доступа к VM.

Чистая установка и ее измерения по-прежнему относятся к
`cc3afc6cab06a4cb441c3f0c705e79cc04910a0f` и
[первоначальной dual-stack приемке](../acceptance/dual-stack-acceptance.md).
Этот аудит повторно проверяет существующий выделенный стенд после двух
исправлений проверок. Новая чистая установка и новый reboot в этом аудите
не выполнялись. Этап FINAL не начат.

## Найденные расхождения

1. Gateway checker принимал любой native IPv6 в `status.addresses`, включая
   непригодный или чужой адрес. Теперь он отклоняет unspecified, loopback,
   multicast, link-local, scoped и mapped адреса. Общий verify передает
   `--expected-address node_ip` и требует точного совпадения с учетом
   канонической записи IPv6. Неверный status больше не может пройти за счет
   успешного HTTP на другом, явно заданном адресе.
2. Готовый image reference проверялся только по digest. Неизвестный repository
   с известным digest мог пройти проверку lock. Теперь сопоставляются
   repository и digest, а при наличии tag — также tag. Все принятые формы
   `repository:tag`, `repository@digest`, `repository:tag@digest` сохранены.
3. План и старые отчеты смешивали начальное состояние проекта, IPv4 и
   strict IPv6-only с текущим dual-stack. Исправлены текущие статусы и ссылки,
   исторические отчеты явно ограничены своей ревизией. Их результаты не
   переписаны задним числом.
4. В инструкциях исправлены PATH инструментов после `make tools`, вычисление
   образа для Docker smoke, выбор kubeconfig из environment и описание
   свежести samples: предел 45 s относится к последнему sample каждой серии,
   а не ко всей истории.

Первые два пункта — воспроизводимые пробелы строгости проверок, а не
доказанные отказы приложения. Фактические адреса и image references стенда
соответствовали конфигурации. Добавлены шесть regression-тестов; независимый
аудитор повторно проверил исправления общих файлов.
[Разбор кода](MVP-CODE-AUDIT.md), [аудит документации](MVP-DOC-AUDIT.md).

## Живые проверки

До исправлений на `ac3abaf` прошли lint, health, повтор bootstrap, повторный
health, verify с недоступным proxy, два deploy/verify, recovery и evidence.
Это подтверждение исходного состояния; новые проверки подтверждает только
следующий отдельный прогон `8ed0e70` с чистым checkout до и после запуска.

| Проверка после исправлений | Фактический результат |
| --- | --- |
| Полный `make lint` на Ubuntu | 68 тестов, Python/Ansible syntax, Helm lint/render, image lock — pass |
| `make health ENV=ubuntu` | Node, runtime, обе сети, DNS и Pod/Service HTTP — pass |
| Verify с недоступным proxy | Все шесть HTTP/HTTPS/ALL_PROXY переменных указывают на недоступный loopback; полный verify pass |
| `make verify-idempotency ENV=ubuntu` | Два deploy, 25 неизменных resource identities, PVC/PV bindings и содержимое Secrets сохранены; оба verify pass |
| `make verify-recovery ENV=ubuntu` | История Prometheus/Loki сохранилась; пять точных CRI markers дочитаны до byte boundaries с тем же inode; replay 0; каждый buffered marker найден один раз |
| Внешние проверки | SSH, аутентифицированный API с CA/SAN verification и точный HTTP-ответ по IPv6 и IPv4 — pass |
| Внутренние проверки | Точные DNS A/AAAA восьми Services, четыре прямых Pod/Service HTTP-пути и 16 HTTP-проб восьми Services по обеим семьям — pass |
| Runtime snapshot | Все 17 Pod Ready; 22 ссылки на образы containers/initContainers в Pod specs (18 уникальных) соответствуют repository/digest lock; версии узла и оба Bound/Retain local PV соответствуют конфигурации |
| Evidence | Архив содержит index и 21 report; каждый побайтово равен исходнику. Шесть reports относятся к `8ed0e70`, остальные — история |

Четыре полных verify новой кодовой ревизии:

- `20261001T145838Z-20a9ad61`;
- `20261001T145845Z-ac908eff-1`;
- `20261001T145845Z-ac908eff-2`;
- `recovery-f3cc436838dd-after`.

Каждый содержит **11 обязательных и 105 вложенных checks pass**, включая
`gateway.address.actual.expected_address_checked=true`. Метрики дали +49
при минимуме 48 от нагрузки 24+24; фоновый трафик допускается контрактом.
Сценарий outage/restart занял 48.04 s; это ограниченный recovery-тест,
без обещания exactly-once доставки или сохранности при любом outage.

Внешние supplementary probes выполнялись из checkout `9b1f25d`, который
отличается от `8ed0e70` только 19 Markdown-файлами. Эквивалентность кода
проверена Git diff. Проверка image lock выше относится к Pod specs;
отдельная повторная проверка содержимого runtime imageID не заявляется.

## Документы и независимая сверка

Документационный агент прочитал все 28 исходных Markdown и четыре JSON
приемки, сопоставил команды и утверждения с кодом, проверил ссылки и
синтаксис примеров. Все 36 публичных URL ответили HTTP 2xx на дату проверки.
После интеграции и добавления отчетов проверка локальных ссылок, code paths,
shell-примеров, CLI arguments и JSON выполнена снова. Выбор kubeconfig из
корневого README проверен на текущем environment без вывода credentials.

Независимый приемщик пересчитал проверки и сравнил raw network snapshots,
HTTP/log markers, metrics, DNS, targets, telemetry, SSH, identity snapshots,
recovery и содержимое архива. Предыдущие **211 SHA256** также совпали:
исторические исходники кода читались через `git show cc3afc6:<path>`,
остальные доказательства — из сохраненных ignored каталогов.

Итоговая JSON-сводка содержит пути и SHA256 новых источников и кодовой
ревизии. Адреса, kubeconfig, ключи и сырые журналы не входят в Git.
Основной стенд оставлен работающим; прежние VM и диски сохранены.
Single-node/local PV, HTTP без TLS, отсутствие HA/backup и автоматического
переключения семей остаются явными ограничениями MVP.
