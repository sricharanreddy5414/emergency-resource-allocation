import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const appSource = fs.readFileSync(path.join(root, "frontend", "app.js"), "utf8");
const page = fs.readFileSync(path.join(root, "frontend", "index.html"), "utf8");

assert.equal(page.includes('id="emergencyRequestApproveBtn"'), true);
assert.equal(page.includes('id="emergencyRequestRejectBtn"'), true);
assert.equal(page.includes('id="emergencyRequestCompleteBtn"'), true);
assert.equal(page.includes("Pending approval"), true);
assert.equal(page.includes('id="modalRequestDescription"'), true);
assert.equal(appSource.includes('REQUESTS_API_URL + "/approve"') || appSource.includes('postEmergencyRequestAction("/approve"'), true);
assert.equal(appSource.includes('postEmergencyRequestAction("/reject"'), true);
assert.equal(appSource.includes('postEmergencyRequestAction("/complete"'), true);
assert.equal(appSource.includes("Partial allocation is not supported"), true);
assert.equal(appSource.includes("js-view-request"), true);
assert.equal(appSource.includes("Approval does not allocate inventory"), true);

console.log("request lifecycle ui passed");
