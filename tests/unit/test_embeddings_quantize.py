import numpy as np

from lovspor.embeddings.quantize import dequantize_int8, quantize_int8


def test_quantize_empty_input_preserves_shape() -> None:
    vectors = np.empty((0, 3), dtype=np.float32)

    quantized, scale = quantize_int8(vectors)

    assert quantized.shape == vectors.shape
    assert quantized.dtype == np.int8
    assert scale == 1.0


def test_quantize_all_zero_input_uses_neutral_scale() -> None:
    vectors = np.zeros((2, 3), dtype=np.float32)

    quantized, scale = quantize_int8(vectors)

    np.testing.assert_array_equal(quantized, np.zeros((2, 3), dtype=np.int8))
    assert scale == 1.0


def test_quantize_round_trip_is_close_to_original_values() -> None:
    vectors = np.array(
        [
            [0.0, 0.25, -0.5],
            [0.75, -1.0, 0.125],
        ],
        dtype=np.float32,
    )

    quantized, scale = quantize_int8(vectors)
    restored = dequantize_int8(quantized, scale)

    assert quantized.dtype == np.int8
    assert scale > 0.0
    assert int(quantized.min()) >= -127
    assert int(quantized.max()) <= 127
    np.testing.assert_allclose(restored, vectors, atol=scale / 2)


def test_quantize_uses_actual_max_abs_for_out_of_range_inputs() -> None:
    vectors = np.array([[2.0, -1.0, 0.0]], dtype=np.float32)

    quantized, scale = quantize_int8(vectors)

    assert scale == np.float32(2.0 / 127.0)
    np.testing.assert_array_equal(quantized, np.array([[127, -64, 0]], dtype=np.int8))
