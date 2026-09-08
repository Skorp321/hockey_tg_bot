"""Защита от регрессий в инлайновом JS страницы расписания.

Этот файл — 2000 строк JS внутри шаблона, без сборки и без другого покрытия, поэтому
ошибки в селекторах там ничем не ловятся. Проверки ниже пиннят ровно те грабли, на
которые уже наступили: при переводе амплуа с радиокнопок на <select> в одном месте
остался селектор input[...]:checked, из-за чего «Запомнить» молча сохраняло цвет,
но теряло амплуа.
"""

from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[1] / "app" / "templates" / "schedule.html"


@pytest.fixture(scope="module")
def html():
    return TEMPLATE.read_text(encoding="utf-8")


def test_no_stale_radio_selector_for_positions(html):
    """Амплуа — <select>, поэтому input[name^="position-type"] ничего не найдёт."""
    assert 'input[name^="position-type"]' not in html, (
        "Остался селектор под радиокнопки: он не находит <select>, "
        "и выбранное амплуа молча теряется"
    )


def test_no_hardcoded_position_values(html):
    """Значения амплуа приходят с сервера, в шаблоне их быть не должно."""
    for stale in ("'forward'", '"forward"', "'defender'", '"defender"'):
        assert stale not in html, f"В шаблоне осталось захардкоженное значение {stale}"


def test_position_options_come_from_server(html):
    assert "const POSITION_OPTIONS" in html
    assert "{% for value, label in positions %}" in html


def test_undeclared_variable_bug_does_not_return(html):
    """allTeamsSelected нигде не объявлена: её чтение роняло checkSelectionChanges.

    Проверяем именно подстановку в шаблонную строку — упоминание в комментарии,
    объясняющем историю бага, ошибкой не является.
    """
    assert "${allTeamsSelected}" not in html


def test_roster_membership_is_visible(html):
    """Админ должен видеть, попадёт игрок в список или в резерв."""
    assert "participant.roster_member" in html
    assert "В составе" in html


def test_preview_uses_textcontent_not_innerhtml(html):
    """Текст списка содержит фамилии от людей — вставлять его как HTML нельзя."""
    assert "textEl.textContent = data.text" in html
