import pytest

from squarepeg.env_interpolation import interpolate_document, interpolate_string, interpolate_value
from squarepeg.errors import ConfigError

SOURCE = "test.yaml (test)"


def istr(value, env=None):
    return interpolate_string(value, source=SOURCE, path="key", env=env or {})


# --- substitution ---


def test_whole_value_is_a_single_reference():
    assert istr("${NAMESPACE}", {"NAMESPACE": "prod"}) == "prod"


def test_partial_embedded_reference():
    assert istr("job-${RUN_TAG}", {"RUN_TAG": "abc"}) == "job-abc"
    assert istr("pre-${V}-post", {"V": "x"}) == "pre-x-post"


def test_multiple_references_in_one_string():
    assert istr("${A}-${B}", {"A": "1", "B": "2"}) == "1-2"
    assert istr("${A}-${A}", {"A": "x"}) == "x-x"


def test_no_references_returned_unchanged():
    assert istr("plain string", {}) == "plain string"


def test_default_used_when_unset():
    assert istr("${VAR:-default}", {}) == "default"


def test_value_used_when_set_non_empty():
    assert istr("${VAR:-default}", {"VAR": "actual"}) == "actual"


def test_default_used_when_set_to_empty_string():
    """Bash ':-' semantics: an empty-string value counts as unset."""
    assert istr("${VAR:-default}", {"VAR": ""}) == "default"


def test_empty_default_yields_empty_string():
    assert istr("${VAR:-}", {}) == ""


def test_default_may_contain_special_characters():
    assert istr("${IMG:-ghcr.io/foo/bar:1.2}", {}) == "ghcr.io/foo/bar:1.2"
    assert istr("${IMG:-has spaces}", {}) == "has spaces"


def test_double_dollar_is_literal_dollar():
    assert istr("$$", {}) == "$"
    assert istr("$${VAR}", {"VAR": "x"}) == "${VAR}"  # escaped, never re-expanded


def test_bare_dollar_var_not_a_reference():
    assert istr("$VAR", {"VAR": "x"}) == "$VAR"
    assert istr("just a $ sign", {}) == "just a $ sign"


def test_value_containing_reference_syntax_is_not_re_expanded():
    assert istr("${OUTER}", {"OUTER": "${INNER}", "INNER": "nope"}) == "${INNER}"


# --- errors ---


def test_missing_var_raises_config_error_naming_var_path_and_source():
    with pytest.raises(ConfigError) as exc_info:
        istr("${MISSING}", {})
    message = str(exc_info.value)
    assert "MISSING" in message
    assert "key" in message
    assert SOURCE in message


def test_missing_var_error_suggests_default_syntax():
    with pytest.raises(ConfigError, match=r"\$\{MISSING:-default\}"):
        istr("${MISSING}", {})


@pytest.mark.parametrize("malformed", ["${}", "${1BAD}", "${A B}", "${VAR:=x}", "${VAR"])
def test_malformed_reference_raises_config_error(malformed):
    with pytest.raises(ConfigError):
        istr(malformed, {})


# --- the recursive walk ---


def test_walk_nested_dict_list_dict():
    doc = {"kubernetes": {"spec": {"imagePullSecrets": [{"name": "${SECRET}"}]}}}
    result = interpolate_value(doc, source=SOURCE, path="", env={"SECRET": "regcred"})
    assert result == {"kubernetes": {"spec": {"imagePullSecrets": [{"name": "regcred"}]}}}


@pytest.mark.parametrize("value", [1, 1.5, True, False, None])
def test_non_string_leaves_pass_through_untouched(value):
    result = interpolate_value(value, source=SOURCE, path="key", env={})
    assert result is value or result == value
    assert type(result) is type(value)


def test_mapping_keys_are_not_interpolated():
    doc = {"${KEY}": "value"}
    result = interpolate_value(doc, source=SOURCE, path="", env={"KEY": "resolved"})
    assert result == {"${KEY}": "value"}


def test_list_element_path_in_error_message():
    doc = {"a": {"b": [{"c": "${MISSING}"}]}}
    with pytest.raises(ConfigError, match=r"a\.b\[0\]\.c"):
        interpolate_value(doc, source=SOURCE, path="", env={})


def test_root_scalar_path_in_error_message():
    doc = {"namespace": "${MISSING}"}
    with pytest.raises(ConfigError, match="namespace"):
        interpolate_value(doc, source=SOURCE, path="", env={})


def test_input_structure_is_not_mutated():
    doc = {"namespace": "${NS}"}
    interpolate_value(doc, source=SOURCE, path="", env={"NS": "resolved"})
    assert doc == {"namespace": "${NS}"}


def test_interpolate_document_uses_os_environ_by_default(monkeypatch):
    monkeypatch.setenv("SQUAREPEG_TEST_INTERP_VAR", "from-os-environ")
    doc = {"namespace": "${SQUAREPEG_TEST_INTERP_VAR}"}
    result = interpolate_document(doc, SOURCE)
    assert result == {"namespace": "from-os-environ"}
