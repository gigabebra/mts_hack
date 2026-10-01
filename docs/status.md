# Состояние MVP

| ID | Статус | Владелец | Результат |
| --- | --- | --- | --- |
| MVP-00 | done | интегратор | Контракт, 20 image digests, checksum downloads, доступ к отдельным VM определен |
| MVP-01 | ready_for_integration | инфраструктура | Первый/повтор bootstrap и reboot pass; ожидается PVC binding |
| MVP-02 | ready_for_integration | Gateway | Ветка объединена; Helm/контейнерные проверки pass |
| MVP-03 | ready_for_integration | метрики | Ветка объединена; render/API failure fixtures pass |
| MVP-04 | ready_for_integration | логирование | Ветка объединена; реальный Docker ingestion/recovery pass |
| MVP-05 | in_progress | интегратор | Общие deploy/verify/evidence и README; live проверка продолжается |
| MVP-06 | todo | независимая приемка | — |

FINAL не назначен. Live-проверки и изменения общей VM выполняет интегратор.
