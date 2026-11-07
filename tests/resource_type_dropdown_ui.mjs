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
    if (start >= 6 && source.slice(start - 6, start) === "async ") {
        start -= 6;
    }
    let depth = 0;
    let inSingle = false;
    let inDouble = false;
    let inTemplate = false;
    let escaped = false;
    for (let index = source.indexOf("{", start); index < source.length; index += 1) {
        const character = source[index];
        if (escaped) {
            escaped = false;
            continue;
        }
        if (character === "\\" && (inSingle || inDouble || inTemplate)) {
            escaped = true;
            continue;
        }
        if (!inDouble && !inTemplate && character === "'") {
            inSingle = !inSingle;
            continue;
        }
        if (!inSingle && !inTemplate && character === '"') {
            inDouble = !inDouble;
            continue;
        }
        if (!inSingle && !inDouble && character === "`") {
            inTemplate = !inTemplate;
            continue;
        }
        if (inSingle || inDouble || inTemplate) {
            continue;
        }
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

assert.equal(page.includes('id="registerModal"'), true);
assert.equal(page.includes('id="newResourceType"'), true);
assert.equal(page.includes('id="newResourceId"'), true);
assert.equal(page.includes('id="newResourceName"'), true);
assert.equal(page.includes('id="newResourceQuantity"'), true);
assert.equal(page.includes('id="newResourceQuantityUnit"'), true);
assert.equal(page.includes('id="newResourceVisibility"'), true);
assert.equal(page.includes('id="newResourcePublicName"'), true);
assert.equal(page.includes('id="newResourcePublicDescription"'), true);
assert.equal(page.includes('id="newResourcePublicContact"'), true);
assert.equal(page.includes('id="newResourceShowAvailability"'), true);
assert.equal(page.includes('id="newResourceLocation"'), true);
assert.equal(page.includes('id="saveResourceTypeBtn"'), true);
assert.equal(page.includes('id="resourceTypeName"'), true);
assert.equal(appSource.includes("async function saveResourceType("), true);
assert.equal(appSource.includes("RESOURCE_TYPES_API_URL"), true);
assert.equal(appSource.includes("No resource types in this organization"), true);
assert.equal(appSource.includes("Unable to load resource types"), true);
assert.equal(appSource.includes("Loading resource types"), true);
assert.equal(appSource.includes("method: \"POST\""), true);
assert.match(appSource, /fetch\(RESOURCE_TYPES_API_URL/);

const hosts = {};
function element(id, extra = {}) {
    hosts[id] = {
        id,
        value: extra.value || "",
        textContent: "",
        innerHTML: extra.innerHTML || "",
        disabled: false,
        hidden: false
    };
    return hosts[id];
}

element("newResourceType", { innerHTML: '<option value="">Select resource type</option>' });
element("newResourceTypeHint");
element("newResourceCategory", { value: "" });
element("saveResourceTypeBtn");

let organizationId = "ORG-A";
let fetchImpl = async () => {
    throw new Error("fetch not stubbed");
};

const context = vm.createContext({
    resourceTypes: [],
    resourceTypesLoadFailed: false,
    resourceTypesLoading: false,
    requestTypes: [],
    RESOURCE_TYPES_API_URL: "https://example.test/dev/resource-types",
    currentUser: { organization: { organization_id: "ORG-A", role: "ADMIN" } },
    fetch: (...args) => fetchImpl(...args),
    getIdToken: () => "id-token",
    waitForIdToken: async () => "id-token",
    tenantQuery: () => "?organization_id=ORG-A",
    selectedOrganizationId: () => organizationId,
    canManageCatalog: () => true,
    renderCatalogAdmin: () => {},
    showSelectedTypeAttributes: () => {},
    escapeHtml: undefined,
    catalogLoadError: undefined,
    parseCatalogTypes: undefined,
    loadCatalog: undefined,
    loadResourceTypes: undefined,
    fillCatalogSelects: undefined,
    $: (id) => hosts[id] || null
});

vm.runInContext(
    [
        extractFunction(appSource, "escapeHtml"),
        extractFunction(appSource, "catalogLoadError"),
        extractFunction(appSource, "parseCatalogTypes"),
        extractFunction(appSource, "fillCatalogSelects"),
        extractFunction(appSource, "loadCatalog"),
        extractFunction(appSource, "loadResourceTypes")
    ].join("\n"),
    context
);

function optionLabels() {
    return [...hosts.newResourceType.innerHTML.matchAll(/<option[^>]*>([^<]*)<\/option>/g)].map((match) => match[1]);
}

assert.equal(JSON.stringify(context.parseCatalogTypes({ types: [], next_token: null })), "[]");
assert.throws(() => context.parseCatalogTypes({ items: [] }), /Unable to load resource types/);
assert.throws(() => context.parseCatalogTypes(null), /Unable to load resource types/);
assert.throws(() => context.parseCatalogTypes([]), /Unable to load resource types/);

organizationId = "ORG-A";
context.resourceTypes = [
    { resource_type_id: "RT-1", name: "Ambulance", status: "ACTIVE" },
    { resource_type_id: "RT-2", name: "Retired van", status: "INACTIVE" }
];
context.resourceTypesLoadFailed = false;
context.resourceTypesLoading = false;
context.fillCatalogSelects();
assert.equal(hosts.newResourceType.disabled, false);
assert.deepEqual(optionLabels(), ["Select resource type", "Ambulance"]);
assert.match(hosts.newResourceType.innerHTML, /value="RT-1"/);
assert.equal(hosts.newResourceType.innerHTML.includes("Retired van"), false);

context.resourceTypes = [
    { resource_type_id: "RT-3", name: "Oxygen cylinder" },
    { resource_type_id: "RT-4", name: "Spare radio", status: "inactive" }
];
context.fillCatalogSelects();
assert.deepEqual(optionLabels(), ["Select resource type", "Oxygen cylinder"]);
assert.match(hosts.newResourceType.innerHTML, /value="RT-3"/);

context.resourceTypes = [
    { resource_type_id: "RT-9", name: "Inactive only", status: "INACTIVE" }
];
context.fillCatalogSelects();
assert.deepEqual(optionLabels(), ["No resource types in this organization"]);

context.resourceTypes = [];
context.resourceTypesLoading = true;
context.resourceTypesLoadFailed = false;
context.fillCatalogSelects();
assert.equal(hosts.newResourceType.disabled, true);
assert.deepEqual(optionLabels(), ["Loading resource types"]);

context.resourceTypesLoading = false;
context.resourceTypesLoadFailed = true;
context.fillCatalogSelects();
assert.equal(hosts.newResourceType.disabled, false);
assert.deepEqual(optionLabels(), ["Unable to load resource types"]);
assert.equal(hosts.newResourceType.innerHTML.includes("statusCode"), false);
assert.equal(hosts.newResourceTypeHint.textContent.includes("500"), false);

fetchImpl = async (url, options) => {
    assert.match(String(url), /resource-types\?organization_id=ORG-A/);
    assert.equal(options.headers.Authorization, "Bearer id-token");
    return {
        ok: true,
        json: async () => ({
            types: [
                { resource_type_id: "RT-A", name: "Water tanker", status: "ACTIVE" },
                { resource_type_id: "RT-B", name: "Old radio", status: "INACTIVE" }
            ],
            next_token: null
        })
    };
};
await context.loadResourceTypes();
assert.equal(context.resourceTypesLoadFailed, false);
assert.equal(context.resourceTypesLoading, false);
assert.equal(context.resourceTypes[0].resource_type_id, "RT-A");
assert.deepEqual(optionLabels(), ["Select resource type", "Water tanker"]);
hosts.newResourceType.value = "RT-A";
context.fillCatalogSelects();
assert.equal(hosts.newResourceType.value, "RT-A");
assert.deepEqual(optionLabels(), ["Select resource type", "Water tanker"]);

fetchImpl = async () => ({
    ok: true,
    json: async () => ({ types: [], next_token: null })
});
await context.loadResourceTypes();
assert.equal(context.resourceTypesLoadFailed, false);
assert.deepEqual(optionLabels(), ["No resource types in this organization"]);
assert.equal(hosts.newResourceTypeHint.textContent.includes("Management"), true);

fetchImpl = async () => ({
    ok: false,
    status: 500,
    json: async () => ({ message: "table ResourceTypes is missing" })
});
await context.loadResourceTypes();
assert.equal(context.resourceTypesLoadFailed, true);
assert.equal(JSON.stringify(context.resourceTypes), "[]");
assert.deepEqual(optionLabels(), ["Unable to load resource types"]);
assert.equal(hosts.newResourceType.innerHTML.includes("ResourceTypes"), false);
assert.equal(hosts.newResourceTypeHint.textContent.includes("ResourceTypes"), false);

fetchImpl = async () => {
    throw new TypeError("Failed to fetch");
};
await context.loadResourceTypes();
assert.equal(context.resourceTypesLoadFailed, true);
assert.deepEqual(optionLabels(), ["Unable to load resource types"]);

fetchImpl = async () => ({
    ok: true,
    json: async () => ({ items: [{ resource_type_id: "RT-X", name: "Hidden", status: "ACTIVE" }] })
});
await context.loadResourceTypes();
assert.equal(context.resourceTypesLoadFailed, true);
assert.equal(JSON.stringify(context.resourceTypes), "[]");
assert.deepEqual(optionLabels(), ["Unable to load resource types"]);
assert.equal(hosts.newResourceType.innerHTML.includes("Hidden"), false);

assert.equal(page.includes('id="saveResourceTypeBtn"'), true);
assert.match(appSource, /async function saveResourceType\(/);
assert.match(appSource, /method:\s*"POST"/);
assert.equal(hosts.saveResourceTypeBtn.hidden, false);

console.log("resource type dropdown ui passed");
