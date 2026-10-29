"""Tests de la diarización determinista (sin cargar pyannote).

Cubren la asignación de hablantes a segmentos: solape máximo, partición de un
segmento entre varios turnos, reparto por palabras y etiquetado S1..Sn.
"""

from __future__ import annotations

from tg_transcriber.diarize import (
    SpeakerTurn,
    assign_speakers,
    drop_short_turns,
    merge_adjacent,
    nearest_turn,
    relabel_by_first_appearance,
    split_segment,
)
from tg_transcriber.stt import Segment


def _seg(start, end, text, source="?", words=None):
    return Segment(start=start, end=end, source=source, text=text, words=words)


def test_relabel_by_first_appearance():
    turns = [
        SpeakerTurn(10, 12, "SPEAKER_02"),
        SpeakerTurn(0, 2, "SPEAKER_00"),
        SpeakerTurn(5, 6, "SPEAKER_01"),
        SpeakerTurn(13, 14, "SPEAKER_02"),
    ]
    out = relabel_by_first_appearance(turns)
    assert [t.label for t in out] == ["S1", "S2", "S3", "S3"]
    assert out[0].start == 0  # ordenado por tiempo


def test_merge_adjacent_same_speaker():
    turns = [
        SpeakerTurn(0, 1, "S1"),
        SpeakerTurn(1.1, 2, "S1"),
        SpeakerTurn(2.1, 3, "S2"),
    ]
    out = merge_adjacent(turns, gap_s=0.25)
    assert [(t.start, t.end, t.label) for t in out] == [(0, 2, "S1"), (2.1, 3, "S2")]


def test_merge_adjacent_respects_gap():
    turns = [SpeakerTurn(0, 1, "S1"), SpeakerTurn(2, 3, "S1")]
    out = merge_adjacent(turns, gap_s=0.25)
    assert len(out) == 2


def test_drop_short_turns_absorbs_into_previous():
    turns = [
        SpeakerTurn(0, 2, "S1"),
        SpeakerTurn(2, 2.05, "S2"),  # micro-cambio ruidoso
        SpeakerTurn(2.1, 4, "S1"),
    ]
    out = drop_short_turns(turns, min_s=0.2)
    assert all(t.label == "S1" for t in out)


def test_nearest_turn_by_overlap():
    turns = [SpeakerTurn(0, 5, "S1"), SpeakerTurn(5, 10, "S2")]
    assert nearest_turn(1, 2, turns).label == "S1"
    assert nearest_turn(6, 7, turns).label == "S2"


def test_nearest_turn_fallback_by_distance():
    turns = [SpeakerTurn(0, 1, "S1"), SpeakerTurn(8, 9, "S2")]
    # segmento en medio, sin solape: gana el más cercano (S2)
    assert nearest_turn(7.5, 7.9, turns).label == "S2"


def test_assign_single_segment_single_speaker():
    turns = [SpeakerTurn(0, 10, "S1")]
    segs = [_seg(1, 3, "hola que tal")]
    out = assign_speakers(segs, turns)
    assert len(out) == 1
    assert out[0].source == "S1"


def test_split_segment_across_two_speakers_proportional():
    turns = [SpeakerTurn(0, 5, "S1"), SpeakerTurn(5, 10, "S2")]
    seg = _seg(4, 6, "una dos tres cuatro")
    out = split_segment(seg, turns)
    assert len(out) == 2
    assert {o.source for o in out} == {"S1", "S2"}
    # el orden temporal se conserva
    assert out[0].start <= out[1].start
    # el texto se reparte completo
    assert " ".join(o.text for o in out).split() == seg.text.split()


def test_split_segment_uses_word_timestamps():
    turns = [SpeakerTurn(0, 5, "S1"), SpeakerTurn(5, 10, "S2")]
    words = [(1.0, 1.4, "hola"), (1.4, 1.8, "que"), (5.5, 5.9, "adios"), (5.9, 6.2, "tu")]
    seg = _seg(1.0, 6.2, "hola que adios tu", words=words)
    out = split_segment(seg, turns)
    assert len(out) == 2
    assert out[0].source == "S1"
    assert out[0].text == "hola que"
    assert out[1].source == "S2"
    assert out[1].text == "adios tu"


def test_assign_speakers_empty_turns_passthrough():
    segs = [_seg(0, 1, "x", source="TU")]
    out = assign_speakers(segs, [])
    assert out == segs


def test_assign_speakers_no_overlap_nearest():
    turns = [SpeakerTurn(0, 1, "S1"), SpeakerTurn(50, 51, "S2")]
    segs = [_seg(49.5, 49.9, "cerca del final")]
    out = assign_speakers(segs, turns)
    assert len(out) == 1
    assert out[0].source == "S2"


def test_split_segment_no_overlap_uses_nearest():
    turns = [SpeakerTurn(0, 1, "S1")]
    seg = _seg(100, 101, "muy lejos")
    out = split_segment(seg, turns)
    assert len(out) == 1
    assert out[0].source == "S1"
