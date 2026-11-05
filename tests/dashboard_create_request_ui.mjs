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

const dashboard = page.slice(page.indexOf('id="dashboard"'), page.indexOf('id="resources"'));
assert.equal(dashboard.includes('id="overviewCreateRequest"'), true);
assert.equal((dashboard.match(/Create request/g) || []).length, 1);
assert.equal(dashboard.includes("Create Request"), false);
assert.equal(/id="activeOperations"[\s\S]*Create [Rr]equest/.test(dashboard), false);

const names = [
    "escapeHtml",
    "emergencyStatusLabel",
    "requestRecordStatus",
    "withoutCreateRequestButtons",
    "renderActiveOperations"
];

const hosts = {};
function element(id) {
    hosts[id] = { id, innerHTML: "" };
    return hosts[id];
}
element("activeOperations");

const context = {
    requests: [],
    $: (id) => hosts[id] || null,
    escapeHtml: undefined,
    emergencyStatusLabel: undefined,
    requestRecordStatus: undefined,
    withoutCreateRequestButtons: undefined,
    renderActiveOperations: undefined
};

vm.createContext(context);
vm.runInContext(names.map((name) => extractFunction(appSource, name)).join("\n"), context);

context.requests = [];
context.renderActiveOperations();
assert.match(hosts.activeOperations.innerHTML, /No active operations/);
assert.equal(/Create\s+Request/i.test(hosts.activeOperations.innerHTML), false);
assert.equal(hosts.activeOperations.innerHTML.includes("<button"), false);

const stripped = context.withoutCreateRequestButtons(
    `<div class="ops-empty"><h3>No active operations</h3><button class="primary-btn js-open-request" type="button">Create Request</button></div>`
);
assert.equal(/Create\s+Request/i.test(stripped), false);
assert.equal(stripped.includes("<button"), false);

context.requests = [{
    request_id: "REQ-P",
    Status: "PENDING",
    Priority: 1,
    ResourceType: "Pending Type",
    Location: "West"
}];
context.renderActiveOperations();
assert.equal(hosts.activeOperations.innerHTML.includes("Pending Type"), true);
assert.equal(/Create\s+Request/i.test(hosts.activeOperations.innerHTML), false);
assert.equal(hosts.activeOperations.innerHTML.includes("js-open-request"), false);

console.log("dashboard create request ui passed");
