import cv2
import numpy as np
import pytest

from normalize import control_points as cp


def test_empty_has_no_anchors():
    data = cp.empty("reference.jpg")
    assert data == {"reference": "reference.jpg", "anchors": {}, "rejected": []}


def test_load_missing_file_returns_empty(tmp_path):
    data = cp.load(tmp_path / "does-not-exist.json")
    assert data == {"reference": None, "anchors": {}, "rejected": []}


def test_load_fills_in_rejected_for_older_files_without_it(tmp_path):
    # A control points file saved before "rejected" existed shouldn't break
    # loading -- it should come back with an empty rejected list.
    path = tmp_path / "control_points.json"
    path.write_text('{"reference": "reference.jpg", "anchors": {}}')

    data = cp.load(path)

    assert data["rejected"] == []


def test_reject_adds_to_rejected_list():
    data = cp.empty("reference.jpg")

    cp.reject(data, "bad_photo.jpg")

    assert data["rejected"] == ["bad_photo.jpg"]


def test_reject_is_idempotent():
    data = cp.empty("reference.jpg")

    cp.reject(data, "bad_photo.jpg")
    cp.reject(data, "bad_photo.jpg")

    assert data["rejected"] == ["bad_photo.jpg"]


def test_reject_removes_existing_anchor_points():
    data = cp.empty("reference.jpg")
    cp.set_anchor_points(
        data,
        "anchor.jpg",
        points_reference=[[0, 0], [10, 0], [10, 10], [0, 10]],
        points_anchor=[[1, 1], [11, 1], [11, 11], [1, 11]],
    )

    cp.reject(data, "anchor.jpg")

    assert "anchor.jpg" not in data["anchors"]
    assert data["rejected"] == ["anchor.jpg"]


def test_set_anchor_points_un_rejects_a_previously_rejected_photo():
    data = cp.empty("reference.jpg")
    cp.reject(data, "anchor.jpg")

    cp.set_anchor_points(
        data,
        "anchor.jpg",
        points_reference=[[0, 0], [10, 0], [10, 10], [0, 10]],
        points_anchor=[[1, 1], [11, 1], [11, 11], [1, 11]],
    )

    assert "anchor.jpg" not in data["rejected"]
    assert "anchor.jpg" in data["anchors"]


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "control_points.json"
    data = cp.empty("reference.jpg")
    cp.set_anchor_points(
        data,
        "anchor.jpg",
        points_reference=[[0, 0], [10, 0], [10, 10], [0, 10]],
        points_anchor=[[1, 1], [11, 1], [11, 11], [1, 11]],
    )

    cp.save(path, data)
    loaded = cp.load(path)

    assert loaded == data


def test_set_anchor_points_rejects_mismatched_lengths():
    data = cp.empty("reference.jpg")
    with pytest.raises(ValueError):
        cp.set_anchor_points(
            data,
            "anchor.jpg",
            points_reference=[[0, 0], [10, 0], [10, 10], [0, 10]],
            points_anchor=[[1, 1], [11, 1], [11, 11]],
        )


def test_set_anchor_points_rejects_too_few_points():
    data = cp.empty("reference.jpg")
    with pytest.raises(ValueError):
        cp.set_anchor_points(
            data,
            "anchor.jpg",
            points_reference=[[0, 0], [10, 0], [10, 10]],
            points_anchor=[[1, 1], [11, 1], [11, 11]],
        )


def test_anchor_transform_recovers_known_homography():
    # A homography with translation, scale, shear, and a touch of
    # perspective, applied to a quad of anchor-space points to produce their
    # reference-space counterparts -- anchor_transform should recover
    # (approximately) the same homography from those correspondences alone.
    true_homography = np.array(
        [[1.2, 0.05, 30], [0.02, 0.9, -20], [0.0001, 0.00005, 1]], dtype=np.float32
    )
    points_anchor = np.float32([[0, 0], [500, 0], [500, 400], [0, 400]])
    points_reference = cv2.perspectiveTransform(
        points_anchor.reshape(-1, 1, 2), true_homography
    ).reshape(-1, 2)

    data = cp.empty("reference.jpg")
    cp.set_anchor_points(data, "anchor.jpg", points_reference.tolist(), points_anchor.tolist())

    recovered = cp.anchor_transform(data, "anchor.jpg")

    assert recovered == pytest.approx(true_homography, abs=1e-3)


def test_anchor_transform_maps_anchor_points_onto_reference_points():
    # A more direct check than comparing matrices: actually warping the
    # anchor-space points through the recovered transform should land them
    # (approximately) on the reference-space points that defined it.
    true_homography = np.array(
        [[1.05, 0.01, 50], [-0.02, 0.97, 20], [0.00005, 0.0001, 1]], dtype=np.float32
    )
    points_anchor = np.float32([[0, 0], [800, 0], [800, 600], [0, 600], [400, 300]])
    points_reference = cv2.perspectiveTransform(
        points_anchor.reshape(-1, 1, 2), true_homography
    ).reshape(-1, 2)

    data = cp.empty("reference.jpg")
    cp.set_anchor_points(data, "anchor.jpg", points_reference.tolist(), points_anchor.tolist())
    matrix = cp.anchor_transform(data, "anchor.jpg")

    mapped = cv2.perspectiveTransform(points_anchor.reshape(-1, 1, 2), matrix).reshape(-1, 2)

    assert mapped == pytest.approx(points_reference, abs=1e-2)
