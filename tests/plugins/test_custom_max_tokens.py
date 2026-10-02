"""silocyber patch (VIGI-511): the custom provider honors model_overrides.<p>.<m>.max_output_tokens,
and a failed lookup never silently sends no cap."""
import logging

import pytest

import agent.models_dev as models_dev
from providers import get_provider_profile


@pytest.fixture
def custom():
    profile = get_provider_profile("custom")
    assert profile is not None
    return profile


def _override(monkeypatch, value):
    monkeypatch.setattr(models_dev, "_explicit_model_override", lambda provider, model, **kw: value)


def test_override_int_is_the_cap(custom, monkeypatch):
    _override(monkeypatch, {"max_output_tokens": 8192})
    assert custom.get_max_tokens("vig-review-deepseek") == 8192


def test_override_float_is_truncated(custom, monkeypatch):
    _override(monkeypatch, {"max_output_tokens": 4096.9})
    assert custom.get_max_tokens("m") == 4096


@pytest.mark.parametrize("bad", [0, -1, True, "8192", None])
def test_bad_values_keep_the_profile_default(custom, monkeypatch, bad):
    _override(monkeypatch, {"max_output_tokens": bad})
    assert custom.get_max_tokens("m") == custom.default_max_tokens


def test_no_override_keeps_the_profile_default(custom, monkeypatch):
    _override(monkeypatch, None)
    assert custom.get_max_tokens("m") == custom.default_max_tokens


def test_no_model_keeps_the_profile_default(custom):
    assert custom.get_max_tokens(None) == custom.default_max_tokens


def test_lookup_failure_caps_and_warns(custom, monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("config unreadable")
    monkeypatch.setattr(models_dev, "_explicit_model_override", boom)
    monkeypatch.delenv("HERMES_FALLBACK_MAX_OUTPUT_TOKENS", raising=False)
    with caplog.at_level(logging.WARNING):
        assert custom.get_max_tokens("m") == 16384
    assert "max_output_tokens lookup failed" in caplog.text


@pytest.mark.parametrize("env,want", [("4096", 4096), ("junk", 16384), ("0", 16384)])
def test_fallback_cap_from_env(custom, monkeypatch, env, want):
    monkeypatch.setattr(models_dev, "_explicit_model_override", lambda *a, **k: 1 / 0)
    monkeypatch.setenv("HERMES_FALLBACK_MAX_OUTPUT_TOKENS", env)
    assert custom.get_max_tokens("m") == want
