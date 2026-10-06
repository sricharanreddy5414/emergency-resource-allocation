import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const appSource = fs.readFileSync(path.join(root, "frontend", "app.js"), "utf8");
const start = appSource.indexOf("function isResourceAvailable(");
const end = appSource.indexOf("function updateAnalytics(", start);
assert.notEqual(start, -1);
assert.notEqual(end, -1);

const context = {};
vm.createContext(context);
vm.runInContext(appSource.slice(start, end), context);

assert.equal(
    context.isResourceAvailable({
        operational_status: "AVAILABLE",
        available: false,
    }),
    true,
    "a released resource with operational status AVAILABLE is ready"
);

assert.equal(
    context.isResourceAvailable({
        operational_status: "ALLOCATED",
        available: true,
    }),
    false,
    "an allocated operational status is not ready"
);

assert.equal(
    context.isResourceAvailable({
        operational_status: "RESERVED",
        available: true,
    }),
    false
);

assert.equal(context.isResourceAvailable({ available: true }), true);
assert.equal(context.isResourceAvailable({ available: false }), false);
assert.equal(context.isResourceAvailable({ available: "true" }), true);

assert.equal(
    context.isResourceAvailable({ operational_status: "RETIRED", available: false }),
    false
);
assert.equal(
    context.isResourceAllocated({ operational_status: "RETIRED", available: false }),
    false,
    "a retired resource is not allocated"
);
assert.equal(
    context.isResourceAllocated({ operational_status: "ALLOCATED", available: false }),
    true
);
assert.equal(
    context.isResourceAllocated({ operational_status: "AVAILABLE", available: true }),
    false
);
assert.equal(context.isResourceAllocated({ available: false }), true);
