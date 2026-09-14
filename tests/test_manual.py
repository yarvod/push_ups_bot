from datetime import date

import pytest

from pullups_bot.application.manual import ManualPrompt, complete_manual, parse_manual
from pullups_bot.domain.models import RuleError


@pytest.mark.parametrize(
    "text", ["09.09.2026 командировка", "2026-09-09 командировка", "9.9.2026 командировка"]
)
def test_both_date_formats(text):
    parsed = parse_manual(text, date(2026, 9, 14))
    assert parsed.day == date(2026, 9, 9)
    assert parsed.reason == "командировка"


@pytest.mark.parametrize(
    "text", ["31.09.2026 причина", "2026-02-30 причина", "09/09/2026 причина", "09.09 причина"]
)
def test_invalid_date_never_becomes_todays_reason(text):
    with pytest.raises(RuleError):
        parse_manual(text, date(2026, 9, 14))


@pytest.mark.parametrize(
    "answer,expected",
    [
        ("командировка", "@weebat 2026-09-09 командировка"),
        ("10.09.2026 болел", "@weebat 10.09.2026 болел"),
        ("@yarvod 10.09.2026 болел", "@yarvod 10.09.2026 болел"),
    ],
)
def test_prompt_preserves_or_overrides_selected_date(answer, expected):
    prompt = ManualPrompt(718724903, "excuse", "@weebat 2026-09-09", "2026-09-09")
    assert complete_manual(prompt, answer) == expected
