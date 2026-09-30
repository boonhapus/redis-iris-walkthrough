"""Load the potion-base-8M static embedding model and encode text with it.

The model is a 256-dimension static embedder from model2vec, about 30 MB on disk, kept in the
Hugging Face cache outside the repository. Turning a vector into the bytes Redis stores stays in
the notebook: that step is a Redis lesson.
"""

import os

# Windows: the Hugging Face cache warns about symlinks on every download without this.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import numpy as np
from huggingface_hub import snapshot_download
from model2vec import StaticModel

EMBEDDING_MODEL_NAME = "minishlab/potion-base-8M"
EMBEDDING_MODEL_FILES = ["config.json", "model.safetensors", "tokenizer.json"]


def load_embedding_model() -> "StaticModel":
    """Load potion-base-8M from the Hugging Face cache, downloading about 30 MB the first time.

    Tries snapshot_download(..., local_files_only=True) with allow_patterns=EMBEDDING_MODEL_FILES
    first, so a warm machine never touches the network, then the online call, then
    StaticModel.from_pretrained(<local path>). Raises RuntimeError with a one-sentence cause when
    neither works. There is no degraded fallback: a word-hashing embedder would make the
    paraphrase examples lie.
    """
    try:
        model_path = snapshot_download(
            EMBEDDING_MODEL_NAME, allow_patterns=EMBEDDING_MODEL_FILES, local_files_only=True
        )
    except Exception:
        try:
            model_path = snapshot_download(
                EMBEDDING_MODEL_NAME, allow_patterns=EMBEDDING_MODEL_FILES
            )
        except Exception as error:
            raise RuntimeError(
                f"{EMBEDDING_MODEL_NAME} is not in the Hugging Face cache and the download "
                f"failed with {type(error).__name__}."
            ) from error
    try:
        return StaticModel.from_pretrained(model_path)
    except Exception as error:
        raise RuntimeError(
            f"{EMBEDDING_MODEL_NAME} is on disk at {model_path} but model2vec could not load it "
            f"({type(error).__name__})."
        ) from error


def embed_texts(model, texts: list[str]) -> "np.ndarray":
    """Encode texts to a (len(texts), model.dim) float32 array of unit-length vectors."""
    vectors = np.asarray(model.encode(list(texts)), dtype=np.float32).reshape(len(texts), -1)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0  # an empty text encodes to zeros; leave it there rather than NaN
    return (vectors / norms).astype(np.float32)
