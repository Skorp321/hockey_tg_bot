# Исправление ошибки 'LIGHT' is not among the defined enum values на проде

## Причина

В БД (PostgreSQL) enum-значения могут храниться или возвращаться как **имена** (`LIGHT`, `DARK`, `FORWARD`), а код по умолчанию ожидает только **значения** (`light`, `dark`, `forward`). Из-за этого при чтении регистраций/напоминаний возникает `LookupError`.

## Что сделано в коде

В `app/models.py` тип `_EnumByValueOrName` при чтении из БД принимает и значение, и имя:

- сначала ищет по значению (`light`, `dark`, …);
- если не найдено — по имени enum (`LIGHT`, `DARK`, …).

Используются колонки: `jersey_type`, `position_type`, `preferred_jersey_type`, `preferred_position_type`.

## Что сделать на удалённой машине

1. **Обновить код на сервере** (через git или копирование файлов):
   - обязательно обновить `app/models.py` (класс `_EnumByValueOrName` и использование `_enum_by_value_or_name(JerseyType)` / `_enum_by_value_or_name(PositionType)` для нужных колонок).

2. **Перезапустить приложение** (чтобы подтянулся новый код):
   ```bash
   # Если через Docker:
   docker compose down
   docker compose up -d

   # Или перезапуск процесса (systemd/supervisor):
   sudo systemctl restart hockey   # или как у вас называется сервис
   ```

3. **Проверить** — снова вызвать действие, которое падало (напоминания об оплате, регистрация на тренировку). Ошибка `'LIGHT' is not among the defined enum values` должна пропасть.

Дополнительные миграции БД или правки данных не требуются: старые значения в БД (по имени или по значению) после обновления кода будут читаться корректно.
