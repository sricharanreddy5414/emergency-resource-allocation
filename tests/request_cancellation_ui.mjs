import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const appSource = fs.readFileSync(path.join(root, "frontend", "app.js"), "utf8");
const page = fs.readFileSync(path.join(root, "frontend", "index.html"), "utf8");
const style = fs.readFileSync(path.join(root, "frontend", "style.css"), "utf8");

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

const cancelSource = extractFunction(appSource, "confirmCancelRequest");
assert.equal(cancelSource.includes("organization_id"), false);
assert.equal(cancelSource.includes("actor_role"), false);
assert.equal(cancelSource.includes("actor_sub"), false);
assert.equal(cancelSource.includes("atob("), false);
assert.equal(cancelSource.includes("localStorage"), false);
assert.equal(cancelSource.includes("sessionStorage"), false);
assert.equal(cancelSource.includes('Status = "CANCELLED"'), false);
assert.equal(cancelSource.includes("REQUESTS_API_URL + \"/cancel\""), true);
assert.equal(cancelSource.includes("request_id: requestId"), true);
assert.equal(cancelSource.includes("getIdToken()"), true);
assert.equal(cancelSource.includes("loadRequests()"), true);

const createSource = extractFunction(appSource, "submitRequestModal");
assert.equal(createSource.includes("request_type_id: requestTypeId"), true);
assert.equal(createSource.includes('fetch(REQUESTS_API_URL,'), true);
assert.equal(createSource.includes("/cancel"), false);

assert.equal(appSource.includes("function renderAllocations("), true);
assert.equal(page.includes('id="allocationsTable"'), true);
assert.equal(page.includes("Release returns the resource"), true);
assert.equal(page.includes('id="requestsStatusFilter"'), true);
assert.equal(page.includes('<option value="CANCELLED">'), true);
assert.equal(page.includes("Cancelled"), true);
assert.equal(page.includes('id="cancelRequestTitle"'), true);
assert.equal(page.includes("Cancel this pending request?"), true);
assert.equal(page.includes("Cancellation cannot be undone."), true);
const cancelModal = page.slice(page.indexOf('id="cancelRequestModal"'), page.indexOf('id="requestModal"'));
assert.equal(cancelModal.includes("<textarea"), false);
assert.equal(cancelModal.includes("reason"), false);
assert.equal(style.includes(".status-badge.status-cancelled"), true);

const names = [
    "escapeHtml",
    "formatResourceType",
    "emergencyStatusLabel",
    "requestRecordStatus",
    "renderActiveOperations",
    "renderRequests",
    "syncCancelRequestDialog",
    "closeCancelRequestDialog",
    "abandonCancelRequestDialog",
    "openCancelRequestDialog",
    "confirmCancelRequest",
    "getApiMessage"
];

const hosts = {};

function element(id, extra = {}) {
    const classes = new Set(extra.hidden ? ["hidden"] : []);
    const node = {
        id,
        value: extra.value || "",
        innerHTML: "",
        textContent: extra.textContent || "",
        disabled: false,
        focus() {},
        classList: {
            add(name) { classes.add(name); },
            remove(name) { classes.delete(name); },
            contains(name) { return classes.has(name); }
        }
    };
    hosts[id] = node;
    return node;
}

element("requestsTable");
element("requestsSearch");
element("requestsStatusFilter", { value: "ALL" });
element("activeOperations");
element("cancelRequestModal", { hidden: true });
element("cancelRequestPrompt", { textContent: "Cancellation cannot be undone." });
element("cancelRequestConfirm");
element("cancelRequestClose");
element("cancelRequestTitle", { textContent: "Cancel this pending request?" });

const toasts = [];
const calls = [];
const http = { queue: [] };
let loadCount = 0;

const context = {
    REQUESTS_API_URL: "https://example.test/requests",
    requests: [],
    requestCancelInFlight: false,
    cancelRequestId: "",
    console,
    document: {
        getElementById(id) {
            return hosts[id] || null;
        }
    },
    showToast(message) {
        toasts.push(String(message));
    },
    getIdToken() {
        return "id-token";
    },
    async loadRequests() {
        loadCount += 1;
    },
    fetch(url, options) {
        const spec = http.queue.shift();
        calls.push({
            url,
            method: options.method,
            body: options.body,
            contentType: options.headers["Content-Type"],
            authorized: String(options.headers.Authorization || "").startsWith("Bearer ")
        });
        if (!spec) {
            throw new Error("unexpected fetch");
        }
        if (spec.hang) {
            return spec.promise;
        }
        return Promise.resolve({
            ok: spec.status < 400,
            status: spec.status,
            text: async () => JSON.stringify(spec.body || {})
        });
    }
};
context.$ = (id) => hosts[id] || null;
vm.createContext(context);
vm.runInContext(names.map(name => extractFunction(appSource, name)).join("\n"), context);

function sample() {
    return [
        { request_id: "REQ-P", ResourceType: "Pending Type", Location: "North", Priority: 2, Status: "PENDING", CreatedAt: "t1" },
        { request_id: "REQ-A", ResourceType: "Allocated Type", Location: "South", Priority: 3, Status: "ALLOCATED", CreatedAt: "t2" },
        { request_id: "REQ-R", ResourceType: "Released Type", Location: "East", Priority: 4, Status: "RELEASED", CreatedAt: "t3" },
        { request_id: "REQ-C", ResourceType: "Cancelled Type", Location: "West", Priority: 1, Status: "CANCELLED", CreatedAt: "t4" }
    ];
}

function reset() {
    context.requests = sample();
    context.requestCancelInFlight = false;
    context.cancelRequestId = "";
    toasts.length = 0;
    calls.length = 0;
    http.queue = [];
    loadCount = 0;
    hosts.requestsStatusFilter.value = "ALL";
    hosts.requestsSearch.value = "";
    hosts.cancelRequestModal.classList.add("hidden");
    hosts.cancelRequestConfirm.disabled = false;
    hosts.cancelRequestConfirm.textContent = "Cancel request";
    context.renderRequests();
}

reset();
assert.equal(hosts.requestsTable.innerHTML.includes('data-request-id="REQ-P"'), true);
assert.equal(hosts.requestsTable.innerHTML.includes('data-request-id="REQ-A"'), false);
assert.equal(hosts.requestsTable.innerHTML.includes('data-request-id="REQ-R"'), false);
assert.equal(hosts.requestsTable.innerHTML.includes('data-request-id="REQ-C"'), false);
assert.equal(hosts.requestsTable.innerHTML.includes("status-cancelled"), true);

context.renderActiveOperations();
assert.equal(hosts.activeOperations.innerHTML.includes("Pending Type"), true);
assert.equal(hosts.activeOperations.innerHTML.includes("Allocated Type"), true);
assert.equal(hosts.activeOperations.innerHTML.includes("Cancelled Type"), false);
assert.equal(hosts.activeOperations.innerHTML.includes("Released Type"), false);
context.requests = [{ request_id: "REQ-C", Status: "CANCELLED", Priority: 1, ResourceType: "Cancelled Type", Location: "West" }];
context.renderActiveOperations();
assert.equal(hosts.activeOperations.innerHTML.includes("No active operations"), true);

reset();
hosts.requestsStatusFilter.value = "CANCELLED";
context.renderRequests();
assert.equal(hosts.requestsTable.innerHTML.includes("REQ-C"), true);
assert.equal(hosts.requestsTable.innerHTML.includes("REQ-P"), false);
hosts.requestsStatusFilter.value = "PENDING";
context.renderRequests();
assert.equal(hosts.requestsTable.innerHTML.includes("REQ-P"), true);
assert.equal(hosts.requestsTable.innerHTML.includes("REQ-C"), false);
hosts.requestsStatusFilter.value = "ALL";
context.renderRequests();
assert.equal(hosts.requestsTable.innerHTML.includes("REQ-C"), true);
assert.equal(hosts.requestsTable.innerHTML.includes("REQ-P"), true);

context.openCancelRequestDialog("REQ-P");
assert.equal(hosts.cancelRequestModal.classList.contains("hidden"), false);
assert.equal(hosts.cancelRequestPrompt.textContent, "Cancellation cannot be undone.");
assert.equal(hosts.cancelRequestTitle.textContent, "Cancel this pending request?");

let releaseHang;
const hanging = new Promise(resolve => {
    releaseHang = resolve;
});
http.queue.push({ hang: true, promise: hanging.then(() => ({
    ok: true,
    status: 200,
    text: async () => JSON.stringify({ message: "Request cancelled" })
})) });
const first = context.confirmCancelRequest();
const second = context.confirmCancelRequest();
assert.equal(calls.length, 1);
assert.equal(hosts.cancelRequestConfirm.disabled, true);
assert.equal(hosts.requestsTable.innerHTML.includes("disabled"), true);
releaseHang();
await first;
await second;
assert.equal(calls.length, 1);
assert.equal(calls[0].method, "POST");
assert.equal(calls[0].url, "https://example.test/requests/cancel");
assert.equal(calls[0].authorized, true);
assert.deepEqual(JSON.parse(calls[0].body), { request_id: "REQ-P" });
assert.equal(loadCount, 1);
assert.equal(toasts.includes("Request cancelled"), true);
assert.equal(hosts.cancelRequestModal.classList.contains("hidden"), true);

async function fail(status, message) {
    reset();
    context.openCancelRequestDialog("REQ-P");
    http.queue.push({ status, body: { message } });
    await context.confirmCancelRequest();
    assert.equal(toasts.includes(message), true, String(status));
    return loadCount;
}

assert.equal(await fail(400, "Request ID is required"), 0);
assert.equal(await fail(401, "Unauthorized"), 0);
assert.equal(await fail(403, "Organization access denied"), 0);
assert.equal(await fail(404, "Record not found"), 0);
assert.equal(await fail(500, "Failed to create request"), 0);
assert.equal(await fail(409, "Request is not eligible for cancellation"), 1);

reset();
context.openCancelRequestDialog("REQ-P");
context.requests = context.requests.map(request => (
    request.request_id === "REQ-P" ? { ...request, Status: "ALLOCATED" } : request
));
context.syncCancelRequestDialog();
assert.equal(hosts.cancelRequestModal.classList.contains("hidden"), true);

reset();
context.openCancelRequestDialog("REQ-P");
context.abandonCancelRequestDialog();
assert.equal(hosts.cancelRequestModal.classList.contains("hidden"), true);
assert.equal(context.cancelRequestId, "");

console.log("request cancellation ui passed");
