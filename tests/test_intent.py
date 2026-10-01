from datetime import date

import pytest

from pullups_bot.presentation.intent import Intent, looks_like_command, parse_intent

TODAY = date(2026, 9, 6)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Ну пошути тогда", None),
        ("Не ставь мне пропуск", None),
        ("Как поставить пропуск?", None),
        ("Покажи долги по пиву", Intent("debts")),
        ("Сколько я отжался?", Intent("stats")),
        ("Покажи мою стату", Intent("stats")),
        ("Сколько у меня отжиманий?", Intent("stats")),
        ("Кто сегодня отжался?", Intent("today")),
        (", покажи сколько я отжался?", Intent("stats")),
        ("Я не отжался", Intent("miss", "сегодня")),
        ("Отметь мне отжимания завтра", Intent("done", "2026-09-07")),
        ("Скинь ссылку на таблицу", Intent("sheet")),
        ("Покажи отметки сегодня", Intent("today")),
        ("Что ты умеешь?", Intent("help")),
        ("Отметь мне отжимания сегодня", Intent("done", "сегодня")),
        ("Я отжался", Intent("done", "сегодня")),
        ("Отжался", Intent("done", "сегодня")),
        ("Пометь, что отжимания сделаны", Intent("done", "сегодня")),
        ("Я сегодня не делал отжимания", Intent("miss", "сегодня")),
        ("Поставь мне уважительную, я заболел", Intent("excuse", "сегодня я заболел")),
        ("Запиши @weebat прогул за 05.09.2026", Intent("miss", "@weebat 05.09.2026")),
        ("Отметь @weebat вчера, потому что болел", Intent("excuse", "@weebat 2026-09-05 болел")),
        ("Привяжи @weebat к Telegram ID 101", Intent("bind", "@weebat 101")),
        ("Отключи беседу", Intent("unbind")),
    ],
)
def test_parse_intent(text, expected):
    assert parse_intent(text, TODAY) == expected


def test_unclear_command_can_be_clarified_without_starting_banter():
    assert looks_like_command("Что с моими отжиманиями?")
    assert not looks_like_command("Ну пошути тогда")
