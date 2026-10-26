"""Text clean-up between Whisper and the translator (made-up German examples only)."""
import pytest

import live_subs as m


@pytest.mark.parametrize("text", ["...", "... ... ...", ". . .", "…", "... ... Ja", "  "])
def test_lines_that_are_mostly_dots_become_empty(text):
    assert m.strip_ellipses(text) == ""


@pytest.mark.parametrize("text, out", [
    ("Wir fangen an ... ... ... und dann gut.", "Wir fangen an und dann gut."),
    ("... ... ... Das Wetter ist schön heute.", "Das Wetter ist schön heute."),
    ("Ja... gut, machen wir so.", "Ja... gut, machen wir so."),
    ("Das ist ... wie soll ich sagen ... schwierig.", "Das ist ... wie soll ich sagen ... schwierig."),
    ("Version 1.2.3 ist fertig.", "Version 1.2.3 ist fertig."),
])
def test_runs_of_three_ellipses_go_and_normal_text_stays(text, out):
    assert m.strip_ellipses(text) == out
