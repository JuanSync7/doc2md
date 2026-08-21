"""
title: backend.provenance
kind: package
layer: backend
public_api: yes
summary: What a run was — code identity, resolved configuration with the source of each value, and the branches the pipeline chose.
"""
# Reproducibility is a product feature here, not an ops nicety: a conversion is
# worth what you can prove about it AND repeat from it. This package owns the facts
# that make a run repeatable, kept separate from `bundle` (which composes the report
# and must stay disk-free) and from `validate` (which measures the CONVERSION, not
# the RUN).
from ._code import code_identity, host_identity, git_commit, package_version
from ._run import (run_block, decision, stamp_stage, config_provenance,
                   redact_argv, safe_value, path_id, corpus_id, compact_run,
                   DECISION_CODES)

__all__ = [
    "code_identity",
    "host_identity",
    "git_commit",
    "package_version",
    "run_block",
    "decision",
    "stamp_stage",
    "config_provenance",
    "redact_argv",
    "safe_value",
    "path_id",
    "corpus_id",
    "compact_run",
    "DECISION_CODES",
]
