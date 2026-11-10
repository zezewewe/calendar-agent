from __future__ import annotations

from calendar_agent.channels import telegram


def test_help_text_lists_every_command():
    for command in ("/help", "/agenda", "/list", "/calendar"):
        assert command in telegram._HELP


def test_help_text_reassures_nothing_is_saved_until_confirmed():
    assert "Nothing is saved until you tap Add" in telegram._HELP
