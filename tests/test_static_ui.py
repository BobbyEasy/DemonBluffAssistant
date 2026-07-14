from __future__ import annotations

from pathlib import Path


STATIC = Path("src/demon_bluff_assistant/static")


def test_companion_page_contains_required_workflow_surfaces() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    for element_id in [
        "new-session-form",
        "capture-panel",
        "board",
        "pending-editor",
        "manual-entry",
        "analysis-panel",
        "undo-button",
        "export-button",
        "export-recognition-button",
        "import-input",
        "model-settings-form",
        "model-provider",
        "model-api-key",
        "vision-engine",
        "glm-vision-form",
        "glm-vision-api-key",
        "detect-village-button",
        "confirm-village-button",
        "village-detection-evidence",
        "capture-lightbox",
        "zoom-in-button",
        "zoom-out-button",
        "workflow-guide",
        "pending-summary",
        "export-analysis-button",
        "export-dataset-button",
        "strategy-chat",
        "chat-form",
    ]:
        assert f'id="{element_id}"' in html
    assert "/static/styles.css" in html
    assert "/static/app.js" in html
    assert "v0.4.0" in html
    assert "本机离线 OCR" in html


def test_frontend_submits_api_key_only_to_local_settings_endpoint() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    assert "OPENAI_API_KEY" not in javascript
    assert "/api/model-settings" in javascript
    assert "model-api-key" in javascript
    assert 'localStorage.setItem("model-api-key"' not in javascript
    assert "/events" in javascript
    assert "/api/captures/latest" in javascript
    assert "/village" in javascript
    assert "setInterval" in javascript
    assert "openCapturePreview" in javascript
    assert "setPreviewZoom" in javascript
    assert "exportRecognition" in javascript
    assert 'ui["pending-json"].value' in javascript
    assert "saveGlmVisionSettings" in javascript
    assert "selectedVisionEngine" in javascript
    assert "exportAnalysis" in javascript
    assert "exportDataset" in javascript
    assert "/analysis/export" in javascript
    assert "sendChatMessage" in javascript
