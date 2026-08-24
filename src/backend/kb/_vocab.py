"""
title: Controlled vocabulary — load, normalise, govern (private)
layer: backend
public_api: no
summary: Load config/vocab.yaml and answer membership, alias, hierarchy and governance questions about a value.
"""
# 3.6-compatible. Stdlib only. Pure policy over the parsed vocabulary mapping —
# the file read is the ONE disk touch, and it is confined to load_vocab().
#
# THE POINT OF THIS MODULE is that a value is never compared to a bare list at a
# call site. Every membership question goes through here so alias normalisation
# ("threatens" -> "targets") happens exactly once and in one place. That is what
# stops synonym pollution: the moment two call sites each keep their own notion of
# a legal value, the corpus grows both.
import os

from backend.ingest import parse_block, YamlSubsetError

__all__ = ["Vocabulary", "VocabularyError", "check_schema_bindings",
           "load_vocab", "vocab_path"]

CLOSED = "closed"
REGISTRY = "registry"
REF = "ref"
REGIMES = (CLOSED, REGISTRY, REF)

# Blocks in vocab.yaml that describe the file itself rather than a governed field.
_NON_FIELD = frozenset(["version", "standards", "lint", "demoted"])


class VocabularyError(ValueError):
    """The vocabulary file is unusable (missing, malformed, or self-inconsistent)."""


def _repo_root():
    # src/backend/kb/_vocab.py -> up 4 to repo root
    here = os.path.abspath(__file__)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(here))))


def vocab_path(repo_root=None, env=None):
    # type: (str, dict) -> str
    """``$DOC2MD_VOCAB`` > ``config/vocab.local.yaml`` > ``config/vocab.yaml``.

    Same local-overrides-committed shape the ingest config uses, so a deployment
    can carry its own term list without editing the tracked default.
    """
    env = os.environ if env is None else env
    explicit = (env.get("DOC2MD_VOCAB") or "").strip()
    if explicit:
        return explicit
    root = repo_root or _repo_root()
    local = os.path.join(root, "config", "vocab.local.yaml")
    if os.path.isfile(local):
        return local
    return os.path.join(root, "config", "vocab.yaml")


class Vocabulary(object):
    """The controlled vocabularies, queried by field name.

    Field names here are VOCABULARY names (``document_types``, ``impact``), not
    document key names — the schema maps one to the other, so several document
    keys can share one vocabulary.
    """

    def __init__(self, data):
        # type: (dict) -> None
        self._d = data or {}
        self.version = self._d.get("version", 0)
        self.standards = self._d.get("standards") or {}
        self.lint = self._d.get("lint") or {}
        self._validate()

    # ------------------------------------------------------------ construction

    def _validate(self):
        # type: () -> None
        """Fail on a self-inconsistent vocabulary at LOAD time, not at use time.

        An alias pointing at a value that no longer exists is the classic way a
        term list rots after an edit — it silently normalises to nothing.
        """
        for name, node in self.fields():
            gov = node.get("governance")
            if gov not in REGIMES:
                raise VocabularyError(
                    "field %r has governance %r (expected one of %s)"
                    % (name, gov, ", ".join(REGIMES)))
            values = node.get("values") or []
            if gov == CLOSED and not values:
                raise VocabularyError("closed field %r declares no values" % name)
            seen = set()
            for v in values:
                if v in seen:
                    raise VocabularyError("field %r repeats value %r" % (name, v))
                seen.add(v)
            for src, dst in (node.get("aliases") or {}).items():
                if gov == CLOSED and dst not in seen:
                    raise VocabularyError(
                        "field %r aliases %r -> %r, which is not a value" % (name, src, dst))
                if src in seen:
                    raise VocabularyError(
                        "field %r aliases %r, which is also a value" % (name, src))
            for v in (node.get("value_maps_to") or {}):
                # No `values and` guard: a registry field ships an EMPTY value list
                # by design, and skipping the check there is the same rot the alias
                # check exists to prevent, just in a table nobody looks at.
                if gov == CLOSED and v not in seen:
                    raise VocabularyError(
                        "field %r maps unknown value %r to a standard" % (name, v))
            aliases = node.get("aliases") or {}
            for src in (node.get("alias_qualifiers") or {}):
                # An alias renamed without updating its qualifier table loses the
                # nuance silently — exactly what load-time validation is for.
                if src not in aliases:
                    raise VocabularyError(
                        "field %r declares alias_qualifiers for %r, which is not an "
                        "alias" % (name, src))

    def fields(self):
        # type: () -> list
        """``(name, node)`` for every governed field, in file order."""
        return [(k, v) for k, v in self._d.items()
                if k not in _NON_FIELD and isinstance(v, dict) and "governance" in v]

    def has(self, field):
        # type: (str) -> bool
        return field in dict(self.fields())

    def _node(self, field):
        # type: (str) -> dict
        node = self._d.get(field)
        if not isinstance(node, dict) or "governance" not in node:
            raise VocabularyError("no such vocabulary field: %r" % (field,))
        return node

    # ------------------------------------------------------------ queries

    def governance(self, field):
        # type: (str) -> str
        return self._node(field)["governance"]

    def values(self, field):
        # type: (str) -> list
        return list(self._node(field).get("values") or [])

    def aliases(self, field):
        # type: (str) -> dict
        return dict(self._node(field).get("aliases") or {})

    def qualifiers(self, field):
        # type: (str) -> dict
        return dict(self._node(field).get("qualifiers") or {})

    def group_type(self, group):
        # type: (str) -> str
        """Entity type implied by an entity GROUP name (``hosts`` -> ``Host``).

        Without this, every entry in a group that omits ``type`` is silently
        unchecked — which is exactly how 15 of 30 entities in the worked example
        escaped validation.
        """
        return (self._node("entity_types").get("group_types") or {}).get(group, "")

    def maps_to(self, field, value=None):
        # type: (str, str) -> str
        """The standard term for a field, or for one of its values.

        Returns ``""`` when nothing maps — never a guess. Value mappings fall back
        to the field mapping, so ``impact/critical`` inherits ``iso31000``.
        """
        node = self._node(field)
        if value is not None:
            vm = node.get("value_maps_to") or {}
            if value in vm:
                return vm[value]
        return node.get("maps_to") or ""

    def normalize(self, field, value):
        # type: (str, object) -> tuple
        """``(canonical, qualifiers)`` for one value.

        An alias resolves to its canonical term and may carry qualifiers with it:
        the MADR collapse turns ``assumed`` into ``accepted`` plus
        ``{"assumption": True}``, so the nuance survives as data instead of
        inflating the enum. An unknown value comes back unchanged with no
        qualifiers — deciding what to DO about that is the linter's job, not this
        one's.
        """
        node = self._node(field)
        if not isinstance(value, str):
            return (value, {})
        alias = (node.get("aliases") or {}).get(value)
        if alias is None:
            return (value, {})
        quals = dict((node.get("alias_qualifiers") or {}).get(value) or {})
        return (alias, quals)

    def is_allowed(self, field, value):
        # type: (str, object) -> bool
        """Membership AFTER alias normalisation. Registry/ref fields allow anything
        here — their governance is enforced by the registry and by referential
        integrity respectively, not by a fixed list."""
        node = self._node(field)
        if node["governance"] != CLOSED:
            return True
        canonical, _ = self.normalize(field, value)
        return canonical in (node.get("values") or [])

    def broader(self, tag, field="tags"):
        # type: (str, str) -> list
        """The ``skos:broader`` chain above a tag, nearest first.

        ``rocky-linux-8 -> [rhel8, linux]`` means a search for ``linux`` reaches
        the Rocky pages without anyone tagging them twice. Cycles terminate rather
        than hang — a mis-edited hierarchy must not wedge a corpus scan.
        """
        table = self._node(field).get("broader") or {}
        out = []  # type: list
        seen = set([tag])
        cur = table.get(tag)
        while cur and cur not in seen:
            out.append(cur)
            seen.add(cur)
            cur = table.get(cur)
        return out

    def threshold(self, name, default=None):
        # type: (str, object) -> object
        return self.lint.get(name, default)


def load_vocab(path=None, text=None):
    # type: (str, str) -> Vocabulary
    """Load the vocabulary from ``path`` (default: ``vocab_path()``).

    ``text`` short-circuits the disk read, which is what unit tests use — this
    package stays pure everywhere except the one open() below.
    """
    src = "<text>"
    if text is None:
        src = path or vocab_path()
        if not os.path.isfile(src):
            raise VocabularyError(
                "vocabulary file not found: %s (set $DOC2MD_VOCAB to point at one; "
                "an installed package does not ship config/vocab.yaml)" % src)
        with open(src, encoding="utf-8") as fh:
            text = fh.read()
    try:
        data = parse_block(text)
    except YamlSubsetError as e:
        # A malformed file is an unusable vocabulary, which is what this error
        # means. Letting the codec's exception escape gives every caller a raw
        # traceback for what is really "your vocabulary has a typo".
        raise VocabularyError("vocabulary %s is not parseable: %s" % (src, e))
    return Vocabulary(data)


def check_schema_bindings(vocab):
    # type: (Vocabulary) -> list
    """Vocabulary names the schema binds but this vocabulary does not declare.

    Kept SEPARATE from ``Vocabulary._validate`` on purpose: that one grades the
    file's internal consistency, which is what a unit test wants to exercise with
    a two-field vocabulary. This grades the file against the SCHEMA, which only a
    real deployment vocabulary can satisfy — and a deployment that overrides
    ``$DOC2MD_VOCAB`` with a file missing one binding would otherwise fail OPEN:
    a closed field whose vocabulary is absent silently accepts anything.

    Returns ``[]`` when every binding resolves; the callers (the two scripts)
    turn a non-empty result into a loud error.
    """
    from ._schema import FIELDS, group_vocab, record_vocab
    missing = []  # type: list
    for f in FIELDS:
        if f.vocab and not vocab.has(f.vocab):
            missing.append("%s -> %s" % (f.name, f.vocab))
        for sub, vname in record_vocab(f.name).items():
            if not vocab.has(vname):
                missing.append("%s[].%s -> %s" % (f.name, sub, vname))
        for role, vname in group_vocab(f.name).items():
            if not vocab.has(vname):
                missing.append("%s(%s) -> %s" % (f.name, role, vname))
    return sorted(set(missing))
