import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const appSource = fs.readFileSync(path.join(root, "frontend", "app.js"), "utf8");
const htmlSource = fs.readFileSync(path.join(root, "frontend", "index.html"), "utf8");

function jwt(payload) {
    const body = Buffer.from(JSON.stringify(payload)).toString("base64url");
    return `header.${body}.signature`;
}

function makeElement(id, className = "") {
    const element = {
        id,
        className,
        hidden: false,
        textContent: "",
        value: "",
        dataset: {},
        children: [],
        listeners: {},
        focus() {},
        addEventListener(type, fn) {
            element.listeners[type] = element.listeners[type] || [];
            element.listeners[type].push(fn);
        },
        appendChild(child) {
            element.children.push(child);
            return child;
        }
    };
    Object.defineProperty(element, "options", { get() { return element.children; } });
    element.classList = {
        add(...names) {
            const set = new Set(element.className.split(/\s+/).filter(Boolean));
            names.forEach(name => set.add(name));
            element.className = [...set].join(" ");
        },
        remove(...names) {
            const set = new Set(element.className.split(/\s+/).filter(Boolean));
            names.forEach(name => set.delete(name));
            element.className = [...set].join(" ");
        },
        contains(name) {
            return element.className.split(/\s+/).includes(name);
        }
    };
    Object.defineProperty(element, "innerHTML", {
        get() {
            return element._html || "";
        },
        set(value) {
            element._html = String(value);
        }
    });
    return element;
}

function loadApp() {
    const ids = [
        "organizationModal",
        "organizationModalTitle",
        "organizationModalDescription",
        "pendingInvitations",
        "myInvitations",
        "organizationCreateFields",
        "organizationNotNowBtn",
        "showOrganizationCreateBtn",
        "organizationModalClose",
        "organizationOnboardingBtn",
        "organizationName",
        "createOrganizationBtn",
        "organizationSwitcher",
        "organizationMembersPanel",
        "organizationMembers",
        "organizationPendingInvitations",
        "removeMemberModal",
        "removeMemberClose",
        "removeMemberCancel",
        "removeMemberConfirm",
        "removeMemberEmail",
        "removeMemberOrganization",
        "inviteMemberForm"
    ];
    const elements = new Map(ids.map(id => [id, makeElement(id, id.endsWith("Modal") ? "modal hidden" : "")]));
    const calls = [];
    const document = {
        listeners: {},
        getElementById(id) {
            return elements.get(id) || null;
        },
        createElement() {
            return makeElement("");
        },
        addEventListener(type, fn) {
            document.listeners[type] = document.listeners[type] || [];
            document.listeners[type].push(fn);
        },
        dispatchEvent(event) {
            (document.listeners[event.type] || []).forEach(fn => fn(event));
        }
    };
    const context = {
        console,
        setTimeout,
        clearTimeout,
        URLSearchParams,
        atob,
        TextDecoder,
        encodeURIComponent,
        crypto: { randomUUID: () => "11111111-1111-1111-1111-111111111111" },
        sessionStorage: {
            store: new Map(),
            getItem(key) { return this.store.has(key) ? this.store.get(key) : null; },
            setItem(key, value) { this.store.set(key, String(value)); },
            removeItem(key) { this.store.delete(key); }
        },
        document,
        location: { origin: "https://example.test", search: "", href: "https://example.test/" },
        confirm() { return false; },
        fetch(input, options = {}) {
            const url = String(input);
            const call = { url, method: options.method || "GET", body: options.body || "" };
            calls.push(call);
            if (call.method === "POST") {
                return jsonResponse({ message: "Membership updated" });
            }
            return jsonResponse({ members: [], pending_invitations: [] });
        }
    };
    context.window = context;
    context.globalThis = context;
    vm.createContext(context);
    vm.runInContext(appSource, context, { filename: "frontend/app.js" });
    context.initializeEvents();
    context.applyOrganizationContext([{
        organization_id: "ORG-D13B30D99127",
        name: "ERAP Pilot Operations",
        role: "OWNER",
        status: "ACTIVE"
    }]);
    return { context, elements, calls };
}

function jsonResponse(body, status = 200) {
    return {
        ok: status >= 200 && status < 300,
        status,
        async json() {
            return body;
        }
    };
}

function members() {
    return [
        {
            user_sub: "803c69fc-5031-70b9-318c-5cf44472cbc7",
            email: "saisanath007@gmail.com",
            role: "ADMIN",
            status: "ACTIVE"
        },
        {
            user_sub: "invite-c4a4ff2b227b497a4d6c6853f71df375",
            email: "saisanath007@gmail.com",
            role: "ADMIN",
            status: "INACTIVE"
        },
        {
            user_sub: "member-sub",
            email: "member@example.com",
            role: "MEMBER",
            status: "INACTIVE"
        },
        {
            user_sub: "owner-sub",
            email: "owner@example.com",
            role: "OWNER",
            status: "ACTIVE"
        }
    ];
}

function tokenFor(subject) {
    const body = Buffer.from(JSON.stringify({
        exp: Math.floor(Date.now() / 1000) + 3600,
        sub: subject
    })).toString("base64url");
    return `header.${body}.signature`;
}

function testDialogMarkup() {
    assert.match(htmlSource, /id="removeMemberModal"/);
    assert.match(htmlSource, /Remove Member\?/);
    assert.match(htmlSource, /They will lose access to this organization\./);
    assert.match(htmlSource, /id="removeMemberCancel"/);
    assert.match(htmlSource, /id="removeMemberConfirm"/);
    assert.equal(htmlSource.includes("alert("), false);
}

function testMemberTableOmitsInvitationHistory() {
    const app = loadApp();
    app.context.renderMemberRows(members());
    app.context.renderPendingOrganizationInvitations([
        { email: "campusdiaries9245@gmail.com", role: "OPERATOR", status: "PENDING", user_sub: "invite-secret" }
    ]);
    const table = app.elements.get("organizationMembers").innerHTML;
    const pending = app.elements.get("organizationPendingInvitations").innerHTML;

    const rows = table.split("<tr>").slice(1).filter(row => row.includes("<td>"));
    assert.equal(rows.length, 3);
    assert.equal(rows.filter(row => row.includes("saisanath007@gmail.com")).length, 1);
    assert.match(table, /class="primary-btn remove-member"/);
    assert.match(table, /Reactivate/);
    assert.doesNotMatch(table, /invite-/);
    assert.doesNotMatch(table, /Deactivate/);
    assert.doesNotMatch(table, /Cancel invitation/);
    assert.match(table, /Save role/);
    assert.doesNotMatch(table, /remove-member" data-user-sub="owner-sub"/);
    assert.match(pending, /campusdiaries9245@gmail\.com/);
    assert.match(pending, /PENDING/);
    assert.match(pending, /data-email="campusdiaries9245@gmail\.com"/);
    assert.doesNotMatch(pending, /invite-/);
}

function testOwnerControlsMatchTheApi() {
    const app = loadApp();
    app.context.sessionStorage.setItem("erap_id_token", tokenFor("owner-sub"));
    app.context.renderMemberRows([
        { user_sub: "owner-sub", email: "owner@example.com", role: "OWNER", status: "ACTIVE" },
        { user_sub: "other-owner", email: "other@example.com", role: "OWNER", status: "ACTIVE" },
        { user_sub: "admin-sub", email: "admin@example.com", role: "ADMIN", status: "ACTIVE" }
    ]);
    const table = app.elements.get("organizationMembers").innerHTML;

    assert.doesNotMatch(table, /data-user-sub="owner-sub"/);
    assert.match(table, /remove-member" data-user-sub="other-owner"/);
    assert.match(table, /data-member-role="other-owner"/);
    assert.match(table, /remove-member" data-user-sub="admin-sub"/);

    app.context.applyOrganizationContext([{
        organization_id: "ORG-D13B30D99127",
        name: "ERAP Pilot Operations",
        role: "ADMIN",
        status: "ACTIVE"
    }]);
    app.context.sessionStorage.setItem("erap_id_token", tokenFor("admin-sub"));
    app.context.renderMemberRows([
        { user_sub: "owner-sub", email: "owner@example.com", role: "OWNER", status: "ACTIVE" },
        { user_sub: "other-owner", email: "other@example.com", role: "OWNER", status: "ACTIVE" },
        { user_sub: "admin-sub", email: "admin@example.com", role: "ADMIN", status: "ACTIVE" },
        { user_sub: "second-admin", email: "second@example.com", role: "ADMIN", status: "ACTIVE" }
    ]);
    const adminTable = app.elements.get("organizationMembers").innerHTML;

    assert.doesNotMatch(adminTable, /data-user-sub="owner-sub"/);
    assert.doesNotMatch(adminTable, /data-user-sub="other-owner"/);
    assert.doesNotMatch(adminTable, /data-user-sub="admin-sub"/);
    assert.match(adminTable, /remove-member" data-user-sub="second-admin"/);
}

async function testRemoveRequiresConfirmation() {
    const app = loadApp();
    const body = Buffer.from(JSON.stringify({
        exp: Math.floor(Date.now() / 1000) + 3600
    })).toString("base64url");
    app.context.sessionStorage.setItem("erap_id_token", `header.${body}.signature`);
    app.context.openRemoveMemberDialog(
        "803c69fc-5031-70b9-318c-5cf44472cbc7",
        "saisanath007@gmail.com"
    );

    assert.equal(app.elements.get("removeMemberModal").classList.contains("hidden"), false);
    assert.equal(app.elements.get("removeMemberEmail").textContent, "saisanath007@gmail.com");
    assert.equal(app.elements.get("removeMemberOrganization").textContent, "ERAP Pilot Operations");
    assert.equal(app.calls.length, 0);

    app.context.closeRemoveMemberDialog();
    assert.equal(app.elements.get("removeMemberModal").classList.contains("hidden"), true);
    assert.equal(app.calls.length, 0);

    app.context.openRemoveMemberDialog(
        "803c69fc-5031-70b9-318c-5cf44472cbc7",
        "saisanath007@gmail.com"
    );
    await app.context.confirmRemoveMember();
    const post = app.calls.find(call => call.method === "POST");
    assert.deepEqual(JSON.parse(post.body), {
        operation: "deactivate_member",
        organization_id: "ORG-D13B30D99127",
        target_user_sub: "803c69fc-5031-70b9-318c-5cf44472cbc7"
    });
    assert.equal(JSON.parse(post.body).role, undefined);
}

testDialogMarkup();
testMemberTableOmitsInvitationHistory();
testOwnerControlsMatchTheApi();
await testRemoveRequiresConfirmation();
console.log("member management ui ok");
