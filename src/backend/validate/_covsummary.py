"""
title: Corpus-wide coverage summary
layer: backend
public_api: no
summary: Turns a pile of per-document coverage records into the short text an operator reads — worst documents first, with the tokens and the figures that were actually lost.
"""
# Per-document reports answer "did THIS conversion hold up?". Nobody reads 544 of
# them. This answers the corpus question — where is the loss, and is it worth
# chasing — and it is the one place that must not flatter the corpus:
#
#   * A mean recall over 544 documents is a number that hides every failure. The
#     summary leads with the WORST documents and names the tokens they lost.
#   * A tiny document at 0.5 recall is noise, not a finding: two words out of four.
#     ``min_tokens`` is the floor below which a ratio means nothing, and the count
#     of what it excluded is reported rather than quietly dropped.
#   * Figure loss is reported SEPARATELY from token loss. A slide deck whose text
#     recall is a perfect 1.0 can still have lost every diagram, and averaging the
#     two would let each hide the other.
#
# Pure: records in, text out. Reading the JSONL files is the runner's job.
#
# CONVENTIONS §1 makes ``__all__`` the machine-checkable public API of a source
# file, private module or not — 32 of the 36 modules under src/backend carry one and
# this was one of the four that did not. ``summarize`` is what the package re-exports
# (as ``summarize_coverage``); ``worst_documents`` and ``figure_losses`` are the two
# halves of it that are separately meaningful, and separately tested, but they stay
# OUT of ``backend.validate.__all__`` deliberately: the package surface is kept tight
# (src/backend/validate/CLAUDE.md), and widening it with helpers no runner calls
# would freeze an internal shape as a contract.
__all__ = ["summarize", "worst_documents", "figure_losses"]

_BAR = "-" * 72


def _pct(value):
    # type: (float) -> str
    return "%.1f%%" % (100.0 * float(value or 0.0))


def _missing_phrase(missing_top, limit=4):
    # type: (list, int) -> str
    """``[["table", 60], ["skew", 5]]`` -> ``table x60, skew x5``."""
    parts = []
    for item in (missing_top or [])[:limit]:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            parts.append("%s x%s" % (item[0], item[1]))
        elif item:
            parts.append(str(item))
    return ", ".join(parts)


def worst_documents(records, worst_n=10, min_tokens=50):
    # type: (list, int, int) -> tuple
    """(worst records by recall, how many were too small to judge).

    Sorted by recall then by absolute tokens missing, so a 0.9 over 2000 tokens
    outranks a 0.9 over 60 — the same ratio, twenty times the loss."""
    gradeable, too_small = [], 0
    for rec in records or []:
        if int(rec.get("n_source", 0) or 0) < int(min_tokens or 0):
            too_small += 1
            continue
        gradeable.append(rec)
    gradeable.sort(key=lambda r: (float(r.get("recall", 1.0) or 0.0),
                                  -int(r.get("n_missing", 0) or 0)))
    worst = [r for r in gradeable if float(r.get("recall", 1.0) or 0.0) < 1.0]
    return worst[:max(0, int(worst_n or 0))], too_small


def figure_losses(records):
    # type: (list) -> list
    """Records whose figures block says pixels were lost, worst first.

    Kept apart from token recall on purpose: ``lossless`` here is a claim about
    figures only, and a document can be perfect on one axis and empty on the other."""
    lossy = []
    for rec in records or []:
        figs = rec.get("figures") or {}
        if not figs:
            continue
        lost = int(figs.get("n_lost", 0) or 0)
        if lost or figs.get("bailed") or figs.get("lossless") is False:
            lossy.append(rec)
    lossy.sort(key=lambda r: -int((r.get("figures") or {}).get("n_lost", 0) or 0))
    return lossy


def summarize(records, worst_n=10, min_tokens=50):
    # type: (list, int, int) -> str
    """The operator-facing summary of a corpus's coverage records."""
    records = list(records or [])
    lines = ["coverage records: %d total" % len(records)]
    if not records:
        lines.append("nothing to summarize: no coverage records were found")
        return "\n".join(lines) + "\n"

    perfect = sum(1 for r in records
                  if float(r.get("recall", 0.0) or 0.0) >= 1.0)
    total_src = sum(int(r.get("n_source", 0) or 0) for r in records)
    total_missing = sum(int(r.get("n_missing", 0) or 0) for r in records)
    lines.append("lossless (recall == 1.0): %d of %d" % (perfect, len(records)))
    if total_src:
        lines.append("tokens: %d source, %d missing (%s of the corpus)"
                     % (total_src, total_missing,
                        _pct(float(total_missing) / total_src)))

    worst, too_small = worst_documents(records, worst_n, min_tokens)
    lines.append(_BAR)
    if worst:
        lines.append("worst documents (recall < 1.0, at least %d source tokens):"
                     % min_tokens)
        for rec in worst:
            lines.append("  %-8s %-40s %d/%d missing"
                         % (_pct(rec.get("recall")), rec.get("rel", rec.get("id", "?")),
                            int(rec.get("n_missing", 0) or 0),
                            int(rec.get("n_source", 0) or 0)))
            phrase = _missing_phrase(rec.get("missing_top"))
            if phrase:
                lines.append("           lost: %s" % phrase)
    else:
        lines.append("no document below 1.0 recall above the %d-token floor"
                     % min_tokens)
    if too_small:
        # Named, never silently dropped: "0 documents below the floor" and
        # "40 documents nobody could grade" must not read the same.
        lines.append("  (%d document(s) under %d tokens: a ratio over that few "
                     "tokens is not a finding)" % (too_small, min_tokens))

    lossy = figure_losses(records)
    lines.append(_BAR)
    if lossy:
        lines.append("figures: %d document(s) lost pixels" % len(lossy))
        for rec in lossy[:max(0, int(worst_n or 0))]:
            figs = rec.get("figures") or {}
            lines.append("  %-40s %d lost of %d, %s"
                         % (rec.get("rel", rec.get("id", "?")),
                            int(figs.get("n_lost", 0) or 0),
                            int(figs.get("n_body", 0) or 0),
                            "binding bailed" if figs.get("bailed") else "gated"))
    else:
        lines.append("figures: no document reports lost pixels")
    return "\n".join(lines) + "\n"
