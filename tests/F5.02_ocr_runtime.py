"""FB1/FB2/FB4/FB5: actual OCR in both runtimes and all supported package origins."""

from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from blocs.ocr.block import OcrBlock
from block_test_packages import install_test_package, surface_payload
from ui_smoke_common import create_run_api, data_edge, display_node, graph_payload, isolated_server, text_node, wait_for_run_terminal
from fixtures import image


def main():
    """Never contact a provider or a real project; recognize a generated image."""
    block = OcrBlock()
    for mode in ("centralized", "zeromq_active"):
        for origin in (None, "managed", "linked"):
            with isolated_server() as server:
                fixture = server.root_dir / "scan.png"
                image(fixture)
                node = block.build_node_payload(node_id="reader", position={"x": 360, "y": 140})
                node["config"] = {**block.default_config(), "allowed_root": str(server.root_dir)}
                if origin:
                    model = install_test_package(server, "ocr", origin=origin)
                    node["block_version"] = model["version"]
                    for surface in ("modal", "inspector_panel", "node_card"):
                        html = surface_payload(server, model, node, surface=surface)["html"]
                        assert "{{" not in html
                        if surface != "node_card":
                            assert 'data-block-config-field="languages"' in html and 'data-owned-apply' in html
                document = graph_payload("Local OCR", [text_node("source", "Path", str(fixture), 80, 140),
                    node, display_node("display", "Text", 700, 140)], [
                    data_edge("source-reader", "source", 1, "reader", 1), data_edge("reader-display", "reader", 1, "display", 1)])
                run = create_run_api(server, document, runtime_mode=mode)
                result = wait_for_run_terminal(server, run["run_id"], timeout_sec=30)
                assert result["status"] == "success", (mode, origin, result.get("logs"))
                assert "HELLO DOCUMENT 123" in result["output_values"]["reader:1"]["value"]
                assert json.loads(result["output_values"]["reader:2"]["value"])["engine"] == "tesseract"
    print("[ok] OCR runtime and surface contracts")


if __name__ == "__main__":
    main()
