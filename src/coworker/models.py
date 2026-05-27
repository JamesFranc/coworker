"""Model registry for coworker-tools.

Single source of truth for supported local models: their HuggingFace repo,
GGUF filename, expected local path, and the model identifier used in API calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ModelEntry:
    label: str
    hf_repo: str
    gguf_filename: str
    model_id: str
    local_path: Path = field(init=False)

    def __post_init__(self) -> None:
        # frozen=True requires object.__setattr__ to set derived fields
        object.__setattr__(self, "local_path", Path.home() / "models" / self.gguf_filename)


REGISTRY: list[ModelEntry] = [
    ModelEntry(
        label="Gemma 4 E4B",
        hf_repo="unsloth/gemma-4-E4B-it-GGUF",
        gguf_filename="gemma-4-E4B-it-UD-Q4_K_XL.gguf",
        model_id="gemma-4-E4B-it-UD-Q4_K_XL",
    ),
    ModelEntry(
        label="Gemma 4 E2B",
        hf_repo="unsloth/gemma-4-E2B-it-GGUF",
        gguf_filename="gemma-4-E2B-it-UD-Q4_K_XL.gguf",
        model_id="gemma-4-E2B-it-UD-Q4_K_XL",
    ),
    ModelEntry(
        label="Qwopus3.5-9B-Coder",
        hf_repo="Jackrong/Qwopus3.5-9B-Coder-MTP-GGUF",
        gguf_filename="Qwopus3.5-9B-Coder-MTP-Q5_K_M.gguf",
        model_id="Qwopus3.5-9B-Coder-MTP-GGUF.Q5_K_M",
    ),
]

DEFAULT_MODEL: ModelEntry = REGISTRY[0]


def get_model_by_label(label: str) -> ModelEntry | None:
    """Return the registry entry matching *label* (case-insensitive), or None."""
    needle = label.lower()
    for entry in REGISTRY:
        if entry.label.lower() == needle:
            return entry
    return None
