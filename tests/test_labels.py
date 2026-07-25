from datetime import datetime

import numpy as np
import pytest
from PIL import Image

from video import labels


def test_format_date_matches_requested_style():
    assert labels.format_date(datetime(2026, 7, 24)) == "July 24, 2026"


def test_format_date_has_no_leading_zero_on_day():
    assert labels.format_date(datetime(2026, 3, 5)) == "March 5, 2026"


def test_draw_label_rejects_unknown_corner():
    image = Image.new("RGB", (200, 100), (10, 20, 30))
    with pytest.raises(ValueError):
        labels.draw_label(image, "text", "top-right", 20, 10)


def test_draw_label_paints_background_box_at_top_left():
    background = (10, 20, 30)
    image = Image.new("RGB", (400, 300), background)

    labels.draw_label(image, "July 24, 2026", "top-left", 30, 15)

    assert image.getpixel((16, 16)) == (0, 0, 0)
    assert image.getpixel((5, 5)) == background
    assert image.getpixel((390, 290)) == background


def test_draw_label_paints_background_box_at_bottom_left():
    background = (10, 20, 30)
    image = Image.new("RGB", (400, 300), background)

    labels.draw_label(image, "some_photo.jpg", "bottom-left", 20, 15)

    assert image.getpixel((16, 285)) == (0, 0, 0)
    assert image.getpixel((5, 5)) == background


def test_draw_label_draws_visible_text_inside_the_box():
    image = Image.new("RGB", (400, 300), (10, 20, 30))

    labels.draw_label(image, "July 24, 2026", "top-left", 30, 15)

    # Somewhere inside the label's background box should be white text
    # pixels, not just solid black -- otherwise the text failed to render.
    box_region = np.array(image.crop((15, 15, 250, 60)))
    assert (box_region == 255).all(axis=-1).any()


def test_draw_label_returns_the_mutated_image():
    image = Image.new("RGB", (200, 100), (10, 20, 30))

    result = labels.draw_label(image, "text", "top-left", 20, 10)

    assert result is image


def test_label_box_size_matches_the_box_draw_label_actually_paints():
    background = (10, 20, 30)
    image = Image.new("RGB", (400, 300), background)
    text, font_size, padding = "some_photo.jpg", 24, 15

    labels.draw_label(image, text, "bottom-left", font_size, padding)

    box_w, box_h = labels.label_box_size(text, font_size)
    # Just inside the box's far corner should be black...
    assert image.getpixel((padding + box_w - 2, 300 - padding - 2)) == (0, 0, 0)
    # ...and just past it should be back to the background.
    assert image.getpixel((padding + box_w + 3, 300 - padding - 2)) == background


def test_draw_label_stack_offset_places_box_further_from_the_edge():
    background = (10, 20, 30)
    image = Image.new("RGB", (400, 300), background)

    labels.draw_label(image, "text", "bottom-left", 20, 15, stack_offset=40)

    # Right at the bottom edge (within the stack_offset gap) should be
    # untouched background, not the label's box.
    assert image.getpixel((16, 295)) == background
