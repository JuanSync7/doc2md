"""
title: Unit — backend.ingest load_ingest_config
kind: tests
layer: backend
summary: Mirrors src/backend/ingest/_config.py. Precedence env > toml > default; validation; path resolution.
"""
import os
import pytest
from backend.ingest import load_ingest_config, IngestConfig, recommend_shards

pytestmark = pytest.mark.unit


def _write(tmp_path, body):
    p = tmp_path / "conf.toml"
    p.write_text(body, encoding="utf-8")
    return str(p)


def test_defaults_when_no_config_no_env(tmp_path):
    cfg = load_ingest_config(repo_root=str(tmp_path), env={},
                             config_path=str(tmp_path / "missing.toml"))
    assert cfg.backend == "native"
    assert cfg.markdown_dir == os.path.join(str(tmp_path), "data", "markdown")


def test_reads_backend_from_toml(tmp_path):
    cp = _write(tmp_path, '[ingest]\nbackend = "docling"\nmarkdown_dir = "mds"\n')
    cfg = load_ingest_config(repo_root=str(tmp_path), env={}, config_path=cp)
    assert cfg.backend == "docling"
    assert cfg.markdown_dir == os.path.join(str(tmp_path), "mds")


def test_env_overrides_toml(tmp_path):
    cp = _write(tmp_path, '[ingest]\nbackend = "native"\n')
    cfg = load_ingest_config(repo_root=str(tmp_path),
                             env={"DOC2MD_INGEST_BACKEND": "docling"}, config_path=cp)
    assert cfg.backend == "docling"


def test_ignores_other_sections(tmp_path):
    cp = _write(tmp_path, '[server]\nbackend = "docling"\n[ingest]\nbackend = "native"\n')
    cfg = load_ingest_config(repo_root=str(tmp_path), env={}, config_path=cp)
    assert cfg.backend == "native"


def test_absolute_markdown_dir_preserved(tmp_path):
    cp = _write(tmp_path, '[ingest]\nmarkdown_dir = "/abs/md"\n')
    cfg = load_ingest_config(repo_root=str(tmp_path), env={}, config_path=cp)
    assert cfg.markdown_dir == "/abs/md"


def test_env_markdown_dir_overrides(tmp_path):
    cfg = load_ingest_config(repo_root=str(tmp_path),
                             env={"DOC2MD_MARKDOWN_DIR": "/x/y"}, config_path=None)
    assert cfg.markdown_dir == "/x/y"


def test_invalid_backend_raises(tmp_path):
    with pytest.raises(ValueError):
        load_ingest_config(repo_root=str(tmp_path),
                           env={"DOC2MD_INGEST_BACKEND": "bogus"}, config_path=None)


def test_returns_ingestconfig_namedtuple(tmp_path):
    cfg = load_ingest_config(repo_root=str(tmp_path), env={}, config_path=None)
    assert isinstance(cfg, IngestConfig)


# --- Tier 1: figure-caption + VLM-OCR settings ------------------------------

def test_caption_ocr_defaults_off(tmp_path):
    cfg = load_ingest_config(repo_root=str(tmp_path), env={}, config_path=None)
    assert cfg.enable_captions is False
    assert cfg.enable_vlm_ocr is False
    assert cfg.assets_dir == os.path.join(str(tmp_path), "data", "assets")
    assert cfg.vlm_url and cfg.vlm_url.startswith("http")
    assert cfg.vlm_model


def test_enable_flags_parse_bools_from_toml(tmp_path):
    cp = _write(tmp_path, '[ingest]\nenable_captions = true\nenable_vlm_ocr = false\n')
    cfg = load_ingest_config(repo_root=str(tmp_path), env={}, config_path=cp)
    assert cfg.enable_captions is True
    assert cfg.enable_vlm_ocr is False


def test_enable_flags_env_override(tmp_path):
    cp = _write(tmp_path, '[ingest]\nenable_captions = false\n')
    cfg = load_ingest_config(
        repo_root=str(tmp_path),
        env={"DOC2MD_ENABLE_CAPTIONS": "1", "DOC2MD_ENABLE_VLM_OCR": "yes"},
        config_path=cp)
    assert cfg.enable_captions is True
    assert cfg.enable_vlm_ocr is True


def test_validation_thresholds_defaults(tmp_path):
    cfg = load_ingest_config(repo_root=str(tmp_path), env={},
                             config_path=str(tmp_path / "missing.toml"))
    assert cfg.min_recall == 0.80
    assert cfg.min_tokens == 50
    assert cfg.fallback_min_recall == 0.50
    assert cfg.fallback_min_tokens == 100
    assert cfg.header_footer_min_frac == 0.50
    assert cfg.content_min_recall == 0.95
    assert cfg.fallback_content_min == 0.85


def test_recovery_and_admission_defaults(tmp_path):
    cfg = load_ingest_config(repo_root=str(tmp_path), env={},
                             config_path=str(tmp_path / "missing.toml"))
    assert cfg.retry_attempts == 2
    assert cfg.escalation_attempts == 1
    assert cfg.big_doc_threads == 0
    assert cfg.big_doc_mem_gb == 24
    assert cfg.load_high_frac == 1.1
    assert cfg.load_low_frac == 0.5


def test_recovery_and_admission_from_toml_and_env(tmp_path):
    cp = _write(tmp_path, ('[ingest]\ncontent_min_recall = 0.9\nretry_attempts = 3\n'
                           'escalation_attempts = 2\nbig_doc_threads = 16\n'
                           'big_doc_mem_gb = 48\nload_high_frac = 1.5\nload_low_frac = 0.3\n'))
    cfg = load_ingest_config(repo_root=str(tmp_path), env={}, config_path=cp)
    assert cfg.content_min_recall == 0.9
    assert cfg.retry_attempts == 3
    assert cfg.escalation_attempts == 2
    assert cfg.big_doc_threads == 16
    assert cfg.big_doc_mem_gb == 48
    assert cfg.load_high_frac == 1.5
    assert cfg.load_low_frac == 0.3
    # env beats toml
    cfg2 = load_ingest_config(repo_root=str(tmp_path),
                              env={"DOC2MD_BIG_DOC_MEM_GB": "64", "DOC2MD_ESCALATION_ATTEMPTS": "0"},
                              config_path=cp)
    assert cfg2.big_doc_mem_gb == 64
    assert cfg2.escalation_attempts == 0


def test_validation_thresholds_from_toml(tmp_path):
    cp = _write(tmp_path, ('[ingest]\nmin_recall = 0.9\nmin_tokens = 30\n'
                           'fallback_min_recall = 0.4\nfallback_min_tokens = 200\n'
                           'header_footer_min_frac = 0.6\n'))
    cfg = load_ingest_config(repo_root=str(tmp_path), env={}, config_path=cp)
    assert cfg.min_recall == 0.9
    assert cfg.min_tokens == 30
    assert cfg.fallback_min_recall == 0.4
    assert cfg.fallback_min_tokens == 200
    assert cfg.header_footer_min_frac == 0.6


def test_validation_thresholds_env_override_and_bad_values(tmp_path):
    cp = _write(tmp_path, '[ingest]\nmin_recall = 0.9\n')
    cfg = load_ingest_config(repo_root=str(tmp_path),
                             env={"DOC2MD_MIN_RECALL": "0.75", "DOC2MD_MIN_TOKENS": "10"},
                             config_path=cp)
    assert cfg.min_recall == 0.75      # env beats toml
    assert cfg.min_tokens == 10
    # a malformed numeric value falls back to the default rather than crashing
    cfg2 = load_ingest_config(repo_root=str(tmp_path),
                              env={"DOC2MD_MIN_RECALL": "notanumber"},
                              config_path=str(tmp_path / "missing.toml"))
    assert cfg2.min_recall == 0.80


def test_recommend_shards_binds_on_scarcer_of_cpu_ram():
    # 32 cores / 256GB, 4 threads/shard, 8GB/shard -> cpu bound: 32//4=8, ram 256//8=32 -> 8
    assert recommend_shards(32, 256, 4, 8) == (8, 4)
    # RAM-scarce laptop: 8 cores but only 12GB -> ram bound: 12//8=1
    assert recommend_shards(8, 12, 4, 8) == (1, 4)
    # cap applies
    assert recommend_shards(64, 512, 4, 8, max_shards=6) == (6, 4)
    # degenerate inputs never go below 1
    assert recommend_shards(0, 0, 0, 0) == (1, 1)


def test_recommend_shards_config_defaults(tmp_path):
    cfg = load_ingest_config(repo_root=str(tmp_path), env={},
                             config_path=str(tmp_path / "missing.toml"))
    assert cfg.threads_per_shard == 4
    assert cfg.mem_per_shard_gb == 8
    assert cfg.max_shards == 0


def test_vlm_url_and_model_from_toml_and_env(tmp_path):
    cp = _write(tmp_path, '[ingest]\nvlm_url = "http://h:9/v1/chat/completions"\nvlm_model = "m1"\n')
    cfg = load_ingest_config(repo_root=str(tmp_path), env={}, config_path=cp)
    assert cfg.vlm_url == "http://h:9/v1/chat/completions"
    assert cfg.vlm_model == "m1"
    cfg2 = load_ingest_config(repo_root=str(tmp_path),
                              env={"DOC2MD_VLM_MODEL": "m2"}, config_path=cp)
    assert cfg2.vlm_model == "m2"


def test_assets_dir_relative_and_absolute(tmp_path):
    cp = _write(tmp_path, '[ingest]\nassets_dir = "data/figs"\n')
    cfg = load_ingest_config(repo_root=str(tmp_path), env={}, config_path=cp)
    assert cfg.assets_dir == os.path.join(str(tmp_path), "data", "figs")
    cfg2 = load_ingest_config(repo_root=str(tmp_path),
                              env={"DOC2MD_ASSETS_DIR": "/abs/figs"}, config_path=None)
    assert cfg2.assets_dir == "/abs/figs"


def test_load_source_root_env_then_paths_then_empty(tmp_path):
    from backend.ingest import load_source_root
    cp = _write(tmp_path, '[paths]\nsource_docs = "/data/corpus"\n[ingest]\nbackend = "native"\n')
    # env wins
    assert load_source_root(repo_root=str(tmp_path), env={"DOC2MD_SRC": "/env/corpus"},
                            config_path=cp) == "/env/corpus"
    # falls back to [paths].source_docs
    assert load_source_root(repo_root=str(tmp_path), env={}, config_path=cp) == "/data/corpus"
    # nothing anywhere -> "" (callers error out; no hardcoded host path)
    assert load_source_root(repo_root=str(tmp_path), env={},
                            config_path=str(tmp_path / "missing.toml")) == ""


def test_image_region_knobs_defaults_and_overrides(tmp_path):
    cfg = load_ingest_config(repo_root=str(tmp_path), env={},
                             config_path=str(tmp_path / "missing.toml"))
    assert cfg.image_region_min_paths == 10
    assert cfg.image_region_pad == 0.01
    assert cfg.image_region_max_frac == 0.85
    cp = _write(tmp_path, '[ingest]\nimage_region_min_paths = 5\nimage_region_pad = 0.02\n')
    cfg2 = load_ingest_config(repo_root=str(tmp_path),
                              env={"DOC2MD_IMAGE_REGION_MAX_FRAC": "0.7"}, config_path=cp)
    assert cfg2.image_region_min_paths == 5
    assert cfg2.image_region_pad == 0.02
    assert cfg2.image_region_max_frac == 0.7


def test_accept_formats_defaults_empty_and_parses_list(tmp_path):
    # default: empty tuple = "accept every supported format"
    cfg = load_ingest_config(repo_root=str(tmp_path), env={},
                             config_path=str(tmp_path / "missing.toml"))
    assert cfg.accept_formats == ()
    # toml list, comma/space separated, de-dotted and lowercased
    cp = _write(tmp_path, '[ingest]\naccept_formats = "docx, .PDF ; xlsx"\n')
    cfg2 = load_ingest_config(repo_root=str(tmp_path), env={}, config_path=cp)
    assert cfg2.accept_formats == ("docx", "pdf", "xlsx")
    # env wins over toml; "all" resolves back to the default (accept everything)
    cfg3 = load_ingest_config(repo_root=str(tmp_path),
                              env={"DOC2MD_ACCEPT_FORMATS": "all"}, config_path=cp)
    assert cfg3.accept_formats == ()


def test_vlm_max_tokens_default_and_override(tmp_path):
    cfg = load_ingest_config(repo_root=str(tmp_path), env={},
                             config_path=str(tmp_path / "missing.toml"))
    assert cfg.vlm_max_tokens == 8192
    cp = _write(tmp_path, '[ingest]\nvlm_max_tokens = 16000\n')
    cfg2 = load_ingest_config(repo_root=str(tmp_path), env={}, config_path=cp)
    assert cfg2.vlm_max_tokens == 16000
    cfg3 = load_ingest_config(repo_root=str(tmp_path),
                              env={"DOC2MD_VLM_MAX_TOKENS": "4096"}, config_path=cp)
    assert cfg3.vlm_max_tokens == 4096
