import threading

import pytest

from squarepeg import ui

LEVELS = ["info", "step", "wait", "success", "warn", "error", "detail"]


@pytest.fixture(autouse=True)
def _reset_ui_state():
    ui.set_color_override(None)
    ui.set_run_tag(None)
    yield
    ui.set_color_override(None)
    ui.set_run_tag(None)


# --- emit: colour / env var precedence ---


@pytest.mark.parametrize("level", LEVELS)
def test_emit_no_ansi_under_capsys_by_default(capsys, level):
    """capsys is a non-TTY, so click.echo's own colour autodetection should strip ANSI."""
    ui.emit("hello world", level=level)
    assert "\x1b" not in capsys.readouterr().err


def test_emit_force_color_adds_ansi(monkeypatch, capsys):
    monkeypatch.setenv("FORCE_COLOR", "1")
    ui.emit("hello", level="step")
    assert "\x1b[36m" in capsys.readouterr().err


def test_no_color_beats_force_color(monkeypatch, capsys):
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("NO_COLOR", "1")
    ui.emit("hello", level="step")
    assert "\x1b" not in capsys.readouterr().err


def test_no_color_override_beats_force_color(monkeypatch, capsys):
    monkeypatch.setenv("FORCE_COLOR", "1")
    ui.set_color_override(False)
    ui.emit("hello", level="step")
    assert "\x1b" not in capsys.readouterr().err


@pytest.mark.parametrize("level", LEVELS)
def test_emit_quiet_prints_nothing(capsys, level):
    ui.emit("should not appear", level=level, quiet=True)
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("level", LEVELS)
def test_emit_body_is_contiguous_substring_even_with_color_forced(monkeypatch, capsys, level):
    """Guards the never-style-a-sub-span rule: existing tests assert on exact substrings of
    chatter output via capsys, and must keep passing even if colour is forced on."""
    monkeypatch.setenv("FORCE_COLOR", "1")
    ui.emit("swept 3 orphaned resource(s) from previous runs", level=level)
    assert "swept 3 orphaned resource(s) from previous runs" in capsys.readouterr().err


# --- Status: non-TTY degrades to a single static line, no thread ---


def test_status_non_tty_prints_one_static_line_including_slow_hint(capsys):
    before = threading.active_count()
    with ui.Status("waiting for pod to start", slow_hint="still pulling", slow_after=999):
        assert threading.active_count() == before
    err = capsys.readouterr().err
    assert "waiting for pod to start" in err
    assert "still pulling" in err


def test_status_non_tty_no_thread_spawned(capsys):
    before = threading.active_count()
    with ui.Status("doing a thing"):
        assert threading.active_count() == before
    assert threading.active_count() == before


def test_status_quiet_emits_nothing_including_success(capsys):
    with ui.Status("doing a thing", quiet=True, success="done"):
        pass
    assert capsys.readouterr().err == ""


def test_status_success_printed_on_clean_exit(capsys):
    with ui.Status("doing a thing", success="all done"):
        pass
    assert "all done" in capsys.readouterr().err


def test_status_success_not_printed_on_exception(capsys):
    from squarepeg.errors import RunnerError

    with pytest.raises(RunnerError):
        with ui.Status("doing a thing", success="all done"):
            raise RunnerError("boom")
    err = capsys.readouterr().err
    assert "all done" not in err


def test_status_propagates_exceptions_and_leaves_no_live_thread():
    from squarepeg.errors import RunnerError

    before = threading.active_count()
    with pytest.raises(RunnerError):
        with ui.Status("doing a thing"):
            raise RunnerError("boom")
    assert threading.active_count() == before


# --- run tag ---


def test_set_run_tag_appears_in_prefix(capsys):
    ui.set_run_tag("abc12345")
    ui.emit("hello")
    assert "[squarepeg:abc12345] hello" in capsys.readouterr().err


def test_no_run_tag_produces_legacy_prefix(capsys):
    ui.emit("hello")
    err = capsys.readouterr().err
    assert err.strip() == "[squarepeg] hello"
    assert "[squarepeg:" not in err


@pytest.mark.parametrize("level", LEVELS)
def test_run_tag_does_not_corrupt_body_substring(monkeypatch, capsys, level):
    monkeypatch.setenv("FORCE_COLOR", "1")
    ui.set_run_tag("abc12345")
    ui.emit("swept 3 orphaned resource(s) from previous runs", level=level)
    assert "swept 3 orphaned resource(s) from previous runs" in capsys.readouterr().err


def test_status_line_carries_run_tag(capsys):
    ui.set_run_tag("abc12345")
    with ui.Status("waiting for pod to start", slow_hint="still pulling", slow_after=999):
        pass
    err = capsys.readouterr().err
    assert "[squarepeg:abc12345]" in err
    assert "waiting for pod to start" in err
    assert "still pulling" in err


def test_status_success_line_carries_run_tag(capsys):
    ui.set_run_tag("abc12345")
    with ui.Status("doing a thing", success="all done"):
        pass
    err = capsys.readouterr().err
    assert "[squarepeg:abc12345] all done" in err


def test_set_run_tag_none_clears_a_previously_set_tag(capsys):
    ui.set_run_tag("abc12345")
    ui.set_run_tag(None)
    ui.emit("hello")
    err = capsys.readouterr().err
    assert "[squarepeg:" not in err
    assert "[squarepeg] hello" in err
