import pytest

from music_librarian.deezer import config, kept

GOOD = {"DEEZER_ARL": "dummy-arl", "DEEZER_PRE_KEY": "AAAAAAAAAAAAAAAA", "DEEZER_IV": "0001020304050607"}


def test_enabled_only_with_the_three_required_values():
    assert config.enabled(GOOD)
    for name in GOOD:
        assert not config.enabled({**GOOD, name: " "})
    assert not config.enabled({})


def test_load():
    cfg = config.load({**GOOD, "DEEZER_LICENSE_TOKEN": "dummy-token"})
    assert (cfg.arl, cfg.pre_key, cfg.iv, cfg.license_token) == (
        "dummy-arl", b"AAAAAAAAAAAAAAAA", bytes.fromhex("0001020304050607"), "dummy-token")
    assert config.load(GOOD).license_token == ""


@pytest.mark.parametrize("name, value, message", [
    ("DEEZER_ARL", "", "not set: DEEZER_ARL"),
    ("DEEZER_PRE_KEY", "short-secret", "DEEZER_PRE_KEY must be 16 bytes long"),
    ("DEEZER_IV", "not-hex-secret!!", "DEEZER_IV must be 16 hexadecimal characters"),
    ("DEEZER_IV", "00010203", "DEEZER_IV must be 16 hexadecimal characters"),
])
def test_a_bad_value_is_named_never_shown(name, value, message):
    with pytest.raises(config.ConfigError) as error:
        config.load({**GOOD, name: value})
    assert str(error.value) == message
    assert not value or value not in str(error.value)


def test_the_settings_never_show_in_their_repr():
    shown = repr(config.load({**GOOD, "DEEZER_LICENSE_TOKEN": "dummy-token"}))
    for secret in ("dummy-arl", "AAAA", "dummy-token", "0001020304050607"):
        assert secret not in shown


def test_the_arl_set_from_the_dashboard_stands_for_deezer_arl(tmp_path):
    keys = {name: value for name, value in GOOD.items() if name != "DEEZER_ARL"}
    assert config.keys_set(keys) and not config.enabled(keys, tmp_path)
    kept.save_arl(tmp_path, "dummy-kept-arl")
    assert config.enabled(keys, tmp_path)
    assert config.load(keys, tmp_path).arl == "dummy-kept-arl"
    assert config.load(GOOD, tmp_path).arl == "dummy-kept-arl"
    assert config.load(GOOD).arl == "dummy-arl"
    assert (tmp_path / kept.ARL).stat().st_mode & 0o777 == 0o600
