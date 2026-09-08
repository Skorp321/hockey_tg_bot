#!/bin/bash

# Применяет SQL-миграции из scripts/migrations/ по порядку имён.
#
# Зачем это вообще нужно: init_models() зовёт Base.metadata.create_all(checkfirst=True),
# который создаёт отсутствующие ТАБЛИЦЫ, но никогда не добавляет колонки к существующим
# и не меняет enum-типы. То есть новые таблицы приезжают сами, а всё остальное - нет.
#
# Применённые файлы записываются в таблицу schema_migrations и повторно не запускаются.
# Сами файлы всё равно пишутся идемпотентными: если процесс упал между применением и
# записью в реестр, повторный запуск должен пройти без вреда.
#
# Использование:
#   bash scripts/run-migrations.sh
#     DIRECT_PSQL=1   - ходить напрямую psql "$DATABASE_URL" вместо docker compose exec
#                       (нужно на изолированных стендах и в тестах)
#
# Порядок в деплое важен: миграции применяются, когда БД поднята, а приложение остановлено.
# Иначе старый код будет читать enum, из которого уже убраны значения.

set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

info() { echo -e "${YELLOW}$*${NC}"; }
ok()   { echo -e "${GREEN}$*${NC}"; }
fail() { echo -e "${RED}$*${NC}"; exit 1; }

cd "$(dirname "$0")/.."

if [ -f .env ]; then
    # Уже заданные переменные окружения имеют приоритет над .env — та же семантика,
    # что у python-dotenv в приложении. Без этого нельзя направить скрипт на стенд.
    _preset_db_url="${DATABASE_URL:-}"
    _preset_db_user="${POSTGRES_USER:-}"
    _preset_db_name="${POSTGRES_DB:-}"
    set -a; . ./.env; set +a
    [ -n "$_preset_db_url" ]  && DATABASE_URL="$_preset_db_url"
    [ -n "$_preset_db_user" ] && POSTGRES_USER="$_preset_db_user"
    [ -n "$_preset_db_name" ] && POSTGRES_DB="$_preset_db_name"
fi

DB_USER="${POSTGRES_USER:-user}"
DB_NAME="${POSTGRES_DB:-training_bot}"
MIGRATIONS_DIR="${MIGRATIONS_DIR:-scripts/migrations}"

if [ "${DIRECT_PSQL:-0}" = "1" ]; then
    [ -n "${DATABASE_URL:-}" ] || fail "❌ DIRECT_PSQL=1 требует DATABASE_URL"
    # psql не понимает +asyncpg в схеме - он там только для SQLAlchemy
    PSQL_URL=$(echo "$DATABASE_URL" | sed 's/+asyncpg//; s/+psycopg2//')
    run_psql() { psql "$PSQL_URL" "$@"; }
else
    if docker compose version >/dev/null 2>&1; then
        COMPOSE="docker compose"
    elif command -v docker-compose >/dev/null 2>&1; then
        COMPOSE="docker-compose"
    else
        fail "❌ Не найден ни 'docker compose', ни 'docker-compose'"
    fi
    run_psql() { $COMPOSE exec -T db psql -U "$DB_USER" -d "$DB_NAME" "$@"; }
fi

query() { run_psql -At -c "$1" | tr -d '\r'; }

[ -d "$MIGRATIONS_DIR" ] || { ok "✅ Каталога '$MIGRATIONS_DIR' нет — миграций не требуется"; exit 0; }

# --- реестр применённых миграций.
# Форма намеренно совместима с Alembic (filename ~ version_num), чтобы потом было
# несложно перейти на него через alembic stamp.
run_psql -v ON_ERROR_STOP=1 -q <<'SQL' || fail "❌ Не удалось создать таблицу schema_migrations"
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
SQL

shopt -s nullglob
FILES=("$MIGRATIONS_DIR"/*.sql)
shopt -u nullglob

if [ ${#FILES[@]} -eq 0 ]; then
    ok "✅ Миграций не найдено"
    exit 0
fi

applied_count=0
skipped_count=0

for f in $(printf '%s\n' "${FILES[@]}" | sort); do
    name=$(basename "$f")
    already=$(query "SELECT 1 FROM schema_migrations WHERE filename = '${name//\'/\'\'}';")

    if [ "$already" = "1" ]; then
        skipped_count=$((skipped_count + 1))
        continue
    fi

    info "▶️  Применяем ${name}..."
    run_psql -v ON_ERROR_STOP=1 -q < "$f" \
        || fail "❌ Миграция ${name} не применилась. Остальные не запускались."

    run_psql -v ON_ERROR_STOP=1 -q \
        -c "INSERT INTO schema_migrations (filename) VALUES ('${name//\'/\'\'}');" \
        || fail "❌ ${name} применилась, но не записалась в реестр. Повторите запуск."

    ok "✅ ${name}"
    applied_count=$((applied_count + 1))
done

echo
ok "✅ Миграции завершены: применено ${applied_count}, пропущено (уже были) ${skipped_count}"
