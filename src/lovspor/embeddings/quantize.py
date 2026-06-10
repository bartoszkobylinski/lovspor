"""Linear int8 quantization for embedding vectors.

For unit-normalized 768-dim embeddings, all components fall in
``[-1, 1]``. Linear quantization to int8 gives an effective range of
``[-127, 127]`` (we reserve -128 to keep the symmetric scale). Cosine
similarity between the original float32 vector and the dequantized
copy is typically ``> 0.999`` — the loss is well below the noise
floor of any retrieval evaluation.

A single ``scale`` factor is computed per batch and stored in the
file header, not per vector. This keeps the on-disk format simple
and matches how dot-product similarity is computed: ``int8 . int8``
followed by a single ``* scale * scale`` correction.

For the lovspor corpus this means ~75% storage savings vs float32:
~50 MB total for the production 4500-doc corpus instead of ~200 MB.
"""

import numpy as np

_INT8_MAX = 127.0


def quantize_int8(vectors: np.ndarray) -> tuple[np.ndarray, float]:
    """Quantize a batch of float32 vectors to int8.

    Returns ``(quantized, scale)``. To recover (a close approximation
    of) the original: ``quantized.astype(float32) * scale``.

    Edge cases:

    - Empty input returns ``(empty int8 array, 1.0)``.
    - All-zero input returns ``(zeros, 1.0)``; the scale is arbitrary
      because every output is zero anyway.
    - Inputs outside ``[-1, 1]`` are handled (we use the actual
      max-abs to compute the scale), but the model is expected to
      output normalized vectors so this rarely matters in practice.
    """
    if vectors.size == 0:
        return np.zeros(vectors.shape, dtype=np.int8), 1.0
    max_abs = float(np.abs(vectors).max())
    if max_abs == 0.0:
        return np.zeros_like(vectors, dtype=np.int8), 1.0
    scale = max_abs / _INT8_MAX
    quantized = np.clip(np.round(vectors / scale), -_INT8_MAX, _INT8_MAX).astype(np.int8)
    return quantized, scale


def dequantize_int8(quantized: np.ndarray, scale: float) -> np.ndarray:
    """Reverse quantization. Returns float32 array matching the input shape.

    Not called by the engine itself — cosine scoring works directly on
    the int8 rows because the positive scale cancels out (see
    ``embeddings.search``). Kept as public API: it is the documented
    inverse of :func:`quantize_int8` for third-party tools that verify
    the corpus ``.bin`` files end-to-end (``docs/embeddings.md``).
    """
    return quantized.astype(np.float32) * scale
