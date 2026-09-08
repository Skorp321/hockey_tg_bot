"""Единственное место, где известен формат списка состава.

Модуль используют и бот (сообщение в канале), и админ-панель (предпросмотр), поэтому
разъехаться они не могут. Лежит в корне пакета, а не в bot/ или web/, потому что
app/web/routes.py уже импортирует из app/bot — обратное направление создало бы цикл.

`render_roster_text` намеренно чистая: никакого ввода-вывода и никакой сессии, чтобы
формат можно было закрепить golden-тестом против настоящего примера.

Разметка (`parse_mode`) не используется. Фамилии приходят от людей, `escape_markdown`
в handlers.py экранирует только `_ * [` и не трогает `] ( )`, а мы дописываем литеральное
«(А)». Обычный текст снимает целый класс ошибок разметки, ничего не теряя визуально.
"""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Optional

from sqlalchemy import select

from .models import (
    JerseyType, PositionType, Player, Registration, SeasonPass, Training, UserPreferences,
)

# --- Внешний вид -----------------------------------------------------------------

GOALKEEPER_SQUARE = "🔲"

JERSEY_SQUARE = {
    JerseyType.LIGHT: "⬜️",
    JerseyType.YELLOW: "🟨",
    JerseyType.DARK: "⬛️",
    JerseyType.RED: "🟥",
    JerseyType.BLUE: "🟦",
}

# Порядок групп и порядок внутри группы берутся из порядка объявления в models.py.
JERSEY_ORDER = list(JerseyType)
POSITION_RANK = {position: index for index, position in enumerate(PositionType)}

POSITION_LABELS = {
    PositionType.LW: "ЛН",
    PositionType.C: "Ц",
    PositionType.RW: "ПН",
    PositionType.LD: "ЛЗ",
    PositionType.RD: "ПЗ",
}

JERSEY_LABELS = {
    JerseyType.LIGHT: "Белый",
    JerseyType.YELLOW: "Жёлтый",
    JerseyType.DARK: "Чёрный",
    JerseyType.RED: "Красный",
    JerseyType.BLUE: "Синий",
}

# Своим списком, а не strftime('%A'): в контейнере локаль C, и там был бы английский.
WEEKDAYS_RU = [
    "Понедельник", "Вторник", "Среда", "Четверг",
    "Пятница", "Суббота", "Воскресенье",
]

MONTHS_RU = [
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]

MARK_PASS = "(А)"
MARK_REGISTERED = "✅"
MARK_PAID = "₽"

DEFAULT_RESERVE_SLOTS = 3

# Telegram считает длину в кодовых единицах UTF-16, а не в символах: каждый цветной
# квадрат стоит две единицы. Держим запас от жёсткого лимита 4096, потому что BadRequest
# при отправке тихо остановит все последующие обновления сообщения.
TELEGRAM_HARD_LIMIT = 4096
TELEGRAM_SAFE_LIMIT = 4000


@dataclass
class RosterLine:
    square: str
    name: str
    has_pass: bool = False
    registered: bool = False
    paid: bool = False


@dataclass
class RosterView:
    date_line: str
    time_line: str = ""
    venue: Optional[str] = None
    deadline_text: Optional[str] = None
    groups: list = field(default_factory=list)   # список групп, каждая — список RosterLine
    reserve: list = field(default_factory=list)  # список RosterLine
    reserve_slots: int = DEFAULT_RESERVE_SLOTS
    warnings: list = field(default_factory=list)


# --- Отрисовка -------------------------------------------------------------------

def telegram_length(text: str) -> int:
    """Длина в кодовых единицах UTF-16 — именно так её считает Telegram."""
    return len(text.encode("utf-16-le")) // 2


def format_date_line(moment: datetime) -> str:
    """«8.09 Вторник» — день без ведущего нуля, месяц с ним."""
    return f"{moment.day}.{moment.month:02d} {WEEKDAYS_RU[moment.weekday()]}"


def format_time_line(start: datetime, end: Optional[time] = None) -> str:
    """«21.30 - 23.00» — разделитель точка, а не двоеточие."""
    line = f"{start.hour:02d}.{start.minute:02d}"
    if end is not None:
        line += f" - {end.hour:02d}.{end.minute:02d}"
    return line


def render_line(line: RosterLine) -> str:
    text = f"{line.square}{line.name}"
    if line.has_pass:
        text += f" {MARK_PASS}"
    if line.registered:
        text += f" {MARK_REGISTERED}"
    if line.paid:
        text += f" {MARK_PAID}"
    return text


def _compose(view: RosterView, reserve: list, reserve_slots: int, note: Optional[str]) -> str:
    lines = []

    if view.deadline_text:
        lines.append(f"❗️{view.deadline_text}")
        lines.append("")

    lines.append(view.date_line)
    if view.time_line:
        lines.append(view.time_line)
    if view.venue:
        lines.append(view.venue)

    for group in view.groups:
        if not group:
            continue
        lines.append("")
        lines.extend(render_line(item) for item in group)

    lines.append("")
    lines.append("")
    lines.append("Резерв:")
    lines.append("")

    position = 0
    for item in reserve:
        position += 1
        lines.append(f"{position}. {render_line(item)}")
    while position < reserve_slots:
        position += 1
        lines.append(f"{position}.")

    if note:
        lines.append("")
        lines.append(note)

    # Хвостовые пробелы в исходном примере — артефакт копирования, не формат.
    return "\n".join(line.rstrip() for line in lines)


def _hard_clamp(text: str) -> str:
    """Гарантирует, что результат влезет в лимит: срезает строки с конца."""
    note = "…список не помещается в одно сообщение"
    budget = TELEGRAM_SAFE_LIMIT - telegram_length(note) - 1
    lines = text.split("\n")
    while lines and telegram_length("\n".join(lines)) > budget:
        lines.pop()
    return "\n".join(lines + [note])


def render_roster_text(view: RosterView) -> str:
    """Собирает текст сообщения, укладываясь в лимит Telegram.

    При переполнении сначала исчезают пустые слоты резерва, затем усекается сам резерв —
    но никогда не режутся группы состава: список-чеклист без части фамилий бесполезен.
    """
    text = _compose(view, view.reserve, view.reserve_slots, None)
    if telegram_length(text) <= TELEGRAM_SAFE_LIMIT:
        return text

    text = _compose(view, view.reserve, 0, None)
    if telegram_length(text) <= TELEGRAM_SAFE_LIMIT:
        view.warnings.append("Пустые слоты резерва убраны: сообщение близко к лимиту Telegram")
        return text

    reserve = list(view.reserve)
    while reserve:
        dropped = len(view.reserve) - len(reserve) + 1
        reserve.pop()
        note = f"…и ещё {dropped} в резерве"
        text = _compose(view, reserve, 0, note)
        if telegram_length(text) <= TELEGRAM_SAFE_LIMIT:
            view.warnings.append(f"Резерв усечён на {dropped}: сообщение упирается в лимит Telegram")
            return text

    # Последний рубеж: даже без резерва не помещается. Резать состав не хочется, но
    # отдать сообщение длиннее 4096 нельзя — Telegram ответит BadRequest, и это тихо
    # остановит все последующие обновления. Обрезанный список хуже полного, но лучше
    # сломанного.
    view.warnings.append("Состав не помещается в одно сообщение Telegram и обрезан")
    return _hard_clamp(_compose(view, [], 0, None))


# --- Сборка из базы --------------------------------------------------------------

def period_start_for(moment: datetime):
    """Первое число месяца, к которому относится дата — ключ абонемента."""
    return moment.date().replace(day=1)


def _month_bounds(year: int, month: int):
    start = datetime(year, month, 1)
    end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    return start, end


async def first_training_of_month(session, year: int, month: int):
    start, end = _month_bounds(year, month)
    return (await session.execute(
        select(Training.date_time)
        .where(Training.date_time >= start)
        .where(Training.date_time < end)
        .order_by(Training.date_time)
        .limit(1)
    )).scalars().first()


async def pass_window(session, now: Optional[datetime] = None, open_days_before: int = 3):
    """Открыто ли сейчас окно покупки абонемента.

    Возвращает (period_start, first_training) либо None.

    Окно: от «первая тренировка месяца минус N дней» до её начала. Проверяем текущий
    месяц и следующий: в конце месяца первая тренировка следующего может оказаться
    ближе, чем через три дня, и окно должно открыться уже тогда.
    """
    now = now or datetime.now()

    candidates = [(now.year, now.month)]
    if now.month == 12:
        candidates.append((now.year + 1, 1))
    else:
        candidates.append((now.year, now.month + 1))

    for year, month in candidates:
        first = await first_training_of_month(session, year, month)
        if first is None:
            continue
        opens_at = first - timedelta(days=open_days_before)
        if opens_at <= now < first:
            return first.date().replace(day=1), first
    return None


async def has_season_pass(session, user_id: int, period_start) -> bool:
    row = (await session.execute(
        select(SeasonPass.id)
        .where(SeasonPass.user_id == user_id)
        .where(SeasonPass.period_start == period_start)
    )).scalars().first()
    return row is not None


def _display_name(player: Player, prefs: Optional[UserPreferences]) -> str:
    if prefs is not None and prefs.display_name:
        return prefs.display_name
    return player.display_name or player.username or "Без имени"


async def build_roster_view(session, training: Training, settings=None) -> RosterView:
    """Собирает представление списка: весь ввод-вывод сосредоточен здесь."""
    settings = settings or {}

    registrations = (await session.execute(
        select(Registration).where(Registration.training_id == training.id)
    )).scalars().all()
    registration_by_user = {r.user_id: r for r in registrations}

    roster_players = (await session.execute(
        select(Player).where(Player.is_roster_member.is_(True)).order_by(Player.id)
    )).scalars().all()

    user_ids = {p.user_id for p in roster_players} | set(registration_by_user)
    prefs_by_user = {}
    if user_ids:
        prefs_rows = (await session.execute(
            select(UserPreferences).where(UserPreferences.user_id.in_(user_ids))
        )).scalars().all()
        prefs_by_user = {p.user_id: p for p in prefs_rows}

    period = period_start_for(training.date_time)
    pass_holders = set()
    if user_ids:
        pass_holders = set((await session.execute(
            select(SeasonPass.user_id)
            .where(SeasonPass.period_start == period)
            .where(SeasonPass.user_id.in_(user_ids))
        )).scalars().all())

    view = RosterView(
        date_line=format_date_line(training.date_time),
        time_line=format_time_line(training.date_time, training.end_time),
        venue=training.venue or settings.get("roster.default_venue"),
        deadline_text=training.signup_deadline_text or settings.get("roster.default_deadline_text"),
        reserve_slots=int(settings.get("roster.reserve_slots", DEFAULT_RESERVE_SLOTS)),
    )

    goalkeepers = []
    by_jersey = {jersey: [] for jersey in JERSEY_ORDER}
    unassigned = []

    for player in roster_players:
        prefs = prefs_by_user.get(player.user_id)
        registration = registration_by_user.get(player.user_id)
        is_goalkeeper = prefs.goalkeeper if prefs is not None else player.goalkeeper
        has_pass = player.user_id in pass_holders
        name = _display_name(player, prefs)

        line = RosterLine(
            square=GOALKEEPER_SQUARE,
            name=name,
            has_pass=has_pass,
            # Галочка означает «человек подтвердил, что придёт», а не «есть строка
            # в базе»: запись мог создать администратор, внося состав руками.
            registered=bool(registration is not None and registration.self_registered),
            # Абонемент закрывает оплату всех тренировок месяца, поэтому ₽ ставится
            # и тогда, когда флаг в регистрации почему-то не проставился.
            paid=bool(registration is not None and (registration.paid or has_pass)),
        )

        if is_goalkeeper:
            goalkeepers.append((player.id, line))
            continue

        jersey = prefs.preferred_jersey_type if prefs is not None else None
        position = prefs.preferred_position_type if prefs is not None else None

        if jersey is None:
            view.warnings.append(f"{name}: не задан цвет")
            line.square = "▫️"
            unassigned.append((player.id, line))
            continue
        if position is None:
            view.warnings.append(f"{name}: не задано амплуа")

        line.square = JERSEY_SQUARE[jersey]
        rank = POSITION_RANK.get(position, len(POSITION_RANK))
        by_jersey[jersey].append((rank, player.id, line))

    # Тай-брейк по players.id, а не по фамилии: переименование не должно переставлять
    # строки в уже опубликованном сообщении.
    goalkeepers.sort(key=lambda item: item[0])
    view.groups.append([line for _, line in goalkeepers])
    for jersey in JERSEY_ORDER:
        group = sorted(by_jersey[jersey], key=lambda item: (item[0], item[1]))
        view.groups.append([line for _, _, line in group])
    unassigned.sort(key=lambda item: item[0])
    view.groups.append([line for _, line in unassigned])

    roster_user_ids = {p.user_id for p in roster_players}
    reserve_registrations = [
        r for r in registrations if r.user_id not in roster_user_ids
    ]
    # Резерв — очередь, поэтому порядок по времени записи.
    reserve_registrations.sort(key=lambda r: (r.registered_at or datetime.min, r.id))
    for registration in reserve_registrations:
        prefs = prefs_by_user.get(registration.user_id)
        name = (
            registration.display_name
            or (prefs.display_name if prefs is not None else None)
            or registration.username
            or "Без имени"
        )
        has_pass = registration.user_id in pass_holders
        view.reserve.append(RosterLine(
            square="",
            name=name,
            has_pass=has_pass,
            registered=bool(registration.self_registered),
            paid=bool(registration.paid or has_pass),
        ))

    return view
