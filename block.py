"""Local Tesseract OCR with source references and no implicit remote upload."""

import json
from bloxsmith_app.block_api import APPLICATION_JSON, TEXT_PLAIN, BlockDefinition, BlockRuntimeOutput, BlockRuntimeResult
from . import logic, ui


# FB1 - Recognize local images and selected PDF pages with the actual Tesseract engine.
# FB2 - Preserve source digest, word boxes, confidence and explicit review/partial flags.
# FB3 - Enforce settings, path, pixel, process, language and output bounds with cancellation.
# FB4 - Execute through both runtimes and declared assets across all package origins.
# FB5 - Provide translated responsive settings with draft, Apply and inspector consistency.
class OcrBlock(BlockDefinition):
    """A one-document task; tools and language packs are supplied by the operator."""

    kind = "ocr"

    def ui_assets(self, surface="modal"):
        """Load only manifest assets for the current release."""
        return list(self.model.get("ui_assets", {}).get(surface, []))

    def execute_runtime(self, context):
        """Emit text and provenance together after every selected page has completed."""
        try:
            values = logic.process(context.input_value("document", "1"), context.config, context.root_dir,
                                   context.services.get("cancel_requested", lambda: False))
            outputs = [BlockRuntimeOutput(port_id=int(port.id), port_name=port.name,
                       value=values[port.name] if port.name == "text" else json.dumps(values[port.name], ensure_ascii=False),
                       content_type=TEXT_PLAIN if port.name == "text" else APPLICATION_JSON)
                       for port in context.output_ports if port.name in values]
            count = len(values["pages"]["pages"])
            return BlockRuntimeResult(status="success", outputs=outputs, content_type=TEXT_PLAIN,
                    metadata={self.kind: {"pages": count, "partial": values["issues"]["partial"],
                                        "review_required": values["issues"]["review_required"]}},
                    logs=[f"[ocr] Recognized {count} page(s); review_required={values['issues']['review_required']}."])
        except (ValueError, TypeError, OSError, RecursionError, RuntimeError, ImportError) as exc:
            detail = str(exc) if isinstance(exc, logic.OcrError) else "The document cannot be safely recognized with the selected settings."
            message = self.translate("block.ocr.error", {"detail": detail}, fallback="OCR failed: {detail}")
            return BlockRuntimeResult(status="failed", outputs=[], error=message, exit_code=1,
                                      logs=[f"[ocr-error] {detail}"])

    def handle_ui_action(self, *, node, action, values, payload=None):
        """Validate settings and apply exact replacements for JSON language lists."""
        if action in {"save_settings", "modal_update_fields", "inspector_update_fields"}:
            patch = (values or {}).get("node_patch") or {}
            if "config" in patch:
                try:
                    logic.configuration({**self.default_config(), **(node.get("config") or {}), **patch["config"]})
                except (ValueError, TypeError) as exc:
                    return {"error": self.translate("block.ocr.error", {"detail": str(exc)}, fallback="OCR failed: {detail}")}
        return ui.replace_config(super().handle_ui_action(node=node, action="modal_update_fields" if action == "save_settings" else action, values=values, payload=payload), node)

    def render_modal(self, *, node, payload=None):
        """Render an opaque, internally scrollable settings panel."""
        return ui.modal(self, node, payload)

    def render_inspector_panel(self, *, node, payload=None):
        """Expose the same settings and standard port panel."""
        return ui.inspector(self, node, payload)

    def render_node_card(self, *, node, payload=None):
        """Show language selection, never document contents."""
        return ui.card(self, node, "Tesseract · " + "+".join((node.get("config") or {}).get("languages", ["eng"])))
