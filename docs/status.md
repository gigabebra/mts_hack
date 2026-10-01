# Состояние MVP

Текущая работа: согласованный dual-stack Kubernetes с IPv6 основным. Код и чистая
приемка нового режима в процессе; таблица ниже фиксирует первоначальный IPv4 MVP.
Прежняя strict IPv6-only приемка прекращена после изменения требования; ее
промежуточные успехи не переносятся на dual-stack. См. [сеть](networking.md).

| ID | Статус | Владелец | Результат |
| --- | --- | --- | --- |
| MVP-00 | done | интегратор | Контракт, 20 image digests, checksum downloads, доступ к отдельным VM определен |
| MVP-01 | done | инфраструктура | Live integration pass; см. docs/reports/MVP-01.md |
| MVP-02 | done | Gateway | Live integration pass; см. docs/reports/MVP-02.md |
| MVP-03 | done | метрики | Live integration pass; см. docs/reports/MVP-03.md |
| MVP-04 | done | логирование | Live integration pass; см. docs/reports/MVP-04.md |
| MVP-05 | done | интегратор | Live integration pass; см. docs/reports/MVP-05.md |
| MVP-06 | done | независимая приемка | MVP accepted, код `337d0c6`; чистая Ubuntu, 10 стадий и 4 verify pass; docs/acceptance/mvp-acceptance.md |

FINAL не назначен. Live-проверки и изменения общей VM выполняет интегратор.

Принятая кодовая ревизия: `337d0c671617929ebf162e1216099524fe8d611d`.
Это историческая приемка исходного IPv4 MVP. Новый сетевой режим требует новой
чистой установки и независимого заключения. Прежние VM и evidence сохраняются.
