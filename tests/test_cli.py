import pytest

from clipper.candidates import CandidatesError
from clipper.cli import _adjust_targets, _clip_slugs
from clipper.models import Candidate


def _cand(title):
    return Candidate(title=title, hook="h", category="quote", start=1.0, end=9.0)


def test_adjust_targets_all():
    assert _adjust_targets("all", 3) == [0, 1, 2]


def test_adjust_targets_single_index_is_one_based():
    assert _adjust_targets("2", 5) == [1]


def test_adjust_targets_rejects_out_of_range():
    with pytest.raises(CandidatesError):
        _adjust_targets("9", 3)
    with pytest.raises(CandidatesError):
        _adjust_targets("0", 3)


def test_adjust_targets_rejects_non_number():
    with pytest.raises(CandidatesError):
        _adjust_targets("xyz", 3)


def test_clip_slugs_are_numbered():
    cands = [_cand("My Clip"), _cand("My Clip"), _cand("Another One")]
    slugs = _clip_slugs(cands)
    # Numeric prefix matches the clip number and keeps names unique even
    # when two clips share a title.
    assert slugs == ["01-my-clip", "02-my-clip", "03-another-one"]
    # Stable: re-deriving gives the identical mapping (so --adjust hits
    # the same files the original render produced).
    assert _clip_slugs(cands) == slugs


def test_clip_slugs_prefix_width_grows_with_count():
    cands = [_cand(f"Clip {i}") for i in range(12)]
    slugs = _clip_slugs(cands)
    assert slugs[0] == "01-clip-0"
    assert slugs[11] == "12-clip-11"
