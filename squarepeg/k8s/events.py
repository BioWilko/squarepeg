"""Surface pod events as diagnostics: the difference between 'it hung' and
'0/12 nodes available: insufficient memory'.
"""

from kubernetes.client.rest import ApiException

from squarepeg.log import chatter


def print_pod_events(session, pod_name: str, *, quiet: bool = False) -> None:
    try:
        events = session.core.list_namespaced_event(
            session.namespace, field_selector=f"involvedObject.name={pod_name}"
        )
    except ApiException:
        return  # best-effort diagnostics; never fail the run because event listing failed

    for event in events.items or []:
        chatter(f"{pod_name}: {event.reason}: {event.message}", quiet=quiet)
