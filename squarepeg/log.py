from squarepeg import ui


def chatter(message: str, *, quiet: bool = False, level: ui.Level = "info") -> None:
    ui.emit(message, level=level, quiet=quiet)
