"""
title: Best-effort coverage for the off-lane documents
layer: backend
public_api: no
summary: The losslessness policy for a digital PDF/HTML document — token and content recall against an independent text layer, with the gap explained bucket by bucket and a gate that can never claim a pass.
"""
# The office lane is graded against a ground-truth semantic tree at token recall
# exactly 1.0. No such tree exists for a PDF: the "source" is an independent
# extraction (poppler's `pdftotext`) which shares no code with docling, and which is
# itself an approximation of the page. So this lane measures rather than gates, and
# the vocabulary has to keep that difference visible:
#
#   * ``gate`` is ``best-effort`` at EVERY recall, including a perfect one. A pass
#     is not claimable without a tree to claim it against, and `build_report`
#     coerces the gate a second time so the contract holds even if a caller lies.
#   * The loss verdict needs BOTH signals low — token recall under `min_recall` AND
#     char-n-gram content recall under `content_min`. Tokenization disagreements
#     between two extractors are routine; a real dropped paragraph moves both.
#   * The gap is explained, not just sized. A bare ``token_recall: 0.97`` does not
#     say whether three words were re-hyphenated across a line break, a running
#     footer survived the strip, or a paragraph is gone — three different fixes,
#     one of which is not a fix at all.
#
# Pure: strings and thresholds in, an OrderedDict out. The poppler and pypdfium2
# subprocess calls that PRODUCE those strings stay in `scripts/build_pdf_bundle.py`.
from collections import OrderedDict

from backend.ingest import (tokenize, coverage, markdown_to_text,
                            char_ngram_recall, is_lossy_explained,
                            strip_running_lines, explain_gap, normalize_pdf_text)

__all__ = ["pdf_coverage_report"]

# How many distinct figure words the block publishes. A bound, not a policy
# threshold: the pool only has to be big enough to grade a caption against.
_FIGURE_TEXT_TOP = 200


def pdf_coverage_report(src_raw, md, furniture, image_text,
                        header_footer_min_frac, min_recall, min_tokens, content_min):
    # type: (str, str, str, str, float, float, int, float) -> tuple
    """The measured best-effort losslessness block for a digital PDF/HTML document.

    Returns ``(loss_block, is_real_loss)``. ``is_real_loss`` is True only under the
    explained-gap model (low token recall AND low content recall), which is what
    should degrade the document's status.

    ``src_raw`` is the RAW page-delimited extraction, not a de-boilerplated one, and
    the difference is load-bearing rather than cosmetic. This function OWNS the
    de-boilerplate policy and applies it once, at one threshold: the body metric
    wants the stripped text so it strips here, and `explain_gap` wants the raw pages
    because it applies the same strip itself in order to bucket what it removes as
    ``residual_boiler`` rather than silently drop it.

    A caller that strips first — at its own threshold — does not merely duplicate the
    work. Those lines leave the ground truth entirely, so the gap denominator stops
    describing the document and the body recall FLATTERS the conversion: in the unit
    test's fixture the same markdown scores 0.77 against the document and a clean
    1.00 against a pre-stripped copy, with the running footer no longer explained but
    simply absent from the measurement. Taking the raw text as the parameter, rather
    than as an optional extra, is what makes that unable to happen by accident.

    The thresholds are REQUIRED and undefaulted for the same class of reason: a
    default here would be a second copy of `ingest.toml`'s policy, free to drift from
    the one the lane actually ran on, and a drift nobody would see in the artifact.
    """
    # The pdf-lane character fold, applied at the ONE choke point every measured
    # string passes through — source, markdown, furniture and figure text alike. A
    # fold applied to one side is not a fold but a thumb on the scale, and doing it
    # here is what makes the symmetry structural rather than a caller's discipline.
    #
    # Without it, a ligature is not merely mismatched, it is INVISIBLE: the shared
    # tokenizer is `[a-z0-9]+`, so `con\ufb01dential` from poppler arrives as `con`
    # + `dential`, neither half matches docling's `confidential`, and a document
    # that lost nothing reports two lost tokens for every ligature it contains.
    src_raw = normalize_pdf_text(src_raw)
    md = normalize_pdf_text(md)
    furniture = normalize_pdf_text(furniture)
    image_text = normalize_pdf_text(image_text)
    exclude = (furniture + " " + image_text).strip()
    md_text = markdown_to_text(md)
    src_stripped = strip_running_lines(src_raw or "", header_footer_min_frac)
    rep = coverage(src_stripped, md_text, exclude=exclude)
    content = char_ngram_recall(
        src_stripped, (md_text + " " + exclude) if exclude else md_text)
    lossy = is_lossy_explained(rep, content, min_recall=min_recall,
                               min_tokens=min_tokens, content_min=content_min)
    loss = OrderedDict()
    loss["method"] = "pdf-text-coverage"
    loss["token_recall"] = round(rep.recall, 4)
    loss["content_recall"] = round(content, 4)
    loss["n_source_tokens"] = rep.n_source
    loss["missing_tokens"] = rep.missing_top if lossy else []
    # Text buried INSIDE figure regions (excluded from the body metric above — it is
    # figure content, not lost body text). Surfaced so the one loss class the text
    # gates cannot recover is VISIBLE per doc: this is exactly what the VLM caption
    # stage exists to bring back.
    fig_tokens = tokenize(image_text) if image_text else []
    loss["figure_text_tokens"] = len(fig_tokens)
    # THE WORDS THEMSELVES, not just how many. Measured on the real corpus:
    # `pdf/kestrel-clocktree.pdf` reports 19 figure-text tokens, and none of those 19
    # words appears anywhere in its bundle — not in document.md, not in
    # structure.json, not here. They are the document's ONLY content, they are
    # excluded from the body ground truth because they are figure content rather
    # than lost body text, and they then exist in no artifact at all.
    #
    # So publishing them is a LOSSLESSNESS gain before it is tooling: without them
    # the bundle genuinely loses the document. It also makes the caption stage
    # gradeable — `backend.ingest.caption_recovery` grades a caption against exactly
    # this pool, so nothing needs a second notion of what a figure's words are.
    #
    # Sorted and deduplicated because it is a POOL, not a transcript, and a stable
    # order keeps the artifact diff-readable. Bounded, because an unbounded dump
    # would make a figure-heavy scan's report enormous and the pool only has to be
    # large enough to grade a caption against. The COUNT above stays truthful
    # whatever the cap does.
    loss["figure_text"] = sorted(set(fig_tokens))[:_FIGURE_TEXT_TOP]
    # WHY the recall is what it is. Each missing occurrence is claimed by the FIRST
    # bucket that can explain it, which leaves `absent` as the only one that is real,
    # unexplained content loss. The buckets PARTITION the source, so a reader can
    # subtract:
    #     covered + fused + numeric + image_text + residual_boiler + short + absent
    #         == gap.n_source
    #
    # `gap.n_source` is deliberately NOT `n_source_tokens`. The body metric excludes
    # furniture and figure text from its ground truth; this one excludes neither, it
    # BUCKETS them — which is the only way a reader can see how much of the gap each
    # of them explained. Two denominators, both named, neither blurred into the other.
    #
    # Stated on every document, including the clean ones: `absent: 0` is a claim about
    # the document, while an absent block is a claim about nobody having looked, and
    # a stated zero is what makes a later non-zero readable.
    gap = explain_gap(src_raw or "", md_text, header_footer_min_frac, image_text)
    loss["gap"] = OrderedDict((
        ("n_source", gap.n_source), ("covered", gap.covered), ("fused", gap.fused),
        ("numeric", gap.numeric), ("image_text", gap.image_text),
        ("residual_boiler", gap.residual_boiler), ("short", gap.short),
        ("absent", gap.absent)))
    loss["absent_top"] = [[tok, n] for tok, n in gap.absent_top]
    loss["ocr_used"] = False
    # Not a variable. There is no branch in this function that can reach "pass".
    loss["gate"] = "best-effort"
    return loss, lossy
