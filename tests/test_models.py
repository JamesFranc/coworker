"""Tests for the coworker.models registry."""

from coworker.models import DEFAULT_MODEL, REGISTRY, ModelEntry, get_model_by_label


def test_three_entries():
    assert len(REGISTRY) == 3


def test_all_entries_are_model_entry():
    for entry in REGISTRY:
        assert isinstance(entry, ModelEntry)


def test_default_is_first_registry_entry():
    assert DEFAULT_MODEL is REGISTRY[0]


def test_local_path_derived_from_gguf_filename():
    for entry in REGISTRY:
        assert entry.local_path.name == entry.gguf_filename


def test_local_path_under_home_models():
    from pathlib import Path

    for entry in REGISTRY:
        assert entry.local_path.parent == Path.home() / "models"


def test_get_model_by_label_unknown_returns_none():
    assert get_model_by_label("does-not-exist") is None


def test_get_model_by_label_exact():
    m = get_model_by_label("Qwopus3.5-9B-Coder")
    assert m is not None
    assert m.hf_repo == "Jackrong/Qwopus3.5-9B-Coder-MTP-GGUF"
    assert m.model_id == "Qwopus3.5-9B-Coder-MTP-GGUF.Q5_K_M"


def test_get_model_by_label_case_insensitive():
    m = get_model_by_label("qwopus3.5-9b-coder")
    assert m is not None
    assert m.label == "Qwopus3.5-9B-Coder"


def test_qwopus_gguf_filename():
    m = get_model_by_label("Qwopus3.5-9B-Coder")
    assert m is not None
    assert m.gguf_filename == "Qwopus3.5-9B-Coder-MTP-Q5_K_M.gguf"
