import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const appSource = fs.readFileSync(path.join(root, "frontend", "app.js"), "utf8");

function makeElement() {
    return { textContent: "", classList: { add() {}, remove() {} } };
}

const elements = {
    commandContext: makeElement(),
    operationalContext: makeElement(),
};

const document = {
    getElementById(id) {
        return elements[id] || null;
    },
    querySelector() {
        return null;
    },
    addEventListener() {},
};

const sessionStorage = {
    store: new Map(),
    getItem(key) {
        return this.store.has(key) ? this.store.get(key) : null;
    },
    setItem(key, value) {
        this.store.set(key, String(value));
    },
    removeItem(key) {
        this.store.delete(key);
    },
};

const context = {
    console,
    setTimeout,
    clearTimeout,
    URLSearchParams,
    encodeURIComponent,
    sessionStorage,
    document,
    location: { origin: "https://example.test", href: "https://example.test/" },
    fetch() {
        return {
            ok: true,
            async json() {
                return {
                    locations: [
                        { location_id: "LOC-6ABA6BDF2CB4", name: "Beta Test Location" },
                    ],
                };
            },
        };
    },
};
context.window = context;
context.globalThis = context;

const probe = `
waitForIdToken = async () => "token";
clearTenantData = () => {};
loadResources = async () => {};
loadRequests = async () => {};
loadAllocations = async () => {};

function heading() {
    return document.getElementById("commandContext").textContent;
}

currentUser.organization = {
    organization_id: "ORG-4708B62B1B9C",
    name: "ERAP First User Beta",
};
currentUser.locations = [
    { location_id: "LOC-6ABA6BDF2CB4", name: "Beta Test Location" },
];

currentUser.location = { location_id: "ALL", name: "All locations" };
refreshShellContext();
if (heading() !== "ERAP First User Beta · All locations") {
    throw new Error("all-location heading was " + heading());
}
if (document.getElementById("operationalContext").textContent !== heading()) {
    throw new Error("operational heading diverged");
}

currentUser.location = { location_id: "LOC-6ABA6BDF2CB4", name: "Beta Test Location" };
refreshShellContext();
if (heading() !== "ERAP First User Beta · Beta Test Location") {
    throw new Error("selected-location heading was " + heading());
}

currentUser.location = null;
refreshShellContext();
if (heading() !== "ERAP First User Beta · All locations") {
    throw new Error("missing-location heading was " + heading());
}

currentUser.location = { location_id: "ALL", name: "All locations" };
refreshShellContext();
await switchLocation("LOC-6ABA6BDF2CB4");
if (heading() !== "ERAP First User Beta · Beta Test Location") {
    throw new Error("switch left a stale heading: " + heading());
}

await switchLocation("ALL");
if (heading() !== "ERAP First User Beta · All locations") {
    throw new Error("all-locations switch left " + heading());
}

currentUser.location = { location_id: "ALL", name: "All locations" };
refreshShellContext();
await loadLocations();
if (heading() !== "ERAP First User Beta · Beta Test Location") {
    throw new Error("loaded location left a stale heading: " + heading());
}
`;

vm.createContext(context);
vm.runInContext(
    appSource + "\nglobalThis.__headingProbe = (async () => {\n" + probe + "\n})();\n",
    context,
    { filename: "frontend/app.js" }
);
await context.__headingProbe;
