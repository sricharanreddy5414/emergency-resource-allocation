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
        disabled: false,
        children: [],
        dataset: {},
        style: {},
        _html: "",
        listeners: {},
        focus() {},
        addEventListener(type, fn) {
            element.listeners[type] = element.listeners[type] || [];
            element.listeners[type].push(fn);
        },
        click() {
            (element.listeners.click || []).forEach(fn => fn({
                target: element,
                preventDefault() {}
            }));
        },
        appendChild(child) {
            element.children.push(child);
            return child;
        },
        get options() {
            return element.children;
        }
    };

    Object.defineProperty(element, "innerHTML", {
        get() {
            return element._html;
        },
        set(value) {
            element._html = String(value);
            element.children = [];
        }
    });

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

    return element;
}

function loadApp() {
    const elements = new Map([
        ["organizationModal", makeElement("organizationModal", "modal hidden")],
        ["organizationModalTitle", makeElement("organizationModalTitle")],
        ["organizationModalDescription", makeElement("organizationModalDescription")],
        ["pendingInvitations", makeElement("pendingInvitations")],
        ["myInvitations", makeElement("myInvitations")],
        ["organizationCreateFields", makeElement("organizationCreateFields")],
        ["organizationNotNowBtn", makeElement("organizationNotNowBtn")],
        ["showOrganizationCreateBtn", makeElement("showOrganizationCreateBtn")],
        ["organizationModalClose", makeElement("organizationModalClose")],
        ["organizationOnboardingBtn", makeElement("organizationOnboardingBtn")],
        ["organizationName", makeElement("organizationName")],
        ["createOrganizationBtn", makeElement("createOrganizationBtn")],
        ["organizationSwitcher", makeElement("organizationSwitcher")]
    ]);
    elements.get("organizationNotNowBtn").hidden = true;
    elements.get("showOrganizationCreateBtn").hidden = true;
    elements.get("organizationOnboardingBtn").hidden = true;

    const calls = [];
    let membership = {
        organizations: [],
        pending_invitations: []
    };
    const store = new Map();
    const sessionStorage = {
        getItem(key) {
            return store.has(key) ? store.get(key) : null;
        },
        setItem(key, value) {
            store.set(key, String(value));
        },
        removeItem(key) {
            store.delete(key);
        }
    };
    const location = {
        origin: "https://main.d3enpe7opotop5.amplifyapp.com",
        search: "",
        href: "https://main.d3enpe7opotop5.amplifyapp.com/"
    };
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

    async function fetchImpl(input, options = {}) {
        const url = typeof input === "string" ? input : (input && input.url) || "";
        const call = {
            url,
            method: options.method || "GET",
            body: options.body || ""
        };
        calls.push(call);

        if (url.includes("/organization") && call.method === "GET") {
            return jsonResponse(membership);
        }

        if (url.includes("/organization") && call.method === "POST") {
            const body = JSON.parse(call.body || "{}");
            if (body.operation === "accept_invitation") {
                membership = {
                    organizations: [{
                        organization_id: body.organization_id,
                        name: "ERAP Pilot Operations",
                        role: "ADMIN",
                        status: "ACTIVE"
                    }],
                    pending_invitations: []
                };
                return jsonResponse({ message: "Invitation accepted" });
            }
            if (body.name) {
                return jsonResponse({
                    message: "Organization created successfully.",
                    organization: {
                        organization_id: "ORG-NEW",
                        name: body.name,
                        role: "OWNER",
                        status: "ACTIVE"
                    }
                }, 201);
            }
        }

        return jsonResponse({
            resources: [],
            requests: [],
            allocations: [],
            locations: [],
            types: []
        });
    }

    const context = {
        console,
        setTimeout,
        clearTimeout,
        URLSearchParams,
        atob,
        TextDecoder,
        encodeURIComponent,
        crypto: {
            randomUUID: () => "11111111-1111-1111-1111-111111111111"
        },
        sessionStorage,
        document,
        location,
        fetch: fetchImpl
    };
    context.window = context;
    context.globalThis = context;
    vm.createContext(context);
    vm.runInContext(appSource, context, { filename: "frontend/app.js" });
    context.initializeEvents();

    return { context, elements, calls, sessionStorage, location, setMembership(next) { membership = next; } };
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

function token() {
    return jwt({
        exp: Math.floor(Date.now() / 1000) + 3600,
        email: "invited@example.com"
    });
}

async function settle() {
    await new Promise(resolve => setTimeout(resolve, 30));
}

const invitation = {
    organization_id: "ORG-D13B30D99127",
    name: "ERAP Pilot Operations",
    role: "ADMIN",
    status: "PENDING"
};

async function testPendingInvitationIsShown() {
    const app = loadApp();
    app.sessionStorage.setItem("erap_id_token", token());
    app.setMembership({ organizations: [], pending_invitations: [invitation] });
    const organizations = await app.context.loadOrganizationMembership();
    app.context.openOrganizationOnboarding();

    assert.deepEqual(organizations, []);
    assert.match(app.elements.get("pendingInvitations").innerHTML, /You've been invited to join <strong>ERAP Pilot Operations<\/strong>/);
    assert.match(app.elements.get("pendingInvitations").innerHTML, /ADMIN/);
    assert.match(app.elements.get("pendingInvitations").innerHTML, /Accept Invitation/);
    assert.equal(app.elements.get("organizationModalTitle").textContent, "You've been invited to join ERAP Pilot Operations");
    assert.equal(app.elements.get("organizationCreateFields").hidden, true);
    assert.equal(app.elements.get("organizationNotNowBtn").hidden, false);
    assert.equal(app.elements.get("organizationModal").classList.contains("hidden"), false);
}

async function testAcceptInvitationUsesExistingOperation() {
    const app = loadApp();
    app.sessionStorage.setItem("erap_id_token", token());
    app.setMembership({ organizations: [], pending_invitations: [invitation] });
    await app.context.loadOrganizationMembership();
    app.context.openOrganizationOnboarding();
    app.calls.length = 0;

    app.context.document.dispatchEvent({
        type: "click",
        target: {
            closest(selector) {
                if (selector === ".accept-invitation-btn") {
                    return { dataset: { organizationId: invitation.organization_id } };
                }
                return null;
            }
        }
    });
    await settle();

    const post = app.calls.find(call => call.method === "POST" && call.url.includes("/organization"));
    const body = JSON.parse(post.body);
    assert.deepEqual(body, {
        operation: "accept_invitation",
        organization_id: "ORG-D13B30D99127"
    });
    assert.equal(Object.hasOwn(body, "role"), false);
    assert.equal(Object.hasOwn(body, "user_sub"), false);
    assert.equal(app.elements.get("organizationModal").classList.contains("hidden"), true);
    assert.equal(app.sessionStorage.getItem("erap_selected_organization_id"), "ORG-D13B30D99127");
    assert.equal(app.elements.get("organizationSwitcher").value, "ORG-D13B30D99127");
    assert.equal(app.elements.get("organizationOnboardingBtn").hidden, true);
    assert.equal(app.calls.some(call => String(call.body).includes("deactivate_member")), false);
}

async function testCloseDoesNotChangeInvitation() {
    const app = loadApp();
    app.sessionStorage.setItem("erap_id_token", token());
    app.setMembership({ organizations: [], pending_invitations: [invitation] });
    await app.context.loadOrganizationMembership();
    app.context.openOrganizationOnboarding();
    const before = app.calls.length;

    app.elements.get("organizationModalClose").click();

    assert.equal(app.calls.length, before);
    assert.equal(app.elements.get("organizationModal").classList.contains("hidden"), true);
    assert.equal(app.elements.get("organizationOnboardingBtn").hidden, false);
    assert.equal(app.elements.get("organizationOnboardingBtn").textContent, "Invitations");

    app.elements.get("organizationNotNowBtn").click();
    app.context.document.dispatchEvent({ type: "keydown", key: "Escape" });
    assert.equal(app.calls.length, before);

    app.context.openOrganizationOnboarding();
    assert.match(app.elements.get("pendingInvitations").innerHTML, /ERAP Pilot Operations/);
    assert.match(app.elements.get("pendingInvitations").innerHTML, /Accept Invitation/);
    assert.equal(app.calls.some(call => String(call.body).includes("accept_invitation")), false);
    assert.equal(app.calls.some(call => String(call.body).includes("deactivate_member")), false);
}

async function testCreateOrganizationRemainsForUsersWithoutInvitations() {
    const app = loadApp();
    app.sessionStorage.setItem("erap_id_token", token());
    app.setMembership({ organizations: [], pending_invitations: [] });
    await app.context.loadOrganizationMembership();
    app.context.openOrganizationOnboarding();

    assert.equal(app.elements.get("organizationModalTitle").textContent, "Create your organization");
    assert.equal(app.elements.get("pendingInvitations").innerHTML, "");
    assert.equal(app.elements.get("organizationCreateFields").hidden, false);
    assert.equal(app.elements.get("organizationNotNowBtn").hidden, true);
    assert.match(htmlSource, /id="createOrganizationBtn"/);
    assert.match(htmlSource, /id="organizationName"/);
}

async function testOwnerCreationAndOrganizationSelection() {
    const app = loadApp();
    app.sessionStorage.setItem("erap_id_token", token());
    app.setMembership({ organizations: [], pending_invitations: [] });
    await app.context.loadOrganizationMembership();
    app.context.openOrganizationOnboarding();
    app.elements.get("organizationName").value = "ERAP Pilot Operations";
    app.calls.length = 0;

    await app.context.createOrganization();
    await settle();

    const post = app.calls.find(call => call.method === "POST");
    const body = JSON.parse(post.body);
    assert.equal(body.name, "ERAP Pilot Operations");
    assert.equal(Object.hasOwn(body, "operation"), false);
    assert.equal(app.sessionStorage.getItem("erap_selected_organization_id"), "ORG-NEW");
    assert.equal(app.elements.get("organizationModal").classList.contains("hidden"), true);

    app.context.applyOrganizationContext([
        { organization_id: "ORG-B", name: "Second", role: "OWNER", status: "ACTIVE" },
        { organization_id: "ORG-A", name: "First", role: "ADMIN", status: "ACTIVE" }
    ]);
    assert.equal(app.elements.get("organizationSwitcher").value, "ORG-A");

    app.sessionStorage.setItem("erap_selected_organization_id", "ORG-B");
    app.context.applyOrganizationContext([
        { organization_id: "ORG-B", name: "Second", role: "OWNER", status: "ACTIVE" },
        { organization_id: "ORG-A", name: "First", role: "ADMIN", status: "ACTIVE" }
    ]);
    assert.equal(app.elements.get("organizationSwitcher").value, "ORG-B");
    assert.equal(app.elements.get("organizationOnboardingBtn").hidden, true);
}

async function testLogoutAndSessionRefreshStayIntact() {
    const app = loadApp();
    app.sessionStorage.setItem("erap_id_token", token());
    app.setMembership({ organizations: [], pending_invitations: [invitation] });
    await app.context.loadOrganizationMembership();
    app.context.openOrganizationOnboarding();
    const invitationHtml = app.elements.get("pendingInvitations").innerHTML;

    app.context.logoutFromCognito();

    assert.match(app.location.href, /\/logout\?/);
    assert.equal(app.elements.get("pendingInvitations").innerHTML, invitationHtml);
    assert.equal(app.calls.some(call => String(call.body).includes("accept_invitation")), false);

    const refresh = appSource.slice(
        appSource.indexOf("async function requestRefreshToken"),
        appSource.indexOf("async function refreshSession")
    );
    const wrapper = appSource.slice(
        appSource.indexOf("window.fetch = async function"),
        appSource.indexOf("function loadAuthenticatedUser")
    );
    assert.match(refresh, /grant_type: "refresh_token"/);
    assert.doesNotMatch(refresh, /console\.error\(error\)/);
    assert.match(refresh, /console\.error\("Cognito session refresh failed"\)/);
    assert.match(wrapper, /response\.status !== 401/);
    assert.match(htmlSource, /id="organizationModalClose"/);
    assert.match(htmlSource, /aria-label="Close"/);
    assert.match(htmlSource, /type="button"/);
}

assert.match(htmlSource, /id="organizationModalClose"/);
await testPendingInvitationIsShown();
await testAcceptInvitationUsesExistingOperation();
await testCloseDoesNotChangeInvitation();
await testCreateOrganizationRemainsForUsersWithoutInvitations();
await testOwnerCreationAndOrganizationSelection();
await testLogoutAndSessionRefreshStayIntact();
console.log("invitation onboarding ui tests passed");
