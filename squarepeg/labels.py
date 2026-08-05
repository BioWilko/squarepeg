"""The label vocabulary squarepeg writes on every Pod/Job it creates, and reads back
when discovering its own orphaned resources for the sweep (squarepeg/k8s/orphans.py).

Kept in one place because these two concerns -- writing labels and querying by them --
must never drift apart. A selector built from a stale copy of a label key doesn't error;
it just silently matches nothing, which is the worst possible failure mode for a feature
whose entire job is finding things.
"""

import getpass

from squarepeg.naming import sanitize_label_value

MANAGED_BY_LABEL = "app.kubernetes.io/managed-by"
MANAGED_BY_VALUE = "squarepeg"
RUN_ID_LABEL = "squarepeg.io/run-id"
CREATED_BY_LABEL = "squarepeg.io/created-by"
KEEP_LABEL = "squarepeg.io/keep"
KEEP_VALUE = "true"


def current_created_by() -> str:
    return sanitize_label_value(getpass.getuser())
