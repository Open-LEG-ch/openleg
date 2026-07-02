# SPDX-License-Identifier: AGPL-3.0-or-later
#!/usr/bin/env python3
"""
Generate OpenLEG images for og-image, favicon, and apple-touch-icon.
Colors follow the violet brand (tailwind.config.js brand.DEFAULT).
"""

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Pillow not installed. Installing...")
    import subprocess

    subprocess.check_call(["pip", "install", "Pillow"])
    from PIL import Image, ImageDraw, ImageFont

import os

# Brand colors (violet)
BRAND_COLOR = "#4f46e5"
WHITE = "#ffffff"
DARK_GRAY = "#1f2937"
INK = "#0f172a"
PAPER = "#f6f4ef"


def create_og_image():
    """Create Open Graph image (1200x630px)"""
    width, height = 1200, 630
    img = Image.new("RGB", (width, height), color=WHITE)
    draw = ImageDraw.Draw(img)

    try:
        font_large = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 100)
        font_small = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 36)
    except Exception:
        try:
            font_large = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 100
            )
            font_small = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36
            )
        except Exception:
            font_large = ImageFont.load_default()
            font_small = ImageFont.load_default()

    draw.rectangle([(0, 0), (width, 80)], fill=BRAND_COLOR)

    text = "OpenLEG"
    bbox = draw.textbbox((0, 0), text, font=font_large)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    x = (width - text_width) // 2
    y = height // 2 - text_height - 10

    draw.text((x, y), "Open", fill=BRAND_COLOR, font=font_large)
    bbox_prefix = draw.textbbox((x, y), "Open", font=font_large)
    prefix_width = bbox_prefix[2] - bbox_prefix[0]
    draw.text((x + prefix_width, y), "LEG", fill=DARK_GRAY, font=font_large)

    subtitle = "Lokale Elektrizitätsgemeinschaft"
    bbox_sub = draw.textbbox((0, 0), subtitle, font=font_small)
    sub_width = bbox_sub[2] - bbox_sub[0]
    sub_x = (width - sub_width) // 2
    sub_y = y + text_height + 25

    draw.text((sub_x, sub_y), subtitle, fill=DARK_GRAY, font=font_small)

    tagline = "Finden Sie Nachbarn für Ihre Energiegemeinschaft"
    try:
        font_tagline = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 28)
    except Exception:
        try:
            font_tagline = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28
            )
        except Exception:
            font_tagline = ImageFont.load_default()

    bbox_tag = draw.textbbox((0, 0), tagline, font=font_tagline)
    tag_width = bbox_tag[2] - bbox_tag[0]
    tag_x = (width - tag_width) // 2
    tag_y = sub_y + bbox_sub[3] - bbox_sub[1] + 20

    draw.text((tag_x, tag_y), tagline, fill="#6b7280", font=font_tagline)

    output_path = "static/images/og-image.png"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    img.save(output_path, "PNG", optimize=True)
    print(f"✓ Created {output_path}")


def create_favicon():
    """Create favicon (32x32px) matching static/favicon.svg."""
    size = 32
    img = Image.new("RGBA", (size, size), color=(0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle([(0, 0), (size - 1, size - 1)], radius=8, fill=INK)
    draw.line(
        [(10, 11), (16.5, 16), (10, 21)],
        fill=BRAND_COLOR,
        width=3,
        joint="curve",
    )
    draw.line([(18, 22), (24, 22)], fill=PAPER, width=3)

    output_path = "static/favicon.ico"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    img.save(output_path, "ICO")
    print(f"✓ Created {output_path}")


def create_apple_touch_icon():
    """Create Apple touch icon matching static/favicon.svg."""
    size = 180
    img = Image.new("RGBA", (size, size), color=(0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle([(0, 0), (size - 1, size - 1)], radius=45, fill=INK)
    draw.line(
        [(56, 62), (93, 90), (56, 118)],
        fill=BRAND_COLOR,
        width=15,
        joint="curve",
    )
    draw.line([(101, 124), (135, 124)], fill=PAPER, width=15)

    output_path = "static/apple-touch-icon.png"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    img.save(output_path, "PNG", optimize=True)
    print(f"✓ Created {output_path}")


if __name__ == "__main__":
    print("Generating OpenLEG images...")
    create_og_image()
    create_favicon()
    create_apple_touch_icon()
    print("\nAll images created successfully!")
