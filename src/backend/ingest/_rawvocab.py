"""
title: Raw-vocabulary token repair (private)
layer: backend
public_api: no
summary: Rejoin identifier tokens a layout model split ("SLV_AD DR4_EN"), anchored to the source's own raw text layer.
"""
# 3.6-compatible, stdlib only. PURE: strings in, strings out.
#
# WHY: TableFormer (docling's table-structure model) rejoins wrapped table-cell text
# with a stray space, so an identifier that wraps inside a cell comes out split:
# ``SMBUS_PERSISTENT_SLV_AD DR4_EN``. The characters all survive (token recall can
# look fine) but the IDENTIFIER is destroyed — the single worst kind of damage for a
# technical corpus, since exact-name lookup/grep/RAG anchors on it. The source's own
# raw text layer (pdftotext) is clean in these cases, so it is the ground truth:
# a split is repaired iff the JOINED form exists verbatim in the raw layer. Measured
# on the experiment corpus: fixes 100% of split artifacts with zero false joins
# (docling_test EXP-1) at ~ms/doc cost.
#
# Vocabulary membership is the guard against false joins: only identifier-LIKE
# tokens (containing ``_`` or a camelCase boundary, length >= MIN_IDENT_LEN) enter
# the vocabulary, so prose word pairs ("the register") can never be glued.
import re

__all__ = ["identifier_vocab", "repair_split_tokens"]

MIN_IDENT_LEN = 6                      # shorter tokens are too join-prone to trust
_PUNCT = ".,:;()[]{}|<>\"'`"
# camelCase boundary — either lower->upper ("docId") or acronym->word ("DFTController",
# whose only case boundary is UPPER,UPPER,lower). Plain capitalized prose ("The") and
# ALL-CAPS words ("REGISTER") match neither.
_CAMEL = re.compile(r"[a-z][A-Z]|[A-Z]{2}[a-z]")
# A candidate fragment: alphanumerics/underscores, where the underscore may be
# markdown-escaped (docling exports ``DEST\_MSIZE``). Escapes are preserved in the
# repaired output; only the vocabulary LOOKUP normalizes them away.
_FRAG = r"(?:[A-Za-z0-9]|\\_|_)+"
# A RUN: fragments separated by single spaces. Repair works run-wise so ADJACENT
# pairs are all considered (a flat left-to-right re.sub skips past every second
# fragment and misses half the candidate pairs).
_RUN = re.compile(r"%s(?: %s)+" % (_FRAG, _FRAG))
_MAX_JOIN = 4                          # an identifier splits into at most a few parts


def identifier_vocab(raw_text, min_len=MIN_IDENT_LEN):
    # type: (str, int) -> set
    """Identifier-like tokens of a source's RAW text layer — the repair ground truth.

    Whitespace-split, punctuation-stripped tokens of length >= ``min_len`` that look
    like identifiers (contain ``_`` or a lowercase->uppercase camelCase boundary).
    Only such tokens can ever authorize a rejoin, which is what makes the repair
    safe on prose."""
    vocab = set()
    for tok in (raw_text or "").split():
        tok = tok.strip(_PUNCT)
        if len(tok) >= min_len and ("_" in tok or _CAMEL.search(tok)):
            vocab.add(tok)
    return vocab


def repair_split_tokens(md, vocab):
    # type: (str, set) -> str
    """Rejoin ``A B`` -> ``AB`` wherever the joined form is a known raw-layer token.

    ``vocab`` comes from ``identifier_vocab`` over the source's own text layer, so a
    join happens only when the source proves the identifier was one token. Markdown
    escapes inside the fragments (``DEST\\_MSIZE``) are preserved verbatim — only the
    membership test normalizes them. Within each run of space-separated fragments the
    LONGEST provable join wins (up to ``_MAX_JOIN`` fragments), so an identifier the
    model split more than once (``A B C``) heals in one pass. Anything not provably
    split is left byte-for-byte untouched."""
    if not md or not vocab:
        return md

    def _repair_run(m):
        parts = m.group(0).split(" ")
        out = []
        i = 0
        n = len(parts)
        while i < n:
            best = -1
            acc = parts[i]
            for k in range(i + 1, min(n, i + _MAX_JOIN)):
                acc += parts[k]
                if acc.replace("\\", "") in vocab:
                    best = k
            if best >= 0:
                out.append("".join(parts[i:best + 1]))
                i = best + 1
            else:
                out.append(parts[i])
                i += 1
        return " ".join(out)

    return _RUN.sub(_repair_run, md)
