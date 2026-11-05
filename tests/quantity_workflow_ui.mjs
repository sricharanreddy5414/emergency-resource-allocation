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

const modal = page.slice(page.indexOf('id="requestModal"'), page.indexOf('id="qrScanModal"'));
assert.equal(modal.includes('id="modalRequestedResource"'), true);
assert.equal(modal.includes('id="modalRequestedQuantity"'), true);
assert.equal(page.includes("Total Quantity"), true);
assert.equal(page.includes("Available Quantity"), true);

const submit = appSource.slice(appSource.indexOf("async function submitRequestModal"), appSource.indexOf("async function submitRequestModal") + 4500);
assert.equal(submit.includes("resource_id: quantityFields.resourceId"), true);
assert.equal(submit.includes("quantity_requested: quantityFields.quantity"), true);
assert.equal(appSource.includes("quantityDisplay(resource, \"quantity_total\")"), true);

const hosts = {};
function element(id, extra = {}) {
    hosts[id] = {
        id,
        value: extra.value || "",
        innerHTML: extra.innerHTML || "",
        textContent: extra.textContent || "",
        hidden: extra.hidden !== false
    };
    return hosts[id];
}

element("modalLocation", { value: "LOC-A" });
element("modalRequestedResource");
element("modalRequestedQuantity", { value: "1" });
element("modalRequestedQuantityUnit", { textContent: "Units" });
element("modalRequestedQuantityError");
element("modalRequestedResourceHint");
element("resourcesPageTable");

const context = {
    resources: [
        {
            id: "POOL-1",
            name: "Water",
            type: "Water",
            location_id: "LOC-A",
            tracking_mode: "QUANTITY",
            quantity_total: 10,
            quantity_available: 6,
            quantity_reserved: 1,
            quantity_allocated: 3,
            quantity_unit: "LITERS"
        },
        {
            id: "R-LEGACY",
            name: "Radio",
            type: "Radio",
            location_id: "LOC-A",
            tracking_mode: "INDIVIDUAL"
        }
    ],
    $: (id) => hosts[id] || null,
    escapeHtml: undefined,
    isQuantityResource: undefined,
    quantityDisplay: undefined,
    quantityUnitDisplay: undefined,
    requestedResourceOptions: undefined,
    fillRequestResourceSelect: undefined,
    selectedRequestResource: undefined,
    updateRequestQuantityHint: undefined,
    readRequestedQuantityFields: undefined
};

vm.createContext(context);
vm.runInContext(
    [
        "escapeHtml",
        "isQuantityResource",
        "quantityDisplay",
        "quantityUnitDisplay",
        "requestedResourceOptions",
        "fillRequestResourceSelect",
        "selectedRequestResource",
        "updateRequestQuantityHint",
        "readRequestedQuantityFields"
    ].map((name) => extractFunction(appSource, name)).join("\n"),
    context
);

context.fillRequestResourceSelect();
assert.equal(hosts.modalRequestedResource.innerHTML.includes("POOL-1"), true);
assert.equal(hosts.modalRequestedResource.innerHTML.includes("Water"), true);
assert.equal(hosts.modalRequestedResource.innerHTML.includes("6 LITERS available"), true);
assert.equal(hosts.modalRequestedResource.innerHTML.includes("R-LEGACY"), true);

hosts.modalRequestedResource.value = "POOL-1";
hosts.modalRequestedQuantity.value = "4";
const valid = context.readRequestedQuantityFields();
assert.equal(valid.resourceId, "POOL-1");
assert.equal(valid.quantity, 4);
assert.equal(valid.unit, "LITERS");

hosts.modalRequestedQuantity.value = "9";
assert.equal(context.readRequestedQuantityFields(), null);
assert.match(hosts.modalRequestedQuantityError.textContent, /available quantity/);

assert.equal(context.quantityDisplay(context.resources[0], "quantity_total"), "10");
assert.equal(context.quantityDisplay(context.resources[0], "quantity_available"), "6");
assert.equal(context.quantityUnitDisplay(context.resources[0]), "LITERS");
assert.equal(context.quantityDisplay(context.resources[1], "quantity_total"), "—");
assert.equal(context.quantityUnitDisplay(context.resources[1]), "—");

console.log("quantity workflow ui passed");
