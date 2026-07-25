"""Burn-in text labels for frames -- e.g. the capture date and source
filename, both meant as an alignment-QA aid (see video/main.py's
--label-date/--label-filename) rather than something that stays on the
final render.
"""

from PIL import ImageDraw, ImageFont

# stroke_width on top of the fill is Pillow's way to fake a bold weight
# without depending on a specific bold font file being installed --
# ImageFont.load_default(size=...) (Pillow's own bundled font, portable
# across the dev machine and the Pi) has no bold variant of its own.
STROKE_WIDTH = 2


def format_date(when):
    """when (a datetime) as e.g. "July 24, 2026" -- the format requested for
    the date label.
    """
    return when.strftime("%B %-d, %Y")


def label_box_size(text, font_size):
    """The (width, height) of the black background box draw_label would use
    for text at font_size, without needing an image/draw context -- lets a
    caller stacking multiple labels in the same corner (see stack_offset)
    work out one label's height before drawing the next one.
    """
    font = ImageFont.load_default(size=font_size)
    text_bbox = font.getbbox(text, stroke_width=STROKE_WIDTH)
    box_pad = max(4, font_size // 4)
    return (text_bbox[2] - text_bbox[0]) + 2 * box_pad, (text_bbox[3] - text_bbox[1]) + 2 * box_pad


def draw_label(image, text, corner, font_size, padding, stack_offset=0):
    """Draw text as bold white on a black background box, anchored in one
    corner of image ("top-left" or "bottom-left") with padding pixels
    between the box and the image edge. Mutates and returns image.

    stack_offset pushes the box an extra distance away from the anchored
    edge, on top of padding -- for stacking a second label in the same
    corner without overlapping the first (pass the first label's
    label_box_size height, plus whatever gap you want between them).
    """
    if corner not in ("top-left", "bottom-left"):
        raise ValueError(f"unknown corner {corner!r} -- expected 'top-left' or 'bottom-left'")

    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=font_size)
    text_bbox = draw.textbbox((0, 0), text, font=font, stroke_width=STROKE_WIDTH)
    text_w = text_bbox[2] - text_bbox[0]
    text_h = text_bbox[3] - text_bbox[1]

    box_pad = max(4, font_size // 4)
    box_w = text_w + 2 * box_pad
    box_h = text_h + 2 * box_pad

    box_x0 = padding
    box_y0 = (
        padding + stack_offset
        if corner == "top-left"
        else image.height - padding - stack_offset - box_h
    )

    draw.rectangle([box_x0, box_y0, box_x0 + box_w, box_y0 + box_h], fill="black")
    text_x = box_x0 + box_pad - text_bbox[0]
    text_y = box_y0 + box_pad - text_bbox[1]
    draw.text(
        (text_x, text_y),
        text,
        font=font,
        fill="white",
        stroke_width=STROKE_WIDTH,
        stroke_fill="white",
    )
    return image
