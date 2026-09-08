"""Golden-тест формата списка состава.

EXPECTED — дословно тот пример, который дал заказчик (с убранными хвостовыми пробелами:
они артефакт копирования). Именно этот тест держит все мелочи, на которых легко ошибиться:
день без ведущего нуля и месяц с ним, точка во времени вместо двоеточия, русский день
недели, порядок групп, пустые строки между ними и padding резерва.
"""

from datetime import datetime, time

from app.models import JerseyType
from app.roster import (
    GOALKEEPER_SQUARE, JERSEY_SQUARE, RosterLine, RosterView,
    TELEGRAM_HARD_LIMIT, format_date_line, format_time_line,
    render_roster_text, telegram_length,
)

EXPECTED = """❗️Просьба записаться до 22:00 понедельника

8.09 Вторник
21.30 - 23.00
Арена Айс Атлетикс

🔲Дячук Сергей
🔲Желтоножский Сергей
🔲Златоверховников Марк
🔲Проскуряков Михаил

⬜️Пыркин Дмитрий (А)
⬜️Бобков Андрей
⬜️Панкратов Максим
⬜️Ковалёв Павел (А)
⬜️Чужаков Андрей (А)

🟨Иванов Александр (А)
🟨Найденко Алексей (А)
🟨Гордеев Денис (А)
🟨Меньшенин Кирилл
🟨Хоменко Андрей (А)

⬛️Яшков Алексей
⬛️Григорьев Сергей
⬛️Судьин Евгений
⬛️Любимов Павел (А)
⬛️Лютиков Андрей

🟥Потапов Сергей (А)
🟥Новиков Илья
🟥Хромышев Александр
🟥Толстых Максим (А)
🟥Боровской Вячеслав (А)

🟦Маркин Дмитрий
🟦Зотов Максим
🟦Гайсин Айнур
🟦Денискин Алексей
🟦Евсенекин Евгений
🟦Кузнецов Пётр


Резерв:

1.
2.
3."""

GOALKEEPERS = ["Дячук Сергей", "Желтоножский Сергей",
               "Златоверховников Марк", "Проскуряков Михаил"]

# (фамилия, есть ли абонемент)
WHITE = [("Пыркин Дмитрий", True), ("Бобков Андрей", False), ("Панкратов Максим", False),
         ("Ковалёв Павел", True), ("Чужаков Андрей", True)]
YELLOW = [("Иванов Александр", True), ("Найденко Алексей", True), ("Гордеев Денис", True),
          ("Меньшенин Кирилл", False), ("Хоменко Андрей", True)]
BLACK = [("Яшков Алексей", False), ("Григорьев Сергей", False), ("Судьин Евгений", False),
         ("Любимов Павел", True), ("Лютиков Андрей", False)]
RED = [("Потапов Сергей", True), ("Новиков Илья", False), ("Хромышев Александр", False),
       ("Толстых Максим", True), ("Боровской Вячеслав", True)]
BLUE = [("Маркин Дмитрий", False), ("Зотов Максим", False), ("Гайсин Айнур", False),
        ("Денискин Алексей", False), ("Евсенекин Евгений", False), ("Кузнецов Пётр", False)]


def sample_view():
    groups = [[RosterLine(square=GOALKEEPER_SQUARE, name=name) for name in GOALKEEPERS]]
    for jersey, people in (
        (JerseyType.LIGHT, WHITE),
        (JerseyType.YELLOW, YELLOW),
        (JerseyType.DARK, BLACK),
        (JerseyType.RED, RED),
        (JerseyType.BLUE, BLUE),
    ):
        groups.append([
            RosterLine(square=JERSEY_SQUARE[jersey], name=name, has_pass=has_pass)
            for name, has_pass in people
        ])
    return RosterView(
        date_line="8.09 Вторник",
        time_line="21.30 - 23.00",
        venue="Арена Айс Атлетикс",
        deadline_text="Просьба записаться до 22:00 понедельника",
        groups=groups,
    )


def test_matches_customer_example_exactly():
    assert render_roster_text(sample_view()) == EXPECTED


def test_date_line_day_without_leading_zero_month_with():
    # 8 сентября 2026 — вторник
    assert format_date_line(datetime(2026, 9, 8, 21, 30)) == "8.09 Вторник"
    # двузначный день не должен внезапно потерять или получить ноль
    assert format_date_line(datetime(2026, 9, 15, 21, 30)) == "15.09 Вторник"


def test_time_line_uses_dot_not_colon():
    assert format_time_line(datetime(2026, 9, 8, 21, 30), time(23, 0)) == "21.30 - 23.00"
    # без времени окончания выводится только начало
    assert format_time_line(datetime(2026, 9, 8, 9, 5)) == "09.05"


def test_marks_order_is_pass_then_check_then_ruble():
    view = RosterView(date_line="1.01 Четверг", groups=[[
        RosterLine(square="🟦", name="Только записался", registered=True),
        RosterLine(square="🟦", name="Записался и оплатил", registered=True, paid=True),
        RosterLine(square="🟦", name="С абонементом", has_pass=True, registered=True, paid=True),
        RosterLine(square="🟦", name="Ничего"),
    ]])
    lines = render_roster_text(view).splitlines()
    assert "🟦Только записался ✅" in lines
    assert "🟦Записался и оплатил ✅ ₽" in lines
    assert "🟦С абонементом (А) ✅ ₽" in lines
    assert "🟦Ничего" in lines


def test_reserve_entries_then_empty_slots():
    view = sample_view()
    view.reserve = [RosterLine(square="", name="Гость Первый", registered=True)]
    text = render_roster_text(view)
    assert "1. Гость Первый ✅" in text
    assert text.endswith("2.\n3.")


def test_reserve_longer_than_slots_is_not_truncated():
    view = RosterView(date_line="1.01 Четверг")
    view.reserve = [RosterLine(square="", name=f"Гость {i}", registered=True) for i in range(1, 6)]
    text = render_roster_text(view)
    for i in range(1, 6):
        assert f"{i}. Гость {i} ✅" in text


def test_empty_training_still_renders_header_and_reserve():
    text = render_roster_text(RosterView(
        date_line="1.01 Четверг", time_line="10.00", venue="Арена",
        deadline_text="Записаться до вечера",
    ))
    assert text.startswith("❗️Записаться до вечера")
    assert text.endswith("Резерв:\n\n1.\n2.\n3.")


def test_stays_within_telegram_limit_for_huge_roster():
    # 120 человек: заведомо больше лимита, если ничего не усекать
    groups = [[
        RosterLine(square=JERSEY_SQUARE[JerseyType.BLUE],
                   name=f"Длиннофамильный Игрок Номер {i}", has_pass=True,
                   registered=True, paid=True)
        for i in range(120)
    ]]
    view = RosterView(date_line="1.01 Четверг", groups=groups)
    view.reserve = [RosterLine(square="", name=f"Резервист {i}") for i in range(40)]
    text = render_roster_text(view)
    assert telegram_length(text) <= TELEGRAM_HARD_LIMIT
    assert view.warnings, "усечение должно быть отмечено в warnings"


def test_telegram_length_counts_utf16_units():
    # цветной квадрат — суррогатная пара, две единицы вместо одной
    assert telegram_length("🟦") == 2
    assert telegram_length("ab") == 2
    assert len("🟦") == 1
