"""
title: Document metadata schema — the tiered field inventory (private)
layer: backend
public_api: no
summary: Which metadata field belongs to which tier, which vocabulary governs it, and who is allowed to write it.
"""
# 3.6-compatible. Stdlib only. Pure data + pure predicates — no disk, no model.
#
# THE TIER MODEL is the part with no standard equivalent, and it is deliberately
# ours: DCMI says what a field MEANS, PROV-O says what PRODUCED it, but neither
# encodes HOW A FIELD IS VERIFIED, which is the only question that decides whether
# a model may write it.
#
#   tier 0  deterministic  — read from the bytes or the filesystem. Same input,
#                            same value, forever. No judgement, no model.
#   tier 1  derived        — computed from tier-0 content by a fixed rule (a slug
#                            from a title, minutes from a word count). Still no
#                            judgement, but it depends on a tier-2 parent when the
#                            parent is the thing being derived from.
#   tier 2  model          — a model PROPOSES and a human CORRECTS. Classification,
#                            summarisation, entity and relation extraction.
#
# Orthogonal to the tier: ``authored_only``. Some fields are a safety or
# accountability boundary — who owns this, how secret is it, when was it last
# reviewed. Their whole purpose is that a person stood behind them, so no tier-2
# machinery may write them even though a model could certainly guess. `owner` and
# `confidentiality` are not "hard tier-2 fields"; they are not tier-2 fields.
#
# AUTHORED ALWAYS WINS. A generated value fills a gap; it never overwrites a value
# a person wrote. Every field records which it was in ``_provenance`` so the
# distinction survives in the file rather than in someone's memory.
from collections import namedtuple, OrderedDict

__all__ = ["Field", "FIELDS", "META_KEY", "PROVENANCE_KEY", "SCHEMA_VERSION",
           "TIER_NAMES", "SOURCE_AUTHORED", "SOURCE_GENERATED", "SOURCE_DERIVED",
           "SOURCE_EXTRACTED", "VALUE_SOURCES", "field", "field_names",
           "model_writable", "proposed_key", "record_required", "record_vocab",
           "group_vocab", "REF_FIELDS", "DOCUMENT_FILE", "KNOWLEDGE_FILE",
           "KNOWLEDGE_HEADER", "in_knowledge", "knowledge_document",
           "knowledge_field_names", "knowledge_payload", "meta_collisions",
           "split_meta", "merge_meta"]

# The whole metadata block lives under ONE top-level front-matter key. The pipeline
# keys (doc_id, markdown_sha256, lane, ...) stay where they are and keep meaning
# exactly what they meant; nesting is what lets the enrichment stage rewrite its own
# block wholesale without ever touching a key it does not own.
META_KEY = "meta"
PROVENANCE_KEY = "_provenance"

# THE DESCRIPTOR / KNOWLEDGE SEAM.
#
# The metadata a document carries is two different things wearing one name:
#
#   DESCRIPTORS describe THE DOCUMENT — id, title, type, tags, status, owner. Small,
#              bounded, and what retrieval filters on. Markdown tooling reads them
#              from front matter for free, so they stay in `document.md`.
#   KNOWLEDGE  describes THE WORLD, extracted from the document — entities,
#              relations, decisions, risks, links. Unbounded, consumed by a graph
#              loader that never wants the prose, and 100% structured.
#
# Measured on the worked example, the knowledge payload was ~19k characters against
# ~3.5k of descriptors — roughly 5:1 — so leaving it in front matter makes every
# consumer that only wants the body pay for a relation table it will discard.
# `structure.json` and `report.json` set the precedent: in this bundle every
# derived, machine-consumed artefact is already a sibling JSON file.
#
# The seam is MECHANICAL, not a matter of taste: a field moves iff it is a `records`
# or `groups` field and is not `authored_only`. The carve-out matters — the
# accountability block (`accountable_roles`) is a record list, but it is a statement
# about who stands behind the document, so it belongs with the document.
KNOWLEDGE_FILE = "knowledge.json"

# The sidecar belongs to THIS file and to no other markdown in the directory. A
# bundle may hold notes.md or a README beside its document.md; binding the sidecar
# to the directory would hand the same entities and relations to each of them,
# double-counting every entity and inventing id collisions between a document and
# its own neighbour.
DOCUMENT_FILE = "document.md"

# Keys `knowledge.json` carries so a graph loader never has to open `document.md`:
# the join keys, plus the versions that say what wrote it.
KNOWLEDGE_HEADER = ("doc_id", "id", "uid", "schema_version", "vocab_version",
                    "markdown_sha256")

# Versions the METADATA BLOCK only — not the bundle contract, which is a separate
# and still-unversioned decision. Bump when a field is added, removed or retiered;
# the vocabulary carries its own version, because a term-list change invalidates
# classifications while leaving every other field untouched.
#   v1  single `meta` block in document.md front matter
#   v2  descriptors stay in front matter; the knowledge payload moves to
#       knowledge.json (see the seam above)
SCHEMA_VERSION = 2

TIER_NAMES = {0: "deterministic", 1: "derived", 2: "model"}

SOURCE_EXTRACTED = "extracted"   # tier 0: lifted from the source bytes
SOURCE_DERIVED = "derived"       # tier 1: computed by a fixed rule
SOURCE_GENERATED = "generated"   # tier 2: proposed by a model
SOURCE_AUTHORED = "authored"     # a person wrote it; outranks all of the above
VALUE_SOURCES = (SOURCE_EXTRACTED, SOURCE_DERIVED, SOURCE_GENERATED, SOURCE_AUTHORED)

# kind:
#   scalar   a single value
#   list     a list of scalars
#   map      a mapping of scalar subfields
#   records  a list of mappings (relations, decisions, risks, open_questions)
#   groups   a mapping of name -> records (entities, links)
Field = namedtuple("Field", ["name", "tier", "kind", "vocab", "authored_only",
                             "maps_to", "note"])


def _f(name, tier, kind, vocab="", authored_only=False, maps_to="", note=""):
    return Field(name, tier, kind, vocab, authored_only, maps_to, note)


FIELDS = (
    # ---- tier 0: deterministic ------------------------------------------------
    _f("schema_version", 0, "scalar", maps_to="local",
       note="which revision of this inventory wrote the block"),
    _f("vocab_version", 0, "scalar", maps_to="local",
       note="which revision of the term list the values were checked against"),
    _f("uid", 0, "scalar", maps_to="dcterms:identifier",
       note="stable machine identity, namespaced from the source path"),
    _f("version", 0, "scalar", maps_to="local",
       note="the source document's own revision, when it declares one"),
    _f("source", 0, "map", maps_to="dcterms:source",
       note="uri/publisher/authored_by/supersedes/is_derivative"),
    _f("extraction", 0, "map", maps_to="prov:Activity",
       note="run_at/schema/extractor — prov:generatedAtTime + prov:SoftwareAgent"),

    # ---- tier 1: derived by rule ----------------------------------------------
    _f("id", 1, "scalar", maps_to="dcterms:identifier",
       note="human-stable slug; authored wins, derived from title otherwise"),
    _f("slug", 1, "scalar", maps_to="local", note="url form of the title"),
    _f("word_count", 1, "scalar", maps_to="local"),
    _f("reading_time_minutes", 1, "scalar", maps_to="local"),

    # ---- tier 2: model proposes, human corrects -------------------------------
    _f("title", 2, "scalar", maps_to="dcterms:title",
       note="extracted when the source has a real one; the junk-title path is "
            "exactly where a model should propose from the first heading"),
    _f("short_title", 2, "scalar", maps_to="local", note="compression judgement"),
    _f("abstract", 2, "scalar", maps_to="dcterms:abstract"),
    _f("type", 2, "scalar", vocab="document_types", maps_to="dcterms:type"),
    _f("subtype", 2, "list", vocab="subtype", maps_to="dcterms:type"),
    _f("lang", 2, "scalar", vocab="lang", maps_to="dcterms:language"),
    _f("tags", 2, "list", vocab="tags", maps_to="skos:Concept"),
    _f("keywords", 2, "list", vocab="keywords", maps_to="skos:Concept"),
    _f("topics", 2, "list", vocab="topics", maps_to="dcterms:subject"),
    _f("audience", 2, "list", vocab="audience", maps_to="dcterms:audience"),
    _f("aliases", 2, "list", maps_to="skos:altLabel"),
    _f("entities", 2, "groups", vocab="entity_types", maps_to="schema:Thing"),
    _f("relations", 2, "records", vocab="relation_predicates", maps_to="local"),
    _f("decisions", 2, "records", vocab="decision_status", maps_to="madr:status"),
    _f("risks", 2, "records", vocab="impact", maps_to="iso31000"),
    _f("open_questions", 2, "records", maps_to="local"),
    _f("links", 2, "groups", vocab="link_categories", maps_to="dcterms:references"),
    _f("see_also", 2, "list", maps_to="dcterms:references"),
    _f("prerequisites", 2, "list", maps_to="dcterms:requires"),
    _f("out_of_scope", 2, "list", maps_to="local"),

    # ---- authored only: accountability and safety boundaries ------------------
    _f("status", 2, "scalar", vocab="document_status", authored_only=True,
       maps_to="local"),
    _f("confidentiality", 2, "scalar", vocab="confidentiality", authored_only=True,
       maps_to="dcterms:accessRights",
       note="a field whose entire purpose is a safety boundary cannot have a "
            "model as its author"),
    _f("classification", 2, "scalar", authored_only=True, maps_to="local",
       note="the human-facing wording of confidentiality"),
    _f("owner", 2, "scalar", authored_only=True, maps_to="prov:wasAttributedTo"),
    _f("accountable_roles", 2, "records", authored_only=True,
       maps_to="prov:wasAttributedTo"),
    _f("review_cadence", 2, "scalar", vocab="review_cadence", authored_only=True,
       maps_to="local"),
    _f("last_reviewed", 2, "scalar", authored_only=True, maps_to="dcterms:modified"),
    _f("next_review_due", 2, "scalar", authored_only=True, maps_to="local"),
    _f("validated_against_version", 2, "scalar", authored_only=True, maps_to="local",
       note="the product/tool version the document's claims were checked against"),
)

_BY_NAME = OrderedDict((f.name, f) for f in FIELDS)

# Sub-keys inside a record list that carry their own controlled vocabulary. Without
# this table a relation's ``p`` would be checked but its ``mode`` qualifier would
# not, and the qualifier is where the nuance was deliberately pushed.
_RECORD_VOCAB = {
    "relations": OrderedDict([("p", "relation_predicates"), ("mode", "failure_modes")]),
    "decisions": OrderedDict([("status", "decision_status")]),
    "risks": OrderedDict([("impact", "impact"), ("mode", "failure_modes")]),
}

# Which of those sub-keys a record MUST carry. The rest are qualifiers: nuance was
# deliberately pushed into them, so most records legitimately omit them and
# demanding one would make every ordinary relation an error. A record missing a
# REQUIRED key is a different thing — nothing then checks it against a vocabulary.
_RECORD_REQUIRED = {
    "relations": ("p",),
    "decisions": ("status",),
    "risks": ("impact",),
}

# For a `groups` field: which vocabulary governs the GROUP NAMES, and (for
# entities) which governs the member ``type``.
_GROUP_VOCAB = {
    "entities": OrderedDict([("member_type", "entity_types")]),
    "links": OrderedDict([("group_name", "link_categories")]),
}

# Governed by referential integrity rather than by a term list (vocab.yaml `refs`).
REF_FIELDS = ("ref", "backs", "see_also", "control", "protects")


def field_names():
    # type: () -> list
    return list(_BY_NAME.keys())


def field(name):
    # type: (str) -> Field
    if name not in _BY_NAME:
        raise KeyError("no such metadata field: %r" % (name,))
    return _BY_NAME[name]


def model_writable(name):
    # type: (str) -> bool
    """May an enrichment pass write this field at all?

    Tier 2 and not an accountability boundary. This is the single predicate the
    enrichment stage consults; nothing else decides what a model may touch.
    """
    f = _BY_NAME.get(name)
    return bool(f) and f.tier == 2 and not f.authored_only


def proposed_key(name):
    # type: (str) -> str
    """Where an unknown REGISTRY value goes: ``tags`` -> ``tags_proposed``.

    A proposal is never written into the field itself. That is what keeps the
    registry's promotion rule (>= 3 documents) meaningful instead of retroactive.
    """
    return name + "_proposed"


def record_vocab(name):
    # type: (str) -> dict
    return dict(_RECORD_VOCAB.get(name) or {})


def record_required(name):
    # type: (str) -> tuple
    """Sub-keys a record of this field must carry (the rest are optional qualifiers)."""
    return tuple(_RECORD_REQUIRED.get(name) or ())


def group_vocab(name):
    # type: (str) -> dict
    return dict(_GROUP_VOCAB.get(name) or {})


def in_knowledge(name):
    # type: (str) -> bool
    """Does this field live in ``knowledge.json`` rather than in front matter?

    Mechanical on purpose (see the seam note above): a `records`/`groups` field is
    the unbounded, graph-shaped payload, EXCEPT when it is `authored_only` — an
    accountability statement belongs with the document, not in a derived sidecar.
    A ``<field>_proposed`` slot follows its field, so the promotion evidence never
    ends up in a different file from the values it is evidence about.
    """
    if name.endswith("_proposed"):
        name = name[:-len("_proposed")]
    f = _BY_NAME.get(name)
    return bool(f) and f.kind in ("records", "groups") and not f.authored_only


def knowledge_field_names():
    # type: () -> list
    return [f.name for f in FIELDS if in_knowledge(f.name)]


def split_meta(meta):
    # type: (dict) -> tuple
    """``meta`` -> ``(front_matter_block, knowledge_payload)``.

    ``_provenance`` splits with its fields, so each file records the origin of what
    it actually holds and neither is a fragment that has to be joined to be read.
    Unknown keys stay in front matter: this function must never be the thing that
    silently drops a field the schema has not heard of.

    ROUND-TRIP: ``merge_meta(*split_meta(m))`` reproduces ``m``'s content exactly,
    with one deliberate normalisation — an EMPTY or ``None`` ``_provenance`` comes
    back absent, because "no provenance recorded" and "an empty provenance record"
    are the same statement (``order_meta`` already drops it on the write path). Key
    ORDER is not preserved; canonical order is ``order_meta``'s job.
    """
    meta = meta or {}
    front, know = OrderedDict(), OrderedDict()
    for key, val in meta.items():
        if key == PROVENANCE_KEY:
            continue
        (know if in_knowledge(key) else front)[key] = val
    prov = meta.get(PROVENANCE_KEY)
    if prov is not None and not isinstance(prov, dict):
        # A malformed `_provenance` is a state the corpus really can be in —
        # `_lint._check_authorship` reports it rather than rejecting the document —
        # so this must not be the thing that crashes the enricher on it. Keep it
        # verbatim on the document side, where the linter will still find it.
        front[PROVENANCE_KEY] = prov
        return (front, know)
    fprov, kprov = OrderedDict(), OrderedDict()
    for name, rec in (prov or {}).items():
        (kprov if in_knowledge(name) else fprov)[name] = rec
    if fprov:
        front[PROVENANCE_KEY] = fprov
    if kprov:
        know[PROVENANCE_KEY] = kprov
    return (front, know)


def meta_collisions(front, knowledge):
    # type: (dict, dict) -> list
    """Keys that exist in BOTH files — a duplicated payload, i.e. two truths.

    ``merge_meta`` picks a winner so readers see one coherent view, and the next
    enrichment run then REWRITES the loser away. That makes a collision a state that
    silently costs data, so it has to be reported rather than merely resolved: the
    merged mapping the linter grades cannot show that two live copies ever existed.

    Reachable from a hand edit, or from a half-finished write. Not from the v1 -> v2
    migration, where the knowledge lives only in front matter and there is no sidecar
    to collide with.
    """
    front, knowledge = front or {}, knowledge or {}
    out = set(k for k in front if k != PROVENANCE_KEY and k in knowledge)
    fp = front.get(PROVENANCE_KEY)
    kp = knowledge.get(PROVENANCE_KEY)
    if isinstance(fp, dict) and isinstance(kp, dict):
        out |= set("%s.%s" % (PROVENANCE_KEY, n) for n in fp if n in kp)
    return sorted(out)


def knowledge_payload(data):
    # type: (dict) -> OrderedDict
    """The payload of a parsed ``knowledge.json``, header keys removed.

    Header keys are duplicated FROM the document on purpose (self-containment), so
    they are not metadata values and must never be merged back in as if they were —
    a stale `id` in a sidecar would otherwise outvote the document's own.
    """
    out = OrderedDict()
    for key, val in (data or {}).items():
        if key not in KNOWLEDGE_HEADER:
            out[key] = val
    return out


def knowledge_document(meta, doc_id="", markdown_sha256="", keep_empty=False):
    # type: (dict, str, str, bool) -> OrderedDict
    """The full ``knowledge.json`` object for a document: header, then payload.

    Key order follows ``meta``'s, so a caller that wants canonical bytes passes an
    already-ordered mapping (``order_meta``) — this module cannot call it without a
    circular import, and ordering is a write-path concern anyway.

    Returns an EMPTY mapping when there is no payload and ``keep_empty`` is false, so
    a caller can tell "nothing to write" from "write this" and a document that never
    had knowledge does not get a file full of nothing. ``keep_empty=True`` returns the
    header alone — that is how a sidecar whose contents were all removed gets
    explicitly emptied instead of left behind serving relations the document no longer
    claims.
    """
    meta = meta or {}
    _front, payload = split_meta(meta)
    if not payload and not keep_empty:
        return OrderedDict()
    out = OrderedDict()
    out["doc_id"] = doc_id or ""
    for key in ("id", "uid", "schema_version", "vocab_version"):
        if meta.get(key) not in (None, ""):
            out[key] = meta[key]
    if markdown_sha256:
        out["markdown_sha256"] = markdown_sha256
    for key, val in payload.items():
        out[key] = val
    return out


def merge_meta(front, knowledge):
    # type: (dict, dict) -> OrderedDict
    """The inverse of ``split_meta``: one view for the linter and the enricher.

    Every rule in this package was written against a single mapping, and keeping it
    that way is the point — the split is a STORAGE decision, so nothing that grades
    or fills metadata should have to know about it.

    Front matter wins on a straight key collision: `document.md` is the file a person
    edits, so if the two disagree the hand-edited side is the one to believe.
    """
    out = OrderedDict()
    prov = OrderedDict()
    malformed = None
    for src in (knowledge or {}, front or {}):
        for key, val in (src or {}).items():
            if key != PROVENANCE_KEY:
                out[key] = val
            elif isinstance(val, dict):
                prov.update(val)
            elif val is not None:
                malformed = val          # carried through, never merged away
    if malformed is not None and not prov:
        out[PROVENANCE_KEY] = malformed
    elif prov:
        out[PROVENANCE_KEY] = prov
    return out
