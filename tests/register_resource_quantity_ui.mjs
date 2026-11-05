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

const typeIndex = page.indexOf('id="newResourceType"');
const visibilityIndex = page.indexOf('id="newResourceVisibility"');
assert.ok(typeIndex > 0);
assert.ok(visibilityIndex > typeIndex);
const between = page.slice(typeIndex, visibilityIndex);
assert.equal(between.includes('id="newResourceQuantity"'), true);
assert.equal(between.includes('id="newResourceQuantityUnit"'), true);
assert.equal(between.includes(">Units<"), true);
assert.equal(between.includes(">Boxes<"), true);
assert.equal(between.includes(">Packets<"), true);
assert.equal(between.includes(">Liters<"), true);
assert.equal(between.includes(">Vehicles<"), true);
assert.equal(page.includes('id="newResourceQuantityError"'), true);

assert.equal(appSource.includes("quantity_total = quantityFields.quantity"), true);
assert.equal(appSource.includes('tracking_mode = "QUANTITY"'), true);
assert.equal(appSource.includes("quantity_unit: quantityFields.unit"), true);

const hosts = {};
function element(id, extra = {}) {
    hosts[id] = {
        id,
        value: extra.value || "",
        textContent: "",
        hidden: true
    };
    return hosts[id];
}

element("newResourceQuantity", { value: "1" });
element("newResourceQuantityUnit", { value: "UNITS" });
element("newResourceQuantityError");

const context = {
    RESOURCE_QUANTITY_UNITS: undefined,
    setResourceQuantityError: undefined,
    readResourceQuantityFields: undefined,
    $: (id) => hosts[id] || null
};
vm.createContext(context);
vm.runInContext(
    [
        "RESOURCE_QUANTITY_UNITS",
        "setResourceQuantityError",
        "readResourceQuantityFields"
    ].map((name) => {
        if (name === "RESOURCE_QUANTITY_UNITS") {
            const start = appSource.indexOf("const RESOURCE_QUANTITY_UNITS");
            const end = appSource.indexOf(";", start);
            return appSource.slice(start, end + 1);
        }
        return extractFunction(appSource, name);
    }).join("\n"),
    context
);

hosts.newResourceQuantity.value = "1";
hosts.newResourceQuantityUnit.value = "BOXES";
const valid = context.readResourceQuantityFields();
assert.equal(valid.quantity, 1);
assert.equal(valid.unit, "BOXES");
assert.equal(hosts.newResourceQuantityError.hidden, true);

hosts.newResourceQuantity.value = "0";
assert.equal(context.readResourceQuantityFields(), null);
assert.match(hosts.newResourceQuantityError.textContent, /at least 1/);

hosts.newResourceQuantity.value = "2.5";
assert.equal(context.readResourceQuantityFields(), null);

hosts.newResourceQuantity.value = "4";
hosts.newResourceQuantityUnit.value = "";
assert.equal(context.readResourceQuantityFields(), null);
assert.match(hosts.newResourceQuantityError.textContent, /unit/i);

console.log("register resource quantity ui passed");
