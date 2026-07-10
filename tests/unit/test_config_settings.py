"""
title: Unit — config.settings tokenizer resolver
kind: tests
layer: n/a
summary: get_token_counter resolves the configured tokenizer to a (callable, model) pair; char default is dependency-free, misconfig fails loud.
"""
import pytest
from config.settings import get_token_counter

pytestmark = pytest.mark.unit


def test_char_backend_is_the_dependency_free_default():
    tc, model = get_token_counter()
    assert tc is None                      # None => pipeline uses its built-in estimate
    assert model == "char-estimate/4"


def test_char_backend_explicit():
    tc, model = get_token_counter({"backend": "char"})
    assert tc is None and model == "char-estimate/4"


def test_callable_backend_returns_the_supplied_function():
    fn = lambda s: len(s.split())
    tc, model = get_token_counter({"backend": "callable", "callable": fn, "model": "ws-v1"})
    assert tc is fn
    assert model == "ws-v1"
    assert tc("a b c") == 3


def test_callable_backend_without_a_function_fails_loud():
    with pytest.raises(RuntimeError) as e:
        get_token_counter({"backend": "callable", "callable": None})
    assert "callable" in str(e.value)


def test_unknown_backend_fails_loud():
    with pytest.raises(RuntimeError) as e:
        get_token_counter({"backend": "definitely-not-a-tokenizer"})
    assert "unknown TOKENIZER backend" in str(e.value)


def test_override_none_values_do_not_clobber_defaults():
    # A --tokenizer flag that resolves to {backend:None} must leave the char default intact.
    tc, model = get_token_counter({"backend": None, "model": None})
    assert tc is None and model == "char-estimate/4"


def test_tiktoken_backend_when_available_else_fails_loud():
    try:
        import tiktoken  # noqa: F401
    except ImportError:
        with pytest.raises(RuntimeError) as e:
            get_token_counter({"backend": "tiktoken"})
        assert "tiktoken" in str(e.value)
        return
    tc, model = get_token_counter({"backend": "tiktoken", "model": "cl100k_base"})
    assert model == "cl100k_base"
    assert callable(tc) and tc("hello world tokens") > 0
