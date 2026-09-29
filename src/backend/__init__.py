"""
title: Backend package
layer: backend
public_api: yes
summary: Namespace for the document->markdown pipeline. The public API lives in the
         ingest and validate subpackages; import from those, never from private (_*) modules.
"""

# A NAMESPACE, not a facade. The public API lives in the subpackages
# (`backend.ingest`, `backend.validate`, `backend.kb`, ...) and importing from here
# would create a second, competing boundary. The empty list is a statement — "this
# package re-exports nothing" — rather than an omission, which is what lets
# `scripts/check_structure.py` tell the two apart.
__all__ = []  # type: list
