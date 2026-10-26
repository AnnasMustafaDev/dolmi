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


@pytest.mark.parametrize("text, out", [
    ("Das ist quasi fertig.", "Das ist fertig."),
    ("Wir haben im Endeffekt, sage ich mal, drei Wochen.", "Wir haben drei Wochen."),
    ("Der Plan steht sozusagen.", "Der Plan steht."),
    ("Das Ergebnis ist gut, sag ich mal.", "Das Ergebnis ist gut."),
    ("Das dauert halt etwas länger.", "Das dauert etwas länger."),
    ("Das ist eben nicht so einfach.", "Das ist nicht so einfach."),
    ("Also, also, also, wir fangen an.", "Also, wir fangen an."),
    ("Quasi, das Modul ist fertig.", "Das Modul ist fertig."),
])
def test_fillers_are_tidied_for_the_translator(text, out):
    assert m.tidy_fillers(text) == out


@pytest.mark.parametrize("text", [
    "Halt! Nicht weiter.",                  # 'Halt' as a command
    "Das war gerade eben.",                 # 'eben' meaning just now
    "Er kam eben noch rechtzeitig.",
    "Eben, das meine ich.",                  # 'Eben' as an answer
    "Eine ebene Fläche.",
    "Sozusagen ist das der Plan.",          # opening filler without a comma: verb order would break
    "Quasi.", "Sozusagen, quasi.",          # never empty the line
    "Das Quasimodo-Projekt läuft.",         # whole words only
])
def test_fillers_are_conservative(text):
    assert m.tidy_fillers(text) == text


class Recorder:
    def __init__(self, tidy):
        self.tidy_fillers, self.seen = tidy, []

    def translate(self, text, lang):
        self.seen.append(text)
        return text


def test_translator_gets_tidied_text_and_the_setting_turns_it_off():
    on, off = Recorder(True), Recorder(False)
    m._translate(on, "Das ist quasi fertig.", "de", ("", "", None))
    m._translate(off, "Das ist quasi fertig.", "de", ("", "", None))
    assert on.seen == ["Das ist fertig."] and off.seen == ["Das ist quasi fertig."]


def test_the_setting_defaults_on():
    import config
    assert config.DEFAULTS["tidy_fillers"] is True
