from pathlib import Path

from clipper.transcript import _pick_vtt, parse_vtt_words

# A YouTube-style auto-caption VTT with inline word timestamps.
AUTO_VTT = """WEBVTT
Kind: captions
Language: es

00:00:01.000 --> 00:00:03.500 align:start position:0%
hola<00:00:01.480><c> a</c><00:00:01.900><c> todos</c>

00:00:03.500 --> 00:00:03.510 align:start position:0%
hola a todos

00:00:03.510 --> 00:00:06.000 align:start position:0%
hola a todos
bienvenidos<00:00:04.100><c> al</c><00:00:04.600><c> video</c>
"""

# YouTube auto-captions put a space-only placeholder line right after the
# header of the first cue. It must not be treated as a cue separator.
# Built explicitly so the " \n" line survives trailing-whitespace stripping.
SPACE_LINE_VTT = (
    "WEBVTT\n"
    "Kind: captions\n"
    "Language: es\n"
    "\n"
    "00:00:02.280 --> 00:00:04.789 align:start position:0%\n"
    " \n"  # space-only placeholder line (YouTube quirk)
    "Discipulos<00:00:02.840><c> en</c><00:00:03.000><c> proceso</c>\n"
    "\n"
    "00:00:04.789 --> 00:00:04.799 align:start position:0%\n"
    "Discipulos en proceso\n"
    "\n"
    "00:00:04.799 --> 00:00:06.510 align:start position:0%\n"
    "Discipulos en proceso\n"
    "buscamos<00:00:05.279><c> crecer</c>\n"
)

# A manual/line-level VTT: no inline word timestamps.
LINE_VTT = """WEBVTT

00:00:01.000 --> 00:00:03.500
hola a todos

00:00:03.500 --> 00:00:06.000
bienvenidos al video
"""


def test_parse_auto_subs_yields_words():
    words = parse_vtt_words(AUTO_VTT)
    assert [w.text for w in words] == [
        "hola",
        "a",
        "todos",
        "bienvenidos",
        "al",
        "video",
    ]


def test_parse_auto_subs_uses_inline_timestamps():
    words = parse_vtt_words(AUTO_VTT)
    by_text = {w.text: w for w in words}
    assert by_text["hola"].start == 1.0  # cue start
    assert by_text["a"].start == 1.48  # inline timestamp
    assert by_text["todos"].start == 1.9
    assert by_text["video"].start == 4.6
    # Timing is monotonic.
    starts = [w.start for w in words]
    assert starts == sorted(starts)


def test_line_level_subs_yield_no_words():
    # Manual subs have no word-level timing -> empty -> signals Whisper fallback.
    assert parse_vtt_words(LINE_VTT) == []


def test_first_cue_survives_space_only_placeholder_line():
    words = parse_vtt_words(SPACE_LINE_VTT)
    texts = [w.text for w in words]
    # The first cue's words must not be dropped by the space-only line.
    assert texts[:3] == ["Discipulos", "en", "proceso"]
    assert "buscamos" in texts and "crecer" in texts


def test_pick_vtt_prefers_the_target_video():
    # A playlist URL can leave sibling videos' captions in the dir; the file
    # for the requested video_id must win over an alphabetically-earlier one.
    files = [
        Path("5n7X1rSxDqg.es.vtt"),
        Path("7EhAdIDcado.es.vtt"),
    ]
    assert _pick_vtt(files, "es", "7EhAdIDcado").name == "7EhAdIDcado.es.vtt"


def test_pick_vtt_prefers_matching_language():
    files = [
        Path("vid.en.vtt"),
        Path("vid.es-419.vtt"),
    ]
    assert _pick_vtt(files, "es", "vid").name == "vid.es-419.vtt"


def test_pick_vtt_falls_back_when_no_id_match():
    files = [Path("other.es.vtt")]
    assert _pick_vtt(files, "es", "vid").name == "other.es.vtt"
