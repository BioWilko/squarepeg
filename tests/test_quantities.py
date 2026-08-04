import pytest

from squarepeg.errors import UsageError
from squarepeg.quantities import docker_cpus_to_k8s, docker_memory_to_k8s


@pytest.mark.parametrize(
    "value,expected",
    [
        ("0.5", "500m"),
        ("2", "2"),
        ("1", "1"),
    ],
)
def test_cpus(value, expected):
    assert docker_cpus_to_k8s(value) == expected


def test_cpus_fractional_millicore_rounds_up():
    assert docker_cpus_to_k8s("0.0001") == "1m"


def test_cpus_rejects_negative():
    with pytest.raises(UsageError):
        docker_cpus_to_k8s("-1")


def test_cpus_rejects_junk():
    with pytest.raises(UsageError):
        docker_cpus_to_k8s("abc")


@pytest.mark.parametrize(
    "value,expected",
    [
        ("512m", "512Mi"),
        ("1g", "1Gi"),
        ("1024k", "1Mi"),  # 1024k == 1Mi exactly; converter picks the largest exact unit
        ("2gb", "2Gi"),
        ("100", "100"),
        ("0", "0"),
    ],
)
def test_memory(value, expected):
    assert docker_memory_to_k8s(value) == expected


def test_memory_rejects_junk():
    with pytest.raises(UsageError):
        docker_memory_to_k8s("512MB?")


def test_memory_rejects_negative():
    with pytest.raises(UsageError):
        docker_memory_to_k8s("-512m")
