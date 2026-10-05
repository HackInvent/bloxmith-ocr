"""Local OCR: stable sources, bounded child processes and explicit uncertain regions."""

import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

DEFAULTS = {"allowed_root": "", "languages": ["eng"], "first_page": 1, "last_page": 0,
            "dpi": 200, "page_segmentation": 3, "confidence_threshold": 70,
            "max_pages": 20, "max_bytes": 20971520, "max_characters": 300000, "timeout_sec": 120}
FORMATS = {"pdf", "png", "jpg", "jpeg", "tif", "tiff", "webp", "bmp", "ppm"}


class OcrError(ValueError):
    """A bounded, content-free diagnostic that can be exposed in a run log."""


def configuration(raw):
    """Validate settings without touching the source or loading language models."""
    if not isinstance(raw, dict) or set(raw) - set(DEFAULTS) - {"execution", "runtime_path", "runtime_path_label"}:
        raise OcrError("Unknown setting.")
    result = {**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS}}
    if not isinstance(result["allowed_root"], str) or len(result["allowed_root"]) > 4096 or "\x00" in result["allowed_root"]:
        raise OcrError("allowed_root must be a directory path.")
    languages = result["languages"]
    if not isinstance(languages, list) or not 1 <= len(languages) <= 5 or any(
            not isinstance(x, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{1,30}", x) for x in languages):
        raise OcrError("languages must contain 1 to 5 installed language codes.")
    if len(set(languages)) != len(languages):
        raise OcrError("Language codes must be distinct.")
    for key, low, high in (("first_page", 1, 10000), ("last_page", 0, 10000), ("dpi", 72, 400),
            ("page_segmentation", 3, 13), ("confidence_threshold", 0, 100), ("max_pages", 1, 100),
            ("max_bytes", 1024, 52428800), ("max_characters", 1, 1000000), ("timeout_sec", 1, 600)):
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise OcrError(f"{key} must be an integer between {low} and {high}.")
    if result["last_page"] and result["last_page"] < result["first_page"]:
        raise OcrError("last_page must be zero or at least first_page.")
    return result


def check(deadline, cancel):
    """Apply one deadline to snapshotting, rendering and OCR."""
    if cancel():
        raise OcrError("OCR cancelled.")
    if time.monotonic() >= deadline:
        raise OcrError("OCR exceeded timeout_sec.")


def command(argv, deadline, cancel, limit):
    """Use an argument vector, bounded disk captures and terminate only our child."""
    executable = shutil.which(argv[0])
    if not executable:
        raise OcrError(f"Required local executable is missing: {argv[0]}.")
    check(deadline, cancel)
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        child = subprocess.Popen([executable, *argv[1:]], stdin=subprocess.DEVNULL, stdout=out,
                stderr=err, env={**os.environ, "LC_ALL": "C", "OMP_THREAD_LIMIT": "1"})
        try:
            while child.poll() is None:
                check(deadline, cancel)
                if os.fstat(out.fileno()).st_size > limit or os.fstat(err.fileno()).st_size > 65536:
                    raise OcrError("Local tool output exceeded its size limit.")
                try:
                    child.wait(timeout=0.05)
                except subprocess.TimeoutExpired:
                    pass
            check(deadline, cancel)
            if child.returncode:
                raise OcrError(f"{argv[0]} failed; check installed languages and the source format.")
            if os.fstat(out.fileno()).st_size > limit or os.fstat(err.fileno()).st_size > 65536:
                raise OcrError("Local tool output exceeded its size limit.")
            out.seek(0)
            return out.read(limit + 1), bool(os.fstat(err.fileno()).st_size)
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=2)


def source_path(value, config, root_dir):
    """Resolve an existing regular file under the configured read boundary."""
    if isinstance(value, str) and value.lstrip().startswith("{"):
        if len(value) > 8192:
            raise OcrError("Document reference is too large.")
        value = json.loads(value)
    if isinstance(value, dict):
        if set(value) != {"path"}:
            raise OcrError("Document references accept only path.")
        value = value["path"]
    if not isinstance(value, str) or not value.strip() or len(value) > 4096 or "\x00" in value:
        raise OcrError("A local document path is required.")
    base = Path(root_dir).resolve()
    root = Path(config["allowed_root"]).expanduser() if config["allowed_root"] else base
    root = (root if root.is_absolute() else base / root).resolve(strict=True)
    path = Path(value).expanduser()
    path = (path if path.is_absolute() else root / path).resolve(strict=True)
    if not root.is_dir() or not path.is_relative_to(root) or not path.is_file():
        raise OcrError("Source must be a regular file inside allowed_root.")
    if path.suffix.lower().lstrip(".") not in FORMATS:
        raise OcrError("Supported sources: PDF, PNG, JPEG, single-page TIFF, WebP, BMP and PPM.")
    return path


def snapshot_file(source_path, target, config, deadline, cancel):
    """Copy a bounded source to a private scratch directory and bind outputs to its digest."""
    digest = hashlib.sha256()
    size = 0
    with source_path.open("rb") as source, target.open("xb") as destination:
        before = os.fstat(source.fileno())
        if before.st_size > config["max_bytes"]:
            raise OcrError("Source exceeds max_bytes.")
        while chunk := source.read(65536):
            check(deadline, cancel)
            size += len(chunk)
            if size > config["max_bytes"]:
                raise OcrError("Source exceeds max_bytes.")
            digest.update(chunk)
            destination.write(chunk)
        after = os.fstat(source.fileno())
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise OcrError("Source changed while being read.")
    return {"path": str(source_path), "sha256": digest.hexdigest(), "bytes": size}


def normalized_image(source, destination):
    """Decode only allowlisted, single-frame images after enforcing pixel dimensions."""
    try:
        from PIL import Image
    except ImportError as exc:
        raise OcrError("Required Python dependency Pillow is missing.") from exc
    with Image.open(source, formats=["PNG", "JPEG", "TIFF", "WEBP", "BMP", "PPM"]) as image:
        width, height = image.size
        if not 1 <= width <= 4096 or not 1 <= height <= 4096 or width * height > 16777216:
            raise OcrError("Image exceeds the 4096-pixel per-side limit; resize a copy first.")
        if getattr(image, "n_frames", 1) != 1:
            raise OcrError("Multi-frame images are not supported; provide separate images or a PDF.")
        # Flatten transparency on white; do not rotate or rewrite the original.
        rgba = image.convert("RGBA")
        paper = Image.new("RGBA", image.size, "white")
        paper.alpha_composite(rgba)
        paper.convert("L").save(destination, format="PNG")
    return width, height


def pdf_pages(snapshot, config, deadline, cancel):
    """Check PDF metadata and select explicit pages without dropping oversized ranges."""
    with snapshot.open("rb") as stream:
        if not stream.read(1024).lstrip().startswith(b"%PDF-"):
            raise OcrError("Source does not have a PDF signature.")
    raw, warned = command(["pdfinfo", str(snapshot)], deadline, cancel, 65536)
    info = raw.decode("utf-8", errors="replace")
    if re.search(r"^Encrypted:\s+yes\b", info, re.M):
        raise OcrError("Encrypted PDF files are not supported.")
    count = re.search(r"^Pages:\s+(\d+)\s*$", info, re.M)
    if not count:
        raise OcrError("PDF page count is unavailable.")
    count = int(count.group(1))
    first, last = config["first_page"], config["last_page"] or count
    if not 1 <= first <= last <= count or last - first + 1 > config["max_pages"]:
        raise OcrError("Selected PDF range is invalid or exceeds max_pages.")
    return list(range(first, last + 1)), warned


def render_page(snapshot, page, config, deadline, cancel):
    """Respect requested DPI unless a PDF page needs reduction to the pixel limit."""
    info, warned = command(["pdfinfo", "-f", str(page), "-l", str(page), "-box", str(snapshot)], deadline, cancel, 65536)
    size = re.search(r"^Page\s+(?:\d+\s+)?size:\s+([\d.]+)\s+x\s+([\d.]+)\s+pts", info.decode("utf-8", errors="replace"), re.M)
    if not size:
        raise OcrError("PDF page dimensions are unavailable.")
    width, height = map(float, size.groups())
    if not all(math.isfinite(x) and 0 < x <= 1000000 for x in (width, height)):
        raise OcrError("PDF page dimensions are invalid.")
    reduced = max(width, height) * config["dpi"] / 72 > 4096
    resolution = ["-scale-to", "4096"] if reduced else ["-r", str(config["dpi"])]
    raw, other_warning = command(["pdftoppm", "-f", str(page), "-l", str(page), "-singlefile", "-gray", "-png",
                                  *resolution, str(snapshot)], deadline, cancel, 33554432)
    return raw, warned or other_warning, reduced


def parse_tsv(raw, page_number, width, height, threshold):
    """Keep engine word boxes and confidence without inventing unreadable content."""
    expected = "level page_num block_num par_num line_num word_num left top width height conf text".split()
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8")), delimiter="\t", quoting=csv.QUOTE_NONE)
    if reader.fieldnames != expected:
        raise OcrError("Unexpected Tesseract TSV schema.")
    words, issues, lines = [], [], {}
    for index, row in enumerate(reader):
        if index >= 100000 or None in row or any(v is None for v in row.values()):
            raise OcrError("Malformed or excessive OCR rows.")
        level = int(row["level"])
        if level != 5:
            continue
        word = row["text"].strip()
        if not word:
            continue
        if len(words) >= 50000 or len(word) > 4096:
            raise OcrError("OCR word limits exceeded.")
        x, y, w, h = (int(row[k]) for k in ("left", "top", "width", "height"))
        if min(x, y, w, h) < 0 or x + w > width or y + h > height:
            raise OcrError("OCR returned a box outside the image.")
        confidence = float(row["conf"])
        if not math.isfinite(confidence) or not -1 <= confidence <= 100:
            raise OcrError("OCR returned invalid confidence.")
        confidence = None if confidence < 0 else confidence
        line = tuple(int(row[k]) for k in ("block_num", "par_num", "line_num"))
        if any(n < 0 for n in line):
            raise OcrError("OCR returned invalid line coordinates.")
        item = {"text": word, "box": [x, y, w, h], "confidence": confidence,
                "block": line[0], "paragraph": line[1], "line": line[2]}
        words.append(item)
        lines.setdefault(line, []).append(word)
        if confidence is None or confidence < threshold:
            issues.append({"page": page_number, "code": "low_confidence", "word_index": len(words) - 1,
                           "box": item["box"], "confidence": confidence})
    text = "\n".join(" ".join(values) for values in lines.values())
    if not words:
        issues.append({"page": page_number, "code": "no_text_detected", "box": [0, 0, width, height]})
    return {"page": page_number, "text": text, "width": width, "height": height, "words": words}, issues


def process(value, raw_config, root_dir, cancel=lambda: False, progress=lambda page, total: None):
    """Recognize every selected page locally; publish nothing on transport/tool failure."""
    config = configuration(raw_config)
    deadline = time.monotonic() + config["timeout_sec"]
    path = source_path(value, config, root_dir)
    available, _ = command(["tesseract", "--list-langs"], deadline, cancel, 65536)
    installed = set(available.decode("utf-8", errors="replace").splitlines())
    if not set(config["languages"]) <= installed:
        raise OcrError("One or more configured Tesseract language packs are not installed.")
    pages, issues = [], []
    characters = 0
    with tempfile.TemporaryDirectory(prefix="blox-ocr-") as temporary:
        folder = Path(temporary)
        snapshot = folder / ("source" + path.suffix.lower())
        source = snapshot_file(path, snapshot, config, deadline, cancel)
        if path.suffix.lower() == ".pdf":
            numbers, warned = pdf_pages(snapshot, config, deadline, cancel)
        else:
            if config["first_page"] != 1 or config["last_page"] not in (0, 1):
                raise OcrError("Single-image inputs have exactly one page.")
            numbers, warned = [1], False
        if warned:
            issues.append({"code": "pdf_metadata_warning"})
        for count, number in enumerate(numbers, 1):
            check(deadline, cancel)
            image = folder / "page.png"
            if path.suffix.lower() == ".pdf":
                raw, warned, reduced = render_page(snapshot, number, config, deadline, cancel)
                width, height = normalized_image(io.BytesIO(raw), image)
                if reduced:
                    issues.append({"page": number, "code": "resolution_reduced"})
                if warned:
                    issues.append({"page": number, "code": "render_warning"})
            else:
                width, height = normalized_image(snapshot, image)
            raw, warned = command(["tesseract", str(image), "stdout", "--oem", "1", "-l", "+".join(config["languages"]),
                    "--psm", str(config["page_segmentation"]), "--dpi", str(config["dpi"]), "tsv"], deadline, cancel, 8388608)
            page, findings = parse_tsv(raw, number, width, height, config["confidence_threshold"])
            # Tesseract may write routine diagnostics to stderr; expose only their existence.
            page["engine_diagnostics"] = warned
            characters += len(page["text"]) + (2 if pages else 0)
            if characters > config["max_characters"]:
                raise OcrError("Recognized text exceeds max_characters.")
            pages.append(page)
            issues.extend(findings)
            if len(json.dumps({"pages": pages, "issues": issues}, ensure_ascii=False).encode("utf-8")) > 6291456:
                raise OcrError("Combined OCR metadata exceeds 6 MiB.")
            progress(count, len(numbers))
    check(deadline, cancel)
    partial = any(issue["code"] in {"low_confidence", "no_text_detected", "render_warning"} for issue in issues)
    return {"text": "\n\n".join(page["text"] for page in pages),
            "pages": {"source": source, "engine": "tesseract", "coordinate_space": "processed_image_pixels",
                      "partial": partial, "pages": pages},
            "issues": {"source": source, "partial": partial, "review_required": bool(issues), "items": issues}}
