"""
title: Knowledge-metadata public API
layer: backend
public_api: yes
summary: Tiered document metadata — the controlled vocabulary, the field inventory, tier-1 derivations, and the linter.
"""
# Callers import FROM HERE, never from the private submodules.
#
# This package answers three questions about a document's metadata block and
# nothing else:
#   WHO MAY WRITE THIS FIELD?   _schema  — tier 0/1/2 plus the authored-only flag
#   IS THIS VALUE A TERM?       _vocab   — closed / registry / ref governance
#   IS THIS DOCUMENT SANE?      _lint    — cardinality, membership, hygiene, refs
#   IS THE CORPUS SANE?         _corpus  — identity, synonymy, entities, skew
# with _derive holding the fixed-rule tier-1 computations. It is PURE apart from
# reading the vocabulary file; the bundle walk lives in scripts/kb_lint.py and the
# model call in scripts/enrich_metadata.py.
#
# _lint and _corpus are split by what they can SEE, not by subject matter. Every
# check in _corpus is one a single document is structurally incapable of answering:
# a duplicated id, one entity spelled two ways, a term used by nobody. Those are not
# defects in any one file — each document is internally consistent — which is why
# they survive per-file linting indefinitely.
from ._corpus import (alias_suggestions, apply_promotions, corpus_findings,
                      coverage_report,
                      entity_report, graph_report, identity_report, norm_key,
                      skew_report, strip_polarity, synonym_report,
                      vocabulary_hygiene, vocabulary_usage, CorpusFinding)
from ._derive import (body_anchors, derive_uid, heading_anchor, keyword_candidates,
                      reading_time_minutes, slugify, word_count, WORDS_PER_MINUTE)
from ._enrich import (abstract_floor, accept_model_meta, harvested_links,
                      is_authored, is_protected, link_category,
                      merge_group_evidence, meta_coverage, next_review_due,
                      order_meta, record_source, request_spec,
                      revalidate_generated, set_provenance, source_url,
                      title_floor, unique_id, value_sha, value_source,
                      EVIDENCE_KEY, REVIEW_INTERVAL_DAYS, UNKNOWN)
from ._lint import (corpus_report, facet_report, lint_document, normalize_document,
                    FacetRow, Finding, ERROR, INFO, WARN, VERDICT_NOT_A_FACET,
                    VERDICT_OK, VERDICT_SPARSE, VERDICT_THIN)
from ._schema import (field, field_names, group_required, group_vocab,
                      in_knowledge,
                      knowledge_document, knowledge_field_names,
                      knowledge_payload, merge_meta, meta_collisions,
                      model_writable,
                      proposed_key, record_required, record_vocab, split_meta,
                      Field, FIELDS, ALIAS_FIELDS, CANONICAL_ID, DOCUMENT_FILE,
                      KNOWLEDGE_FILE, KNOWLEDGE_HEADER, META_KEY,
                      PROVENANCE_KEY, REF_FIELDS, SCHEMA_VERSION, SOURCE_AUTHORED,
                      SOURCE_DERIVED, SOURCE_EXTRACTED, SOURCE_GENERATED,
                      TIER_NAMES, VALUE_SOURCES)
from ._vocab import (check_schema_bindings, load_vocab, vocab_path, Vocabulary,
                     VocabularyError)

__all__ = [
    "ALIAS_FIELDS",
    "CANONICAL_ID",
    "CorpusFinding",
    "DOCUMENT_FILE",
    "ERROR",
    "EVIDENCE_KEY",
    "FIELDS",
    "FacetRow",
    "Field",
    "Finding",
    "INFO",
    "KNOWLEDGE_FILE",
    "KNOWLEDGE_HEADER",
    "META_KEY",
    "PROVENANCE_KEY",
    "REF_FIELDS",
    "REVIEW_INTERVAL_DAYS",
    "SCHEMA_VERSION",
    "SOURCE_AUTHORED",
    "SOURCE_DERIVED",
    "SOURCE_EXTRACTED",
    "SOURCE_GENERATED",
    "TIER_NAMES",
    "UNKNOWN",
    "VALUE_SOURCES",
    "VERDICT_NOT_A_FACET",
    "VERDICT_OK",
    "VERDICT_SPARSE",
    "VERDICT_THIN",
    "Vocabulary",
    "VocabularyError",
    "WARN",
    "WORDS_PER_MINUTE",
    "abstract_floor",
    "accept_model_meta",
    "alias_suggestions",
    "apply_promotions",
    "body_anchors",
    "check_schema_bindings",
    "corpus_findings",
    "corpus_report",
    "coverage_report",
    "derive_uid",
    "entity_report",
    "facet_report",
    "field",
    "field_names",
    "graph_report",
    "group_required",
    "group_vocab",
    "harvested_links",
    "heading_anchor",
    "identity_report",
    "in_knowledge",
    "is_authored",
    "is_protected",
    "keyword_candidates",
    "knowledge_document",
    "knowledge_field_names",
    "knowledge_payload",
    "link_category",
    "lint_document",
    "load_vocab",
    "merge_group_evidence",
    "merge_meta",
    "meta_collisions",
    "meta_coverage",
    "model_writable",
    "next_review_due",
    "norm_key",
    "normalize_document",
    "order_meta",
    "proposed_key",
    "reading_time_minutes",
    "record_required",
    "record_source",
    "record_vocab",
    "request_spec",
    "revalidate_generated",
    "set_provenance",
    "skew_report",
    "slugify",
    "source_url",
    "split_meta",
    "strip_polarity",
    "synonym_report",
    "title_floor",
    "unique_id",
    "value_sha",
    "value_source",
    "vocab_path",
    "vocabulary_hygiene",
    "vocabulary_usage",
    "word_count",
]
