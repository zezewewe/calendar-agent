from __future__ import annotations

import pytest

from calendar_agent.config import Config, VisionModel


def test_default_is_vision_raus(monkeypatch):
    monkeypatch.delenv("EXTRACT_MODEL", raising=False)
    assert Config.from_env().extract_model == "vision-raus"


def test_env_override_switches_the_model(monkeypatch):
    monkeypatch.setenv("EXTRACT_MODEL", "ds-flash")
    assert Config.from_env().extract_model == "ds-flash"


def test_every_alias_parses():
    for member in VisionModel:
        assert VisionModel.parse(member.value) is member


def test_member_names_parse_case_insensitively():
    assert VisionModel.parse("FLASH") is VisionModel.FLASH
    assert VisionModel.parse("flash") is VisionModel.FLASH


def test_blank_values_default_to_raus():
    assert VisionModel.parse(None) is VisionModel.RAUS
    assert VisionModel.parse("") is VisionModel.RAUS
    assert VisionModel.parse("   ") is VisionModel.RAUS


def test_unknown_model_is_rejected_with_the_choices():
    with pytest.raises(ValueError, match="vision-raus"):
        VisionModel.parse("gpt-4o")
