import dataclasses

from app.config import OPENROUTER_BASE_URL, Settings, load_settings

ENV_KEYS = [
    "OPENROUTER_API_KEY", "OPENROUTER_BASE_URL", "OPENROUTER_CHAT_MODEL", "OPENROUTER_EMBED_MODEL",
    "EMBED_DIM", "ALLOW_REGISTRATION", "SESSION_TTL_HOURS", "LOGIN_MAX_FAILURES", "LOGIN_WINDOW_SECONDS",
    "ALLOWED_ORIGINS", "MAX_UPLOAD_MB",
]


def _clean(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_defaults_target_openrouter_and_builtin_auth(monkeypatch):
    _clean(monkeypatch)
    s = load_settings()
    assert s.openrouter_base_url == OPENROUTER_BASE_URL == "https://openrouter.ai/api/v1"
    assert s.openrouter_chat_model.startswith("qwen/")
    assert s.openrouter_embed_model.startswith("qwen/")
    assert s.embed_dim == 1024
    assert s.openrouter_api_key == ""
    assert s.allow_registration is True
    assert s.session_ttl_hours == 12


def test_jwt_settings_are_gone():
    names = {f.name for f in dataclasses.fields(Settings)}
    assert not any("jwt" in n or "qwen" in n for n in names)


def test_environment_overrides_are_applied(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://proxy.example.test/api/v1/")
    monkeypatch.setenv("ALLOW_REGISTRATION", "false")
    monkeypatch.setenv("SESSION_TTL_HOURS", "3")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://a.test, https://b.test ,")
    s = load_settings()
    assert s.openrouter_api_key == "sk-or-test"
    assert s.openrouter_base_url == "https://proxy.example.test/api/v1"  # trailing slash stripped
    assert s.allow_registration is False
    assert s.session_ttl_hours == 3
    assert s.allowed_origins == ("https://a.test", "https://b.test")


def test_boolean_parsing_accepts_common_spellings(monkeypatch):
    _clean(monkeypatch)
    for raw, expected in [("1", True), ("YES", True), ("on", True), ("0", False), ("no", False)]:
        monkeypatch.setenv("ALLOW_REGISTRATION", raw)
        assert load_settings().allow_registration is expected, raw
