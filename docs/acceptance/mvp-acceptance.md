# Независимая приемка MVP

Итог: **MVP accepted**. Дата: 2026-10-01.
Принятая кодовая ревизия: `337d0c671617929ebf162e1216099524fe8d611d`.
База независимого аудита:
`7a9b305f417b6e509ef57dc20ea57aa9394f9605`.
После этой ревизии приемщик добавляет только документы и сводки evidence.

Требования сверены с исходным «Кейс DevOps (MTC ENGINEER HACK).docx»,
[планом](../implementation-plan.md) и карточкой MVP-06 в
[сессиях](../agent-sessions.md). Это приемка обязательной части; FINAL
не назначен, публикация репозитория и окончательная сдача сюда не входят.

## Метод и границы независимости

MVP-06 самостоятельно читает код, README, контракт и исходное задание,
задает критерии и проверяет результаты. Единственный оператор VM —
интегратор. Приемщик не выполняет SSH/kubectl и не изменяет стенды.
Интегратор исполняет README на второй чистой Ubuntu из официального cloud
image, со свежим clone Git bundle и пустыми проектными tool caches.
Bundle заменяет транспорт получения checkout; он не проверяет публичный
анонимный clone, который остается отдельным пунктом FINAL-07.

Установка на первом стенде и синтетические fixtures не считаются чистой
приемкой. Сводка первого стенда — [mvp-integration.json](mvp-integration.json).
Локальная независимая проверка — [mvp-static-audit.json](mvp-static-audit.json).
Сводка второй VM — [mvp-clean-vm.json](mvp-clean-vm.json): clean checkout,
десять команд с exit codes/длительностью, пять исходных отчетов и SHA256,
внешний HTTP, идентичность ресурсов и замеры. Полные JSON проверены
до подготовки сводки; содержимое evidence tar побайтно совпадает с ними.
Сырые журналы хранятся в игнорируемых artifacts/. В Git попадают только
результаты, идентификаторы прогонов и SHA256; адреса, ключи, kubeconfig,
содержимое Secrets и сырые error-логи не публикуются.

## Выполненный протокол чистого прогона

1. Записать HEAD и чистоту checkout, `/etc/os-release`, ядро, vCPU/RAM/диск;
   подтвердить отсутствие kubeadm/containerd и `/etc/kubernetes` до bootstrap.
2. Установить предпосылки из README, выполнить `make tools`, `make lint`,
   заполнить локальный environment своим SSH key и node name. Отсутствие
   конкретного публичного источника в списке зависимостей исправлять в README.
3. Выполнить `make bootstrap ENV=ubuntu`, `make deploy ENV=ubuntu`,
   `make verify ENV=ubuntu`; сохранить exit codes, времена и JSON отчеты.
4. Повторить `make bootstrap ENV=ubuntu` при уже развернутом приложении,
   затем `make health ENV=ubuntu` и `make verify ENV=ubuntu`.
5. Выполнить `make verify-idempotency ENV=ubuntu`, сохранить его отчет
   и оба вложенных verify. Сравнение включает UIDs, PVC bindings и
   содержимое существующих прикладных Secrets; Helm history исключена.
6. Выполнить `make evidence ENV=ubuntu`. С контроллера за пределами VM
   получить HTTP 200, тело ровно `Hello World!` (12 bytes) и тот же
   `X-Request-ID` через NodePort; не использовать port-forward приложения.
7. Записать длительность установки и отдельные фазы, RAM/CPU/диск после
   установки. Различать снимок потребления и пик; минимально возможный размер
   VM из единственного успешного прогона не выводить.

Все десять стадий завершились с exit 0. Checkout был чистым в начале и в
конце, Python — 3.12.3. Ручных созданий/правок Kubernetes-ресурсов во время
установки не потребовалось. Bundle не содержал HEAD, поэтому явно выбрана
ветка main (`git checkout main`, эквивалент `git clone -b main`).

| Проверка / run-id | Результат |
| --- | --- |
| Первый verify `20261001T114640Z-116d8df2` | pass: 8 обязательных групп и все вложенные проверки |
| После repeat bootstrap `20261001T114921Z-4eda473d` | pass; kubeadm init пропущен, Node/2PV/2PVC сохранили UID и bindings |
| Idempotency `20261001T114927Z-a83b06ec` | pass; два deploy, сохранены identities/PVC/существующие Secrets |
| Вложенные verify `20261001T114927Z-a83b06ec-1` и `-2` | pass; полный HTTP/metrics/logs/runtime после каждого deploy |
| Внешний curl `mvp06-external-7a87c161a376` | pass: контроллер вне VM → NodePort, HTTP 200, 12 bytes, тот же ID |
| `make evidence` | pass; index + пять JSON, архив сверен с исходными файлами |

Первый verify дал counter 1→25 после 24 успешных проб. Exact Request-ID
найден в stdout access, отдельный missing-file marker — в stderr через
Loki API; новые log-пробы при поиске не создавались. Проверены актуальные
conditions, свежие samples всех четырех targets, Nginx UID 101, CapEff=0,
read-only root и отсутствие service-account token.

Persistence/короткий outage: `pass` на первом стенде
(`recovery-162280d8e552`, ревизия `ad27c75`), `not run` на второй VM.
Эти сценарии не изменены исправлениями приемки; первое evidence просмотрено,
но не представляется вторым независимым recovery-прогоном.

## Измеренная среда

Ubuntu 24.04.5 LTS amd64, kernel 6.8.0-142-generic, 4 vCPU, 8 GiB nominal
RAM (7.76 GiB MemTotal), диск 60 GiB (57.08 GiB root filesystem).
Kubelet/Kubernetes 1.34.3 и containerd 1.7.28 подтверждены nodeInfo.

| Показатель | Фактический результат |
| --- | --- |
| tools / lint / bootstrap / deploy / первый verify | 10.37 / 8.83 / 199.95 / 104.68 / 30.08 s |
| До первого полного verify | 353.91 s (5 min 53.91 s) |
| Repeat bootstrap / health / verify после bootstrap | 88.51 / 42.79 / 5.59 s |
| Двойной deploy с verify / evidence | 45.21 / 0.09 s |
| Все десять стадий | 536.11 s (8 min 56.11 s) |
| RAM после прогона | MemAvailable 5.87 GiB; MemTotal − MemAvailable 1.88 GiB |
| Root filesystem после прогона | Занято 6.12 GiB; свободно 50.94 GiB |
| CPU, 30 секунд после проверки | busy 3.89%, iowait 0.19%, 4 vCPU |

Создание VM и apt-предпосылки не входят в указанные длительности. Это один
прогон и снимок после него, не пики ресурсов, benchmark или доказательство
минимально необходимого размера. CPU усреднен по четырем vCPU.

## Матрица «требование → файл → команда → доказательство»

Команды исполняются из корня checkout после подготовки README. `pass` в
столбце «локально» означает только аудит/fixtures, не live-приемку.

| Требование | Реализация в репозитории | Команда | Доказательство / статус |
| --- | --- | --- | --- |
| Ubuntu 24.04, kubeadm, версия Kubernetes | `ansible/bootstrap.yaml`, `ansible/tasks/runtime.yaml`, `ansible/templates/kubeadm.yaml.j2`, `config/versions.yaml` | `make bootstrap ENV=ubuntu` | pass; `mvp-clean-vm.json`: pristine, run.stages, initial_verify |
| Node Ready, DNS и pod network | `ansible/tasks/health.yaml`, `ansible/templates/health.yaml.j2` | `make health ENV=ubuntu` | pass; bootstrap/health logs и readiness в четырех verify, реальный DNS/cross-pod HTTP Job |
| Публичные закрепленные зависимости, отсутствие homelab | `config/versions.yaml`, `config/images.lock.yaml`, `requirements.txt`, `scripts/stacklib.py` | `make tools`, `make lint`, `make bootstrap`, `make deploy` | pass; аудит URLs/lock и чистая загрузка без домашних services/credentials; публичный Git clone отложен |
| Приложение с точным ответом | `deploy/app/files/nginx.conf`, `deploy/app/templates/`, `scripts/check-gateway.py` | `make verify ENV=ubuntu`; curl из README | pass; четыре http checks + external_http: HTTP 200, 12 bytes, Request-ID |
| Настоящий маршрут Gateway API → Service | `deploy/gateway/templates/gateway.yaml`, `deploy/gateway/traefik-values.yaml` | `make verify ENV=ubuntu` | pass; gateway_conditions: current Accepted/Programmed/ResolvedRefs и внешний NodePort |
| Prometheus получает живые метрики | `deploy/monitoring/values.yaml`, `deploy/monitoring/extras/`, `scripts/check-metrics.py` | `make verify ENV=ubuntu`; `/api/v1/targets`, `/api/v1/query` из README | pass; metrics_baseline/delta: четыре targets up, свежие samples, 1→25 в первом verify |
| Fluentd доставляет access/error | `deploy/logging/files/fluent.conf`, `deploy/logging/templates/fluentd.yaml`, `scripts/check-logs.py` | `make verify ENV=ubuntu`; Loki query из README | pass; initial_verify.logs и последующие verify: exact access и отдельный stderr |
| Автоматическая установка, повтор без потери ресурсов | `Makefile`, `scripts/bootstrap.py`, `scripts/deploy.py`, `scripts/verify-idempotency.py` | Повтор `make bootstrap`; `make verify-idempotency` | pass; bootstrap_identity и idempotency JSON + два полных verify |
| Проверяемый отказ общего verify | `scripts/verify.py`, `scripts/check-*.py` | `make lint`; `make verify PROFILE=final`; `make verify ENV=missing-mvp06` | Локально pass: unsupported profile/missing env возвращают ненулевой код и fail JSON; негативные component fixtures pass |
| Runtime минимальные права приложения | `deploy/app/templates/deployment.yaml`, `scripts/verify.py` | `make verify ENV=ubuntu` | pass; runtime_security во всех verify: PID1 UID=101, CapEff=0, root ro, token absent |
| Persistent history и ограниченный buffer | `deploy/storage/`, `deploy/logging/`, `tests/e2e/mvp-recovery.py` | `make verify-recovery ENV=ubuntu` | Первый стенд pass: history Prometheus/Loki, offsets, пять buffered markers; на второй VM not run, реализация не менялась |
| Воспроизводимая документация и паспорт | `README.md`, `docs/contracts.md`, `docs/passport/mvp-draft.md` | Установка только по README, сверка матрицы | pass; замечания закрыты до чистого прогона; черновик сверен с исходным заданием |

## Что проверяет и чего не доказывает evidence

HTTP и access-log связаны точным Request-ID. HTTP counter — агрегат
фиксированного scope; Request-ID не становится metric/Loki label. Рост
counter может включать фоновый трафик. Reset обнаруживается по уменьшению
между наблюдаемыми raw samples; reset, целиком скрытый между scrape, этим
алгоритмом не исключается.

`verify-idempotency` сравнивает существующие прикладные Secrets, но не создает
искусственный secret ради проверки. UIDs/PVC bindings сами по себе не
доказывают сохранение содержимого: для истории есть отдельный recovery.
Зеленый fixture проверяет поведение проверяющего скрипта; действующий
Prometheus/Loki подтверждаются только API evidence живого стенда.

Retain/local PV не заменяют backup и HA. Два PV по 12Gi не задают квоты
файловой системы. Buffer Fluentd 256MiB и CRI retention ограничивают outage;
дубли допустимы, заполнение всего буфера и длительная ротация не тестировались.
Runtime check относится к Nginx, а не ко всем системным контейнерам.
Health после reboot запускается после завершения загрузки ОС и старта
служб: преждевременный вызов возвращает ошибку, а не скрывает неготовность.

## Замечания независимого аудита

| Наблюдение | Решение / статус |
| --- | --- |
| README использует curl, но не устанавливает его; список источников не называет PyPI/get.helm.sh/dl.k8s.io | Исправлено интегратором в `e920b1d`; исправленный текст проверен |
| План чистого прогона повторяет deploy, но не bootstrap | Добавлен отдельный repeat bootstrap после deploy с последующим health/verify |
| Monitoring semantic `check_render.py` не вызывается общим lint | Включен интегратором в `337d0c6`; независимый полный повтор lint pass |
| Формулировка README обещает отсутствие незамеченного reset | Исправлено в `337d0c6`: утверждение ограничено наблюдаемыми samples |
| HTTP counter query не ограничивает namespace/service/endpoint | Labels первого live baseline подтверждены; selector/README/контракт/fixture исправлены в `337d0c6` |
| Песочница запрещает loopback HTTP socket metrics fixture | Первый локальный lint fail по среде; повтор с escalation pass, ошибки не скрыты |

Все обязательные пункты MVP gate подтверждены. Незакрытых блокирующих
замечаний нет. Публичный анонимный clone, окончательный PDF и архив —
`not run`, поскольку FINAL-07 пока не назначен; завершение MVP не является
заявлением о завершенной сдаче.
