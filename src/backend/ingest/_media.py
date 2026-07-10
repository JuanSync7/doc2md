"""
title: Office media ref-location resolution (private)
layer: backend
public_api: no
summary: From the .rels graph, decide which embedded image is BODY content vs page CHROME (header/footer/master), body-wins — the formula-safe furniture signal the gate trusts.
"""
# 3.6-compatible. Stdlib only (ElementTree). Pure: given the office package's .rels parts as
# {rels_name: xml}, map each referenced media part to 'body' / 'chrome' / 'unknown'. Placement
# is the one furniture signal a formula can never carry (a formula lives in the body), so the
# gate drops 'chrome' but never touches 'body'. BODY-WINS: an image referenced from BOTH the
# body and a header is body (kept), never dropped as chrome.
import os
import re
import xml.etree.ElementTree as _ET

__all__ = ["resolve_media_refs", "is_body_part"]

# Parts whose images are real content. Anything not body and matching a chrome part is chrome;
# everything else is 'unknown' (kept, treated as body by the gate's body-wins default).
_BODY_PART = re.compile(
    r"^word/document\.xml$"
    r"|^word/(footnotes|endnotes|comments)\.xml$"
    r"|^ppt/slides/slide\d+\.xml$"
    r"|^ppt/notesSlides/notesSlide\d+\.xml$"
    r"|^xl/worksheets/[^/]+\.xml$"
    r"|^xl/drawings/drawing\d+\.xml$"
    r"|^(word|ppt|xl)/(diagrams|charts)/")
# ONLY word headers/footers are unambiguous page chrome at PART granularity. pptx
# slideMasters/slideLayouts are NOT blanket chrome — a shared diagram or FORMULA authored on
# a layout/master is inherited by content slides yet referenced only from the layout's rels,
# so treating the whole directory as chrome would DROP it before the model (formula-safety
# violation). Those parts fall through to 'unknown' (kept via body-wins). True pptx chrome
# (slide-number/date/footer placeholders) is placeholder-scoped and not resolvable here.
_CHROME_PART = re.compile(r"^word/(header|footer)\d*\.xml$")


def is_body_part(part):
    # type: (str) -> bool
    """True if images referenced from ``part`` are BODY content (document body, a slide, a
    worksheet/drawing, footnotes/comments), False for page chrome or unknown parts."""
    return bool(_BODY_PART.match(part or ""))


def _owner_of(rels_name):
    # type: (str) -> str
    """The part a ``.rels`` file belongs to: ``word/_rels/document.xml.rels`` ->
    ``word/document.xml``; ``_rels/.rels`` -> ``""`` (package root)."""
    d = os.path.dirname(rels_name)                      # word/_rels
    base = os.path.basename(rels_name)                  # document.xml.rels
    if base.endswith(".rels"):
        base = base[:-5]
    owner_dir = os.path.dirname(d)                      # word
    return (owner_dir + "/" + base).lstrip("/") if base else owner_dir


def _resolve_target(owner, target):
    # type: (str, str) -> str
    """Resolve a rels Target (possibly ``../media/x.png``) against its owner part's dir into
    a normalized package path (``ppt/media/x.png``)."""
    t = (target or "").replace("\\", "/").strip()
    if t.startswith("/"):
        return t.lstrip("/")
    base_dir = os.path.dirname(owner)
    return os.path.normpath(os.path.join(base_dir, t)).replace("\\", "/").lstrip("/")


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


_IMG_EXT = re.compile(r"\.(png|jpe?g|gif|bmp|tiff?|emf|wmf|webp|svg)$", re.I)
# Precedence so body always wins over chrome over unknown.
_RANK = {"body": 2, "chrome": 1, "unknown": 0}


def resolve_media_refs(rels_by_part):
    # type: (dict) -> dict
    """``{media_part: 'body'|'chrome'|'unknown'}`` for every image the ``.rels`` graph
    references. BODY-WINS: a media part referenced from any body part is 'body' even if a
    header also references it. Non-image relationships (hyperlinks, etc.) are ignored."""
    best = {}
    for rels_name, xml in (rels_by_part or {}).items():
        try:
            root = _ET.fromstring(xml)
        except _ET.ParseError:
            continue
        owner = _owner_of(rels_name)
        placement = "body" if is_body_part(owner) else ("chrome" if _CHROME_PART.match(owner) else "unknown")
        for rel in root:
            if _local(rel.tag) != "Relationship":
                continue
            typ = rel.attrib.get("Type", "")
            target = rel.attrib.get("Target", "")
            if rel.attrib.get("TargetMode", "") == "External":
                continue
            if not (typ.endswith("/image") or _IMG_EXT.search(target)):
                continue
            media = _resolve_target(owner, target)
            if media not in best or _RANK[placement] > _RANK[best[media]]:
                best[media] = placement       # keep the highest-ranked placement (body-wins)
    return best
