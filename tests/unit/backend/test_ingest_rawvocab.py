"""
title: Unit — backend.ingest raw-vocabulary token repair
kind: tests
layer: backend
summary: Rejoin layout-model-split identifiers anchored to the source's raw text layer; zero false joins.
"""
import pytest
from backend.ingest import identifier_vocab, repair_split_tokens

pytestmark = pytest.mark.unit

RAW = ("The SMBUS_PERSISTENT_SLV_ADDR4_EN bit gates DFTController access; "
       "see DEST_MSIZE and the plain the register words.")


def test_vocab_keeps_identifiers_only():
    v = identifier_vocab(RAW)
    assert "SMBUS_PERSISTENT_SLV_ADDR4_EN" in v      # underscore identifier
    assert "DFTController" in v                       # camelCase identifier
    assert "DEST_MSIZE" in v
    assert "register" not in v                        # prose word: no _ / no camel
    assert "bit" not in v                             # too short AND prose


def test_vocab_strips_punctuation_and_honors_min_len():
    v = identifier_vocab("(AB_CD), X_Y end.")
    assert not v                                      # AB_CD < 6 chars, X_Y too short
    assert identifier_vocab("(ABC_DEF).") == {"ABC_DEF"}


def test_repair_rejoins_a_known_split():
    # THE TableFormer artifact: a wrapped table cell rejoined with a stray space.
    v = identifier_vocab(RAW)
    md = "| SMBUS_PERSISTENT_SLV_AD DR4_EN | 0x1 |"
    assert "SMBUS_PERSISTENT_SLV_ADDR4_EN" in repair_split_tokens(md, v)


def test_repair_never_joins_prose_pairs():
    # Word pairs whose concatenation is NOT a raw-layer identifier stay untouched —
    # the vocabulary membership is the false-join guard (EXP-1: zero false joins).
    v = identifier_vocab(RAW)
    md = "the register holds DFT Controller notes"    # "DFTController" IS in vocab...
    out = repair_split_tokens(md, v)
    assert "DFTController" in out                      # ...so a real split heals
    assert "theregister" not in out                    # but prose never glues


def test_repair_preserves_markdown_escapes():
    # docling exports escaped underscores (DEST\_MSIZE): lookup normalizes them,
    # output keeps them byte-for-byte.
    v = identifier_vocab(RAW)
    md = r"field DEST\_MS IZE is wide"
    # normalized join DEST_MSIZE... note the split lands mid-token after the escape
    out = repair_split_tokens(md, v)
    assert r"DEST\_MSIZE" in out


def test_repair_handles_a_double_split():
    v = identifier_vocab("token ALPHA_BETA_GAMMA_DELTA here")
    md = "see ALPHA_BETA _GAMMA _DELTA now"
    assert "ALPHA_BETA_GAMMA_DELTA" in repair_split_tokens(md, v)


def test_repair_is_noop_without_vocab_or_text():
    assert repair_split_tokens("", {"A_B_C"}) == ""
    assert repair_split_tokens("A B", set()) == "A B"
