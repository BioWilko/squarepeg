"""squarepeg's own status output: coloured, levelled lines plus an optional animated
"what's happening now" spinner for stages that can take a noticeable while.

Everything here writes to stderr only, never stdout -- stdout is reserved for the
container's own output (and, for --dry-run/--dry-run-server, the manifest/response YAML),
and that separation must never be crossed. See squarepeg/log.py, which every existing
call site still imports; chatter() is now a thin wrapper around emit() below so none of
those call sites needed to change.

Colour/animation precedence (highest first): --quiet (nothing at all) > --no-color
(forces colour off) > $NO_COLOR (forces colour off) > $FORCE_COLOR (forces colour on) >
autodetect from sys.stderr.isatty() (click's own default behaviour).
"""

import itertools
import os
import sys
import threading
import time
from typing import Literal

import click

Level = Literal["info", "step", "wait", "success", "warn", "error", "detail"]

_STYLES: dict[Level, dict] = {
    "info": {},
    "step": {"fg": "cyan"},
    "wait": {"fg": "cyan", "dim": True},
    "success": {"fg": "green"},
    "warn": {"fg": "yellow"},
    "error": {"fg": "red", "bold": True},
    "detail": {"fg": "bright_black"},
}

_SPINNER_FRAMES_UNICODE = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_SPINNER_FRAMES_ASCII = ["|", "/", "-", "\\"]
_SPINNER_INTERVAL = 0.1

_LOCK = threading.RLock()
_ACTIVE: "Status | None" = None

_color_override: bool | None = None  # None = no override, resolved from env/TTY instead


def set_color_override(value: bool | None) -> None:
    """Called once from cli.run() for --no-color. None restores auto-detection."""
    global _color_override
    _color_override = value


def color_enabled() -> bool | None:
    """Tri-state, passed straight through to click.echo(color=...): True/False force
    colour on/off, None lets click autodetect from sys.stderr.isatty()."""
    if _color_override is not None:
        return _color_override
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return None


def animation_enabled() -> bool:
    if _color_override is False:
        return False
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return getattr(sys.stderr, "isatty", lambda: False)()


def _spinner_frames() -> list[str]:
    encoding = getattr(sys.stderr, "encoding", None) or ""
    if "utf" in encoding.lower():
        return _SPINNER_FRAMES_UNICODE
    return _SPINNER_FRAMES_ASCII


def _styled_line(message: str, level: Level) -> str:
    prefix = click.style("[squarepeg] ", dim=True)
    body = click.style(message, **_STYLES[level])
    return prefix + body


def emit(message: str, *, level: Level = "info", quiet: bool = False) -> None:
    """Print one status line at `level`. Never touches stdout."""
    if quiet:
        return
    with _LOCK:
        if _ACTIVE is not None:
            _ACTIVE._erase()
        click.echo(_styled_line(message, level), file=sys.stderr, color=color_enabled())
        # the active Status's own animation loop redraws unconditionally on its next tick,
        # so nothing further is needed here to restore the spinner line


class Status:
    """Context manager for a stage that may take a while.

    On a real terminal, shows an animated spinner line (stderr only) with an elapsed
    timer and, past `slow_after` seconds, a `slow_hint`. Everywhere else (non-TTY,
    --no-color, --quiet, TERM=dumb) it degrades to a single static line printed once on
    entry -- including the slow_hint text upfront, since there is no timer to add it
    later -- and nothing further is printed on exit besides the optional `success` line.
    """

    def __init__(
        self,
        message: str,
        *,
        quiet: bool = False,
        slow_hint: str | None = None,
        slow_after: float = 20.0,
        spinner_delay: float = 0.4,
        success: str | None = None,
    ):
        self.message = message
        self.quiet = quiet
        self.slow_hint = slow_hint
        self.slow_after = slow_after
        self.spinner_delay = spinner_delay
        self.success = success
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._start_time: float | None = None
        self._line_drawn = False

    def update(self, message: str) -> None:
        with _LOCK:
            self.message = message

    def __enter__(self) -> "Status":
        global _ACTIVE
        if self.quiet:
            return self
        if not animation_enabled():
            text = self.message
            if self.slow_hint:
                text = f"{self.message} ({self.slow_hint})"
            click.echo(_styled_line(text, "wait"), file=sys.stderr, color=color_enabled())
            return self

        self._start_time = time.monotonic()
        with _LOCK:
            click.echo(_styled_line(self.message, "wait"), file=sys.stderr, nl=False, color=color_enabled())
            self._line_drawn = True
            _ACTIVE = self
        self._thread = threading.Thread(target=self._animate, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> Literal[False]:
        global _ACTIVE
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        with _LOCK:
            self._erase()
            if _ACTIVE is self:
                _ACTIVE = None
            if self.success is not None and exc_type is None and not self.quiet:
                click.echo(_styled_line(self.success, "success"), file=sys.stderr, color=color_enabled())
        return False

    def _erase(self) -> None:
        """Erase the currently-drawn spinner line, if any. Caller holds _LOCK."""
        if self._line_drawn:
            click.echo("\r\x1b[2K", file=sys.stderr, nl=False, color=color_enabled())
            self._line_drawn = False

    def _animate(self) -> None:
        frames = itertools.cycle(_spinner_frames())
        # Below spinner_delay we leave the plain static line (drawn in __enter__) alone --
        # this is what keeps a fast create/delete from ever showing spinner motion at all.
        while not self._stop.is_set() and time.monotonic() - self._start_time < self.spinner_delay:
            self._stop.wait(_SPINNER_INTERVAL)
        while not self._stop.is_set():
            with _LOCK:
                elapsed = time.monotonic() - self._start_time
                text = self.message
                if elapsed >= 2:
                    text = f"{text} ({int(elapsed)}s)"
                if self.slow_hint and elapsed >= self.slow_after:
                    text = f"{text} — {self.slow_hint}"
                line = f"\r\x1b[2K{_styled_line(f'{next(frames)} {text}', 'wait')}"
                click.echo(line, file=sys.stderr, nl=False, color=color_enabled())
                self._line_drawn = True
            self._stop.wait(_SPINNER_INTERVAL)
