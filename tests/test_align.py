from datetime import datetime

import cv2
import numpy as np
import pytest
from PIL import ExifTags, Image

from normalize import align


def _textured_image(size=400, seed=0):
    """A synthetic image with enough distinct corners/edges for ORB to find
    reliable keypoints — random noise alone doesn't give stable features,
    so this draws a grid of randomly placed, randomly sized rectangles.
    """
    rng = np.random.default_rng(seed)
    image = np.full((size, size, 3), 30, dtype=np.uint8)
    for _ in range(60):
        x1, y1 = rng.integers(0, size - 40, size=2)
        w, h = rng.integers(10, 40, size=2)
        color = tuple(int(c) for c in rng.integers(0, 255, size=3))
        cv2.rectangle(image, (x1, y1), (x1 + w, y1 + h), color, thickness=-1)
    return image


def test_common_crop_box_trims_to_all_valid_region():
    mask = np.full((100, 100), 255, dtype=np.uint8)
    mask[:10, :] = 0  # invalid top strip
    mask[:, :20] = 0  # invalid left strip

    top, left, bottom, right = align.common_crop_box(mask)

    assert (top, left, bottom, right) == (10, 20, 100, 100)


def test_common_crop_box_returns_full_extent_when_all_valid():
    mask = np.full((50, 80), 255, dtype=np.uint8)

    assert align.common_crop_box(mask) == (0, 0, 50, 80)


def test_estimate_alignment_returns_none_when_not_enough_features():
    blank_a = np.zeros((200, 200), dtype=np.uint8)
    blank_b = np.zeros((200, 200), dtype=np.uint8)

    matrix, inliers = align.estimate_alignment(blank_a, blank_b, min_matches=10)

    assert matrix is None
    assert inliers == 0


def test_estimate_alignment_recovers_translation():
    reference = _textured_image()
    reference_gray = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)

    shift_matrix = np.array([[1, 0, 15], [0, 1, -10]], dtype=np.float32)
    shifted = cv2.warpAffine(reference, shift_matrix, (reference.shape[1], reference.shape[0]))
    shifted_gray = cv2.cvtColor(shifted, cv2.COLOR_BGR2GRAY)

    matrix, inliers = align.estimate_alignment(reference_gray, shifted_gray, min_matches=10)

    assert matrix is not None
    assert inliers >= 10
    # Mapping the shifted image back onto the reference should recover
    # approximately the inverse of the shift that was applied to it.
    assert matrix[0, 2] == pytest.approx(-15, abs=2)
    assert matrix[1, 2] == pytest.approx(10, abs=2)


def test_estimate_alignment_rejects_match_below_min_matches_threshold():
    reference = _textured_image()
    reference_gray = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)

    shift_matrix = np.array([[1, 0, 15], [0, 1, -10]], dtype=np.float32)
    shifted = cv2.warpAffine(reference, shift_matrix, (reference.shape[1], reference.shape[0]))
    shifted_gray = cv2.cvtColor(shifted, cv2.COLOR_BGR2GRAY)

    # The same pair of images that aligns fine at the default threshold
    # should be rejected once min_matches is set higher than the number of
    # features this synthetic pair can actually produce.
    matrix, inliers = align.estimate_alignment(reference_gray, shifted_gray, min_matches=100_000)

    assert matrix is None
    assert inliers < 100_000


def _write_jpeg_with_exif_datetime(path, when):
    image = Image.new("RGB", (40, 40), color=(120, 120, 120))
    _set_exif_datetime(image, path, when)


def _set_exif_datetime(image, path, when):
    """Save image to path with an EXIF DateTimeOriginal tag — used instead of
    just re-opening+re-saving an already-written file, since a plain
    Image.open().save() round-trip through Pillow's JPEG encoder can still
    subtly perturb pixels enough to matter for the alignment tests.
    """
    exif = image.getexif()
    exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
    exif_ifd[36867] = when.strftime(align.EXIF_DATETIME_FORMAT)  # DateTimeOriginal
    exif[ExifTags.IFD.Exif] = exif_ifd
    image.save(path, format="JPEG", exif=exif)


def test_capture_time_reads_exif_datetime_original(tmp_path):
    path = tmp_path / "photo.jpg"
    _write_jpeg_with_exif_datetime(path, datetime(2026, 1, 15, 8, 30, 0))

    assert align.capture_time(path) == datetime(2026, 1, 15, 8, 30, 0)


def test_capture_time_falls_back_to_mtime_without_exif(tmp_path):
    path = tmp_path / "photo.jpg"
    cv2.imwrite(str(path), np.zeros((10, 10, 3), dtype=np.uint8))

    assert align.capture_time(path) == datetime.fromtimestamp(path.stat().st_mtime)


def test_list_images_orders_by_exif_time_not_filename(tmp_path):
    # "z_first.jpg" sorts last by filename but has the earliest EXIF
    # timestamp — list_images must use capture time, not the name.
    _write_jpeg_with_exif_datetime(tmp_path / "z_first.jpg", datetime(2026, 1, 1, 8, 0, 0))
    _write_jpeg_with_exif_datetime(tmp_path / "a_second.jpg", datetime(2026, 1, 1, 9, 0, 0))
    _write_jpeg_with_exif_datetime(tmp_path / "m_third.jpg", datetime(2026, 1, 1, 10, 0, 0))

    ordered = [p.name for p in align.list_images(tmp_path)]

    assert ordered == ["z_first.jpg", "a_second.jpg", "m_third.jpg"]


def test_normalize_sequence_processes_frames_in_exif_order(tmp_path):
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    output_dir = tmp_path / "out"

    reference = _textured_image()
    # Filenames are alphabetically reversed vs. their EXIF capture times, so
    # this only passes if normalize_sequence picks the EXIF-earliest frame
    # ("z_reference.jpg") as the default reference.
    ref_path = input_dir / "z_reference.jpg"
    _set_exif_datetime(
        Image.fromarray(cv2.cvtColor(reference, cv2.COLOR_BGR2RGB)),
        ref_path,
        datetime(2026, 1, 1, 8, 0, 0),
    )

    shift_matrix = np.array([[1, 0, 8], [0, 1, -5]], dtype=np.float32)
    shifted = cv2.warpAffine(reference, shift_matrix, (reference.shape[1], reference.shape[0]))
    shifted_path = input_dir / "a_later.jpg"
    _set_exif_datetime(
        Image.fromarray(cv2.cvtColor(shifted, cv2.COLOR_BGR2RGB)),
        shifted_path,
        datetime(2026, 1, 1, 9, 0, 0),
    )

    result = align.normalize_sequence(input_dir, output_dir, min_matches=10)

    assert set(result["aligned"]) == {"z_reference.jpg", "a_later.jpg"}


def test_normalize_sequence_writes_uniform_sized_frames_and_reports_skips(tmp_path):
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    output_dir = tmp_path / "out"

    reference = _textured_image()
    cv2.imwrite(str(input_dir / "a_reference.jpg"), reference)

    shift_matrix = np.array([[1, 0, 8], [0, 1, -5]], dtype=np.float32)
    shifted = cv2.warpAffine(reference, shift_matrix, (reference.shape[1], reference.shape[0]))
    cv2.imwrite(str(input_dir / "b_shifted.jpg"), shifted)

    blank = np.full((400, 400, 3), 128, dtype=np.uint8)
    cv2.imwrite(str(input_dir / "c_blank.jpg"), blank)

    result = align.normalize_sequence(input_dir, output_dir, min_matches=10)

    assert set(result["aligned"]) == {"a_reference.jpg", "b_shifted.jpg"}
    assert [name for name, _ in result["skipped"]] == ["c_blank.jpg"]

    written = {p.name: cv2.imread(str(p)) for p in output_dir.iterdir()}
    assert set(written) == {"a_reference.jpg", "b_shifted.jpg"}
    assert written["a_reference.jpg"].shape == written["b_shifted.jpg"].shape


def test_normalize_sequence_applies_requested_output_size(tmp_path):
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    output_dir = tmp_path / "out"

    reference = _textured_image()
    cv2.imwrite(str(input_dir / "a_reference.jpg"), reference)
    cv2.imwrite(str(input_dir / "b_same.jpg"), reference)

    result = align.normalize_sequence(input_dir, output_dir, min_matches=10, output_size=(100, 80))

    frame = cv2.imread(str(output_dir / "a_reference.jpg"))
    assert frame.shape[:2] == (80, 100)
    assert result["crop_box"] == (0, 0, reference.shape[0], reference.shape[1])
