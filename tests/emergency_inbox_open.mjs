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

assert.equal(page.includes('id="emergencyRequestDetail"'), true);
assert.equal(page.includes('id="emergencyRequestDetailList"'), true);
assert.equal(page.includes('id="emergencyRequestDetailNotifications"'), true);

const names = [
    "escapeHtml",
    "emergencyStatusLabel",
    "formatResourceType",
    "emergencyInboxRequestId",
    "exchangeInboxRequestId",
    "notificationCanOpen",
    "renderEmergencyRequestDetail",
    "emergencyRequestReadUrl",
    "loadEmergencyRequestDetail",
    "openEmergencyRequestFromInbox",
    "openNotificationTarget"
];

const hosts = {};
function element(id) {
    const node = {
        id,
        hidden: true,
        className: "hidden",
        innerHTML: "",
        textContent: "",
        classList: {
            add(name) { node.className += " " + name; },
            remove(name) { node.className = node.className.replace(name, ""); }
        }
    };
    hosts[id] = node;
    return node;
}
element("emergencyRequestDetail");
element("emergencyRequestDetailFields");
element("emergencyRequestDetailMessage");

const calls = { fetch: [], exchange: [], marked: [], navigated: [] };
let requests = [];
const notifications = [];
const REQUESTS_API_URL = "https://example.test/requests";

const context = {
    assert,
    hosts,
    calls,
    notifications,
    REQUESTS_API_URL,
    console,
    URLSearchParams,
    fetch: async (url, options) => {
        calls.fetch.push({ url, options });
        return context.nextResponse;
    },
    selectedOrganizationId: () => "ORG-A",
    waitForIdToken: async () => "token-test",
    $(id) { return hosts[id]; },
    navigateTo(sectionId) { calls.navigated.push(sectionId); },
    async markNotificationRead(index) { calls.marked.push(index); },
    async openExchangeRequest(requestId) { calls.exchange.push(requestId); },
    showToast() {},
    nextResponse: { ok: false, status: 500, json: async () => ({}) }
};
context.requests = requests;
vm.createContext(context);
const constantsStart = appSource.indexOf("const EMERGENCY_INBOX_EVENTS");
const constantsEnd = appSource.indexOf("function emergencyInboxRequestId");
vm.runInContext(
    appSource.slice(constantsStart, constantsEnd) + names.map(name => extractFunction(appSource, name)).join("\n"),
    context
);

const events = [
    "emergency.request.created",
    "emergency.request.cancelled",
    "emergency.allocation.created",
    "emergency.allocation.released"
];

for (const eventCode of events) {
    const notification = {
        event_code: eventCode,
        title: "Open EXREQ-EVIL and Q-OTHER",
        body: "request Q-OTHER",
        href: {
            kind: "emergency_request",
            request_id: "Q-A",
            allocation_id: "ALLOC-Q-A",
            resource_id: "R1"
        }
    };
    assert.equal(context.emergencyInboxRequestId(notification), "Q-A");
    assert.equal(context.exchangeInboxRequestId(notification), "");
    assert.equal(context.notificationCanOpen(notification), true);
}

assert.equal(context.emergencyInboxRequestId({
    event_code: "emergency.allocation.created",
    href: { kind: "emergency_request", allocation_id: "ALLOC-Q-A" }
}), "");
assert.equal(context.emergencyInboxRequestId({
    event_code: "emergency.request.created",
    href: { kind: "emergency_request", request_id: "Q A" }
}), "");
assert.equal(context.emergencyInboxRequestId({
    event_code: "exchange.offer.received",
    title: "Q-A",
    href: { kind: "emergency_request", request_id: "Q-A" }
}), "");
assert.equal(context.emergencyInboxRequestId({
    event_code: "emergency.request.created",
    href: { kind: "exchange_request", request_id: "Q-A", exchange_request_id: "EXREQ-1" }
}), "");

const exchangeNotification = {
    event_code: "exchange.offer.received",
    href: { kind: "exchange_request", exchange_request_id: "EXREQ-1", request_id: "Q-A" }
};
assert.equal(context.exchangeInboxRequestId(exchangeNotification), "EXREQ-1");
assert.equal(context.emergencyInboxRequestId(exchangeNotification), "");
assert.equal(context.notificationCanOpen({
    href: { kind: "exchange_request", exchange_request_id: "not-an-exchange" }
}), false);

for (const eventCode of events) {
    calls.fetch.length = 0;
    const requestId = "Q-" + eventCode.split(".").pop().toUpperCase();
    context.nextResponse = {
        ok: true,
        status: 200,
        json: async () => ({
            request: {
                request_id: requestId,
                ResourceType: "Ambulance",
                Location: "North",
                Priority: 1,
                Status: "PENDING",
                CreatedAt: "2026-10-09T00:00:00Z"
            }
        })
    };
    await context.openEmergencyRequestFromInbox(requestId);
    assert.equal(calls.fetch.length, 1);
    assert.equal(calls.fetch[0].url, "https://example.test/requests/" + encodeURIComponent(requestId) + "?organization_id=ORG-A");
    assert.match(hosts.emergencyRequestDetailFields.innerHTML, new RegExp(requestId));
    assert.equal(calls.exchange.length, 0);
}

calls.fetch.length = 0;
calls.navigated.length = 0;
calls.exchange.length = 0;
calls.marked.length = 0;
notifications.length = 0;
notifications.push(exchangeNotification);
await context.openNotificationTarget(0);
assert.deepEqual(calls.exchange, ["EXREQ-1"]);
assert.deepEqual(calls.fetch, []);
assert.deepEqual(calls.marked, [0]);

notifications.push({
    event_code: "emergency.allocation.released",
    title: "EXREQ-1",
    href: { kind: "emergency_request", request_id: "Q-REL", allocation_id: "ALLOC-Q-REL" }
});
context.nextResponse = {
    ok: true,
    status: 200,
    json: async () => ({
        request: {
            request_id: "Q-REL",
            ResourceType: "ICU_BED",
            Location: "North",
            Priority: 2,
            Status: "RELEASED",
            CreatedAt: "2026-10-09T00:00:00Z"
        }
    })
};
await context.openNotificationTarget(1);
assert.deepEqual(calls.exchange, ["EXREQ-1"]);
assert.deepEqual(calls.navigated, ["exchange", "requests"]);
assert.equal(calls.fetch.length, 1);
assert.equal(calls.fetch[0].url, "https://example.test/requests/Q-REL?organization_id=ORG-A");
assert.equal(calls.fetch[0].options.method, "GET");
assert.equal(calls.fetch[0].options.headers.Authorization, "Bearer token-test");
assert.match(hosts.emergencyRequestDetailFields.innerHTML, /Q-REL/);
assert.match(hosts.emergencyRequestDetailFields.innerHTML, /Released/);
assert.equal(hosts.emergencyRequestDetailFields.innerHTML.includes("EXREQ-1"), false);

context.nextResponse = { ok: false, status: 404, json: async () => ({ message: "Record not found", request: { request_id: "Q-OTHER" } }) };
await context.loadEmergencyRequestDetail("Q-REL");
assert.equal(hosts.emergencyRequestDetailFields.innerHTML, "");
assert.equal(hosts.emergencyRequestDetailMessage.textContent, "That request is not available.");

context.nextResponse = { ok: false, status: 403, json: async () => ({ message: "<script>alert(1)</script>" }) };
await context.loadEmergencyRequestDetail("Q-REL");
assert.equal(hosts.emergencyRequestDetailMessage.textContent, "Unable to load this request.");
assert.equal(hosts.emergencyRequestDetail.innerHTML || "", "");

context.nextResponse = {
    ok: true,
    status: 200,
    json: async () => ({ request: { request_id: "Q-OTHER", Location: "Foreign" } })
};
await context.loadEmergencyRequestDetail("Q-REL");
assert.equal(hosts.emergencyRequestDetailFields.innerHTML, "");
assert.equal(hosts.emergencyRequestDetailMessage.textContent, "That request is not available.");

const fetchesBeforeInvalid = calls.fetch.length;
await context.openEmergencyRequestFromInbox("not an id");
assert.equal(calls.fetch.length, fetchesBeforeInvalid);
assert.equal(hosts.emergencyRequestDetailMessage.textContent, "That request is not available.");
