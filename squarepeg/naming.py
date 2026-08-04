import re
import secrets

from squarepeg.errors import UsageError

MAX_NAME_LENGTH = 63
_RFC1123_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


def validate_rfc1123(name: str, *, what: str = "name") -> str:
    if not name or len(name) > MAX_NAME_LENGTH or not _RFC1123_RE.match(name):
        raise UsageError(
            f"invalid {what} {name!r}: must be a lowercase RFC1123 label "
            f"(alphanumeric and '-', starting/ending alphanumeric, max {MAX_NAME_LENGTH} chars)"
        )
    return name


def image_basename(image: str) -> str:
    """Strip registry host, port, tag and digest, leaving a short slug."""
    ref = image.split("@", 1)[0]  # drop digest

    # split off the leading registry host (must contain '.' or ':' or be 'localhost')
    parts = ref.split("/", 1)
    if len(parts) == 2 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        ref = parts[1]

    # drop tag: last ':' after the final '/'
    last_slash = ref.rfind("/")
    last_colon = ref.rfind(":")
    if last_colon > last_slash:
        ref = ref[:last_colon]

    slug = ref.rsplit("/", 1)[-1]
    slug = re.sub(r"[^a-z0-9-]+", "-", slug.lower()).strip("-")
    return slug or "image"


def sanitize_label_value(value: str) -> str:
    """Sanitize a string into a valid k8s label value (alnum, '-', '_', '.', <=63 chars)."""
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-_.")
    return (cleaned or "unknown")[:MAX_NAME_LENGTH]


def generate_name(image: str) -> str:
    slug = image_basename(image)
    suffix = secrets.token_hex(3)  # 6 hex chars
    prefix = "squarepeg-"
    budget = MAX_NAME_LENGTH - len(prefix) - len(suffix) - 1
    if budget < 1:
        slug = slug[:1]
    else:
        slug = slug[:budget]
    slug = slug.strip("-") or "image"
    return validate_rfc1123(f"{prefix}{slug}-{suffix}")
