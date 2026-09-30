/* QR handover helpers. Token stays in memory for the current preview/confirm only. */

(function (root, factory) {
    const api = factory();
    if (typeof module !== "undefined" && module.exports) {
        module.exports = api;
    }
    root.ErapQrHandover = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
    const PREFIX = "erap-hq.v1.";

    function isHandoverQrPayload(value) {
        const text = String(value || "").trim();
        if (!text.startsWith(PREFIX)) {
            return false;
        }
        return /^[A-Za-z0-9_-]{43,80}$/.test(text.slice(PREFIX.length));
    }

    function qrIssuePath(requestId) {
        return "/requests/" + encodeURIComponent(requestId) + "/handover/qr";
    }

    function qrPreviewPath() {
        return "/handover/qr/preview";
    }

    function qrConfirmPath() {
        return "/handover/qr/confirm";
    }

    function qrPreviewBody(token) {
        return { token: String(token || "") };
    }

    function qrConfirmBody(token, preview, destinationResourceId) {
        const body = { token: String(token || "") };
        const mode = String(preview && preview.tracking_mode || "").toUpperCase();
        if (mode === "QUANTITY") {
            body.quantity = preview.quantity;
            const destination = String(destinationResourceId || "").trim();
            if (destination) {
                body.destination_resource_id = destination;
            }
        }
        return body;
    }

    function qrUxMessage(status, message) {
        const text = String(message || "");
        const lower = text.toLowerCase();
        if (lower.includes("already completed")) {
            return { kind: "completed", text: "Handover completed" };
        }
        if (status === 429 || lower.includes("generation limit")) {
            return {
                kind: "limit",
                text: "QR generation limit reached for this transfer. Please use the existing QR or manual handover."
            };
        }
        if (lower.includes("expired")) {
            return { kind: "expired", text: "QR code has expired. Ask the provider to generate a new one." };
        }
        if (status === 404 || lower.includes("not valid") || lower.includes("revoked")) {
            return {
                kind: "invalid",
                text: "QR code is no longer valid. Ask the provider to generate a new QR code."
            };
        }
        if (status === 403 && (lower.includes("subscription") || lower.includes("billing"))) {
            return { kind: "billing", text: "Subscription required for this operation." };
        }
        if (status === 401) {
            return { kind: "auth", text: "Sign in again to continue this handover." };
        }
        if (status === 400) {
            return { kind: "rejected", text: "This QR code is not an ERAP handover code." };
        }
        if (!status || status >= 500) {
            return {
                kind: "retry",
                text: "Handover could not be completed. Check the connection, then try again. Confirmation is not repeated automatically."
            };
        }
        if (status === 409) {
            return { kind: "state", text: "This exchange is not ready for QR handover." };
        }
        return { kind: "error", text: "This handover action could not be completed." };
    }

    function qrCountdownLabel(expiresAt, nowMs) {
        const end = Date.parse(expiresAt || "");
        if (!Number.isFinite(end)) {
            return "Expiration time unavailable";
        }
        const left = end - nowMs;
        if (left <= 0) {
            return "This QR code has expired. Generate a new QR code.";
        }
        const total = Math.floor(left / 1000);
        const minutes = Math.floor(total / 60);
        const seconds = String(total % 60).padStart(2, "0");
        return "Expires in " + minutes + ":" + seconds;
    }

    function qrActionsFor(role, status, party) {
        const operate = role === "OWNER" || role === "ADMIN" || role === "OPERATOR";
        const pending = String(status || "").toUpperCase() === "TRANSFER_PENDING";
        if (!operate || !pending) {
            return { generate: false, scan: false };
        }
        return {
            generate: party === "provider",
            scan: party === "requester"
        };
    }

    function quantityDestinationCopy(destinationResourceId, destinationName) {
        const id = String(destinationResourceId || "").trim();
        if (!id) {
            return "A new PRIVATE quantity pool will be created at the exchange destination.";
        }
        const name = String(destinationName || id).trim();
        return "Quantity will merge into " + name + ".";
    }

    function createQrCameraSession(env) {
        const root = env || rootWindow();
        let stream = null;
        let timer = 0;
        let video = null;

        function supported() {
            return typeof root.BarcodeDetector === "function"
                && root.navigator
                && root.navigator.mediaDevices
                && typeof root.navigator.mediaDevices.getUserMedia === "function";
        }

        function stop() {
            if (timer && typeof root.cancelAnimationFrame === "function") {
                root.cancelAnimationFrame(timer);
            }
            timer = 0;
            if (stream && typeof stream.getTracks === "function") {
                stream.getTracks().forEach(track => {
                    if (track && typeof track.stop === "function") {
                        track.stop();
                    }
                });
            }
            stream = null;
            if (video) {
                video.srcObject = null;
                video.hidden = true;
            }
            video = null;
        }

        async function start(videoEl, onCode) {
            if (!supported()) {
                return { supported: false };
            }
            stop();
            video = videoEl;
            stream = await root.navigator.mediaDevices.getUserMedia({
                audio: false,
                video: { facingMode: "environment" }
            });
            if (video) {
                video.srcObject = stream;
                video.hidden = false;
                if (typeof video.play === "function") {
                    try {
                        await video.play();
                    } catch (_error) {
                        /* Autoplay can reject before metadata; detection waits for frames. */
                    }
                }
            }
            const detector = new root.BarcodeDetector({ formats: ["qr_code"] });

            async function tick() {
                if (!stream) {
                    return;
                }
                try {
                    const ready = video && video.readyState >= 2;
                    const codes = ready ? await detector.detect(video) : [];
                    const value = codes && codes[0] ? String(codes[0].rawValue || "") : "";
                    if (value) {
                        stop();
                        if (typeof onCode === "function") {
                            onCode(value);
                        }
                        return;
                    }
                } catch (_error) {
                    /* Keep scanning until the user closes the sheet. */
                }
                if (stream && typeof root.requestAnimationFrame === "function") {
                    timer = root.requestAnimationFrame(tick);
                }
            }

            if (typeof root.requestAnimationFrame === "function") {
                timer = root.requestAnimationFrame(tick);
            }
            return { supported: true };
        }

        return {
            supported: supported,
            start: start,
            stop: stop,
            isActive: function () {
                return Boolean(stream);
            }
        };
    }

    function rootWindow() {
        return typeof window !== "undefined" ? window : globalThis;
    }

    return {
        PREFIX: PREFIX,
        isHandoverQrPayload: isHandoverQrPayload,
        qrIssuePath: qrIssuePath,
        qrPreviewPath: qrPreviewPath,
        qrConfirmPath: qrConfirmPath,
        qrPreviewBody: qrPreviewBody,
        qrConfirmBody: qrConfirmBody,
        qrUxMessage: qrUxMessage,
        qrCountdownLabel: qrCountdownLabel,
        qrActionsFor: qrActionsFor,
        quantityDestinationCopy: quantityDestinationCopy,
        createQrCameraSession: createQrCameraSession
    };
});
