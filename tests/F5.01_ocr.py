"""FB1/FB2/FB3: real Tesseract, PDF pages, confidence references, limits and cancellation."""

from pathlib import Path
import sys
import time
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from blocs.ocr import logic
from fixtures import pdf, image


def rejected(call):
    """Invalid inputs must be rejected instead of generating a fabricated recognition."""
    try:
        call()
    except (ValueError, OSError):
        return
    raise AssertionError("Invalid OCR input accepted")


def main():
    """Run real local tools only against generated temporary fixtures."""
    with TemporaryDirectory(prefix="ocr-test-") as temporary:
        root = Path(temporary).resolve()
        boundary = root / "input"
        boundary.mkdir()
        cfg = {"allowed_root": str(boundary)}
        image(boundary / "hello.png")
        original = (boundary / "hello.png").read_bytes()
        updates = []
        result = logic.process({"path": "hello.png"}, cfg, root, progress=lambda *value: updates.append(value))
        assert "HELLO DOCUMENT 123" in result["text"], result
        assert result["pages"]["pages"][0]["width"] == 1000
        assert result["pages"]["pages"][0]["words"][0]["confidence"] is not None
        assert len(result["pages"]["source"]["sha256"]) == 64 and updates == [(1, 1)]
        assert original == (boundary / "hello.png").read_bytes()
        pdf(boundary / "sample.pdf")
        result = logic.process('{"path":"sample.pdf"}', cfg, root)
        assert "Hello document" in result["text"], result
        assert [p["page"] for p in result["pages"]["pages"]] == [1, 2]
        assert result["issues"]["partial"] is True
        assert {"page": 2, "code": "no_text_detected", "box": [0, 0, 556, 556]} in result["issues"]["items"], result
        selected = logic.process("sample.pdf", {**cfg, "first_page": 2, "last_page": 2}, root)
        assert selected["text"] == "" and selected["pages"]["pages"][0]["page"] == 2
        rejected(lambda: logic.process("sample.pdf", {**cfg, "max_pages": 1}, root))
        rejected(lambda: logic.process("hello.png", {**cfg, "first_page": 2}, root))
        rejected(lambda: logic.process("hello.png", {**cfg, "max_characters": 2}, root))
        rejected(lambda: logic.process("hello.png", {**cfg, "languages": ["not_installed"]}, root))
        rejected(lambda: logic.process("hello.png", cfg, root, cancel=lambda: True))
        rejected(lambda: logic.command([sys.executable, "-c", "pass"], time.monotonic() - 1, lambda: False, 100))
        rejected(lambda: logic.command([sys.executable, "-c", "import time; time.sleep(10)"], time.monotonic() + 0.1, lambda: False, 100))
        rejected(lambda: logic.command([sys.executable, "-c", "print('x' * 10000)"], time.monotonic() + 5, lambda: False, 100))
        with patch.object(logic.shutil, "which", return_value=None):
            rejected(lambda: logic.process("hello.png", cfg, root))
        (root / "outside.png").write_bytes(original)
        (boundary / "escape.png").symlink_to(root / "outside.png")
        rejected(lambda: logic.process("escape.png", cfg, root))
        rejected(lambda: logic.process("../outside.png", cfg, root))
        (boundary / "broken.pdf").write_bytes(b"not a PDF")
        rejected(lambda: logic.process("broken.pdf", cfg, root))
        from PIL import Image
        Image.new("L", (4097, 1)).save(boundary / "large.png")
        rejected(lambda: logic.process("large.png", cfg, root))
        first = Image.new("L", (20, 20))
        first.save(boundary / "multi.tiff", save_all=True, append_images=[first.copy()])
        rejected(lambda: logic.process("multi.tiff", cfg, root))
        header = b"level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        row = b"5\t1\t1\t1\t1\t1\t2\t3\t10\t12\t12.5\tuncertain\n"
        page, issues = logic.parse_tsv(header + row, 8, 200, 200, 70)
        assert page["text"] == "uncertain" and issues[0]["page"] == 8 and issues[0]["word_index"] == 0
        rejected(lambda: logic.parse_tsv(header + row.replace(b"12.5", b"nan"), 1, 200, 200, 70))
        rejected(lambda: logic.parse_tsv(header + row, 1, 5, 5, 70))
        for invalid in ({"languages": ["eng+fra"]}, {"languages": ["eng", "eng"]}, {"dpi": True},
                        {"max_pages": 101}, {"last_page": 1, "first_page": 2}, {"secret": "no"}):
            rejected(lambda: logic.configuration(invalid))
        assert logic.configuration({"execution": {}, "runtime_path": "ocr", "runtime_path_label": "OCR"})["dpi"] == 200
    print("[ok] OCR: actual Tesseract/Poppler and bounded local recognition")


if __name__ == "__main__":
    main()
