import json
from datetime import datetime

import cv2
import numpy as np
import pytest
from PIL import ExifTags, Image

from normalize import align
from normalize import control_points as cp


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


def test_estimate_alignment_handles_single_descriptor_reference(monkeypatch):
    """Regression test: cv2's knnMatch(k=2) only returns as many neighbors per
    query as the reference (train) set has descriptors. A reference with
    exactly one descriptor makes every match a 1-tuple, and unpacking
    `for m, n in matches` raises ValueError instead of estimate_alignment
    reporting "no match" like it does for any other unmatchable pair.
    """

    class FakeORB:
        def __init__(self):
            self.calls = 0

        def detectAndCompute(self, image, mask):
            self.calls += 1
            if self.calls == 1:  # reference image: exactly one descriptor
                return [cv2.KeyPoint(0, 0, 1)], np.zeros((1, 32), dtype=np.uint8)
            return [cv2.KeyPoint(0, 0, 1)] * 5, np.zeros((5, 32), dtype=np.uint8)

    monkeypatch.setattr(align.cv2, "ORB_create", lambda nfeatures=2000: FakeORB())

    matrix, inliers = align.estimate_alignment(
        np.zeros((10, 10), dtype=np.uint8), np.zeros((10, 10), dtype=np.uint8)
    )

    assert matrix is None
    assert inliers == 0


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


def test_estimate_alignment_ecc_recovers_translation():
    reference = _textured_image()
    reference_gray = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)

    shift_matrix = np.array([[1, 0, 15], [0, 1, -10]], dtype=np.float32)
    shifted = cv2.warpAffine(reference, shift_matrix, (reference.shape[1], reference.shape[0]))
    shifted_gray = cv2.cvtColor(shifted, cv2.COLOR_BGR2GRAY)

    matrix, confidence = align.estimate_alignment_ecc(
        reference_gray, shifted_gray, pyramid_widths=(100, 200, 400)
    )

    assert matrix is not None
    assert confidence > 0.9
    # matrix maps shifted (target) -> reference, i.e. it undoes shift_matrix
    # -- the inverse translation, not shift_matrix's own (+15, -10).
    assert matrix[0, 2] == pytest.approx(-15, abs=1)
    assert matrix[1, 2] == pytest.approx(10, abs=1)


def test_estimate_alignment_ecc_handles_differing_shapes():
    # Simulates two photos of the same scene at different resolutions/aspect
    # ratios (e.g. a drone app's own re-encoding) -- unlike estimate_alignment,
    # ECC needs same-shape inputs internally, so estimate_alignment_ecc must
    # resize target_gray to reference_gray's shape itself and report the
    # resulting matrix back in each image's own native resolution.
    reference = _textured_image()
    reference_gray = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)
    target_gray = cv2.resize(reference_gray, (300, 350))

    matrix, confidence = align.estimate_alignment_ecc(
        reference_gray, target_gray, pyramid_widths=(100, 200, 400)
    )

    assert matrix is not None
    assert confidence > 0.9
    # target_gray is reference_gray shrunk to (300, 350) from (400, 400), so
    # the recovered scale should undo that: ~400/300 in x, ~400/350 in y.
    assert matrix[0, 0] == pytest.approx(400 / 300, abs=0.05)
    assert matrix[1, 1] == pytest.approx(400 / 350, abs=0.05)


def test_estimate_alignment_ecc_recovers_rotation_and_scale_in_correct_direction():
    # Regression test for a real direction bug: cv2.findTransformECC's raw
    # result maps *reference* content onto target (confirmed empirically --
    # warping reference by the raw result reproduces target), the opposite
    # of what this function must return (target -> reference, matching
    # estimate_alignment and what warp_to_reference expects). A pure
    # translation can't tell the two directions apart as clearly as
    # rotation + scale can, so this uses both.
    reference = _textured_image()
    reference_gray = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)

    h, w = reference_gray.shape
    known = cv2.getRotationMatrix2D((w / 2, h / 2), 8, 1.15)
    known[0, 2] += 12
    known[1, 2] -= 7
    target = cv2.warpAffine(reference, known, (w, h))
    target_gray = cv2.cvtColor(target, cv2.COLOR_BGR2GRAY)

    matrix, confidence = align.estimate_alignment_ecc(reference_gray, target_gray)

    assert matrix is not None
    assert confidence > 0.9
    # matrix must undo `known` (map target -> reference), not equal it.
    known_inverse = cv2.invertAffineTransform(known)
    assert matrix == pytest.approx(known_inverse, abs=0.1)

    # The real-world check that actually matters: warping target by matrix
    # should reconstruct reference, not the other way around.
    reconstructed = cv2.warpAffine(target, matrix, (w, h))
    valid = cv2.warpAffine(np.full((h, w), 255, dtype=np.uint8), matrix, (w, h)) > 0
    err = np.abs(
        cv2.cvtColor(reconstructed, cv2.COLOR_BGR2GRAY)[valid].astype(np.float32)
        - reference_gray[valid].astype(np.float32)
    ).mean()
    assert err < 5.0


def test_estimate_alignment_ecc_returns_none_for_featureless_input():
    reference = _textured_image()
    reference_gray = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)
    blank = np.full(reference_gray.shape, 128, dtype=np.uint8)

    matrix, confidence = align.estimate_alignment_ecc(
        reference_gray, blank, pyramid_widths=(100, 200)
    )

    assert matrix is None
    assert confidence == 0.0


def test_plausible_affine_rejects_extreme_scale():
    reasonable = np.array([[1.1, 0, 5], [0, 0.9, -3]], dtype=np.float32)
    too_small = np.array([[0.1, 0, 0], [0, 0.1, 0]], dtype=np.float32)
    too_large = np.array([[10.0, 0, 0], [0, 10.0, 0]], dtype=np.float32)

    assert align._plausible_affine(reasonable)
    assert not align._plausible_affine(too_small)
    assert not align._plausible_affine(too_large)


def test_normalize_sequence_ecc_method_processes_frames(tmp_path):
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

    result = align.normalize_sequence(input_dir, output_dir, method="ecc", min_confidence=0.5)

    assert set(result["aligned"]) == {"a_reference.jpg", "b_shifted.jpg"}
    assert [name for name, _ in result["skipped"]] == ["c_blank.jpg"]


def test_normalize_sequence_rejects_unknown_method(tmp_path):
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    cv2.imwrite(str(input_dir / "a.jpg"), _textured_image())

    with pytest.raises(ValueError):
        align.normalize_sequence(input_dir, tmp_path / "out", method="not-a-real-method")


def test_normalize_sequence_excludes_rejected_photos(tmp_path):
    from normalize import control_points as cp

    input_dir = tmp_path / "in"
    input_dir.mkdir()
    output_dir = tmp_path / "out"

    reference = _textured_image()
    cv2.imwrite(str(input_dir / "a_reference.jpg"), reference)

    shift_matrix = np.array([[1, 0, 8], [0, 1, -5]], dtype=np.float32)
    shifted = cv2.warpAffine(reference, shift_matrix, (reference.shape[1], reference.shape[0]))
    cv2.imwrite(str(input_dir / "b_shifted.jpg"), shifted)

    blank = np.full((400, 400, 3), 128, dtype=np.uint8)
    cv2.imwrite(str(input_dir / "c_unrelated.jpg"), blank)

    data = cp.empty("a_reference.jpg")
    cp.reject(data, "b_shifted.jpg")

    result = align.normalize_sequence(input_dir, output_dir, control_points=data)

    assert "b_shifted.jpg" not in result["aligned"]
    reasons = dict(result["skipped"])
    assert "not belonging to this sequence" in reasons["b_shifted.jpg"]


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


def test_list_images_breaks_capture_time_ties_by_filename(tmp_path):
    # Both photos share the same EXIF timestamp — order must be deterministic
    # (by filename), not whatever order Path.iterdir() happens to yield.
    _write_jpeg_with_exif_datetime(tmp_path / "b_second.jpg", datetime(2026, 1, 1, 8, 0, 0))
    _write_jpeg_with_exif_datetime(tmp_path / "a_first.jpg", datetime(2026, 1, 1, 8, 0, 0))

    ordered = [p.name for p in align.list_images(tmp_path)]

    assert ordered == ["a_first.jpg", "b_second.jpg"]


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

    written = {p.name: cv2.imread(str(p)) for p in output_dir.glob("*.jpg")}
    assert set(written) == {"a_reference.jpg", "b_shifted.jpg"}
    assert written["a_reference.jpg"].shape == written["b_shifted.jpg"].shape


def test_normalize_sequence_applies_requested_output_size(tmp_path):
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    output_dir = tmp_path / "out"

    reference = _textured_image()
    cv2.imwrite(str(input_dir / "a_reference.jpg"), reference)
    cv2.imwrite(str(input_dir / "b_same.jpg"), reference)

    result = align.normalize_sequence(
        input_dir, output_dir, min_matches=10, output_size=(100, 80), crop="intersection"
    )

    frame = cv2.imread(str(output_dir / "a_reference.jpg"))
    assert frame.shape[:2] == (80, 100)
    assert result["crop_box"] == (0, 0, reference.shape[0], reference.shape[1])


def test_normalize_sequence_crop_none_keeps_full_frame_with_black_border(tmp_path):
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    output_dir = tmp_path / "out"

    reference = _textured_image()
    cv2.imwrite(str(input_dir / "a_reference.jpg"), reference)

    # A shift large enough that warping this frame into the reference's
    # coordinate space necessarily leaves a real black border, but modest
    # enough for ECC to reliably converge on this small synthetic image.
    shift_matrix = np.array([[1, 0, 25], [0, 1, 20]], dtype=np.float32)
    shifted = cv2.warpAffine(reference, shift_matrix, (reference.shape[1], reference.shape[0]))
    cv2.imwrite(str(input_dir / "b_shifted.jpg"), shifted)

    result = align.normalize_sequence(input_dir, output_dir, crop="none")

    assert result["crop_box"] == (0, 0, reference.shape[0], reference.shape[1])
    frame_a = cv2.imread(str(output_dir / "a_reference.jpg"))
    frame_b = cv2.imread(str(output_dir / "b_shifted.jpg"))
    # Both frames keep the reference's full size -- crop="none" never shrinks
    # the canvas, unlike crop="intersection".
    assert frame_a.shape == reference.shape
    assert frame_b.shape == reference.shape
    # The shifted frame's warp leaves a real black region (unlike
    # crop="intersection", which would have cropped it away).
    assert (frame_b == 0).any()


def test_normalize_sequence_rejects_unknown_crop(tmp_path):
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    cv2.imwrite(str(input_dir / "a.jpg"), _textured_image())

    with pytest.raises(ValueError):
        align.normalize_sequence(input_dir, tmp_path / "out", crop="not-a-real-crop-mode")


def test_compose_with_anchor_chains_photo_to_anchor_and_anchor_to_reference():
    # anchor_to_reference: a pure translation (10, 20). photo_to_anchor: a
    # pure translation (3, -4). Composed, a point should land where both
    # translations applied in sequence would put it.
    anchor_to_reference = np.array([[1, 0, 10], [0, 1, 20], [0, 0, 1]], dtype=np.float32)
    photo_to_anchor = np.array([[1, 0, 3], [0, 1, -4]], dtype=np.float32)

    composed = align._compose_with_anchor(anchor_to_reference, photo_to_anchor)

    point = np.float32([[[5, 5]]])
    mapped = cv2.perspectiveTransform(point, composed)[0, 0]
    assert mapped == pytest.approx([5 + 3 + 10, 5 - 4 + 20])


def test_best_candidate_alignment_prefers_anchor_over_distant_reference():
    # A photo close to an anchor (small shift) but far from the reference
    # (large rotation + scale change) should align via the anchor, not by
    # attempting a direct (and much harder) match to the reference.
    reference = _textured_image()
    reference_gray = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)
    h, w = reference.shape[:2]

    big_change = cv2.getRotationMatrix2D((w / 2, h / 2), 25, 0.7)
    anchor = cv2.warpAffine(reference, big_change, (w, h))
    anchor_gray = cv2.cvtColor(anchor, cv2.COLOR_BGR2GRAY)

    points_reference = np.float32([[50, 50], [350, 50], [350, 350], [50, 350]])
    points_anchor = cv2.transform(points_reference.reshape(-1, 1, 2), big_change).reshape(-1, 2)
    data = cp.empty("reference.jpg")
    cp.set_anchor_points(data, "anchor.jpg", points_reference.tolist(), points_anchor.tolist())
    anchor_matrix = cp.anchor_transform(data, "anchor.jpg")

    small_shift = np.array([[1, 0, 8], [0, 1, -5]], dtype=np.float32)
    photo = cv2.warpAffine(anchor, small_shift, (w, h))
    photo_gray = cv2.cvtColor(photo, cv2.COLOR_BGR2GRAY)

    anchors = {"anchor.jpg": (anchor_gray, anchor_matrix)}
    matrix, score, label = align._best_candidate_alignment(
        photo_gray, reference_gray, anchors, "ecc", 10, 0.2, 20000
    )

    assert label == "anchor.jpg"
    warped, mask = align.warp_to_reference(photo, matrix, reference.shape)
    valid = mask > 0
    err = np.abs(
        cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)[valid].astype(np.float32)
        - reference_gray[valid].astype(np.float32)
    ).mean()
    assert err < 5.0


def test_normalize_sequence_with_control_points_uses_anchor_for_similar_photo(tmp_path):
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    output_dir = tmp_path / "out"

    reference = _textured_image()
    ref_path = input_dir / "reference.jpg"
    cv2.imwrite(str(ref_path), reference)
    h, w = reference.shape[:2]

    big_change = cv2.getRotationMatrix2D((w / 2, h / 2), 25, 0.7)
    anchor = cv2.warpAffine(reference, big_change, (w, h))
    cv2.imwrite(str(input_dir / "anchor.jpg"), anchor)

    small_shift = np.array([[1, 0, 8], [0, 1, -5]], dtype=np.float32)
    photo = cv2.warpAffine(anchor, small_shift, (w, h))
    cv2.imwrite(str(input_dir / "photo.jpg"), photo)

    points_reference = np.float32([[50, 50], [350, 50], [350, 350], [50, 350]])
    points_anchor = cv2.transform(points_reference.reshape(-1, 1, 2), big_change).reshape(-1, 2)
    data = cp.empty("reference.jpg")
    cp.set_anchor_points(data, "anchor.jpg", points_reference.tolist(), points_anchor.tolist())

    result = align.normalize_sequence(
        input_dir, output_dir, reference=ref_path, crop="none", control_points=data
    )

    assert set(result["aligned"]) == {"reference.jpg", "anchor.jpg", "photo.jpg"}

    warped_photo = cv2.imread(str(output_dir / "photo.jpg"))
    reference_gray = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)
    warped_gray = cv2.cvtColor(warped_photo, cv2.COLOR_BGR2GRAY)
    valid = warped_gray > 0
    err = np.abs(
        warped_gray[valid].astype(np.float32) - reference_gray[valid].astype(np.float32)
    ).mean()
    assert err < 5.0


def test_normalize_sequence_writes_manifest_with_capture_timestamps(tmp_path):
    # cv2.imwrite strips EXIF from the aligned/cropped output, so the
    # manifest is the only place a downstream video builder can recover each
    # frame's real capture time (needed for proportional-duration timing).
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    output_dir = tmp_path / "out"

    reference = _textured_image()
    ref_path = input_dir / "a_reference.jpg"
    _set_exif_datetime(
        Image.fromarray(cv2.cvtColor(reference, cv2.COLOR_BGR2RGB)),
        ref_path,
        datetime(2026, 1, 1, 8, 0, 0),
    )

    shift_matrix = np.array([[1, 0, 8], [0, 1, -5]], dtype=np.float32)
    shifted = cv2.warpAffine(reference, shift_matrix, (reference.shape[1], reference.shape[0]))
    shifted_path = input_dir / "b_later.jpg"
    _set_exif_datetime(
        Image.fromarray(cv2.cvtColor(shifted, cv2.COLOR_BGR2RGB)),
        shifted_path,
        datetime(2026, 1, 1, 9, 30, 0),
    )

    align.normalize_sequence(input_dir, output_dir, min_matches=10)

    manifest = json.loads((output_dir / align.MANIFEST_FILENAME).read_text())
    assert manifest == {
        "a_reference.jpg": "2026-01-01T08:00:00",
        "b_later.jpg": "2026-01-01T09:30:00",
    }


def test_normalize_sequence_manifest_omits_skipped_frames(tmp_path):
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    output_dir = tmp_path / "out"

    reference = _textured_image()
    cv2.imwrite(str(input_dir / "a_reference.jpg"), reference)

    blank = np.full((400, 400, 3), 128, dtype=np.uint8)
    cv2.imwrite(str(input_dir / "b_blank.jpg"), blank)

    align.normalize_sequence(input_dir, output_dir, min_matches=10)

    manifest = json.loads((output_dir / align.MANIFEST_FILENAME).read_text())
    assert set(manifest) == {"a_reference.jpg"}
