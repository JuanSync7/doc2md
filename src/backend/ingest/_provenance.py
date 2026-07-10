"""
title: Document provenance -> YAML front matter (private)
layer: backend
public_api: no
summary: Pull title/author/version/dates out of OOXML core props or pdfinfo and render front matter.
"""
# 3.6-compatible. Stdlib only. Pure policy — operates on XML *strings* / info dicts,
# never touches disk or a zip. The file/zip reading lives in the producer scripts
# (office_convert.py opens the OOXML docProps; docling_convert.py shells pdfinfo),
# which feed these functions.
#
# Both markdown-producing lanes prepend the SAME provenance block so every document
# — office or PDF — carries title/author/version/dates it can be sorted and cited by:
#   * OOXML lane (office_convert.py):  core_properties(docProps/core.xml, app.xml)
#   * docling lane (docling_convert.py): pdf_info_meta(pdfinfo dict)
# rendered by front_matter().
import re
from collections import OrderedDict

__all__ = ["core_properties", "pdf_info_meta", "front_matter"]


def _unescape(s):
    # type: (str) -> str
    # Order matters: &amp; last so we don't double-unescape.
    return (s.replace("&lt;", "<").replace("&gt;", ">")
             .replace("&quot;", '"').replace("&apos;", "'")
             .replace("&amp;", "&"))


def _tag_text(xml, local):
    # type: (str, str) -> str
    """First ``<...:local>value</...:local>`` (any/no namespace prefix), unescaped."""
    m = re.search(r"<(?:\w+:)?%s\b[^>]*>(.*?)</(?:\w+:)?%s>" % (local, local), xml, re.S)
    return _unescape(m.group(1)).strip() if m else ""


def core_properties(core_xml, app_xml=None):
    # type: (str, str) -> OrderedDict
    """Document provenance from ``docProps/core.xml`` (+ optional ``app.xml``).

    Returns an OrderedDict with only the fields actually present, keyed for human
    front matter: title, author, version, created, modified, last_modified_by,
    company, app_version.
    """
    meta = OrderedDict()
    if core_xml:
        for key, local in (("title", "title"), ("author", "creator"),
                           ("version", "revision"), ("created", "created"),
                           ("modified", "modified"),
                           ("last_modified_by", "lastModifiedBy")):
            val = _tag_text(core_xml, local)
            if val:
                meta[key] = val
    if app_xml:
        for key, local in (("company", "Company"), ("app_version", "AppVersion")):
            val = _tag_text(app_xml, local)
            if val:
                meta[key] = val
    return meta


_JUNK_TITLE_EXT = (".doc", ".docx", ".pdf", ".ppt", ".pptx", ".xls", ".xlsx", ".rtf")


def pdf_info_meta(info):
    # type: (dict) -> OrderedDict
    """Provenance from a ``pdfinfo``-style dict -> the same keys as core_properties.

    Maps Title/Author/CreationDate/ModDate to title/author/created/modified. Drops
    junk titles PDF producers auto-fill (a bare filename like ``spec.pdf`` or a
    ``Microsoft Word - X.docx`` stub), so front matter never carries noise.
    """
    meta = OrderedDict()
    if not info:
        return meta
    title = (info.get("Title") or "").strip()
    if title and not _is_junk_title(title):
        meta["title"] = title
    for key, src in (("author", "Author"), ("created", "CreationDate"),
                     ("modified", "ModDate")):
        val = (info.get(src) or "").strip()
        if val:
            meta[key] = val
    return meta


def _is_junk_title(title):
    # type: (str) -> bool
    low = title.strip().lower()
    if low.startswith("microsoft word -") or low.startswith("microsoft powerpoint -"):
        return True
    return low.endswith(_JUNK_TITLE_EXT)


def _yaml_scalar(val):
    # type: (object) -> str
    """Double-quote a scalar, escaping so it stays a single valid YAML line.

    Backslash and quote are escaped; control chars that would break the one-line
    block (newline/carriage-return/tab) become their ``\\n``/``\\t`` escapes.
    """
    s = "%s" % (val,)
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    s = s.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n").replace("\t", "\\t")
    return '"%s"' % s


def front_matter(meta):
    # type: (dict) -> str
    """Render an ordered mapping as a YAML front-matter block (``---`` fenced).

    Returns ``""`` for an empty mapping so callers can unconditionally prepend it.
    Iterates ``meta`` in its own order (pass an OrderedDict for a stable layout).
    """
    if not meta:
        return ""
    lines = ["---"]
    for k, v in meta.items():
        lines.append("%s: %s" % (k, _yaml_scalar(v)))
    lines.append("---")
    return "\n".join(lines) + "\n"
