/* ERAP delivery tracking. Authenticated API records. Not GPS, signatures, or AWS proof. */

(function (global) {

    const STATUSES = [
        "ALLOCATED",
        "READY_FOR_DISPATCH",
        "DISPATCHED",
        "IN_TRANSIT",
        "DELIVERED",
        "PARTIALLY_DELIVERED",
        "RECEIVED",
        "PARTIALLY_RECEIVED",
        "HANDOVER_PENDING",
        "HANDOVER_COMPLETED",
        "DELIVERY_FAILED",
        "CANCELLED"
    ];

    const LABELS = {
        ALLOCATED: "Allocated",
        READY_FOR_DISPATCH: "Ready for dispatch",
        DISPATCHED: "Dispatched",
        IN_TRANSIT: "In transit",
        DELIVERED: "Delivered",
        PARTIALLY_DELIVERED: "Partially delivered",
        RECEIVED: "Received",
        PARTIALLY_RECEIVED: "Partially received",
        HANDOVER_PENDING: "Handover pending",
        HANDOVER_COMPLETED: "Completed",
        DELIVERY_FAILED: "Failed",
        CANCELLED: "Cancelled"
    };

    let deliveries = [];
    let counts = emptyCounts();
    let selectedId = "";
    let selectedDelivery = null;
    let loadError = "";
    let trackingInitialized = false;
    let apiAvailable = false;
    let deliveryBusy = false;

    function $(id) {
        return document.getElementById(id);
    }

    function emptyCounts() {
        return {
            total: 0,
            ALLOCATED: 0,
            READY_FOR_DISPATCH: 0,
            DISPATCHED: 0,
            IN_TRANSIT: 0,
            DELIVERED: 0,
            PARTIALLY_DELIVERED: 0,
            receipt_pending: 0,
            handover_pending: 0,
            completed: 0,
            failed: 0
        };
    }

    function apiBase() {
        if (typeof DELIVERIES_API_URL === "string" && DELIVERIES_API_URL) {
            return DELIVERIES_API_URL;
        }
        return "https://4c6dni17l3.execute-api.eu-north-1.amazonaws.com/dev/allocate/deliveries";
    }

    function tenantParams(extra) {
        const params = new URLSearchParams();
        const org = typeof selectedOrganizationId === "function" ? selectedOrganizationId() : "";
        const loc = typeof selectedLocationId === "function" ? selectedLocationId() : "";
        if (org) {
            params.set("organization_id", org);
        }
        if (loc) {
            params.set("location_id", loc);
        }
        Object.keys(extra || {}).forEach(key => {
            const value = extra[key];
            if (value !== undefined && value !== null && String(value).trim() !== "") {
                params.set(key, String(value).trim());
            }
        });
        return params;
    }

    function canWriteDeliveries() {
        if (typeof canOperateResources === "function") {
            return canOperateResources();
        }
        const role = (typeof currentUser !== "undefined" && currentUser.organization?.role) || "";
        return role === "OWNER" || role === "ADMIN" || role === "OPERATOR";
    }

    function gatewayUndeployed(status, payload, rawText) {
        const message = ((payload && payload.message) || rawText || "").toLowerCase();
        return (status === 403 || status === 404)
            && message.replace(/[\s_]/g, "").includes("missingauthenticationtoken");
    }

    function undeployedMessage() {
        return "The delivery collection is not available on this API. Browser session records are not used.";
    }

    function deliverySuffix(deliveryId, rest) {
        let path = "/" + encodeURIComponent(deliveryId || "");
        String(rest || "").split("/").filter(Boolean).forEach(part => {
            path += "/" + encodeURIComponent(part);
        });
        return path;
    }

    async function deliveryRequest(method, suffix, body, extraQuery) {
        if (typeof waitForIdToken !== "function") {
            throw new Error("Authentication is not available");
        }
        const idToken = await waitForIdToken();
        if (!idToken) {
            throw new Error("Cognito ID token is not available");
        }
        const org = typeof selectedOrganizationId === "function" ? selectedOrganizationId() : "";
        if (!org) {
            throw new Error("Select an organization");
        }
        const params = tenantParams(extraQuery);
        let url = apiBase() + (suffix || "");
        const query = params.toString();
        if (query) {
            url += "?" + query;
        }
        const headers = {
            Accept: "application/json",
            Authorization: "Bearer " + idToken
        };
        const options = { method: method, headers: headers };
        if (body) {
            headers["Content-Type"] = "application/json";
            options.body = JSON.stringify(Object.assign({ organization_id: org }, body));
        }
        const response = await fetch(url, options);
        const rawText = await response.text();
        let payload = null;
        try {
            payload = rawText ? JSON.parse(rawText) : {};
        } catch (error) {
            payload = null;
        }
        if (gatewayUndeployed(response.status, payload, rawText)) {
            const error = new Error(undeployedMessage());
            error.code = "DELIVERY_API_UNDEPLOYED";
            throw error;
        }
        if (!response.ok) {
            const message = typeof getApiMessage === "function"
                ? getApiMessage(payload)
                : (payload && payload.message) || "";
            throw new Error(message || ("Delivery API returned " + response.status));
        }
        return payload || {};
    }

    function isDeliveryRecord(row) {
        return Boolean(
            row
            && typeof row === "object"
            && !Array.isArray(row)
            && typeof row.delivery_id === "string"
            && row.delivery_id.indexOf("DELIV-") === 0
            && typeof row.allocation_id === "string"
            && row.allocation_id
            && STATUSES.indexOf(row.status) !== -1
        );
    }

    function sanitizeRows(rows) {
        const org = typeof selectedOrganizationId === "function" ? selectedOrganizationId() : "";
        if (!Array.isArray(rows)) {
            return [];
        }
        return rows.filter(row => {
            if (!isDeliveryRecord(row)) {
                return false;
            }
            if (org && row.organization_id && row.organization_id !== org) {
                return false;
            }
            return true;
        });
    }

    function toast(message) {
        if (typeof showToast === "function") {
            showToast(message);
        }
    }

    function setSourceBanner() {
        const note = $("deliverySourceNote");
        if (!note) {
            return;
        }
        note.textContent = apiAvailable
            ? "Counts and rows are delivery records returned by the authenticated ERAP API for the selected organization. Application records are not GPS or carrier proof."
            : loadError || undeployedMessage();
    }

    function filteredRows() {
        const deliveryQuery = ($("deliverySearchId")?.value || "").trim().toUpperCase();
        const allocationQuery = ($("deliverySearchAllocation")?.value || "").trim().toUpperCase();
        const resourceQuery = ($("deliverySearchResource")?.value || "").trim().toLowerCase();
        const status = ($("deliveryStatusFilter")?.value || "ALL").toUpperCase();
        const locationId = typeof selectedLocationId === "function" ? selectedLocationId() : "";
        return deliveries.filter(row => {
            if (deliveryQuery && String(row.delivery_id || "").toUpperCase() !== deliveryQuery) {
                return false;
            }
            if (allocationQuery && String(row.allocation_id || "").toUpperCase() !== allocationQuery) {
                return false;
            }
            if (status !== "ALL" && row.status !== status) {
                return false;
            }
            if (locationId && row.location_id && row.location_id !== locationId) {
                return false;
            }
            if (resourceQuery) {
                const hay = [row.resource_id, row.resource_type, row.delivery_id, row.allocation_id]
                    .join(" ")
                    .toLowerCase();
                if (!hay.includes(resourceQuery)) {
                    return false;
                }
            }
            return true;
        });
    }

    function badgeClass(status) {
        if (status === "HANDOVER_COMPLETED") {
            return "status-released";
        }
        if (status === "DELIVERY_FAILED" || status === "CANCELLED" || status === "PARTIALLY_DELIVERED" || status === "PARTIALLY_RECEIVED") {
            return "status-returned";
        }
        return "allocated";
    }

    function renderCounts() {
        const map = {
            deliveryCountTotal: counts.total || 0,
            deliveryCountAllocated: counts.ALLOCATED || 0,
            deliveryCountReady: counts.READY_FOR_DISPATCH || 0,
            deliveryCountDispatched: counts.DISPATCHED || 0,
            deliveryCountTransit: counts.IN_TRANSIT || 0,
            deliveryCountDelivered: counts.DELIVERED || 0,
            deliveryCountPartial: counts.PARTIALLY_DELIVERED || 0,
            deliveryCountReceipt: counts.receipt_pending || 0,
            deliveryCountHandover: counts.handover_pending || 0,
            deliveryCountCompleted: counts.completed || 0,
            deliveryCountFailed: counts.failed || 0
        };
        Object.keys(map).forEach(id => {
            if ($(id)) {
                $(id).textContent = String(map[id]);
            }
        });
    }

    function renderTable() {
        const table = $("deliveriesTable");
        if (!table) {
            return;
        }
        renderCounts();
        if (loadError) {
            table.innerHTML = `<tr><td colspan="8">${escapeHtml(loadError)}</td></tr>`;
            return;
        }
        if (!deliveries.length) {
            table.innerHTML = `<tr><td colspan="8">No delivery records for this organization.</td></tr>`;
            return;
        }
        const rows = filteredRows();
        if (!rows.length) {
            table.innerHTML = `<tr><td colspan="8">No loaded deliveries match these filters.</td></tr>`;
            return;
        }
        table.innerHTML = rows.map(item => `
            <tr class="${item.discrepancy ? "flow-released" : "flow-live"}">
                <td>${escapeHtml(item.delivery_id)}</td>
                <td>${escapeHtml(item.allocation_id)}</td>
                <td>${escapeHtml(item.resource_id || "-")}</td>
                <td><span class="status-badge ${badgeClass(item.status)}">${escapeHtml(LABELS[item.status] || item.status)}</span></td>
                <td>${escapeHtml(String(item.quantity_allocated ?? "-"))}</td>
                <td>${escapeHtml(String(item.quantity_dispatched ?? "0"))}</td>
                <td>${escapeHtml(String(item.quantity_received ?? "0"))}${item.discrepancy ? " · discrepancy" : ""}</td>
                <td>
                    <button type="button" class="text-action" data-open-delivery="${escapeHtml(item.delivery_id)}">Open</button>
                </td>
            </tr>
        `).join("");
    }

    function field(label, value) {
        return `<div class="setting-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value === undefined || value === "" || value === null ? "—" : String(value))}</strong></div>`;
    }

    function renderDetail() {
        const root = $("deliveryDetail");
        if (!root) {
            return;
        }
        const item = selectedDelivery;
        if (!item) {
            root.innerHTML = `<div class="empty-state"><h3>Select a delivery</h3><p>Open a row to review transport, receipt, handover, and history.</p></div>`;
            return;
        }
        const events = (item.events || []).map(event => `
            <li>
                <strong>${escapeHtml(LABELS[event.to_status] || event.action)}</strong>
                <span>${escapeHtml(event.at || "")}</span>
                <span>${escapeHtml(event.actor_sub || "")}</span>
                <p>${escapeHtml(event.note || event.action)}</p>
            </li>
        `).join("");
        root.innerHTML = `
            <p class="panel-label">Delivery ${escapeHtml(item.delivery_id)}</p>
            <h3>${escapeHtml(item.resource_type || item.resource_id || "Resource")}</h3>
            ${field("Allocation", item.allocation_id)}
            ${field("Resource", item.resource_id)}
            ${field("Status", LABELS[item.status] || item.status)}
            ${field("Allocated quantity", item.quantity_allocated)}
            ${field("Dispatched", item.quantity_dispatched)}
            ${field("Received / damaged / missing", `${item.quantity_received} / ${item.quantity_damaged} / ${item.quantity_missing}`)}
            ${field("Handed over", item.quantity_handed_over)}
            ${field("Discrepancy", item.discrepancy ? "Yes" : "No")}
            ${field("Vehicle", item.vehicle_number)}
            ${field("Transport provider", item.transport_provider)}
            ${field("Driver", item.driver_name)}
            ${field("Source", item.source_location)}
            ${field("Destination", item.destination_location)}
            ${field("Dispatch at", item.dispatch_at)}
            ${field("Actual arrival", item.actual_arrival)}
            ${field("Receiver", item.receiver_name)}
            ${field("Handover", item.handover_id)}
            ${field("Handover verified", item.handover_verified ? "Yes" : "No")}
            <div class="hero-actions" id="deliveryActions">${actionButtons(item)}</div>
            <ol class="ops-timeline">${events}</ol>
            <p class="queue-note">Delivery reported, receipt confirmed, handover recorded, and handover verified are separate authenticated API steps. They are not GPS or photographic proof.</p>
        `;
        bindDetailForms(item);
    }

    function actionButtons(item) {
        if (!apiAvailable || !canWriteDeliveries()) {
            return "";
        }
        if (item.status === "ALLOCATED") {
            return formReady(item);
        }
        if (item.status === "READY_FOR_DISPATCH") {
            return `<button type="button" class="primary-btn" data-delivery-act="dispatch">Dispatch</button>
                    <button type="button" class="secondary-btn" data-delivery-act="cancel">Cancel</button>`;
        }
        if (item.status === "DISPATCHED") {
            return `<button type="button" class="primary-btn" data-delivery-act="transit">In transit</button>
                    <button type="button" class="secondary-btn" data-delivery-act="delivered">Delivered</button>
                    <button type="button" class="secondary-btn" data-delivery-act="partial">Partially delivered</button>
                    <button type="button" class="secondary-btn" data-delivery-act="fail">Failed</button>`;
        }
        if (item.status === "IN_TRANSIT") {
            return `<button type="button" class="primary-btn" data-delivery-act="delivered">Delivered</button>
                    <button type="button" class="secondary-btn" data-delivery-act="partial">Partially delivered</button>
                    <button type="button" class="secondary-btn" data-delivery-act="fail">Failed</button>`;
        }
        if (item.status === "DELIVERED" || item.status === "PARTIALLY_DELIVERED") {
            return formReceipt();
        }
        if (item.status === "RECEIVED" || item.status === "PARTIALLY_RECEIVED") {
            return formHandover(item);
        }
        if (item.status === "HANDOVER_PENDING") {
            return `<button type="button" class="primary-btn" data-delivery-act="verify">Verify physical handover</button>`;
        }
        return "";
    }

    function formReady(item) {
        return `
            <form id="deliveryReadyForm" class="delivery-form">
                <label>Dispatch quantity <input id="deliveryDispatchQty" type="number" min="1" required></label>
                <label>Vehicle number <input id="deliveryVehicle" type="text"></label>
                <label>Transport provider <input id="deliveryProvider" type="text"></label>
                <label>Driver name <input id="deliveryDriver" type="text"></label>
                <label>Driver contact <input id="deliveryDriverContact" type="text"></label>
                <label>Source location <input id="deliverySource" type="text" required value="${escapeHtml(item.source_location || "")}"></label>
                <label>Destination location <input id="deliveryDestination" type="text" required></label>
                <label>Estimated arrival <input id="deliveryEta" type="text"></label>
                <label>Remarks <input id="deliveryRemarks" type="text"></label>
                <button class="primary-btn" type="submit">Ready for dispatch</button>
            </form>
        `;
    }

    function formReceipt() {
        return `
            <form id="deliveryReceiptForm" class="delivery-form">
                <label>Receiver name <input id="deliveryReceiverName" type="text" required></label>
                <label>Receiver organization <input id="deliveryReceiverOrg" type="text"></label>
                <label>Receiver contact <input id="deliveryReceiverContact" type="text"></label>
                <label>Quantity received <input id="deliveryQtyReceived" type="number" min="0" required></label>
                <label>Quantity damaged <input id="deliveryQtyDamaged" type="number" min="0" value="0"></label>
                <label>Quantity missing <input id="deliveryQtyMissing" type="number" min="0" value="0"></label>
                <label>Notes <input id="deliveryReceiptNotes" type="text"></label>
                <button class="primary-btn" type="submit">Confirm receipt</button>
            </form>
        `;
    }

    function formHandover(item) {
        return `
            <form id="deliveryHandoverForm" class="delivery-form">
                <label>Handing-over person <input id="deliveryHandoverSender" type="text" required></label>
                <label>Receiver <input id="deliveryHandoverReceiver" type="text" value="${escapeHtml(item.receiver_name || "")}" required></label>
                <label>Quantity handed over <input id="deliveryQtyHanded" type="number" min="0" required></label>
                <label>Remarks <input id="deliveryHandoverRemarks" type="text"></label>
                <button class="primary-btn" type="submit">Record physical handover</button>
            </form>
        `;
    }

    function bindDetailForms(item) {
        $("deliveryReadyForm")?.addEventListener("submit", event => {
            event.preventDefault();
            const vehicle = ($("deliveryVehicle")?.value || "").trim();
            const provider = ($("deliveryProvider")?.value || "").trim();
            if (!vehicle && !provider) {
                toast("Vehicle number or transport provider is required");
                return;
            }
            run(async () => deliveryRequest("POST", deliverySuffix(item.delivery_id, "ready"), {
                dispatch_quantity: $("deliveryDispatchQty").value,
                vehicle_number: vehicle,
                transport_provider: provider,
                driver_name: $("deliveryDriver").value,
                driver_contact: $("deliveryDriverContact").value,
                source_location: $("deliverySource").value,
                destination_location: $("deliveryDestination").value,
                estimated_arrival: $("deliveryEta").value,
                transport_remarks: $("deliveryRemarks").value
            }), "Ready for dispatch recorded");
        });
        $("deliveryReceiptForm")?.addEventListener("submit", event => {
            event.preventDefault();
            run(async () => deliveryRequest("POST", deliverySuffix(item.delivery_id, "receipt"), {
                receiver_name: $("deliveryReceiverName").value,
                receiver_organization: $("deliveryReceiverOrg").value,
                receiver_contact: $("deliveryReceiverContact").value,
                quantity_received: $("deliveryQtyReceived").value,
                quantity_damaged: $("deliveryQtyDamaged").value,
                quantity_missing: $("deliveryQtyMissing").value,
                receipt_notes: $("deliveryReceiptNotes").value
            }), "Receipt confirmed");
        });
        $("deliveryHandoverForm")?.addEventListener("submit", event => {
            event.preventDefault();
            run(async () => deliveryRequest("POST", deliverySuffix(item.delivery_id, "handover"), {
                handover_sender: $("deliveryHandoverSender").value,
                handover_receiver: $("deliveryHandoverReceiver").value,
                quantity_handed_over: $("deliveryQtyHanded").value,
                handover_remarks: $("deliveryHandoverRemarks").value
            }), "Physical handover recorded. Verification is still required.");
        });
        $("deliveryActions")?.addEventListener("click", event => {
            const act = event.target.closest && event.target.closest("[data-delivery-act]")?.dataset?.deliveryAct;
            if (!act) {
                return;
            }
            const ident = item.delivery_id;
            if (act === "dispatch") {
                run(async () => deliveryRequest("POST", deliverySuffix(ident, "dispatch"), {}), "Dispatched");
            }
            if (act === "transit") {
                run(async () => deliveryRequest("POST", deliverySuffix(ident, "status"), { status: "IN_TRANSIT" }), "In transit");
            }
            if (act === "delivered") {
                run(async () => deliveryRequest("POST", deliverySuffix(ident, "status"), { status: "DELIVERED" }), "Delivery reported");
            }
            if (act === "partial") {
                run(async () => deliveryRequest("POST", deliverySuffix(ident, "status"), { status: "PARTIALLY_DELIVERED" }), "Partial delivery reported");
            }
            if (act === "fail") {
                run(async () => deliveryRequest("POST", deliverySuffix(ident, "status"), { status: "DELIVERY_FAILED" }), "Delivery failed");
            }
            if (act === "cancel") {
                run(async () => deliveryRequest("POST", deliverySuffix(ident, "status"), { status: "CANCELLED" }), "Delivery cancelled");
            }
            if (act === "verify") {
                run(async () => deliveryRequest("POST", deliverySuffix(ident, "handover/verify"), { confirmation: "verified" }), "Physical handover verified");
            }
        });
    }

    async function run(work, success) {
        if (deliveryBusy) {
            return;
        }
        deliveryBusy = true;
        try {
            const payload = await work();
            const delivery = payload.delivery;
            if (!isDeliveryRecord(delivery)) {
                throw new Error("Delivery API returned an invalid record");
            }
            selectedId = delivery.delivery_id;
            selectedDelivery = delivery;
            toast(success);
            await loadDeliveries();
        } catch (error) {
            toast(error.message || "Delivery update failed");
        } finally {
            deliveryBusy = false;
        }
    }

    async function loadDeliveries() {
        const table = $("deliveriesTable");
        if (table) {
            table.innerHTML = `<tr><td colspan="8">Loading deliveries...</td></tr>`;
        }
        try {
            const payload = await deliveryRequest("GET", "", null, {
                delivery_id: $("deliverySearchId")?.value,
                allocation_id: $("deliverySearchAllocation")?.value,
                q: $("deliverySearchResource")?.value,
                status: $("deliveryStatusFilter")?.value === "ALL" ? "" : $("deliveryStatusFilter")?.value
            });
            apiAvailable = true;
            loadError = "";
            deliveries = sanitizeRows(payload.deliveries);
            counts = payload.counts && typeof payload.counts === "object" ? payload.counts : emptyCounts();
            if (selectedId) {
                try {
                    const detail = await deliveryRequest("GET", deliverySuffix(selectedId), null);
                    selectedDelivery = isDeliveryRecord(detail.delivery) ? detail.delivery : null;
                    if (selectedDelivery) {
                        const history = await deliveryRequest("GET", deliverySuffix(selectedId, "history"), null);
                        if (Array.isArray(history.events)) {
                            selectedDelivery.events = history.events;
                        }
                    }
                } catch (error) {
                    selectedDelivery = deliveries.find(row => row.delivery_id === selectedId) || null;
                    if (!selectedDelivery) {
                        selectedId = "";
                    }
                }
            }
        } catch (error) {
            apiAvailable = false;
            deliveries = [];
            counts = emptyCounts();
            selectedDelivery = null;
            loadError = error.message || "Unable to load deliveries";
        }
        setSourceBanner();
        setDeliveryChrome();
        renderTable();
        renderDetail();
    }

    function setDeliveryChrome() {
        const nav = document.querySelector('[data-section="deliveries"]');
        if (nav) {
            nav.hidden = !apiAvailable;
        }
        document.querySelectorAll("[data-create-delivery]").forEach(button => {
            button.hidden = !apiAvailable || !canWriteDeliveries();
        });
        document.querySelectorAll('.dock [data-section="deliveries"]').forEach(button => {
            button.hidden = !apiAvailable;
        });
    }

    async function createFromAllocation(allocation) {
        if (deliveryBusy) {
            return;
        }
        if (!canWriteDeliveries()) {
            toast("Operator access is required to create a delivery");
            return;
        }
        deliveryBusy = true;
        try {
            if (!allocation || !allocation.allocation_id) {
                throw new Error("allocation_id is required");
            }
            if (String(allocation.status || "").toUpperCase() !== "ALLOCATED") {
                throw new Error("Only ALLOCATED allocations can create a delivery");
            }
            const payload = await deliveryRequest("POST", "", {
                allocation_id: allocation.allocation_id
            });
            if (!isDeliveryRecord(payload.delivery)) {
                throw new Error("Delivery API returned an invalid record");
            }
            selectedId = payload.delivery.delivery_id;
            selectedDelivery = payload.delivery;
            if (typeof navigateTo === "function") {
                navigateTo("deliveries");
            }
            toast("Delivery created. Record transport next.");
            await loadDeliveries();
        } catch (error) {
            toast(error.message || "Unable to create delivery");
        } finally {
            deliveryBusy = false;
        }
    }

    async function openDelivery(deliveryId) {
        selectedId = deliveryId;
        try {
            const payload = await deliveryRequest("GET", deliverySuffix(deliveryId), null);
            selectedDelivery = isDeliveryRecord(payload.delivery) ? payload.delivery : null;
            if (selectedDelivery) {
                const history = await deliveryRequest("GET", deliverySuffix(deliveryId, "history"), null);
                if (Array.isArray(history.events)) {
                    selectedDelivery.events = history.events;
                }
            }
        } catch (error) {
            selectedDelivery = null;
            toast(error.message || "Unable to load delivery");
        }
        renderDetail();
    }

    function initializeDeliveryTracking() {
        if (trackingInitialized) {
            return;
        }
        trackingInitialized = true;
        ["deliverySearchId", "deliverySearchAllocation", "deliverySearchResource", "deliveryStatusFilter"].forEach(id => {
            $(id)?.addEventListener("input", renderTable);
            $(id)?.addEventListener("change", renderTable);
        });
        $("deliveriesRefreshBtn")?.addEventListener("click", () => {
            loadDeliveries();
        });
        document.addEventListener("click", event => {
            const openId = event.target.closest && event.target.closest("[data-open-delivery]")?.dataset?.openDelivery;
            if (openId) {
                openDelivery(openId);
            }
            const allocationId = event.target.closest && event.target.closest("[data-create-delivery]")?.dataset?.createDelivery;
            if (allocationId) {
                const allocation = typeof allocations !== "undefined"
                    ? allocations.find(row => row.allocation_id === allocationId)
                    : null;
                if (!allocation) {
                    toast("Load the allocation before creating a delivery.");
                    return;
                }
                createFromAllocation(allocation);
            }
        });
    }

    global.initializeDeliveryTracking = initializeDeliveryTracking;
    global.loadDeliveries = loadDeliveries;
    global.createDeliveryFromAllocation = createFromAllocation;
    global.ERAP_DELIVERY_STATUSES = STATUSES;

})(window);
