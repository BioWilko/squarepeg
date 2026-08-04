import re

import pytest

from squarepeg.dockerargs import UNSUPPORTED, check_supported_image, parse_env_entries, reject_unsupported
from squarepeg.errors import UnsupportedFlagError, UsageError


def test_parse_env_key_value():
    assert parse_env_entries(("FOO=bar",)) == {"FOO": "bar"}


def test_parse_env_value_contains_equals():
    assert parse_env_entries(("FOO=a=b=c",)) == {"FOO": "a=b=c"}


def test_parse_env_repeatable():
    assert parse_env_entries(("A=1", "B=2")) == {"A": "1", "B": "2"}


def test_parse_env_bare_key_reads_local_environment(monkeypatch):
    monkeypatch.setenv("SQUAREPEG_TEST_VAR", "value-from-env")
    assert parse_env_entries(("SQUAREPEG_TEST_VAR",)) == {"SQUAREPEG_TEST_VAR": "value-from-env"}


def test_parse_env_bare_key_errors_when_unset(monkeypatch):
    monkeypatch.delenv("SQUAREPEG_TEST_UNSET_VAR", raising=False)
    with pytest.raises(UsageError):
        parse_env_entries(("SQUAREPEG_TEST_UNSET_VAR",))


def test_parse_env_empty_key_rejected():
    with pytest.raises(UsageError):
        parse_env_entries(("=value",))


def test_check_supported_image_rejects_leading_dash():
    with pytest.raises(UnsupportedFlagError):
        check_supported_image("--keep")


def test_check_supported_image_allows_normal_image():
    check_supported_image("alpine:3.19")


@pytest.mark.parametrize("flag", list(UNSUPPORTED.keys()))
def test_reject_unsupported_names_the_flag(flag):
    with pytest.raises(UnsupportedFlagError, match=re.escape(flag)):
        reject_unsupported(flag)


def test_reject_unsupported_noop_for_supported_flag():
    reject_unsupported("--env")  # not in the table; must not raise
