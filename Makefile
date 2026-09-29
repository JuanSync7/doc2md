# doc2md — one command per thing worth proving.
#
# `make verify` is the DONE-GATE: if it is green, the change is safe to push. Every
# target below is also a CI job, so a green local verify and a green CI run are the
# same claim rather than two different ones.
#
# FARM SUBMISSION. This repo lives on a shared RDP host; a full suite run pins CPUs
# other people are using. Every test target therefore submits to the Slurm farm when
# `srun` is available and falls back to running locally when it is not, so the same
# command works on a laptop, on the RDP box, and in CI. Override with FARM=0 to force
# local, or FARM=1 to fail loudly if the farm is missing rather than quietly running
# on this host.
#
#   make verify           the done-gate: everything below, in dependency order
#   make check            CONVENTIONS.md's own structure/labelling rules
#   make test             unit + integration suite
#   make test-ciring      the suite as CI sees it (no PyYAML, no Pillow)
#   make test-pillow      the suite WITH Pillow, covering branches CI's rings cannot
#   make corpus           (re)generate the eval corpus
#   make eval             office + text lanes
#   make eval-pdf         all lanes, needs the 3.12 venv and pinned docling weights
#   make rubric           the executable quality rubric (docs/quality-plan.md)
#   make kb-lint          the knowledge layer's own judge
#   make determinism      corpus generation is byte-identical on 3.6 and 3.12
#   make idempotency      a second conversion produces byte-identical output
#
.POSIX:
SHELL := /bin/bash

PY        ?= python3
VENV_PY   ?= $(CURDIR)/.venv/bin/python
FARM      ?= auto
PARTITION ?= interactive
CPUS      ?= 8
MEM       ?= 8G
WALL      ?= 00:30:00

# Resolve the runner once. `srun` without an explicit --mem is OOM-killed by the
# cgroup on a full suite run, measured — so the memory request is not optional.
SRUN := $(shell command -v srun 2>/dev/null)
ifeq ($(FARM),0)
  RUN :=
else ifeq ($(SRUN),)
  ifeq ($(FARM),1)
    $(error FARM=1 was requested but srun is not on PATH)
  endif
  RUN :=
else
  RUN := $(SRUN) --partition=$(PARTITION) --ntasks=1 --cpus-per-task=$(CPUS) \
         --mem=$(MEM) --time=$(WALL) --job-name=doc2md
endif

CORPUS  ?= data/eval_corpus
BUNDLES ?= data/eval_bundles
SCRATCH ?= $(CURDIR)/.make

.PHONY: verify check test test-ciring test-pillow corpus eval eval-pdf rubric \
        kb-lint determinism idempotency clean where

where:
	@echo "runner : $(if $(RUN),$(RUN),local (no farm))"
	@echo "python : $(PY) ($$($(PY) -c 'import sys;print(sys.version.split()[0])'))"
	@echo "venv   : $(VENV_PY) $$(test -x $(VENV_PY) && $(VENV_PY) -c 'import sys;print(sys.version.split()[0])' || echo '(absent)')"

# The done-gate. Ordered so the cheapest, most-likely-to-fail thing runs first.
verify: check test test-ciring corpus determinism eval idempotency rubric kb-lint
	@echo
	@echo "=============================================================="
	@echo " verify: GREEN — structure, suite, CI ring, corpus determinism,"
	@echo " office eval, idempotency, rubric and kb-lint all pass."
	@echo " NOT covered here: the PDF lane (make eval-pdf) needs the 3.12"
	@echo " venv and the pinned docling weights."
	@echo "=============================================================="

# CONVENTIONS.md section 6 promises this is "checked, not just documented".
# It now is. Cheap and dependency-free, so it runs first.
check:
	$(PY) scripts/check_structure.py --quiet

test:
	$(RUN) $(PY) -m pytest -q

# CI installs neither PyYAML nor Pillow. Reproducing that here is what stops a test
# from passing locally on a dependency CI does not have — the bug this ring exists
# to catch has actually happened, in both directions.
test-ciring:
	PYTHONPATH=$(CURDIR)/tests/ciring $(RUN) $(PY) -m pytest -q

# The OTHER side of the same coin. `image_caption.prepare_png` behaves differently
# with Pillow present (it truly decodes, and it can downscale), and no ring covered
# that until a farm node with Pillow 5.1.1 turned 34 tests red. A skip here reads
# exactly like a pass, so this target fails if the Pillow-only tests did not run.
test-pillow:
	@$(PY) -c "import PIL" 2>/dev/null || { echo "Pillow not installed here — 'pip install pillow' or run on a node that has it"; exit 1; }
	$(RUN) $(PY) -m pytest -q tests/unit/backend/test_image_caption.py -rs -p no:cacheprovider \
	  | tee $(SCRATCH)/pillow.txt
	@grep -qi 'skipped' $(SCRATCH)/pillow.txt \
	  && { echo "ERROR: the Pillow-only branches SKIPPED on the ring that installs Pillow"; exit 1; } || true

corpus:
	@mkdir -p $(SCRATCH)
	DOC2MD_LIBREOFFICE="$$(command -v soffice)" DOC2MD_PDF_PYTHON="$(VENV_PY)" \
	  $(PY) evals/gen_corpus.py --out $(CORPUS)

# Hand-built corpus bytes must not depend on the interpreter that wrote them — the
# 3.6 ring and the 3.12 ring convert the SAME fixtures, so a difference here makes
# every downstream comparison meaningless.
determinism:
	@mkdir -p $(SCRATCH)
	@rm -rf $(SCRATCH)/det36 $(SCRATCH)/det312
	$(PY) evals/gen_corpus.py --out $(SCRATCH)/det36 --handbuilt-only >/dev/null
	@test -x $(VENV_PY) && $(VENV_PY) evals/gen_corpus.py --out $(SCRATCH)/det312 --handbuilt-only >/dev/null \
	  || { echo "SKIP: no 3.12 venv, cannot compare interpreters"; exit 0; }
	@diff -r $(SCRATCH)/det36 $(SCRATCH)/det312 >/dev/null \
	  && echo "determinism: corpus byte-identical across interpreters" \
	  || { echo "ERROR: corpus generation DIFFERS between interpreters"; exit 1; }

eval:
	DOC2MD_LIBREOFFICE="$$(command -v soffice)" $(PY) evals/run_eval.py --skip-pdf

eval-pdf:
	@test -x $(VENV_PY) || { echo "no 3.12 venv at $(VENV_PY)"; exit 1; }
	TORCHDYNAMO_DISABLE=1 \
	DOC2MD_LIBREOFFICE="$$(command -v soffice)" \
	DOC2MD_PDF_PYTHON="$(VENV_PY)" \
	DOCLING_ARTIFACTS_PATH="$(CURDIR)/vendor/docling-artifacts" \
	  $(PY) evals/run_eval.py

# A conversion that is not reproducible cannot be cached, replayed or deduplicated
# downstream. Two runs over the same corpus must produce byte-identical markdown.
idempotency:
	@mkdir -p $(SCRATCH)
	@DOC2MD_LIBREOFFICE="$$(command -v soffice)" $(PY) evals/run_eval.py --skip-pdf >/dev/null 2>&1
	@find $(BUNDLES) -name document.md | sort | xargs cat | $(PY) -c \
	  "import hashlib,sys;print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())" > $(SCRATCH)/idem1
	@DOC2MD_LIBREOFFICE="$$(command -v soffice)" $(PY) evals/run_eval.py --skip-pdf >/dev/null 2>&1
	@find $(BUNDLES) -name document.md | sort | xargs cat | $(PY) -c \
	  "import hashlib,sys;print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())" > $(SCRATCH)/idem2
	@diff -q $(SCRATCH)/idem1 $(SCRATCH)/idem2 >/dev/null \
	  && echo "idempotency: second run byte-identical ($$(cat $(SCRATCH)/idem1 | cut -c1-16)...)" \
	  || { echo "ERROR: a second conversion changed document.md"; exit 1; }

rubric:
	$(PY) scripts/grade_output.py

kb-lint:
	$(PY) scripts/kb_lint.py --bundles $(BUNDLES) --quiet

clean:
	rm -rf $(SCRATCH)
