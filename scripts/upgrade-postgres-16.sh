#!/bin/bash

# Переход PostgreSQL 13 -> 16 с переносом данных.
#
# Каталоги данных мажорных версий PostgreSQL несовместимы: PG16 не стартует
# поверх данных, созданных PG13 ("database files are incompatible with server").
# Поэтому данные переносятся через дамп:
#   дамп контейнером СТАРОЙ версии -> копия volume -> чистый volume -> PG16 -> restore
#
# Дамп снимается отдельным контейнером старой версии, а не через docker-compose:
# в compose образ уже переключён на 16, и он просто не поднимется на данных 13.
#
# Использование: bash scripts/upgrade-postgres-16.sh
#   AUTO_YES=1        - не спрашивать подтверждение (для CI/деплоя)
#   SKIP_APP_START=1  - не поднимать бота в конце (нужно локально: polling с боевым
#                       токеном перехватит апдейты у продакшен-экземпляра;
#                       в деплое приложение поднимается следующим шагом)
#
# Скрипт идемпотентен и умеет доводить до конца прерванную миграцию: если данные
# уже в формате 16, но база пустая, а в backups/ лежит дамп - он восстановит его.
# Поэтому его безопасно вызывать при каждом деплое.
#
# Откат: см. инструкцию, которую скрипт печатает в конце.

set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

info()  { echo -e "${YELLOW}$*${NC}"; }
ok()    { echo -e "${GREEN}$*${NC}"; }
fail()  { echo -e "${RED}$*${NC}"; exit 1; }

if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
else
    fail "❌ Не найден ни 'docker compose', ни 'docker-compose'"
fi

cd "$(dirname "$0")/.."

if [ -f .env ]; then
    set -a; . ./.env; set +a
fi

DB_USER="${POSTGRES_USER:-user}"
DB_PASS="${POSTGRES_PASSWORD:-password}"
DB_NAME="${POSTGRES_DB:-training_bot}"
PROJECT="${COMPOSE_PROJECT_NAME:-$(basename "$PWD")}"
VOLUME="${POSTGRES_VOLUME:-${PROJECT}_postgres_data}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_DIR="${BACKUP_DIR:-./backups}"
DUMP_FILE="${BACKUP_DIR}/pre_pg16_upgrade_${TIMESTAMP}.sql"
VOLUME_BACKUP="${VOLUME}_pg13_${TIMESTAMP}"
TMP_OLD="pg_upgrade_src_$$"

mkdir -p "$BACKUP_DIR"

cleanup_tmp() { docker rm -f "$TMP_OLD" >/dev/null 2>&1 || true; }
trap cleanup_tmp EXIT

docker volume inspect "$VOLUME" >/dev/null 2>&1 \
    || fail "❌ Volume '$VOLUME' не найден. Задайте POSTGRES_VOLUME=<имя> и повторите."

# Считаем именно COUNT(*), а не n_live_tup: последнее - оценка планировщика,
# она бывает устаревшей и даёт ложное расхождение до/после.
COUNT_QUERY="
SELECT string_agg(format('%s=%s', tbl, cnt), ',' ORDER BY tbl) FROM (
  SELECT c.relname AS tbl,
         (xpath('/row/c/text()',
                query_to_xml(format('SELECT count(*) AS c FROM %I.%I', n.nspname, c.relname),
                             false, true, '')))[1]::text::bigint AS cnt
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE c.relkind = 'r' AND n.nspname = 'public'
) t;"

TABLE_COUNT_QUERY="
SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r' AND n.nspname = 'public';"

UPPER_QUERY="
SELECT count(*) FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
WHERE t.typname IN ('jerseytype','positiontype') AND e.enumlabel <> lower(e.enumlabel);"

# Ждём, пока БД РЕАЛЬНО отвечает на запрос, а не просто слушает сокет.
#
# Одного pg_isready мало - он был причиной падения деплоя 2026-09-07:
# docker-entrypoint во время initdb поднимает временный сервер ДО того, как
# создаст POSTGRES_DB, и pg_isready на нём уже отвечает "accepting connections".
# Скрипт шёл дальше и падал на 'database "training_bot" does not exist'.
# Плюс сразу после создания БД временный сервер останавливается перед запуском
# настоящего - там есть второе окно отказа. Поэтому требуем три успешных
# SELECT 1 подряд с паузой: это гарантированно перекрывает оба окна.
wait_db_ready() {
    local streak=0 i
    for i in $(seq 1 120); do
        if "$@" -tAc 'SELECT 1' >/dev/null 2>&1; then
            streak=$((streak + 1))
            [ "$streak" -ge 3 ] && return 0
        else
            streak=0
        fi
        sleep 1
    done
    return 1
}

compose_psql()  { $COMPOSE exec -T db psql -U "$DB_USER" -d "$DB_NAME" "$@"; }
compose_query() { compose_psql -At -c "$1" | tr -d '\r'; }

validate_dump() {
    local f="$1"
    [ -s "$f" ] || fail "❌ Дамп пустой: $f"
    grep -q "CREATE TABLE" "$f" || fail "❌ В дампе нет CREATE TABLE: $f"
}

restore_dump() {
    local f="$1"
    info "📥 Восстановление дампа: $f"
    compose_psql -v ON_ERROR_STOP=1 -q < "$f" \
        || fail "❌ Ошибка восстановления из $f"
    compose_psql -c "ANALYZE;" >/dev/null
}

# Приведение enum к нижнему регистру.
# В части баз positiontype/jerseytype хранят метки в ВЕРХНЕМ регистре (FORWARD,
# DEFENDER), а модели ожидают значения (forward, defender). Python-фолбэк
# _EnumByValueOrName на PostgreSQL не срабатывает: SQLAlchemy адаптирует Enum к
# postgresql.ENUM и теряет подкласс. Из-за этого список участников падает с
# LookupError. Чиним данными - тем же скриптом, что лежит в репозитории.
fix_enum_case() {
    local upper
    upper=$(compose_query "$UPPER_QUERY")
    if [ "${upper:-0}" != "0" ]; then
        info "🔧 Меток enum в верхнем регистре: ${upper}. Применяем fix_enum_lowercase.sql..."
        compose_psql -v ON_ERROR_STOP=1 -q < scripts/fix_enum_lowercase.sql \
            || fail "❌ Не удалось привести enum к нижнему регистру"
        local still
        still=$(compose_query "$UPPER_QUERY")
        [ "${still:-1}" = "0" ] || fail "❌ Метки в верхнем регистре остались (${still})"
        ok "✅ enum приведены к нижнему регистру"
    else
        ok "✅ enum уже в нижнем регистре, правка не требуется"
    fi
}

start_app_if_needed() {
    if [ "${SKIP_APP_START:-0}" = "1" ]; then
        info "⏭️  SKIP_APP_START=1 — бот не запускается, поднята только БД"
    else
        info "🚀 Запуск приложения..."
        $COMPOSE up -d
    fi
}

# --- 1. Текущая версия данных.
# Пустая строка = volume пустой (свежая установка), инициализацию сделает compose.
CURRENT=$(docker run --rm -v "${VOLUME}":/d alpine cat /d/PG_VERSION 2>/dev/null | tr -d '[:space:]' || true)

if [ -z "$CURRENT" ]; then
    info "📊 Volume '$VOLUME' пуст — свежая установка"
else
    info "📊 Текущая версия данных: PostgreSQL ${CURRENT}"
fi

# --- 2. Данные уже в формате 16 (или volume пуст).
# Отдельно проверяем, что база НЕ пустая: миграция могла оборваться между
# очисткой volume и восстановлением дампа. Тогда PG_VERSION уже 16, но данных нет,
# и наивный "миграция не требуется" поднял бы приложение на пустой базе.
if [ -z "$CURRENT" ] || [ "$CURRENT" = "16" ]; then
    info "🚀 Запуск PostgreSQL 16 для проверки состояния базы..."
    $COMPOSE up -d db
    wait_db_ready $COMPOSE exec -T db psql -U "$DB_USER" -d "$DB_NAME" \
        || { $COMPOSE logs db 2>&1 | tail -10; fail "❌ PostgreSQL 16 не отвечает"; }

    TABLES=$(compose_query "$TABLE_COUNT_QUERY")
    if [ "${TABLES:-0}" != "0" ]; then
        ok "✅ Данные уже в формате PostgreSQL 16 (таблиц: ${TABLES}), миграция не требуется"
        exit 0
    fi

    LATEST_DUMP=$(ls -1t "${BACKUP_DIR}"/pre_pg16_upgrade_*.sql 2>/dev/null | head -1 || true)

    if [ -z "$LATEST_DUMP" ]; then
        ok "✅ База пуста и дампов нет — чистая установка, схему создаст приложение"
        exit 0
    fi

    echo
    info "⚠️  База PostgreSQL 16 ПУСТАЯ, но найден дамп предыдущей миграции."
    info "    Похоже, миграция оборвалась после очистки volume. Доводим до конца."
    validate_dump "$LATEST_DUMP"
    restore_dump "$LATEST_DUMP"
    fix_enum_case

    RESTORED=$(compose_query "$COUNT_QUERY")
    echo "   строк после восстановления: ${RESTORED:-(пусто)}"
    [ -n "$RESTORED" ] || fail "❌ После восстановления база всё ещё пуста"

    start_app_if_needed
    echo
    ok "✅ Прерванная миграция завершена, данные восстановлены"
    exit 0
fi

# --- 3. Полная миграция со старой версии.
echo
info "⚠️  Будет выполнено:"
echo "   1. Дамп базы '${DB_NAME}' контейнером postgres:${CURRENT}-alpine"
echo "   2. Копия volume '${VOLUME}' -> '${VOLUME_BACKUP}' (для отката)"
echo "   3. Очистка '${VOLUME}' и запуск PostgreSQL 16"
echo "   4. Восстановление, приведение enum к нижнему регистру, сверка строк"
echo
if [ "${AUTO_YES:-0}" = "1" ]; then
    info "🤖 AUTO_YES=1 — подтверждение пропущено (автоматический деплой)"
else
    read -r -p "Продолжить? (yes/no): " confirm
    [ "$confirm" = "yes" ] || { info "Отменено"; exit 0; }
fi

# --- 3a. Дамп контейнером СТАРОЙ версии
info "🛑 Останавливаем стек..."
$COMPOSE down >/dev/null 2>&1 || true

info "🚀 Временный postgres:${CURRENT}-alpine на боевом volume..."
docker run -d --name "$TMP_OLD" \
    -v "${VOLUME}":/var/lib/postgresql/data \
    -e POSTGRES_USER="$DB_USER" -e POSTGRES_PASSWORD="$DB_PASS" -e POSTGRES_DB="$DB_NAME" \
    "postgres:${CURRENT}-alpine" >/dev/null

wait_db_ready docker exec "$TMP_OLD" psql -U "$DB_USER" -d "$DB_NAME" \
    || { docker logs "$TMP_OLD" 2>&1 | tail -10; fail "❌ PostgreSQL ${CURRENT} не поднялся. Данные не тронуты."; }
ok "✅ Исходная БД поднята"

info "📸 Количество строк до миграции..."
COUNTS_BEFORE=$(docker exec "$TMP_OLD" psql -U "$DB_USER" -d "$DB_NAME" -At -c "$COUNT_QUERY" | tr -d '\r')
echo "   до: ${COUNTS_BEFORE:-(пусто)}"

info "📦 Создание дампа..."
docker exec "$TMP_OLD" pg_dump -U "$DB_USER" --clean --if-exists "$DB_NAME" > "$DUMP_FILE"
validate_dump "$DUMP_FILE"
ok "✅ Дамп: ${DUMP_FILE} ($(du -h "$DUMP_FILE" | cut -f1))"

docker rm -f "$TMP_OLD" >/dev/null

# --- 3b. Копия старого volume для мгновенного отката
info "🗄️  Копия старого volume -> ${VOLUME_BACKUP}..."
docker volume create "$VOLUME_BACKUP" >/dev/null
docker run --rm -v "${VOLUME}":/from:ro -v "${VOLUME_BACKUP}":/to \
    alpine sh -c 'cd /from && cp -a . /to/'
ok "✅ Откат возможен из volume '${VOLUME_BACKUP}'"

# --- 3c. Чистый volume под PG16
info "🧹 Очистка volume '${VOLUME}'..."
docker volume rm "$VOLUME" >/dev/null
docker volume create "$VOLUME" >/dev/null

info "🚀 Запуск PostgreSQL 16..."
$COMPOSE up -d db
wait_db_ready $COMPOSE exec -T db psql -U "$DB_USER" -d "$DB_NAME" \
    || { $COMPOSE logs db 2>&1 | tail -10; fail "❌ PostgreSQL 16 не поднялся. Откат: см. '${VOLUME_BACKUP}'"; }

NEW_VERSION=$(compose_query "SHOW server_version;")
ok "✅ Поднялся PostgreSQL ${NEW_VERSION}"

# --- 3d. Восстановление
restore_dump "$DUMP_FILE"
fix_enum_case

COUNTS_AFTER=$(compose_query "$COUNT_QUERY")
echo "   после: ${COUNTS_AFTER:-(пусто)}"

if [ "$COUNTS_BEFORE" = "$COUNTS_AFTER" ]; then
    ok "✅ Количество строк совпадает по всем таблицам"
else
    echo -e "${RED}⚠️  Количество строк РАЗЛИЧАЕТСЯ:${NC}"
    echo "   до:    ${COUNTS_BEFORE}"
    echo "   после: ${COUNTS_AFTER}"
    echo -e "${RED}   Проверьте данные вручную перед запуском бота.${NC}"
fi

start_app_if_needed

echo
ok "✅ Миграция на PostgreSQL 16 завершена"
echo
[ "${SKIP_APP_START:-0}" = "1" ] || info "Проверьте: curl -f http://localhost:${BOT_PORT:-5000}/health"
echo
info "Если что-то пошло не так — откат на PostgreSQL ${CURRENT}:"
echo "   1. Верните в docker-compose.yml: image: postgres:${CURRENT}-alpine"
echo "   2. ${COMPOSE} down"
echo "   3. docker volume rm ${VOLUME} && docker volume create ${VOLUME}"
echo "   4. docker run --rm -v ${VOLUME_BACKUP}:/from:ro -v ${VOLUME}:/to alpine sh -c 'cd /from && cp -a . /to/'"
echo "   5. ${COMPOSE} up -d"
echo
info "Когда убедитесь, что всё работает, освободите место:"
echo "   docker volume rm ${VOLUME_BACKUP}"
