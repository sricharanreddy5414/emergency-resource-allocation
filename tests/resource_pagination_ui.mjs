import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const appSource = fs.readFileSync(path.join(root, "frontend", "app.js"), "utf8");
const pageStart = appSource.indexOf("/* RESOURCE_PAGE_START */");
const pageEnd = appSource.indexOf("/* RESOURCE_PAGE_END */");
assert.notEqual(pageStart, -1);
assert.notEqual(pageEnd, -1);
const pageBlock = appSource.slice(pageStart, pageEnd);

for (const banned of ["localStorage", "sessionStorage", "atob(", "btoa(", "console.log"]) {
    assert.equal(pageBlock.includes(banned), false, banned);
}
assert.equal(pageBlock.includes('params.set("page_token", pageToken)'), true);
assert.equal(pageBlock.includes("selectedOrganizationId()"), true);
assert.equal(pageBlock.includes("JSON.parse(resourceNextPageToken"), false);
assert.equal(pageBlock.includes("pageToken.split"), false);

function extractFunction(source, name) {
    const signature = `function ${name}(`;
    const start = source.indexOf(signature);
    assert.notEqual(start, -1, name);
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

const hosts = {};
const calls = [];
const toasts = [];
const responses = [];
let hanging = null;

function responseFor(spec) {
    return {
        ok: spec.status ? spec.status < 400 : true,
        status: spec.status || 200,
        json: async () => spec.body
    };
}

const context = {
    RESOURCES_API_URL: "https://example.test/resources",
    currentUser: {
        organization: { organization_id: "ORG-A" },
        location: { location_id: "LOC-1" }
    },
    resources: [],
    requests: [],
    allocations: [],
    resourceTypes: [],
    requestTypes: [],
    hosts,
    calls,
    toasts,
    console,
    URLSearchParams,
    document: {
        getElementById(id) {
            if (!hosts[id]) {
                hosts[id] = {
                    id,
                    value: "ALL",
                    innerHTML: "",
                    children: [],
                    classList: { add() {}, remove() {}, contains() { return false; } },
                    replaceChildren() { this.children = []; },
                    appendChild(child) { this.children.push(child); }
                };
            }
            return hosts[id];
        },
        createElement() {
            return {
                type: "button",
                className: "",
                textContent: "",
                disabled: false,
                listeners: {},
                addEventListener(name, handler) { this.listeners[name] = handler; },
                click() { return this.listeners.click(); }
            };
        }
    },
    $(id) {
        return context.document.getElementById(id);
    },
    getIdToken: () => "session",
    showToast(message) { toasts.push(message); },
    normalizeResources() {},
    updateDashboardStats() {},
    updateAnalytics() {},
    updateAdminDashboard() {},
    renderResourcesTable() {},
    renderResourcesPage() {},
    fillCatalogSelects() {},
    renderCatalogAdmin() {},
    renderRequests() {},
    renderAllocations() {},
    resourceRowActions(resource) { return resource.id; },
    isResourceAvailable() { return false; },
    fetch(url) {
        calls.push(String(url));
        if (hanging) {
            const pending = hanging;
            hanging = null;
            return pending;
        }
        const spec = responses.shift();
        assert.ok(spec, `unexpected request ${url}`);
        if (spec.error) {
            return Promise.reject(spec.error);
        }
        return Promise.resolve(responseFor(spec));
    }
};
context.document_hosts = hosts;
vm.createContext(context);

function install(name) {
    vm.runInContext(extractFunction(appSource, name), context);
}

install("selectedOrganizationId");
install("selectedLocationId");
install("tenantQuery");
install("getApiMessage");
vm.runInContext(pageBlock, context);
install("escapeHtml");
install("getOperationalStatusLabel");
install("getResourceStatus");
install("applyResourcePageFilters");
install("clearTenantData");

function query(url) {
    return new URLSearchParams(String(url).split("?")[1] || "");
}

function buttons() {
    return ["resourceLoadMore", "overviewResourceLoadMore"].map(id => hosts[id] && hosts[id].children[0]).filter(Boolean);
}

function resource(id, status) {
    return {
        id,
        type: "Bed",
        location: id === "R1" ? "North" : "South",
        operational_status: status,
        available: status === "AVAILABLE",
        visibility: "PRIVATE"
    };
}

const foreignToken = "eyJvIjoiT1JHLU9USEVSIn0.signature-part_1";
const secondToken = "page-two.token_value-kept";

responses.push({
    body: { resources: [resource("R1", "AVAILABLE")], next_token: foreignToken }
});
await context.loadResources();

const first = query(calls[0]);
assert.equal(first.get("limit"), "100");
assert.equal(first.has("page_token"), false);
assert.equal(first.get("organization_id"), "ORG-A");
assert.equal(first.get("location_id"), "LOC-1");
assert.equal(first.has("status"), false);
assert.equal(first.has("visibility"), false);
assert.equal(context.resources.map(item => item.id).join(","), "R1");
assert.equal(buttons().length, 2);
assert.equal(buttons().every(button => button.textContent === "Load more" && button.className === "secondary-btn resource-load-more-btn"), true);
assert.equal(toasts.some(message => message.includes(foreignToken)), false);

responses.push({
    body: { resources: [], next_token: secondToken }
});
await buttons()[0].click();
const emptyPage = query(calls[1]);
assert.equal(emptyPage.get("page_token"), foreignToken);
assert.equal(emptyPage.get("limit"), "100");
assert.equal(emptyPage.get("organization_id"), "ORG-A");
assert.equal(context.resources.map(item => item.id).join(","), "R1");
assert.equal(buttons().length, 2);

responses.push({
    body: { resources: [resource("R2", "ALLOCATED")], next_token: null }
});
await buttons()[0].click();
const third = query(calls[2]);
assert.equal(third.get("page_token"), secondToken);
assert.equal(context.resources.map(item => item.id).join(","), "R1,R2");
assert.equal(buttons().length, 0);

context.$("resourcesPageSearch").value = "";
context.$("resourcesPageFilter").value = "ALLOCATED";
context.$("resourcesPageTypeFilter").value = "ALL";
context.$("resourcesPageLocationFilter").value = "ALL";
context.$("resourcesPageVisibilityFilter").value = "ALL";
context.applyResourcePageFilters();
assert.equal(context.$("resourcesPageTable").innerHTML.includes("R2"), true);
assert.equal(context.$("resourcesPageTable").innerHTML.includes("R1"), false);
context.$("resourcesPageSearch").value = "north";
context.$("resourcesPageFilter").value = "ALL";
context.applyResourcePageFilters();
assert.equal(context.$("resourcesPageTable").innerHTML.includes("R1"), true);
assert.equal(context.$("resourcesPageTable").innerHTML.includes("R2"), false);

responses.push({
    body: { resources: [resource("R1", "AVAILABLE")], next_token: foreignToken }
});
await context.loadResources();
assert.equal(query(calls.at(-1)).has("page_token"), false);
assert.equal(context.resources.map(item => item.id).join(","), "R1");

let releaseHang;
hanging = new Promise(resolve => { releaseHang = resolve; });
const firstClick = buttons()[0].click();
const duplicate = context.loadResources({ append: true });
assert.equal(calls.length, 5);
releaseHang(responseFor({ body: { resources: [resource("R2", "ALLOCATED")], next_token: "page-three.token_value" } }));
await firstClick;
await duplicate;
assert.equal(calls.filter(url => query(url).get("page_token") === foreignToken).length, 2);
assert.equal(context.resources.map(item => item.id).join(","), "R1,R2");
assert.equal(buttons()[0].disabled, false);

responses.push({ status: 400, body: { message: "Invalid page token" } });
await buttons()[0].click();
assert.equal(context.resources.map(item => item.id).join(","), "R1,R2");
assert.equal(query(calls.at(-1)).get("page_token"), "page-three.token_value");
assert.equal(buttons().length, 0);
assert.equal(toasts.at(-1), "This resource page expired. Refresh to load the first page.");
assert.equal(toasts.at(-1).includes("page-three"), false);
const callsAfterInvalid = calls.length;
await context.loadResources({ append: true });
assert.equal(calls.length, callsAfterInvalid);

responses.push({
    body: { resources: [resource("R9", "AVAILABLE")], next_token: null }
});
await context.loadResources();
assert.equal(query(calls.at(-1)).has("page_token"), false);
assert.equal(context.resources.map(item => item.id).join(","), "R9");

responses.push({
    body: { resources: [resource("R1", "AVAILABLE")], next_token: foreignToken }
});
await context.loadResources();
responses.push({ status: 500, body: { message: "unavailable" } });
await buttons()[0].click();
assert.equal(context.resources.map(item => item.id).join(","), "R1");
assert.equal(query(calls.at(-1)).get("page_token"), foreignToken);
assert.equal(buttons().length, 2);
assert.equal(buttons()[0].disabled, false);
assert.equal(toasts.at(-1), "Unable to load more resources from AWS");

responses.push({ error: new Error("network") });
await buttons()[0].click();
assert.equal(context.resources.map(item => item.id).join(","), "R1");
assert.equal(query(calls.at(-1)).get("page_token"), foreignToken);
assert.equal(buttons().length, 2);

context.currentUser.location = { location_id: "LOC-2" };
responses.push({
    body: { resources: [resource("R3", "AVAILABLE")], next_token: null }
});
await context.loadResources({ append: true });
const locationReset = query(calls.at(-1));
assert.equal(locationReset.has("page_token"), false);
assert.equal(locationReset.get("location_id"), "LOC-2");
assert.equal(locationReset.get("organization_id"), "ORG-A");
assert.equal(context.resources.map(item => item.id).join(","), "R3");

responses.push({
    body: { resources: [resource("R1", "AVAILABLE")], next_token: foreignToken }
});
await context.loadResources();
context.currentUser.organization = { organization_id: "ORG-B" };
responses.push({
    body: { resources: [resource("R4", "AVAILABLE")], next_token: "org-b.token_value" }
});
await context.loadResources({ append: true });
const organizationReset = query(calls.at(-1));
assert.equal(organizationReset.has("page_token"), false);
assert.equal(organizationReset.get("organization_id"), "ORG-B");
assert.equal(organizationReset.get("page_token"), null);
assert.equal(context.resources.map(item => item.id).join(","), "R4");
responses.push({
    body: { resources: [resource("R4B", "ALLOCATED")], next_token: null }
});
await buttons()[0].click();
assert.equal(query(calls.at(-1)).get("page_token"), "org-b.token_value");
assert.equal(query(calls.at(-1)).get("organization_id"), "ORG-B");
assert.equal(context.resources.map(item => item.id).join(","), "R4,R4B");

context.clearTenantData();
assert.equal(context.resources.length, 0);
assert.equal(buttons().length, 0);
responses.push({
    body: { resources: [resource("R5", "AVAILABLE")], next_token: null }
});
await context.loadResources();
assert.equal(query(calls.at(-1)).has("page_token"), false);
assert.equal(query(calls.at(-1)).get("organization_id"), "ORG-B");

assert.equal(appSource.includes("function clearTenantData()") && appSource.includes("resetResourcePagination();"), true);
assert.equal(appSource.slice(appSource.indexOf("async function switchOrganization"), appSource.indexOf("async function switchLocation")).includes("clearTenantData();"), true);
assert.equal(appSource.slice(appSource.indexOf("async function switchLocation"), appSource.indexOf("async function createLocation")).includes("clearTenantData();"), true);
