import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const appSource = fs.readFileSync(path.join(root, "frontend", "app.js"), "utf8");
const page = fs.readFileSync(path.join(root, "frontend", "index.html"), "utf8");

function extractFunction(source, name) {
    const signature = `function ${name}(`;
    let start = source.indexOf(signature);
    assert.notEqual(start, -1, name);
    if (source.slice(Math.max(0, start - 6), start) === "async ") {
        start -= 6;
    }
    let depth = 0;
    for (let index = source.indexOf("{", start); index < source.length; index += 1) {
        const character = source[index];
        if (character === "{") {
            depth += 1;
        } else if (character === "}") {
            depth -= 1;
            if (depth === 0) {
                return source.slice(start, index + 1);
            }
        }
    }
    throw new Error(`unterminated ${name}`);
}

assert.equal(page.includes('<option value="RELEASED">'), true);
assert.equal(page.includes('id="allocationStatusFilter"'), true);
assert.equal(page.includes('id="queueCancelled"'), true);
assert.equal(page.includes("pending in loaded results"), true);
assert.equal(page.includes("These counts are from the requests already loaded."), true);
assert.equal(page.includes("No allocations are loaded for this organization and location."), true);
assert.equal(page.includes("WAITING"), false);
assert.equal(page.includes("aria-describedby=\"cancelRequestPrompt\""), true);
assert.equal(appSource.includes("statusLabel = \"WAITING\""), false);
assert.equal(appSource.includes("localStorage.setItem"), false);

const allocationFilterStart = appSource.lastIndexOf('$("allocationStatusFilter")');
const allocationFilter = appSource.slice(allocationFilterStart, allocationFilterStart + 280);
assert.equal(allocationFilter.includes("renderAllocations"), true);
assert.equal(allocationFilter.includes("loadAllocations"), false);
assert.equal(allocationFilter.includes("page_token"), false);

const names = [
    "emergencyStatusLabel",
    "requestRecordStatus",
    "escapeHtml",
    "formatResourceType",
    "recordChangedMessage",
    "syncAllocationRequestHint",
    "renderRequests",
    "renderAllocations",
    "submitRequestModal",
    "allocationPayload",
    "setAllocationBusy",
    "clearAllocationPreview",
    "renderAllocationPreview",
    "cancelAllocationPreview",
    "postAllocation",
    "submitAllocation",
    "confirmAllocation",
    "releaseResource"
];

const hosts = {};

function element(id, extra = {}) {
    const node = hosts[id] || {
        id,
        value: extra.value || "",
        innerHTML: "",
        textContent: extra.textContent || "",
        disabled: false,
        className: "",
        style: {},
        selectedOptions: [{ textContent: "Medical" }],
        classList: {
            add(name) { node.className += " " + name; },
            remove() {},
            contains() { return false; }
        },
        setAttribute() {},
        removeAttribute() {},
        focus() {},
        hidden: false,
        reset() {}
    };
    if (extra.value !== undefined) {
        node.value = extra.value;
    }
    hosts[id] = node;
    return node;
}

element("requestsTable");
element("requestsSearch", { value: "" });
element("requestsStatusFilter", { value: "ALL" });
element("allocationsTable");
element("allocationSearch", { value: "" });
element("allocationStatusFilter", { value: "ALL" });
element("allocationRequestHint");
element("requestId", { value: "" });
element("allocationForm");
element("allocateBtn");
element("confirmAllocationBtn", { textContent: "Confirm allocation" });
element("cancelAllocationPreviewBtn", { textContent: "Cancel" });
element("allocationPreview");
element("allocationPreviewDetails");
element("allocationPreviewMessage");
element("requestModalResult");
element("submitRequestModal");
element("modalRequestId", { value: "REQ-NEW" });
element("modalRequestType", { value: "TYPE-1" });
element("modalLocation", { value: "LOC-1" });
element("modalPriority", { value: "1" });
element("requestModal");
element("requestModalForm");

const calls = [];
const toasts = [];
const results = [];
const confirms = [];
let confirmAnswer = true;

const context = {
    REQUESTS_API_URL: "https://example.test/requests",
    API_URL: "https://example.test/allocate",
    RELEASE_RESOURCE_API_URL: "https://example.test/allocate/resources/release",
    requests: [],
    allocations: [],
    requestCancelInFlight: false,
    requestCreateInFlight: false,
    allocationSubmitInFlight: false,
    allocationPreviewResourceId: "",
    allocationPreviewRequestId: "",
    allocationPreviewEpoch: 0,
    emergencyReleaseInFlight: false,
    requestNextPageToken: null,
    tenantContextLoading: false,
    console,
    document: { getElementById: (id) => hosts[id] || null },
    selectedOrganizationId: () => "ORG-8E62A29199FB",
    readAttributeValues: () => ({}),
    getIdToken: () => "id-token",
    getApiMessage: (data) => (data && data.message) || "",
    showToast: (message) => toasts.push(String(message)),
    showResult: (message) => results.push(String(message)),
    hideResult() {},
    addNotification() {},
    async loadRequests() { calls.push("loadRequests"); },
    async loadAllocations() { calls.push("loadAllocations"); },
    async loadResources() { calls.push("loadResources"); },
    fetch(url, options) {
        const spec = context.http.queue.shift();
        calls.push({
            url,
            method: options.method,
            body: options.body
        });
        if (url === context.API_URL) {
            calls.push({
                busy: hosts.allocateBtn.textContent,
                confirmBusy: hosts.confirmAllocationBtn.textContent,
                previewDisabled: hosts.allocateBtn.disabled,
                confirmDisabled: hosts.confirmAllocationBtn.disabled
            });
        }
        if (!spec) {
            throw new Error("unexpected fetch " + url);
        }
        return Promise.resolve({
            ok: spec.status < 400,
            status: spec.status,
            text: async () => JSON.stringify(spec.body || {}),
            json: async () => spec.body || {}
        });
    },
    http: { queue: [] },
    window: {},
    pendingTimers: [],
    setTimeout(fn) { context.pendingTimers.push(fn); }
};
context.window.confirm = (message) => {
    confirms.push(String(message));
    return confirmAnswer;
};
context.$ = (id) => hosts[id] || null;
vm.createContext(context);
vm.runInContext(names.map(name => extractFunction(appSource, name)).join("\n"), context);

function requests() {
    return [
        { request_id: "REQ-P", resource_type: "Pending Type", location: "North", priority: 1, status: "PENDING" },
        { request_id: "REQ-A", resource_type: "Allocated Type", location: "South", priority: 2, status: "ALLOCATED" },
        { request_id: "REQ-R", resource_type: "Released Type", location: "East", priority: 3, status: "RELEASED" },
        { request_id: "REQ-C", resource_type: "Cancelled Type", location: "West", priority: 4, status: "CANCELLED" }
    ];
}

context.requests = requests();
context.renderRequests();
assert.equal(hosts.requestsTable.innerHTML.includes("status-pending"), true);
assert.equal(hosts.requestsTable.innerHTML.includes("status-allocated"), true);
assert.equal(hosts.requestsTable.innerHTML.includes("status-released"), true);
assert.equal(hosts.requestsTable.innerHTML.includes("status-cancelled"), true);
assert.equal(hosts.requestsTable.innerHTML.includes("Pending"), true);
assert.equal(hosts.requestsTable.innerHTML.includes("ALLOCATED"), false);
assert.equal(hosts.requestsTable.innerHTML.includes("WAITING"), false);
assert.equal(hosts.requestsTable.innerHTML.includes('data-request-id="REQ-P"'), true);
assert.equal(hosts.requestsTable.innerHTML.includes('data-request-id="REQ-A"'), false);
assert.equal(hosts.requestsTable.innerHTML.includes("Cancel pending request REQ-P"), true);

hosts.requestsStatusFilter.value = "RELEASED";
context.renderRequests();
assert.equal(hosts.requestsTable.innerHTML.includes("REQ-R"), true);
assert.equal(hosts.requestsTable.innerHTML.includes("REQ-P"), false);

hosts.requestsStatusFilter.value = "PENDING";
hosts.requestsSearch.value = "missing";
context.renderRequests();
assert.equal(hosts.requestsTable.innerHTML.includes("No loaded requests match this search or status."), true);
assert.equal(calls.length, 0);

context.requests = [];
hosts.requestsSearch.value = "";
hosts.requestsStatusFilter.value = "ALL";
context.renderRequests();
assert.equal(hosts.requestsTable.innerHTML.includes("No requests are loaded for this organization and location."), true);

context.allocations = [
    { allocation_id: "AL-A", request_id: "REQ-A", resource_id: "RES-1", resource_type: "Bed", location: "North", priority: 1, status: "ALLOCATED" },
    { allocation_id: "AL-T", request_id: "REQ-T", resource_id: "RES-2", resource_type: "Kit", location: "South", priority: 2, status: "RETURNED" },
    { allocation_id: "AL-R", request_id: "REQ-R", resource_id: "RES-3", resource_type: "Van", location: "East", priority: 3, status: "RELEASED" }
];
hosts.allocationStatusFilter.value = "ALL";
hosts.allocationSearch.value = "";
context.renderAllocations();
assert.equal(hosts.allocationsTable.innerHTML.includes("Emergency release"), true);
assert.equal(hosts.allocationsTable.innerHTML.includes("Returned"), true);
assert.equal(hosts.allocationsTable.innerHTML.includes("status-released"), true);
assert.equal(hosts.allocationsTable.innerHTML.includes("status-returned"), true);
assert.equal((hosts.allocationsTable.innerHTML.match(/releaseResource/g) || []).length, 1);

hosts.allocationStatusFilter.value = "RETURNED";
context.renderAllocations();
assert.equal(hosts.allocationsTable.innerHTML.includes("AL-T"), true);
assert.equal(hosts.allocationsTable.innerHTML.includes("AL-A"), false);
assert.equal(hosts.allocationsTable.innerHTML.includes("Emergency release"), false);

hosts.allocationSearch.value = "no-such";
context.renderAllocations();
assert.equal(hosts.allocationsTable.innerHTML.includes("No loaded allocations match this search or status."), true);

context.allocations = [];
hosts.allocationSearch.value = "";
hosts.allocationStatusFilter.value = "ALL";
context.renderAllocations();
assert.equal(hosts.allocationsTable.innerHTML.includes("No allocations are loaded for this organization and location."), true);

context.requests = requests();
element("resourceType", { value: "TYPE-1" });
element("location", { value: "LOC-1" });
element("priority", { value: "1" });
hosts.requestId.value = "REQ-C";
context.syncAllocationRequestHint();
assert.equal(hosts.allocationRequestHint.textContent.includes("not available for allocation"), true);

calls.length = 0;
confirmAnswer = true;
await context.submitAllocation({ preventDefault() {} });
assert.equal(calls.length, 0);
assert.equal(results.some(item => item.includes("Cancelled")), true);

hosts.requestId.value = "REQ-P";
calls.length = 0;
await context.cancelAllocationPreview();
assert.equal(calls.length, 0);
assert.equal(hosts.allocationPreview.hidden, true);

const proposed = {
    preview: true,
    request_id: "REQ-P",
    resource_id: "RES-1",
    name: "Ward <bed>",
    resource_type: "Bed",
    location: "North",
    location_id: "LOC-1",
    available: true,
    match: ["same location"]
};
context.http.queue.push({ status: 200, body: proposed });
await context.submitAllocation({ preventDefault() {} });
const previewCall = calls.find(call => call.url === "https://example.test/allocate");
assert.equal(JSON.parse(previewCall.body).preview, true);
assert.equal(JSON.parse(previewCall.body).confirm, undefined);
assert.equal(calls.some(call => call.busy === "Finding match..." && call.previewDisabled === true), true);
assert.equal(hosts.allocationPreviewDetails.innerHTML.includes("RES-1"), true);
assert.equal(hosts.allocationPreviewDetails.innerHTML.includes("Ward &lt;bed&gt;"), true);
assert.equal(hosts.allocationPreviewDetails.innerHTML.includes("<bed>"), false);
assert.equal(hosts.confirmAllocationBtn.disabled, false);
assert.equal(hosts.allocateBtn.textContent, "Preview match");
assert.equal(hosts.allocateBtn.disabled, false);

calls.length = 0;
await context.cancelAllocationPreview();
assert.equal(calls.length, 0);
assert.equal(context.allocationPreviewResourceId, "");
assert.equal(hosts.confirmAllocationBtn.disabled, true);

context.http.queue.push({ status: 404, body: { message: "No suitable resource available" } });
await context.submitAllocation({ preventDefault() {} });
assert.equal(results.some(item => item.includes("No matching resource is available.")), true);
assert.equal(hosts.confirmAllocationBtn.disabled, true);

calls.length = 0;
results.length = 0;
context.http.queue.push({ status: 200, body: proposed });
await context.submitAllocation({ preventDefault() {} });
context.http.queue.push({ status: 409, body: { message: "The proposed resource is no longer available. Preview the match again." } });
await context.confirmAllocation();
const confirmCalls = calls.filter(call => call.url === "https://example.test/allocate");
assert.equal(confirmCalls.length, 2);
assert.equal(JSON.parse(confirmCalls[1].body).confirm, true);
assert.equal(JSON.parse(confirmCalls[1].body).resource_id, "RES-1");
assert.equal(calls.some(call => call.confirmBusy === "Allocating..." && call.confirmDisabled === true), true);
assert.equal(results.some(item => item.includes("The resource is no longer available. Preview the match again.")), true);
assert.equal(calls.includes("loadRequests"), true);
assert.equal(calls.includes("loadAllocations"), true);
assert.equal(calls.includes("loadResources"), true);
assert.equal(context.allocationPreviewResourceId, "");
assert.equal(context.allocations.length, 0);

calls.length = 0;
results.length = 0;
context.http.queue.push({ status: 200, body: proposed });
await context.submitAllocation({ preventDefault() {} });
context.http.queue.push({
    status: 200,
    body: { message: "Resource allocated successfully", status: "ALLOCATED", resource_id: "RES-1", allocation_id: "ALLOC-REQ-P" }
});
await context.confirmAllocation();
assert.equal(calls.filter(call => call.url === "https://example.test/allocate").length, 2);
assert.equal(JSON.parse(calls.filter(call => call.url === "https://example.test/allocate")[1].body).confirm, true);
assert.equal(calls.includes("loadRequests"), true);
assert.equal(calls.includes("loadAllocations"), true);
assert.equal(calls.includes("loadResources"), true);
assert.equal(results.some(item => item.includes("Resource allocated successfully")), true);
assert.equal(hosts.allocateBtn.textContent, "Preview match");
assert.equal(hosts.allocateBtn.disabled, false);
assert.equal(hosts.allocationPreview.hidden, true);

context.allocationSubmitInFlight = true;
const guarded = calls.length;
await context.submitAllocation({ preventDefault() {} });
assert.equal(calls.length, guarded);
context.allocationSubmitInFlight = false;

calls.length = 0;
context.http.queue.push({ status: 400, body: { message: "Priority is invalid" } });
await context.submitRequestModal({ preventDefault() {} });
assert.equal(hosts.requestModalResult.textContent, "Priority is invalid");
assert.equal(calls.includes("loadRequests"), false);
assert.equal(hosts.submitRequestModal.disabled, false);

calls.length = 0;
context.http.queue.push({ status: 201, body: { message: "Request created successfully." } });
await context.submitRequestModal({ preventDefault() {} });
assert.equal(calls.includes("loadRequests"), true);
assert.equal(hosts.requestModalResult.textContent.includes("Request created"), true);
assert.equal(hosts.submitRequestModal.disabled, true);
context.pendingTimers.shift()();
assert.equal(hosts.submitRequestModal.disabled, false);

context.allocations = [
    { allocation_id: "AL-A", request_id: "REQ-A", resource_id: "RES-1", status: "ALLOCATED" }
];
calls.length = 0;
confirmAnswer = false;
await context.releaseResource("RES-1");
assert.equal(calls.length, 0);

confirmAnswer = true;
context.http.queue.push({ status: 409, body: { message: "Resource is not allocated" } });
await context.releaseResource("RES-1");
assert.equal(calls.some(call => call.url === "https://example.test/allocate/resources/release"), true);
assert.equal(calls.includes("loadAllocations"), true);
assert.equal(toasts.some(item => item.includes("may have changed")), true);
assert.equal(context.emergencyReleaseInFlight, false);

calls.length = 0;
context.http.queue.push({ status: 200, body: { message: "Released" } });
await context.releaseResource("RES-1");
assert.equal(calls.includes("loadResources"), true);
assert.equal(calls.includes("loadRequests"), true);
assert.equal(context.allocations.some(item => item.status === "RELEASED"), false);

console.log("workflow ux ui passed");
