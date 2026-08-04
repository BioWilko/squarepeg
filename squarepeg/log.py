import sys


def chatter(message: str, *, quiet: bool = False) -> None:
    if not quiet:
        print(f"[squarepeg] {message}", file=sys.stderr)
