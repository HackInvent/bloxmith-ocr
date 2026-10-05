"""Locally generated PDF and image fixtures; no downloaded documents or private data."""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

def pdf(path):
    """Build a portable two-page fixture: text followed by a genuinely blank page."""
    content = b"BT /F1 12 Tf 30 160 Td (Hello document) Tj ET"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 5 0 R >> >> /Contents 6 0 R >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >> /Contents 7 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Length 0 >>\nstream\n\nendstream"]
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, value in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(str(index).encode() + b" 0 obj\n" + value + b"\nendobj\n")
    offset = len(data)
    data.extend(b"xref\n0 8\n0000000000 65535 f \n")
    for item in offsets[1:]:
        data.extend(f"{item:010d} 00000 n \n".encode())
    data.extend(b"trailer\n<< /Size 8 /Root 1 0 R >>\nstartxref\n" + str(offset).encode() + b"\n%%EOF\n")
    path.write_bytes(data)



def image(path):
    """Draw a high-contrast fixture with Pillow’s bundled scalable default font."""
    canvas = Image.new("RGB", (1000, 300), "white")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=54)
    draw.text((50, 80), "HELLO DOCUMENT 123", fill="black", font=font)
    canvas.save(path)
