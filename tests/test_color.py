import numpy as np
import pytest

from video import color


def test_sample_patch_returns_mean_color_within_the_box():
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    image[10:20, 10:20] = [40, 80, 120]  # patch region
    image[:] = np.where(image == 0, 200, image)  # everything else bright

    patch_color = color.sample_patch(image, (10, 10, 20, 20))

    assert patch_color == pytest.approx([40, 80, 120])


def test_compute_gains_recovers_exact_ratio_needed():
    frame_patch = np.array([50.0, 100.0, 150.0])
    target_patch = np.array([100.0, 100.0, 100.0])

    gains = color.compute_gains(frame_patch, target_patch, clamp=(0.1, 10.0))

    assert gains == pytest.approx([2.0, 1.0, 100.0 / 150.0])


def test_compute_gains_clamps_extreme_ratios():
    frame_patch = np.array([10.0, 100.0, 100.0])
    target_patch = np.array([100.0, 100.0, 5.0])

    gains = color.compute_gains(frame_patch, target_patch, clamp=(0.5, 2.0))

    assert gains[0] == 2.0  # would be 10x, clamped to 2.0
    assert gains[1] == 1.0
    assert gains[2] == 0.5  # would be 0.05x, clamped to 0.5


def test_compute_gains_returns_none_for_too_dark_patch():
    frame_patch = np.array([5.0, 5.0, 5.0])
    target_patch = np.array([100.0, 100.0, 100.0])

    assert color.compute_gains(frame_patch, target_patch) is None


def test_compute_gains_accepts_patch_right_at_the_brightness_floor():
    frame_patch = np.array(
        [color.MIN_PATCH_BRIGHTNESS, color.MIN_PATCH_BRIGHTNESS, color.MIN_PATCH_BRIGHTNESS]
    )
    target_patch = np.array([100.0, 100.0, 100.0])

    assert color.compute_gains(frame_patch, target_patch) is not None


def test_limit_gains_for_headroom_caps_gain_that_would_blow_out_highlights():
    # A frame whose brightest content is already at 200 -- a gain of 2.0
    # would push that past 255 (blown out), so it must be scaled down.
    image = np.full((10, 10, 3), 200, dtype=np.uint8)

    limited = color.limit_gains_for_headroom(image, np.array([2.0, 2.0, 2.0]), ceiling=250)

    assert (limited * 200 <= 250 + 1e-6).all()
    assert (limited < 2.0).all()


def test_limit_gains_for_headroom_leaves_safe_gains_untouched():
    # Highlights are dim (50); a 2.0 gain only reaches 100, well under
    # ceiling, so nothing should be capped.
    image = np.full((10, 10, 3), 50, dtype=np.uint8)

    limited = color.limit_gains_for_headroom(image, np.array([2.0, 2.0, 2.0]), ceiling=250)

    assert limited == pytest.approx([2.0, 2.0, 2.0])


def test_limit_gains_for_headroom_never_increases_a_gain():
    # A gain of 0.5 (darkening) should never get pushed up, even though
    # ceiling/highlight would mathematically allow a much bigger gain.
    image = np.full((10, 10, 3), 10, dtype=np.uint8)

    limited = color.limit_gains_for_headroom(image, np.array([0.5, 0.5, 0.5]), ceiling=250)

    assert limited == pytest.approx([0.5, 0.5, 0.5])


def test_limit_gains_for_headroom_ignores_a_few_extreme_outlier_pixels():
    # A handful of already-blown-out specular highlights (255) shouldn't
    # single-handedly zero out the gain for the rest of a mostly-dim image --
    # the 99th percentile should look past them.
    image = np.full((100, 100, 3), 60, dtype=np.uint8)
    image[0, 0] = 255  # a single outlier pixel

    limited = color.limit_gains_for_headroom(image, np.array([2.0, 2.0, 2.0]), ceiling=250)

    assert limited == pytest.approx([2.0, 2.0, 2.0])


def test_apply_gains_scales_and_clips_to_uint8_range():
    image = np.array([[[100, 100, 100]]], dtype=np.uint8)

    corrected = color.apply_gains(image, np.array([2.0, 1.0, 0.5]))

    assert corrected.dtype == np.uint8
    assert list(corrected[0, 0]) == [200, 100, 50]


def test_apply_gains_clips_overflow_instead_of_wrapping():
    image = np.array([[[200, 200, 200]]], dtype=np.uint8)

    corrected = color.apply_gains(image, np.array([2.0, 1.0, 1.0]))

    # 200 * 2.0 = 400, must clip to 255, not wrap around to 144 (400 % 256)
    assert corrected[0, 0, 0] == 255
