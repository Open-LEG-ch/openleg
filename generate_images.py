#!/usr/bin/env python3
"""
Generate BadenLEG images for og-image, favicon, and apple-touch-icon
"""
try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Pillow not installed. Installing...")
    import subprocess
    subprocess.check_call(["pip", "install", "Pillow"])
    from PIL import Image, ImageDraw, ImageFont

import os

# Brand color: Baden red
BRAND_COLOR = "#c7021a"
WHITE = "#ffffff"
DARK_GRAY = "#1f2937"

def create_og_image():
    """Create Open Graph image (1200x630px)"""
    width, height = 1200, 630
    img = Image.new('RGB', (width, height), color=WHITE)
    draw = ImageDraw.Draw(img)
    
    # Try to use a nice font, fallback to default
    try:
        # Try to use system fonts (macOS)
        font_large = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 100)
        font_small = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 36)
    except:
        try:
            # Try Linux fonts
            font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 100)
            font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
        except:
            # Fallback to default font
            font_large = ImageFont.load_default()
            font_small = ImageFont.load_default()
    
    # Draw a subtle background accent (top bar)
    draw.rectangle([(0, 0), (width, 80)], fill=BRAND_COLOR)
    
    # Draw main title "BadenLEG"
    text = "BadenLEG"
    bbox = draw.textbbox((0, 0), text, font=font_large)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    
    x = (width - text_width) // 2
    y = height // 2 - text_height - 10
    
    # Draw "Baden" in brand color
    draw.text((x, y), "Baden", fill=BRAND_COLOR, font=font_large)
    bbox_baden = draw.textbbox((x, y), "Baden", font=font_large)
    baden_width = bbox_baden[2] - bbox_baden[0]
    
    # Draw "LEG" in dark gray
    draw.text((x + baden_width, y), "LEG", fill=DARK_GRAY, font=font_large)
    
    # Draw subtitle
    subtitle = "Lokale Elektrizitätsgemeinschaft"
    bbox_sub = draw.textbbox((0, 0), subtitle, font=font_small)
    sub_width = bbox_sub[2] - bbox_sub[0]
    sub_x = (width - sub_width) // 2
    sub_y = y + text_height + 25
    
    draw.text((sub_x, sub_y), subtitle, fill=DARK_GRAY, font=font_small)
    
    # Draw tagline
    tagline = "Finden Sie Nachbarn für Ihre Energiegemeinschaft"
    try:
        font_tagline = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 28)
    except:
        try:
            font_tagline = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
        except:
            font_tagline = ImageFont.load_default()
    
    bbox_tag = draw.textbbox((0, 0), tagline, font=font_tagline)
    tag_width = bbox_tag[2] - bbox_tag[0]
    tag_x = (width - tag_width) // 2
    tag_y = sub_y + bbox_sub[3] - bbox_sub[1] + 20
    
    draw.text((tag_x, tag_y), tagline, fill="#6b7280", font=font_tagline)
    
    # Save
    output_path = "static/images/og-image.png"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    img.save(output_path, "PNG", optimize=True)
    print(f"✓ Created {output_path}")

def create_favicon():
    """Create favicon (32x32px)"""
    size = 32
    img = Image.new('RGB', (size, size), color=BRAND_COLOR)
    draw = ImageDraw.Draw(img)
    
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 22)
    except:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        except:
            font = ImageFont.load_default()
    
    # Draw "BL" initials in white on red background
    text = "BL"
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    
    x = (size - text_width) // 2
    y = (size - text_height) // 2
    
    draw.text((x, y), text, fill=WHITE, font=font)
    
    # Save as ICO
    output_path = "static/favicon.ico"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    img.save(output_path, "ICO")
    print(f"✓ Created {output_path}")

def create_apple_touch_icon():
    """Create Apple touch icon (180x180px)"""
    size = 180
    img = Image.new('RGB', (size, size), color=WHITE)
    draw = ImageDraw.Draw(img)
    
    # Draw background with brand color at top
    draw.rectangle([(0, 0), (size, 40)], fill=BRAND_COLOR)
    
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 50)
    except:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 50)
        except:
            font = ImageFont.load_default()
    
    # Draw "BadenLEG"
    text = "BadenLEG"
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    
    x = (size - text_width) // 2
    y = (size - text_height) // 2
    
    # Draw "Baden" in brand color
    draw.text((x, y), "Baden", fill=BRAND_COLOR, font=font)
    bbox_baden = draw.textbbox((x, y), "Baden", font=font)
    baden_width = bbox_baden[2] - bbox_baden[0]
    
    # Draw "LEG" in dark gray
    draw.text((x + baden_width, y), "LEG", fill=DARK_GRAY, font=font)
    
    # Save
    output_path = "static/apple-touch-icon.png"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    img.save(output_path, "PNG", optimize=True)
    print(f"✓ Created {output_path}")

if __name__ == "__main__":
    print("Generating BadenLEG images...")
    create_og_image()
    create_favicon()
    create_apple_touch_icon()
    print("\nAll images created successfully!")

