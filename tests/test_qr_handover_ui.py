"""Phase 9C QR handover frontend checks. No live transfer."""

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "frontend" / "style.css").read_text(encoding="utf-8")
QR_JS = (ROOT / "frontend" / "qr-handover.js").read_text(encoding="utf-8")
CODE_JS = (ROOT / "frontend" / "qr-code.js").read_text(encoding="utf-8")


def node_executable():
    found = shutil.which("node")
    if found:
        return found
    portable = Path(r"C:\Users\sri charan reddy\AppData\Local\Temp\erap-node\node-v22.20.0-win-x64\node.exe")
    if portable.is_file():
        return str(portable)
    raise RuntimeError("node was not found")


def run_node(script):
    result = subprocess.run(
        [node_executable(), "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout


def test_qr_scripts_are_loaded_and_do_not_store_tokens():
    assert '<script src="qr-code.js"></script>' in HTML
    assert '<script src="qr-handover.js"></script>' in HTML
    combined = QR_JS + CODE_JS
    assert "localStorage" not in combined
    assert "sessionStorage" not in combined
    assert "console.log" not in combined
    assert "analytics" not in combined.lower()
    assert "erap-hq.v1." not in APP.split("function issueExchangeQr", 1)[-1].split("function clearQrRequesterSecret", 1)[0]


def test_generate_preview_and_confirm_use_json_bodies():
    assert 'id="exchangeGenerateQrBtn">Generate QR</button>' in APP
    assert 'id="exchangeRegenerateQrBtn"' in APP
    assert "qrIssuePath(requestId)" in APP
    assert "method: \"POST\", body: {}" in APP
    assert "qrPreviewPath()" in APP
    assert "qrPreviewBody(token)" in APP
    assert "qrConfirmPath()" in APP
    assert "qrConfirmBody(token, preview" in APP
    assert "/handover/qr/preview" in QR_JS
    assert "/handover/qr/confirm" in QR_JS
    assert "/handover/qr\"" in QR_JS or "handover/qr" in QR_JS
    preview = run_node(
        "const q=require('./frontend/qr-handover.js');"
        "const body=q.qrPreviewBody('erap-hq.v1.'+'a'.repeat(43));"
        "if (q.qrPreviewPath().includes('?') || q.qrPreviewPath().includes(body.token)) process.exit(1);"
        "if (q.qrConfirmPath().includes(body.token) || q.qrIssuePath('EXREQ-1').includes(body.token)) process.exit(1);"
        "console.log(JSON.stringify(body));"
    )
    assert json.loads(preview) == {"token": "erap-hq.v1." + ("a" * 43)}


def test_preview_does_not_confirm_and_success_refreshes():
    preview = APP.split("async function previewQrToken", 1)[1].split("function renderQrPreview", 1)[0]
    assert "qrConfirmPath" not in preview
    assert "confirmScannedQr" not in preview
    confirm = APP.split("async function confirmScannedQr", 1)[1].split("async function refreshExchangeAfterQr", 1)[0]
    assert "refreshExchangeAfterQr" in confirm
    refresh = APP.split("async function refreshExchangeAfterQr", 1)[1].split("function initializeExchange", 1)[0]
    assert "loadExchangeWorkspace" in refresh
    assert "loadResources" in refresh
    assert "loadAllocations" in refresh
    assert "refreshNotificationBadge" in refresh
    assert "Handover completed" in APP
    assert "Handover already completed" in APP


def test_prefix_countdown_roles_and_errors():
    script = r"""
const q = require("./frontend/qr-handover.js");
const token = "erap-hq.v1." + "a".repeat(43);
if (!q.isHandoverQrPayload(token)) process.exit(2);
if (q.isHandoverQrPayload("https://example.test/" + token)) process.exit(3);
if (q.isHandoverQrPayload("erap-hq.v1.short")) process.exit(4);
const soon = new Date(Date.now() + 65000).toISOString();
const label = q.qrCountdownLabel(soon, Date.now());
if (!label.startsWith("Expires in ")) process.exit(5);
if (!q.qrCountdownLabel(new Date(Date.now() - 1000).toISOString(), Date.now()).includes("expired")) process.exit(6);
const member = q.qrActionsFor("MEMBER", "TRANSFER_PENDING", "provider");
if (member.generate || member.scan) process.exit(7);
const provider = q.qrActionsFor("OPERATOR", "TRANSFER_PENDING", "provider");
const requester = q.qrActionsFor("OWNER", "TRANSFER_PENDING", "requester");
if (!provider.generate || provider.scan || !requester.scan || requester.generate) process.exit(8);
const expired = q.qrUxMessage(409, "Handover QR has expired");
if (!expired.text.includes("QR code has expired")) process.exit(9);
const revoked = q.qrUxMessage(404, "Handover QR is not valid");
if (!revoked.text.includes("no longer valid")) process.exit(10);
const limit = q.qrUxMessage(429, "Handover QR generation limit reached");
if (!limit.text.includes("QR generation limit reached")) process.exit(11);
const done = q.qrUxMessage(200, "Handover already completed");
if (done.kind !== "completed" || done.text !== "Handover completed") process.exit(12);
const body = q.qrConfirmBody(token, { tracking_mode: "QUANTITY", quantity: 2 }, "EXQTY-1");
if (body.quantity !== 2 || body.destination_resource_id !== "EXQTY-1" || body.token !== token) process.exit(13);
const created = q.qrConfirmBody(token, { tracking_mode: "QUANTITY", quantity: 2 }, "");
if ("destination_resource_id" in created) process.exit(14);
if (!q.quantityDestinationCopy("", "").includes("PRIVATE")) process.exit(15);
if (!q.quantityDestinationCopy("EXQTY-1", "Pool A").includes("Pool A")) process.exit(16);
console.log("ok");
"""
    assert run_node(script).strip() == "ok"


def test_camera_detector_cleanup_and_manual_fallback():
    script = r"""
const q = require("./frontend/qr-handover.js");
const manual = q.createQrCameraSession({});
if (manual.supported()) process.exit(2);
let stopped = 0;
let frames = 0;
const tracks = [{ stop() { stopped += 1; } }];
const env = {
  BarcodeDetector: class {
    detect() { return Promise.resolve([{ rawValue: "erap-hq.v1." + "b".repeat(43) }]); }
  },
  navigator: { mediaDevices: { getUserMedia() { return Promise.resolve({ getTracks() { return tracks; } }); } } },
  requestAnimationFrame(fn) { frames += 1; return setTimeout(fn, 0); },
  cancelAnimationFrame(id) { clearTimeout(id); }
};
const session = q.createQrCameraSession(env);
if (!session.supported()) process.exit(3);
const video = { readyState: 2, hidden: true, srcObject: null, play() { return Promise.resolve(); } };
let scanned = "";
session.start(video, value => { scanned = value; }).then(() => {
  setTimeout(() => {
    if (!scanned.startsWith("erap-hq.v1.")) process.exit(4);
    if (session.isActive()) process.exit(5);
    if (stopped < 1) process.exit(6);
    if (video.srcObject !== null) process.exit(7);
    session.stop();
    if (frames < 1) process.exit(8);
    console.log("ok");
  }, 30);
});
"""
    assert run_node(script).strip() == "ok"


def test_qr_symbol_matches_known_encoder_for_handover_payload():
    payload = "erap-hq.v1." + ("a" * 43)
    digest = run_node(
        "const crypto=require('crypto'); const q=require('./frontend/qr-code.js');"
        f"const m=q.modules({json.dumps(payload)}, {{mask:0}});"
        "if (m.length !== 33 || m[m.length - 8][8] !== 1 || m[0][0] !== 1) process.exit(2);"
        "console.log(crypto.createHash('sha256').update(m.map(row => row.join('')).join('')).digest('hex'));"
    ).strip()
    # Mask 0, byte mode, error correction M, checked against the qrcode library.
    assert digest == "dc0eeeedc309e5778d44dd9da6608dd435c2602312c60e9d7c7cb5c85cf9c05b"
    assert "One-time handover QR code" in APP
    assert 'aria-label="One-time handover QR code"' in APP


def test_responsive_qr_rules_and_member_actions_hidden():
    assert ".qr-handover-frame" in CSS
    assert "width: min(240px, 100%)" in CSS
    assert "max-width: 100%" in CSS
    assert "@media (max-width: 390px)" in CSS
    assert "minmax(0, 1fr)" in CSS
    provider = APP.split("QR Handover", 1)[1]
    assert "canWriteExchange() && isProvider && status === \"TRANSFER_PENDING\"" in APP
    assert "canWriteExchange() && isRequester && status === \"TRANSFER_PENDING\"" in APP
    assert "Start camera" in HTML
    assert "Enter QR payload manually" in HTML
    assert 'autocomplete="off"' in HTML
    assert "Nothing is transferred until you confirm." in HTML
    assert "id=\"qrScanTitle\"" in HTML
    assert provider
