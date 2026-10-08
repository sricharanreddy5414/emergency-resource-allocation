import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const appSource = fs.readFileSync(path.join(root, "frontend", "app.js"), "utf8");
const html = fs.readFileSync(path.join(root, "frontend", "index.html"), "utf8");
const start = appSource.indexOf("/* EMERGENCY_PAGE_START */");
const end = appSource.indexOf("/* EMERGENCY_PAGE_END */");
assert.notEqual(start, -1);
assert.notEqual(end, -1);
const block = appSource.slice(start, end);

for (const banned of ["localStorage", "sessionStorage", "atob(", "btoa(", "console.log", "JSON.parse(requestNextPageToken", "JSON.parse(allocationNextPageToken"]) {
    assert.equal(block.includes(banned), false, banned);
}
assert.equal(block.includes('params.set("page_token", pageToken)'), true);
assert.equal(block.includes("pageToken.split"), false);
assert.equal(html.includes('id="requestLoadMore"'), true);
assert.equal(html.includes('id="allocationLoadMore"'), true);
assert.equal(appSource.includes("resetRequestPagination();"), true);
assert.equal(appSource.includes("resetAllocationPagination();"), true);
const requestControls = appSource.slice(
    appSource.indexOf("function initializeRequestControls"),
    appSource.indexOf("function escapeHtml") > appSource.indexOf("function initializeRequestControls")
        ? appSource.indexOf("function requestRecordStatus", appSource.indexOf("function initializeRequestControls"))
        : appSource.indexOf("function initializeRequestControls") + 800
);
assert.equal(requestControls.includes("renderRequests"), true);
assert.equal(requestControls.includes("loadRequests({ append"), false);
assert.equal(appSource.includes("clearTenantData();"), true);
assert.equal(block.includes("localStorage"), false);
assert.equal(block.includes("console.log"), false);

function extractFunction(source, name) {
    const signature = `function ${name}(`;
    let startIndex = source.indexOf(signature);
    assert.notEqual(startIndex, -1, name);
    if (source.slice(Math.max(0, startIndex - 6), startIndex) === "async ") {
        startIndex -= 6;
    }
    let depth = 0;
    for (let index = source.indexOf("{", startIndex); index < source.length; index += 1) {
        const character = source[index];
        if (character === "{") {
            depth += 1;
        } else if (character === "}") {
            depth -= 1;
            if (depth === 0) {
                return source.slice(startIndex, index + 1);
            }
        }
    }
    throw new Error(`unterminated ${name}`);
}

const preambleEnd = block.indexOf("async function loadRequests");
const preamble = block.slice(0, preambleEnd);
const source = [
    preamble,
    extractFunction(block, "loadRequests"),
    extractFunction(block, "loadAllocations"),
    `globalThis.__emergency = {
        get requestNextPageToken() { return requestNextPageToken; },
        set requestNextPageToken(value) { requestNextPageToken = value; },
        get allocationNextPageToken() { return allocationNextPageToken; },
        set allocationNextPageToken(value) { allocationNextPageToken = value; },
        get requests() { return requests; },
        get allocations() { return allocations; },
        get requestPageLoading() { return requestPageLoading; },
        get allocationPageLoading() { return allocationPageLoading; }
    };`
].join("\n");

const hosts = {};
const calls = [];
const toasts = [];
const responses = [];

function element(id) {
    if (!hosts[id]) {
        hosts[id] = {
            id,
            value: id === "requestsStatusFilter" ? "ALL" : "",
            innerHTML: "",
            children: [],
            disabled: false,
            className: "",
            textContent: "",
            replaceChildren() {
                this.children = [];
            },
            appendChild(child) {
                this.children.push(child);
            },
            addEventListener() {}
        };
    }
    return hosts[id];
}

const context = {
    REQUESTS_API_URL: "https://example.test/requests",
    ALLOCATIONS_API_URL: "https://example.test/allocate/allocations",
    requests: [],
    allocations: [],
    calls,
    toasts,
    console,
    URLSearchParams,
    document: { getElementById: element, createElement: () => element("button") },
    selectedOrganizationId: () => context.currentUser.organization.organization_id,
    selectedLocationId: () => context.currentUser.location.location_id,
    tenantQuery() {
        return `?organization_id=${context.currentUser.organization.organization_id}&location_id=${context.currentUser.location.location_id}`;
    },
    currentUser: {
        organization: { organization_id: "ORG-A" },
        location: { location_id: "LOC-1" }
    },
    waitForIdToken: async () => "id-token",
    getApiMessage: (payload) => payload.message || "",
    showToast(message) {
        toasts.push(message);
    },
    updateAnalytics() {},
    updateAdminDashboard() {},
    renderRequests() {},
    renderAllocations() {},
    syncCancelRequestDialog() {},
    fetch: async (url) => {
        calls.push(url);
        const next = responses.shift();
        if (!next) {
            throw new Error("missing response");
        }
        if (next.hang) {
            await new Promise(() => {});
        }
        return {
            ok: (next.status || 200) < 400,
            status: next.status || 200,
            json: async () => next.body
        };
    },
    $(id) {
        return element(id);
    }
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context);

function resetCalls() {
    calls.length = 0;
    toasts.length = 0;
    responses.length = 0;
    context.requests = [];
    context.allocations = [];
    context.requestNextPageToken = null;
    context.allocationNextPageToken = null;
    context.requestPageLoading = false;
    context.allocationPageLoading = false;
}

async function testRequests() {
    resetCalls();
    responses.push({
        body: { requests: [{ request_id: "Q1", status: "PENDING" }], next_token: "opaque-request-token" }
    });
    await context.loadRequests();
    assert.equal(calls[0].includes("limit=100"), true);
    assert.equal(calls[0].includes("page_token="), false);
    assert.equal(context.__emergency.requests.length, 1);
    assert.equal(context.__emergency.requestNextPageToken, "opaque-request-token");
    assert.equal(element("requestLoadMore").children.length, 1);
    assert.equal(element("requestLoadMore").children[0].textContent, "Load more");
    assert.equal(JSON.stringify(element("requestLoadMore")).includes("opaque-request-token"), false);

    responses.push({
        body: { requests: [{ request_id: "Q2", status: "ALLOCATED" }], count: 1 }
    });
    const first = context.loadRequests({ append: true });
    const second = context.loadRequests({ append: true });
    await first;
    await second;
    assert.equal(calls.length, 2);
    assert.equal(calls[1].includes("page_token=opaque-request-token"), true);
    assert.deepEqual(context.__emergency.requests.map(item => item.request_id), ["Q1", "Q2"]);
    assert.equal(context.__emergency.requestNextPageToken, null);
    assert.equal(element("requestLoadMore").children.length, 0);

    context.__emergency.requestNextPageToken = "still-there";
    element("requestsStatusFilter").value = "CANCELLED";
    assert.equal(context.__emergency.requestNextPageToken, "still-there");

    context.currentUser.organization.organization_id = "ORG-B";
    responses.push({ body: { requests: [{ request_id: "QB", status: "PENDING" }] } });
    await context.loadRequests();
    assert.deepEqual(context.__emergency.requests.map(item => item.request_id), ["QB"]);
    assert.equal(calls.at(-1).includes("organization_id=ORG-B"), true);
    assert.equal(calls.at(-1).includes("page_token="), false);
}

async function testAllocations() {
    resetCalls();
    context.currentUser.organization.organization_id = "ORG-A";
    responses.push({
        body: { allocations: [{ allocation_id: "A1" }], next_token: "opaque-allocation-token" }
    });
    await context.loadAllocations();
    assert.equal(calls[0].includes("limit=100"), true);
    assert.equal(context.__emergency.allocations.length, 1);
    responses.push({ body: { allocations: [{ allocation_id: "A1" }, { allocation_id: "A2" }] } });
    await context.loadAllocations({ append: true });
    assert.deepEqual(context.__emergency.allocations.map(item => item.allocation_id), ["A1", "A2"]);
    assert.equal(calls[1].includes("page_token=opaque-allocation-token"), true);

    context.__emergency.allocationNextPageToken = "old-token";
    context.currentUser.location.location_id = "LOC-2";
    responses.push({ body: { allocations: [{ allocation_id: "A9" }] } });
    await context.loadAllocations();
    assert.deepEqual(context.__emergency.allocations.map(item => item.allocation_id), ["A9"]);
    assert.equal(calls.at(-1).includes("location_id=LOC-2"), true);
    assert.equal(calls.at(-1).includes("page_token="), false);
}

async function testEmptyVisiblePageKeepsToken() {
    resetCalls();
    responses.push({
        body: { requests: [], next_token: "continue-after-empty" }
    });
    await context.loadRequests();
    assert.equal(context.__emergency.requests.length, 0);
    assert.equal(context.__emergency.requestNextPageToken, "continue-after-empty");
    assert.equal(element("requestLoadMore").children.length, 1);
}

await testRequests();
await testAllocations();
await testEmptyVisiblePageKeepsToken();
console.log("emergency pagination ui ok");
