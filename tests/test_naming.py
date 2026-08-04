import pytest

from squarepeg.errors import UsageError
from squarepeg.naming import MAX_NAME_LENGTH, generate_name, image_basename, validate_rfc1123


@pytest.mark.parametrize(
    "image,expected",
    [
        ("alpine", "alpine"),
        ("Alpine", "alpine"),
        ("alpine:3.19", "alpine"),
        ("library/nextflow_pipeline", "nextflow-pipeline"),
        ("registry.example.com:5000/foo/bar:tag", "bar"),
        ("localhost:5000/foo/bar:tag@sha256:" + "a" * 64, "bar"),
        ("ghcr.io/org/tool@sha256:" + "b" * 64, "tool"),
    ],
)
def test_image_basename(image, expected):
    assert image_basename(image) == expected


def test_generate_name_is_valid_rfc1123():
    name = generate_name("registry.example.com/some/very-long-image-name-indeed:latest")
    validate_rfc1123(name)
    assert len(name) <= MAX_NAME_LENGTH
    assert name.startswith("squarepeg-")


def test_generate_name_truncates_long_slug():
    name = generate_name("x" * 200)
    assert len(name) <= MAX_NAME_LENGTH


def test_validate_rfc1123_rejects_uppercase():
    with pytest.raises(UsageError):
        validate_rfc1123("Bad-Name")


def test_validate_rfc1123_rejects_too_long():
    with pytest.raises(UsageError):
        validate_rfc1123("a" * (MAX_NAME_LENGTH + 1))


def test_validate_rfc1123_rejects_leading_hyphen():
    with pytest.raises(UsageError):
        validate_rfc1123("-bad")
