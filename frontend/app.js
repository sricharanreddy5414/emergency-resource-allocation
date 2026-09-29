/* =========================================================
   ERAP - Emergency Resource Allocation Platform
   Cognito Authentication + AWS API Integration
========================================================= */


/* =========================================================
   AWS API CONFIGURATION
========================================================= */

const API_URL =
    "https://4c6dni17l3.execute-api.eu-north-1.amazonaws.com/dev/allocate";

const RESOURCES_API_URL =
    "https://4c6dni17l3.execute-api.eu-north-1.amazonaws.com/dev/allocate/resources";
const RELEASE_RESOURCE_API_URL =
    "https://4c6dni17l3.execute-api.eu-north-1.amazonaws.com/dev/allocate/resources/release";

const RESOURCE_HISTORY_API_URL =
    "https://4c6dni17l3.execute-api.eu-north-1.amazonaws.com/dev/allocate/resources/history";

const RESOURCE_RESERVE_API_URL =
    RESOURCES_API_URL + "/reserve";
const RESOURCE_RESERVATION_RELEASE_API_URL =
    RESOURCES_API_URL + "/reservation-release";
const RESOURCE_EVERYDAY_ALLOCATE_API_URL =
    RESOURCES_API_URL + "/everyday";
const RESOURCE_EVERYDAY_RETURN_API_URL =
    RESOURCES_API_URL + "/everyday/return";
const RESOURCE_MAINTENANCE_API_URL =
    RESOURCES_API_URL + "/maintenance";
const RESOURCE_MAINTENANCE_COMPLETE_API_URL =
    RESOURCES_API_URL + "/maintenance/complete";
const RESOURCE_DAMAGE_API_URL =
    RESOURCES_API_URL + "/damage";
const RESOURCE_DAMAGE_RECOVER_API_URL =
    RESOURCES_API_URL + "/damage/recover";
const RESOURCE_RETIRE_API_URL =
    RESOURCES_API_URL + "/retire";
const RESOURCE_IN_USE_API_URL =
    RESOURCES_API_URL + "/in-use";
const RESOURCE_IN_USE_RETURN_API_URL =
    RESOURCES_API_URL + "/in-use/return";
const RESOURCE_ASSIGN_API_URL =
    RESOURCES_API_URL + "/assign";
const RESOURCE_UNASSIGN_API_URL =
    RESOURCES_API_URL + "/unassign";

const REQUESTS_API_URL =
    "https://4c6dni17l3.execute-api.eu-north-1.amazonaws.com/dev/requests";

const ALLOCATIONS_API_URL =
    "https://4c6dni17l3.execute-api.eu-north-1.amazonaws.com/dev/allocate/allocations";

const ORGANIZATION_API_URL =
    "https://4c6dni17l3.execute-api.eu-north-1.amazonaws.com/dev/organization";

const BILLING_API_URL =
    "https://4c6dni17l3.execute-api.eu-north-1.amazonaws.com/dev/billing";

const LOCATIONS_API_URL =
    "https://4c6dni17l3.execute-api.eu-north-1.amazonaws.com/dev/locations";

const RESOURCE_TYPES_API_URL =
    "https://4c6dni17l3.execute-api.eu-north-1.amazonaws.com/dev/resource-types";

const REQUEST_TYPES_API_URL =
    "https://4c6dni17l3.execute-api.eu-north-1.amazonaws.com/dev/request-types";

const EXCHANGE_API_URL =
    "https://4c6dni17l3.execute-api.eu-north-1.amazonaws.com/dev/exchange";


/* =========================================================
   AMAZON COGNITO CONFIGURATION
========================================================= */

const COGNITO_DOMAIN =
    "https://eu-north-1vv7adaac9.auth.eu-north-1.amazoncognito.com";

const COGNITO_CLIENT_ID =
    "3je7latr22bqhggoavlva00hp5";

const REDIRECT_URI = window.location.origin;

const COGNITO_SCOPES =
    "openid";

/* =========================================================
   APPLICATION STATE
========================================================= */

let resources = [];
const everydayAllocationByResource = {};
let showAdminSection = function () {};
let editingResourceId = "";

let allocations = [];

let tenantContextLoading = false;

let resourceTypes = [];

let requestTypes = [];
let requestTypesLoadFailed = false;

let notifications = [];

let currentUser = {

    name: "Admin",

    email: "Not signed in",

    phone: "Not available",

    organizations: [],

    organization: null,

    locations: [],

    location: null

};

let organizationOnboardingRequired = false;
let organizationCreateRevealed = false;
let pendingInvitations = [];
let pendingMemberRemoval = null;

let organizationRequestId = null;

let dashboardDataStarted = false;


/* =========================================================
   DOM HELPER
========================================================= */

const $ = (id) =>
    document.getElementById(id);


/* =========================================================
   COGNITO TOKEN STORAGE
========================================================= */

function getAccessToken() {

    return sessionStorage.getItem(
        "erap_access_token"
    );

}


function getIdToken() {

    return sessionStorage.getItem(
        "erap_id_token"
    );

}


async function waitForIdToken(maxWait = 5000) {

    const start = Date.now();

    while (!getIdToken()) {

        if (Date.now() - start >= maxWait) {
            return null;
        }

        await new Promise(
            resolve => setTimeout(resolve, 100)
        );

    }

    return getIdToken();

}

function saveTokens(tokens) {

    if (tokens.access_token) {

        sessionStorage.setItem(
            "erap_access_token",
            tokens.access_token
        );

    }


    if (tokens.id_token) {

        sessionStorage.setItem(
            "erap_id_token",
            tokens.id_token
        );

    }


    if (tokens.refresh_token) {

        sessionStorage.setItem(
            "erap_refresh_token",
            tokens.refresh_token
        );

    }

}


function clearTokens() {

    sessionStorage.removeItem(
        "erap_access_token"
    );

    sessionStorage.removeItem(
        "erap_id_token"
    );

    sessionStorage.removeItem(
        "erap_refresh_token"
    );

    sessionStorage.removeItem(
        "erap_selected_organization_id"
    );

    Object.keys(sessionStorage).forEach(key => {

        if (key.startsWith("erap_location_")) {

            sessionStorage.removeItem(key);

        }

    });

}


/* =========================================================
   PKCE HELPERS
========================================================= */

function generateRandomString(length = 64) {

    const characters =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~";

    let result = "";

    const array =
        new Uint8Array(length);

    crypto.getRandomValues(array);

    for (let i = 0; i < length; i++) {

        result +=
            characters[
                array[i] % characters.length
            ];

    }

    return result;

}


function base64UrlEncode(arrayBuffer) {

    let binary = "";

    const bytes =
        new Uint8Array(arrayBuffer);

    bytes.forEach(byte => {

        binary += String.fromCharCode(
            byte
        );

    });


    return btoa(binary)

        .replace(/\+/g, "-")

        .replace(/\//g, "_")

        .replace(/=+$/, "");

}


async function createCodeChallenge(verifier) {

    const encoder =
        new TextEncoder();

    const data =
        encoder.encode(verifier);

    const digest =
        await crypto.subtle.digest(
            "SHA-256",
            data
        );

    return base64UrlEncode(
        digest
    );

}


/* =========================================================
   START COGNITO LOGIN
========================================================= */

async function loginWithCognito() {

    try {

        const verifier =
            generateRandomString(96);


        const challenge =
            await createCodeChallenge(
                verifier
            );


        sessionStorage.setItem(
            "erap_pkce_verifier",
            verifier
        );


        const params =
            new URLSearchParams({

                client_id:
                    COGNITO_CLIENT_ID,

                response_type:
                    "code",

                scope:
                    COGNITO_SCOPES,

                redirect_uri:
                    REDIRECT_URI,

                code_challenge:
                    challenge,

                code_challenge_method:
                    "S256"

            });


        const loginUrl =
            `${COGNITO_DOMAIN}/oauth2/authorize?${params.toString()}`;


        window.location.href =
            loginUrl;

    }

    catch (error) {

        console.error(
            "Cognito login error:",
            error
        );

        showToast(
            "Unable to start login."
        );

    }

}


/* =========================================================
   EXCHANGE AUTHORIZATION CODE
========================================================= */

async function handleCognitoCallback() {

    const params =
        new URLSearchParams(
            window.location.search
        );


    const code =
        params.get("code");


    if (!code) {

        return false;

    }


    const verifier =
        sessionStorage.getItem(
            "erap_pkce_verifier"
        );


    if (!verifier) {

        console.error(
            "PKCE verifier not found."
        );

        return false;

    }


    try {

        const tokenUrl =
            `${COGNITO_DOMAIN}/oauth2/token`;


        const body =
            new URLSearchParams({

                grant_type:
                    "authorization_code",

                client_id:
                    COGNITO_CLIENT_ID,

                code:
                    code,

                redirect_uri:
                    REDIRECT_URI,

                code_verifier:
                    verifier

            });


        const response =
            await fetch(
                tokenUrl,
                {

                    method: "POST",

                    headers: {

                        "Content-Type":
                            "application/x-www-form-urlencoded"

                    },

                    body:
                        body.toString()

                }
            );


        if (!response.ok) {

            const errorText =
                await response.text();

            throw new Error(
                errorText ||
                `Token request failed: ${response.status}`
            );

        }


        const tokens =
            await response.json();


        saveTokens(tokens);


        sessionStorage.removeItem(
            "erap_pkce_verifier"
        );


        /*
           Remove ?code=... from browser URL
           after successful authentication.
        */

        window.history.replaceState(
            {},
            document.title,
            REDIRECT_URI
        );


        return true;

    }

    catch (error) {

        console.error(
            "Cognito token exchange failed:",
            error
        );


        clearTokens();


        showToast(
            "Login could not be completed."
        );


        return false;

    }

}


/* =========================================================
   JWT DECODER
========================================================= */

function decodeJwt(token) {

    try {

        const parts =
            token.split(".");


        if (parts.length !== 3) {

            return null;

        }


        let payload =
            parts[1];


        payload =
            payload
                .replace(/-/g, "+")
                .replace(/_/g, "/");


        while (
            payload.length % 4
        ) {

            payload += "=";

        }


        return JSON.parse(
            atob(payload)
        );

    }

    catch (error) {

        console.error(
            "JWT decode error:",
            error
        );

        return null;

    }

}


/* =========================================================
   SESSION REFRESH
========================================================= */

const originalFetch = window.fetch.bind(window);

let sessionRefresh = null;


function tokenExpiry() {

    const claims = decodeJwt(getIdToken() || "");
    const expiresAt = Number(claims && claims.exp);

    return expiresAt ? expiresAt * 1000 : 0;

}


function tokenStillUsable() {

    return tokenExpiry() > Date.now() + 5000;

}


function idTokenNeedsRefresh() {

    if (!getIdToken()) {

        return Boolean(sessionStorage.getItem("erap_refresh_token"));

    }

    return tokenExpiry() <= Date.now() + 60000;

}


async function requestRefreshToken() {

    const refreshToken = sessionStorage.getItem("erap_refresh_token");

    if (!refreshToken) {

        if (!tokenStillUsable()) {

            await loginWithCognito();

        }

        return false;

    }

    try {

        const body = new URLSearchParams({

            grant_type: "refresh_token",

            client_id: COGNITO_CLIENT_ID,

            refresh_token: refreshToken

        });

        const response = await originalFetch(
            `${COGNITO_DOMAIN}/oauth2/token`,
            {

                method: "POST",

                headers: {

                    "Content-Type": "application/x-www-form-urlencoded"

                },

                body: body.toString()

            }
        );

        if (!response.ok) {

            if (!tokenStillUsable()) {

                clearTokens();

                await loginWithCognito();

            }

            return false;

        }

        saveTokens(await response.json());

        return Boolean(getIdToken());

    } catch (error) {

        console.error("Cognito session refresh failed");

        if (!tokenStillUsable()) {

            clearTokens();

            await loginWithCognito();

        }

        return false;

    }

}


async function refreshSession() {

    if (!sessionRefresh) {

        sessionRefresh = requestRefreshToken().finally(() => {

            sessionRefresh = null;

        });

    }

    return sessionRefresh;

}


function authorizationHeader(headers) {

    if (!headers) {

        return "";

    }

    if (typeof Headers !== "undefined" && headers instanceof Headers) {

        return headers.get("Authorization") || headers.get("authorization") || "";

    }

    return headers.Authorization || headers.authorization || "";

}


window.fetch = async function (input, options) {

    const url = typeof input === "string" ? input : (input && input.url) || "";
    const authed = url.includes("execute-api") && authorizationHeader(options && options.headers);

    if (!authed) {

        return originalFetch(input, options);

    }

    const next = {
        ...options,
        headers: {
            ...(options.headers || {})
        }
    };

    if (idTokenNeedsRefresh()) {

        const refreshed = await refreshSession();

        if (!refreshed) {

            return originalFetch(input, next);

        }

    }

    next.headers.Authorization = "Bearer " + getIdToken();

    let response = await originalFetch(input, next);

    if (response.status !== 401) {

        noteBillingRequired(response);

        return response;

    }

    const refreshed = await refreshSession();

    if (!refreshed) {

        return response;

    }

    next.headers.Authorization = "Bearer " + getIdToken();

    response = await originalFetch(input, next);

    noteBillingRequired(response);

    return response;

};


/* =========================================================
   LOAD AUTHENTICATED USER
========================================================= */

function loadAuthenticatedUser() {

    const idToken =
        getIdToken();


    if (!idToken) {

        currentUser = {

            name: "Admin",

            email:
                "Not signed in",

            phone:
                "Not available",

            organizations: [],

            organization: null,

            locations: [],

            location: null

        };

        return;

    }


    const claims =
        decodeJwt(idToken);


    if (!claims) {

        return;

    }


        currentUser = {

        name:
            claims.name ||
            claims.given_name ||
            claims.username ||
            claims.email ||
            "ERAP User",

        email:
            claims.email ||
            "Email unavailable",

        phone:
            claims.phone_number ||
            "Phone unavailable",

        isAdmin:
            Array.isArray(claims["cognito:groups"]) &&
            claims["cognito:groups"].includes("Admin"),

        organizations:
            currentUser.organizations || [],

        organization:
            currentUser.organization || null,

        locations:
            currentUser.locations || [],

        location:
            currentUser.location || null

    };


    updateUserInterface();

}


/* =========================================================
   UPDATE USER UI
========================================================= */

function updateUserInterface() {

    const name =
        currentUser.name ||
        "ERAP User";


    const initials =
        getInitials(name);


    if ($("userName")) {

        $("userName")
            .textContent =
            name;

    }


    if ($("profileIcon")) {

        $("profileIcon")
            .textContent =
            initials;

    }


    if ($("profileName")) {

        $("profileName")
            .textContent =
            name;

    }


    if ($("profileEmail")) {

        $("profileEmail")
            .textContent =
            currentUser.email;

    }


    if ($("profilePhone")) {

        $("profilePhone")
            .textContent =
            currentUser.phone;

    }


    if ($("profileOrganization")) {

        $("profileOrganization")
            .textContent =
            currentUser.organization?.name ||
            "—";

    }


    if ($("profileRole")) {

        $("profileRole")
            .textContent =
            currentUser.organization?.role ||
            "—";

    }

    refreshShellContext();

    const membersPanel = $("organizationMembersPanel");

    if (membersPanel && !canManageMembers()) {

        membersPanel.hidden = true;

    }


    const managementNav = $("managementNav");

    if (managementNav) {

        managementNav.hidden = !(currentUser.isAdmin || canManageCatalog());

    }

    const billingNav = $("billingNavItem");

    if (billingNav) {

        billingNav.hidden = !canViewBilling();

    }

    if ($("modalAvatar")) {

        $("modalAvatar")
            .textContent =
            initials;

    }

}


/* =========================================================
   LOGOUT
========================================================= */

function logoutFromCognito() {

    clearTokens();


    const params =
        new URLSearchParams({

            client_id:
                COGNITO_CLIENT_ID,

            logout_uri:
                REDIRECT_URI

        });


    const logoutUrl =
        `${COGNITO_DOMAIN}/logout?${params.toString()}`;


    window.location.href =
        logoutUrl;

}


/* =========================================================
   AUTHENTICATION GUARD
========================================================= */

async function initializeAuthentication() {

    /*
       First check whether Cognito has redirected
       back with an authorization code.
    */

    const callbackHandled =
        await handleCognitoCallback();


    if (callbackHandled) {

        loadAuthenticatedUser();

        return true;

    }


    /*
       If there is no access token,
       send the user to Cognito Managed Login.
    */

    if (!getAccessToken() && !sessionStorage.getItem("erap_refresh_token")) {

        await loginWithCognito();

        return false;

    }


    /*
       An expired ID token still leaves the user on screen.
       Refresh it before any protected API call.
    */

    if (idTokenNeedsRefresh()) {

        const refreshed = await refreshSession();

        if (!refreshed) {

            return false;

        }

    }


    if (!getIdToken()) {

        await loginWithCognito();

        return false;

    }


    loadAuthenticatedUser();

    return true;

}


/* =========================================================
   TOAST
========================================================= */

function showToast(message) {

    const toast =
        $("toast");

    const toastMessage =
        $("toastMessage");


    if (
        !toast ||
        !toastMessage
    ) {

        return;

    }


    toastMessage.textContent =
        message;


    toast.classList.add(
        "show"
    );


    clearTimeout(
        window.toastTimer
    );


    window.toastTimer =
        setTimeout(
            () => {

                toast.classList.remove(
                    "show"
                );

            },
            3000
        );

}


/* =========================================================
   RESULT MESSAGE
========================================================= */

function showResult(
    message,
    type = "success"
) {

    const result =
        $("result");


    if (!result) return;


    result.className =
        `result-box ${type}`;


    result.textContent =
        message;


    result.classList.remove(
        "hidden"
    );

}


function hideResult() {

    const result =
        $("result");


    if (!result) return;


    result.classList.add(
        "hidden"
    );

}


/* =========================================================
   INITIALS
========================================================= */

function getInitials(name) {

    if (!name) {

        return "ER";

    }


    const parts =
        name
            .trim()
            .split(/\s+/);


    if (
        parts.length === 1
    ) {

        return parts[0]
            .substring(0, 2)
            .toUpperCase();

    }


    return (

        parts[0][0] +

        parts[
            parts.length - 1
        ][0]

    ).toUpperCase();

}


/* =========================================================
   HTML ESCAPE
========================================================= */

function escapeHtml(value) {

    if (
        value === null ||
        value === undefined
    ) {

        return "";

    }


    return String(value)

        .replaceAll(
            "&",
            "&amp;"
        )

        .replaceAll(
            "<",
            "&lt;"
        )

        .replaceAll(
            ">",
            "&gt;"
        )

        .replaceAll(
            '"',
            "&quot;"
        )

        .replaceAll(
            "'",
            "&#039;"
        );

}


/* =========================================================
   NAVIGATION
========================================================= */

const pageTitles = {

    dashboard:
        "Operations",

    admin:
        "Organization",

    resources:
        "Resources",

    requests:
        "Requests",

    allocations:
        "Allocations",

    exchange:
        "Exchange",

    notifications:
        "Notifications",

    settings:
        "Platform",

    help:
        "Help",

    billing:
        "Billing"

};


function refreshShellContext() {

    const welcome = typeof document.querySelector === "function"
        ? document.querySelector(".welcome")
        : null;

    if (welcome) {

        const hour = new Date().getHours();
        const greeting = hour < 12
            ? "Good morning"
            : hour < 17
                ? "Good afternoon"
                : "Good evening";
        const name = currentUser.name || "";
        const usable = name && !name.includes("@") && !["Admin", "Loading...", "Not signed in", "ERAP User"].includes(name);
        welcome.textContent = usable ? greeting + ", " + name.split(" ")[0] : greeting;

    }

    const organization = currentUser.organization && currentUser.organization.name
        ? currentUser.organization.name
        : "No organization selected";
    const location = currentUser.location && currentUser.location.name
        ? currentUser.location.name
        : "All locations";
    const line = organization + " · " + location;
    const context = $("operationalContext");

    if (context) {

        context.textContent = line;

    }

    const command = $("commandContext");

    if (command) {

        command.textContent = line;

    }

    const opsState = $("opsState");

    if (opsState) {

        const ready = Boolean(currentUser.organization && currentUser.organization.name);
        opsState.textContent = ready ? "Operational" : "Awaiting organization";

    }

}


function navigateTo(sectionId) {

    const sections =
        document.querySelectorAll(
            ".page-section"
        );


    const navItems =
        document.querySelectorAll(
            ".nav-item"
        );


    sections.forEach(
        section => {

            section.classList.remove(
                "active-section"
            );

        }
    );


    navItems.forEach(
        item => {

            item.classList.remove(
                "active"
            );

        }
    );


    document.querySelectorAll(".dashboard-only-section").forEach(section => {

        section.style.display =
            sectionId === "dashboard" ? "" : "none";

    });


    const target =
        $(sectionId);


    if (!target) return;


    target.classList.add(
        "active-section"
    );


    document
        .querySelectorAll(
            `.nav-item[data-section="${sectionId}"]`
        )
        .forEach(navItem => {

            navItem.classList.add(
                "active"
            );

        });


    if ($("pageTitle")) {

        $("pageTitle").textContent =
            pageTitles[sectionId] ||
            "ERAP";

    }

    refreshShellContext();


    window.scrollTo({

        top: 0,

        behavior: "smooth"

    });


    const sidebar =
        $("sidebar");


    if (sidebar) {

        sidebar.classList.remove(
            "mobile-open"
        );

    }


    if (
        sectionId ===
        "resources"
    ) {

        renderResourcesPage();

    }
    if (
    sectionId ===
    "requests"
) {

    loadRequests();

}


    if (
        sectionId ===
        "allocations"
    ) {

        loadAllocations();

    }

    if (sectionId === "exchange") {

        loadExchangeWorkspace();

    }

    if (
        sectionId ===
        "admin"
    ) {

        loadAllocations();
        loadOrganizationMembers();

    }

    if (sectionId === "billing") {

        loadBilling();

    }

}


/* =========================================================
   NAVIGATION INITIALIZATION
========================================================= */

function initializeAdminDesk() {

    const root = $("admin");

    if (!root || typeof root.querySelectorAll !== "function") {
        return;
    }

    const buttons = root.querySelectorAll("[data-desk]");
    const panels = root.querySelectorAll("[data-desk-panel]");

    function show(name) {

        panels.forEach(panel => {

            if (panel.id === "organizationMembersPanel" && !canManageMembers()) {
                panel.hidden = true;
                return;
            }

            panel.hidden = panel.dataset.deskPanel !== name;

        });

        buttons.forEach(button => {
            button.classList.toggle("is-on", button.dataset.desk === name);
        });

    }

    buttons.forEach(button => {
        button.addEventListener("click", () => show(button.dataset.desk));
    });

    showAdminSection = show;

    show("brief");

}


function initializeResourceDesk() {

    const table = $("resourcesPageTable");
    const desk = $("resourceDesk");

    if (!table || !desk || table.dataset.bound === "1") {
        return;
    }

    table.dataset.bound = "1";

    table.addEventListener("click", event => {

        if (event.target.closest("button")) {
            return;
        }

        const row = event.target.closest("tr");
        const resource = resources.find(item => item.id === row?.dataset.resourceId);

        if (!resource) {
            return;
        }

        const name = $("resourceDeskName");
        const meta = $("resourceDeskMeta");

        if (name) {
            name.textContent = resource.name || resource.type || "Resource";
        }

        if (meta) {
            meta.textContent = [
                resource.type,
                resource.location,
                "Emergency: " + getEmergencyAvailabilityLabel(resource),
                "Operational: " + getOperationalStatusLabel(resource),
                resource.visibility || "PRIVATE",
                resource.id
            ].filter(Boolean).join(" · ");
        }

        desk.hidden = false;

    });

    $("resourceDeskClose")?.addEventListener("click", () => {
        desk.hidden = true;
    });

}


function initializeNavigation() {

    document
        .querySelectorAll(
            ".nav-item"
        )
        .forEach(
            item => {

                item.addEventListener(
                    "click",
                    () => {

                        navigateTo(
                            item.dataset.section
                        );

                        if (item.dataset.desk) {

                            showAdminSection(item.dataset.desk);

                        }

                    }
                );

            }
        );

}


/* =========================================================
   MOBILE MENU
========================================================= */

function initializeMobileMenu() {

    const button =
        $("mobileMenuBtn");


    const sidebar =
        $("sidebar");


    if (
        !button ||
        !sidebar
    ) {

        return;

    }


    const overlay = $("navOverlay");

    function setMenu(open) {

        sidebar.classList.toggle("mobile-open", open);

        if (overlay) {

            overlay.hidden = !open;

        }

        button.setAttribute("aria-expanded", open ? "true" : "false");

    }

    button.addEventListener("click", () => {

        setMenu(!sidebar.classList.contains("mobile-open"));

    });

    $("navCloseBtn")?.addEventListener("click", () => setMenu(false));

    $("openLocationFormBtn")?.addEventListener("click", () => {

        $("locationModal")?.classList.remove("hidden");

    });

    overlay?.addEventListener("click", () => setMenu(false));

    document.addEventListener("keydown", event => {

        if (event.key === "Escape") {

            setMenu(false);

        }

    });

    sidebar.querySelectorAll(".nav-item").forEach(item => {

        item.addEventListener("click", () => setMenu(false));

    });

}


/* =========================================================
   NOTIFICATIONS
========================================================= */

function addNotification(
    title,
    message
) {

    notifications.unshift({

        title,

        message,

        time:
            new Date()

    });


    renderNotifications();

    updateNotificationCount();

}


function renderNotifications() {

    const list =
        $("notificationList");


    if (!list) return;


    if (
        notifications.length === 0
    ) {

        list.innerHTML = `

            <div class="empty-state">

                <h3>
                    No notifications
                </h3>

                <p>
                    Allocation and request messages from this session appear here.
                </p>

            </div>

        `;

        return;

    }


    list.innerHTML =
        notifications
            .map(
                notification => `

                    <article class="notification-item">

                        <div>

                            <strong>
                                ${escapeHtml(
                                    notification.title
                                )}
                            </strong>

                            <p>
                                ${escapeHtml(
                                    notification.message
                                )}
                            </p>

                            <time>
                                ${escapeHtml(
                                    notification.time
                                        ? new Date(notification.time).toLocaleString()
                                        : ""
                                )}
                            </time>

                        </div>

                    </article>

                `
            )
            .join("");

}


function updateNotificationCount() {

    const count =
        $("notificationCount");


    if (!count) return;


    count.textContent =
        notifications.length;

}


function updateAdminDashboard() {

    if (!currentUser || !currentUser.isAdmin) {
        return;
    }

    const totalResources =
        resources.length;

    const availableResources =
        resources.filter(resource =>
            isResourceAvailable(resource)
        ).length;

    const totalRequests =
        requests.length;

    const activeAllocations =
        allocations.filter(allocation =>
            String(
                allocation.status ??
                allocation.Status ??
                ""
            ).toUpperCase() === "ALLOCATED"
        ).length;

    if ($("adminTotalResources")) {
        $("adminTotalResources").textContent =
            totalResources;
    }

    if ($("adminAvailableResources")) {
        $("adminAvailableResources").textContent =
            availableResources;
    }

    if ($("adminTotalRequests")) {
        $("adminTotalRequests").textContent =
            totalRequests;
    }

    if ($("adminActiveAllocations")) {
        $("adminActiveAllocations").textContent =
            activeAllocations;
    }

    const allocatedResources =
        totalResources -
        availableResources;

    if ($("adminResourceOverview")) {
        $("adminResourceOverview").innerHTML = `
            <div class="admin-overview-row">
                <span>Available</span>
                <strong>${availableResources}</strong>
            </div>

            <div class="admin-overview-row">
                <span>Allocated</span>
                <strong>${allocatedResources}</strong>
            </div>

            <div class="admin-overview-row">
                <span>Total</span>
                <strong>${totalResources}</strong>
            </div>
        `;
    }

    const pendingRequests =
        requests.filter(request =>
            String(
                request.status ??
                request.Status ??
                ""
            ).toUpperCase() === "PENDING"
        ).length;

    const releasedRequests =
        requests.filter(request =>
            String(
                request.status ??
                request.Status ??
                ""
            ).toUpperCase() === "RELEASED"
        ).length;

    if ($("adminRequestOverview")) {
        $("adminRequestOverview").innerHTML = `
            <div class="admin-overview-row">
                <span>Pending</span>
                <strong>${pendingRequests}</strong>
            </div>

            <div class="admin-overview-row">
                <span>Allocated</span>
                <strong>${activeAllocations}</strong>
            </div>

            <div class="admin-overview-row">
                <span>Released</span>
                <strong>${releasedRequests}</strong>
            </div>

            <div class="admin-overview-row">
                <span>Total</span>
                <strong>${totalRequests}</strong>
            </div>
        `;
    }
}

/* =========================================================
   LOAD RESOURCES FROM API GATEWAY
========================================================= */

async function loadResources() {

    try {

        showToast(
            "Loading resources..."
        );


        /*
           IMPORTANT:
           No Content-Type header on GET.
           This avoids unnecessary CORS preflight.
        */

        const response =
            await fetch(
                RESOURCES_API_URL + tenantQuery(),
                {
                    method:
                        "GET",

                    headers: {
                        "Authorization":
                            "Bearer " + getIdToken()
                    }
                }
            );


        if (!response.ok) {

            throw new Error(
                `API returned ${response.status}`
            );

        }


        const data =
            await response.json();


        let parsed =
            data;


        if (
            typeof data.body ===
            "string"
        ) {

            try {

                parsed =
                    JSON.parse(
                        data.body
                    );

            }

            catch {

                parsed =
                    data.body;

            }

        }


        if (
            Array.isArray(parsed)
        ) {

            resources =
                parsed;

        }

        else if (
            Array.isArray(
                parsed.resources
            )
        ) {

            resources =
                parsed.resources;

        }

        else if (
            Array.isArray(
                parsed.Items
            )
        ) {

            resources =
                parsed.Items;

        }

        else if (
            parsed.body &&
            Array.isArray(
                parsed.body
            )
        ) {

            resources =
                parsed.body;

        }

        else {

            resources = [];

        }


        normalizeResources();

        updateDashboardStats();
        updateAnalytics();
        updateAdminDashboard();

        renderResourcesTable();

        renderResourcesPage();


        showToast(
            `${resources.length} resources loaded from AWS`
        );

    }

    catch (error) {

        console.error(
            "Resource loading error:",
            error
        );


        resources = [];


        updateDashboardStats();
        updateAnalytics();
        updateAdminDashboard();

        renderResourcesTable();

        renderResourcesPage();


        showToast(
            "Unable to load resources from AWS"
        );

    }

}
// =====================================================
// REQUESTS - LIVE AWS DATA
// =====================================================

let requests = [];


async function loadRequests() {

    const table = document.getElementById("requestsTable");

    if (!table) {
        return;
    }

    table.innerHTML = `
        <tr>
            <td colspan="6">
                Loading requests...
            </td>
        </tr>
    `;


    try {

        const idToken = await waitForIdToken();

        if (!idToken) {

            throw new Error(
                "Cognito ID token is not available"
            );

        }


        const response = await fetch(REQUESTS_API_URL + tenantQuery(), {
            method: "GET",
            headers: {
                "Accept": "application/json",
                "Authorization": "Bearer " + idToken
            }
        });


        if (!response.ok) {

            throw new Error(
                `Request API returned ${response.status}`
            );

        }


        const data = await response.json();

        if (Array.isArray(data)) {

            requests = data;

        } else if (Array.isArray(data.requests)) {

            requests = data.requests;

        } else {

            requests = [];

        }


        updateAnalytics();
        updateAdminDashboard();

        renderRequests();

    } catch (error) {

        console.error(
            "Unable to load requests:",
            error
        );


        table.innerHTML = `
            <tr>
                <td colspan="6">
                    Unable to load requests from AWS.
                </td>
            </tr>
        `;

    }

}



// =====================================================
// RENDER REQUESTS
// =====================================================

function requestRecordStatus(request) {

    return String(request.status ?? request.Status ?? "").toUpperCase();

}


function renderActiveOperations() {

    const container = $("activeOperations");

    if (!container) {

        return;

    }

    const active = requests
        .filter(request => {
            const status = requestRecordStatus(request);
            return status && status !== "RELEASED" && status !== "COMPLETED";
        })
        .sort((left, right) =>
            Number(left.priority ?? left.Priority ?? 99) -
            Number(right.priority ?? right.Priority ?? 99)
        );

    if (!active.length) {

        container.innerHTML = `
            <div class="ops-empty">
                <h3>No active operations</h3>
                <p>New requests will appear here when your organization creates them.</p>
                <button class="primary-btn js-open-request" type="button">Create Request</button>
            </div>
        `;

        return;

    }

    container.innerHTML = active.slice(0, 6).map(request => {
        const priority = request.priority ?? request.Priority ?? "—";
        const status = requestRecordStatus(request) || "PENDING";
        const type = request.resource_type ?? request.ResourceType ?? request.request_type ?? "Request";
        const location = request.location ?? request.Location ?? "Location unavailable";
        const created = request.CreatedAt ?? request.createdAt ?? request.created_at ?? "";
        const emphasis = Number(priority) <= 2 ? " ops-item-high" : "";

        return `
            <article class="ops-item${emphasis}">
                <div>
                    <p class="panel-label">${Number(priority) <= 2 ? "High priority" : "Request"}</p>
                    <h3>${escapeHtml(String(type))}</h3>
                    <p>${escapeHtml(String(location))}</p>
                </div>
                <div class="ops-item-meta">
                    <span class="priority-badge">Priority ${escapeHtml(String(priority))}</span>
                    <span class="status-badge ${status === "ALLOCATED" ? "allocated" : "pending"}">${escapeHtml(status)}</span>
                    <span class="cell-meta">${escapeHtml(String(created))}</span>
                    <button class="secondary-btn js-view-requests" type="button">View requests</button>
                </div>
            </article>
        `;
    }).join("");

}


function renderRecentOperations() {

    const container = $("recentOperations");

    if (!container) {

        return;

    }

    const events = [];

    requests.forEach(request => {
        events.push({
            title: "Request created",
            detail: String(request.request_id || "Request") + " · " + String(request.resource_type ?? request.ResourceType ?? ""),
            time: String(request.CreatedAt ?? request.createdAt ?? request.created_at ?? "")
        });
    });

    allocations.forEach(item => {
        const status = String(item.status || "").toUpperCase();
        events.push({
            title: status === "RELEASED" ? "Resource released" : "Allocation completed",
            detail: String(item.request_id || "") + " · " + String(item.resource_id || ""),
            time: String(item.created_at || item.allocated_at || item.updated_at || "")
        });
    });

    if (!events.length) {

        container.innerHTML = `
            <div class="ops-empty">
                <h3>No recent operations</h3>
                <p>Requests and allocations already loaded for this organization will appear here.</p>
            </div>
        `;

        return;

    }

    container.innerHTML = `<ol class="ops-timeline">${events.slice(0, 8).map(event => `
        <li>
            <strong>${escapeHtml(event.title)}</strong>
            <span>${escapeHtml(event.detail)}</span>
            <em>${escapeHtml(event.time)}</em>
        </li>
    `).join("")}</ol>`;

}


function renderRequests() {

    const table = document.getElementById("requestsTable");

    if (!table) {
        return;
    }


    const searchInput =
        document.getElementById("requestsSearch");

    const statusFilter =
        document.getElementById("requestsStatusFilter");


    const searchTerm =
        searchInput
            ? searchInput.value.trim().toLowerCase()
            : "";


    const selectedStatus =
        statusFilter
            ? statusFilter.value
            : "ALL";


    const filteredRequests = requests.filter(request => {

        const requestId =
            String(request.request_id || "").toLowerCase();

        const resourceType =
    String(
        request.resource_type ??
        request.ResourceType ??
        ""
    ).toLowerCase();

        const location =
    String(
        request.location ??
        request.Location ??
        ""
    ).toLowerCase();

        const status =
    String(request.status ?? request.Status ?? "").toUpperCase();


        const matchesSearch =
            !searchTerm ||
            requestId.includes(searchTerm) ||
            resourceType.includes(searchTerm) ||
            location.includes(searchTerm);


        const matchesStatus =
            selectedStatus === "ALL" ||
            status === selectedStatus;


        return matchesSearch && matchesStatus;

    });


    if (filteredRequests.length === 0) {

        table.innerHTML = `
            <tr>
                <td colspan="6">
                    No matching requests. Create a request when your organization needs a resource.
                </td>
            </tr>
        `;

        return;

    }


    table.innerHTML = filteredRequests.map(request => {

        const requestId =
    request.request_id || "—";

const resourceType =
    request.resource_type ??
    request.ResourceType ??
    "—";

const location =
    request.location ??
    request.Location ??
    "—";

const priority =
    request.priority ??
    request.Priority ??
    "—";

const status =
    String(
        request.status ??
        request.Status ??
        "UNKNOWN"
    ).toUpperCase();

const createdAt =
    request.CreatedAt ??
    request.createdAt ??
    "-";


        let statusClass = "status-badge";
        let statusLabel = status;

        if (status === "ALLOCATED") {

            statusClass += " status-allocated";
            statusLabel = "ALLOCATED";

        } else if (status === "PENDING") {

            statusClass += " status-pending";
            statusLabel = "PENDING";

        } else if (status === "WAITING") {

            statusClass += " status-waiting";
            statusLabel = "WAITING";

        }

        const high = Number(priority) <= 2 ? " request-high" : "";

        return `
            <tr class="${high.trim()}">

                <td>
                    <strong>
                        ${escapeHtml(requestId)}
                    </strong>
                </td>

                <td>
                    ${formatResourceType(resourceType)}
                </td>

                <td>
                    ${escapeHtml(location)}
                </td>

                <td>
                    <span class="priority-badge">
                        P${escapeHtml(priority)}
                    </span>
                </td>

                <td>
                    <span class="${statusClass}">
                        ${escapeHtml(statusLabel)}
                    </span>
                </td>

                <td>
                    ${escapeHtml(createdAt)}
                </td>

            </tr>
        `;

    }).join("");

}



// =====================================================
// RESOURCE TYPE FORMATTER
// =====================================================

function formatResourceType(type) {

    if (type === "ICU_BED") {
        return "ICU Bed";
    }


    if (type === "GENERAL_BED") {
        return "General Bed";
    }


    return escapeHtml(type || "—");

}



// =====================================================
// SAFE HTML OUTPUT
// =====================================================

function escapeHtml(value) {

    return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");

}



// =====================================================
// REQUEST SEARCH + FILTER
// =====================================================

function initializeRequestControls() {

    const searchInput =
        document.getElementById("requestsSearch");


    const statusFilter =
        document.getElementById("requestsStatusFilter");


    const refreshButton =
        document.getElementById("requestsRefreshBtn");


    if (searchInput) {

        searchInput.addEventListener(
            "input",
            renderRequests
        );

    }


    if (statusFilter) {

        statusFilter.addEventListener(
            "change",
            renderRequests
        );

    }


    if (refreshButton) {

        refreshButton.addEventListener(
            "click",
            loadRequests
        );

    }

}


/* =========================================================
   NORMALIZE RESOURCE DATA
========================================================= */

function normalizeResources() {

    resources =
        resources.map(
            resource => {

                return {

                    id:
                        resource.id ??
                        resource.resource_id ??
                        resource.ResourceId ??
                        resource.ResourceID ??
                        resource.resourceId ??
                        "",


                    type:
                        resource.type ??
                        resource.resource_type ??
                        resource.ResourceType ??
                        resource.Type ??
                        "",


                    location:
                        resource.location ??
                        resource.Location ??
                        "",


                    available:
                        resource.available ??
                        resource.Available ??
                        false,

                    visibility:
                        resource.visibility || "PRIVATE",

                    resource_type_id:
                        resource.resource_type_id || "",

                    location_id:
                        resource.location_id || "",

                    attributes:
                        resource.attributes || {},

                    name:
                        resource.name || "",

                    public_name:
                        resource.public_name || "",

                    public_description:
                        resource.public_description || "",

                    public_contact:
                        resource.public_contact || "",

                    show_availability:
                        resource.show_availability === true,

                    operational_status:
                        resource.operational_status ??
                        resource.operationalStatus ??
                        "",

                    tracking_mode:
                        resource.tracking_mode ??
                        resource.trackingMode ??
                        "INDIVIDUAL",

                    condition:
                        resource.condition || "",

                    serial_number:
                        resource.serial_number || "",

                    asset_tag:
                        resource.asset_tag || "",

                    department:
                        resource.department || "",

                    responsible_team:
                        resource.responsible_team || "",

                    assigned_to:
                        resource.assigned_to || "",

                    description:
                        resource.description || ""

                };

            }
        );

}


/* =========================================================
   DASHBOARD STATISTICS
========================================================= */

function updateDashboardStats() {

    const total =
        resources.length;


    const available =
        resources.filter(
            resource =>
                isResourceAvailable(
                    resource
                )
        ).length;


    const allocated =
        total - available;


    if (
        $("totalResources")
    ) {

        $("totalResources")
            .textContent =
            total;

    }


    if (
        $("availableResources")
    ) {

        $("availableResources")
            .textContent =
            available;

    }


    if (
        $("allocatedResources")
    ) {

        $("allocatedResources")
            .textContent =
            allocated;

    }

}


/* =========================================================
   RESOURCE STATUS
========================================================= */

function isResourceAvailable(
    resource
) {

    return (

        resource.available ===
        true

        ||

        String(
            resource.available
        ).toLowerCase() ===
        "true"

    );

}



/* =========================================================
   ADVANCED ANALYTICS
========================================================= */

function updateAnalytics() {

    const totalResources =
        resources.length;

    const availableResources =
        resources.filter(
            resource =>
                isResourceAvailable(resource)
        ).length;

    const allocatedResources =
        totalResources -
        availableResources;


    const totalRequests =
        requests.length;

    const allocatedRequests =
        requests.filter(
            request =>
                String(
                    request.status ??
                    request.Status ??
                    ""
                ).toUpperCase() ===
                "ALLOCATED"
        ).length;

    const pendingRequests =
        requests.filter(
            request =>
                String(
                    request.status ??
                    request.Status ??
                    ""
                ).toUpperCase() ===
                "PENDING"
        ).length;

    const releasedRequests =
        requests.filter(
            request =>
                String(
                    request.status ??
                    request.Status ??
                    ""
                ).toUpperCase() ===
                "RELEASED"
        ).length;


    if ($("analyticsTotalRequests")) {
        $("analyticsTotalRequests")
            .textContent =
            totalRequests;
    }

    if ($("analyticsAllocatedRequests")) {
        $("analyticsAllocatedRequests")
            .textContent =
            allocatedRequests;
    }

    if ($("analyticsPendingRequests")) {
        $("analyticsPendingRequests")
            .textContent =
            pendingRequests;
    }

    if ($("analyticsReleasedRequests")) {
        $("analyticsReleasedRequests")
            .textContent =
            releasedRequests;
    }

    const availableBar = $("readinessAvailableBar");

    if (availableBar) {

        const width = totalResources
            ? Math.round((availableResources / totalResources) * 100)
            : 0;

        availableBar.style.width = width + "%";

    }

    renderActiveOperations();
    renderRecentOperations();

    if ($("queuePending")) {
        $("queuePending").textContent = pendingRequests;
    }

    if ($("queueAllocated")) {
        $("queueAllocated").textContent = allocatedRequests;
    }

    if ($("queueReleased")) {
        $("queueReleased").textContent = releasedRequests;
    }


    renderAnalyticsBars(
        "resourceStatusChart",
        [
            {
                label: "Available",
                value: availableResources
            },
            {
                label: "Allocated",
                value: allocatedResources
            }
        ]
    );


    renderAnalyticsBars(
        "requestStatusChart",
        [
            {
                label: "Allocated",
                value: allocatedRequests
            },
            {
                label: "Pending",
                value: pendingRequests
            },
            {
                label: "Released",
                value: releasedRequests
            }
        ]
    );


    const priorityCounts = {
        1: 0,
        2: 0,
        3: 0,
        4: 0,
        5: 0
    };


    requests.forEach(
        request => {

            const priority =
                Number(
                    request.priority ??
                    request.Priority
                );

            if (
                priority >= 1 &&
                priority <= 5
            ) {
                priorityCounts[priority]++;
            }

        }
    );


    renderAnalyticsBars(
        "priorityChart",
        [
            {
                label: "Priority 1",
                value: priorityCounts[1]
            },
            {
                label: "Priority 2",
                value: priorityCounts[2]
            },
            {
                label: "Priority 3",
                value: priorityCounts[3]
            },
            {
                label: "Priority 4",
                value: priorityCounts[4]
            },
            {
                label: "Priority 5",
                value: priorityCounts[5]
            }
        ]
    );
}


function renderAnalyticsBars(
    elementId,
    items
) {

    const container =
        $(elementId);

    if (!container) {
        return;
    }


    const total =
        items.reduce(
            (
                sum,
                item
            ) =>
                sum + item.value,
            0
        );


    if (total === 0) {

                container.innerHTML = `
            <div class="analytics-empty">
                No activity in the current view.
            </div>
        `;

        return;
    }


    container.innerHTML =
        items
            .map(
                item => {

                    const percentage =
                        Math.max(
                            0,
                            Math.min(
                                100,
                                (
                                    item.value /
                                    total
                                ) *
                                100
                            )
                        );

                    return `
                        <p class="tally-line">
                            <span>${item.label}</span>
                            <b>${item.value}</b>
                        </p>
                    `;

                }
            )
            .join("");
}

function getResourceStatus(
    resource
) {

    return getOperationalStatusLabel(resource);

}


function getEmergencyAvailabilityLabel(
    resource
) {

    return isResourceAvailable(resource)
        ? "Available"
        : "Unavailable";

}


function getOperationalStatusLabel(
    resource
) {

    const stored = String(
        resource.operational_status || ""
    ).trim().toUpperCase();

    if (stored) {
        return stored;
    }

    return isResourceAvailable(resource)
        ? "AVAILABLE"
        : "ALLOCATED";

}


function isQuantityResource(
    resource
) {

    return String(resource.tracking_mode || "INDIVIDUAL").toUpperCase() === "QUANTITY";

}


/* =========================================================
   RESOURCE TABLE
========================================================= */

function canOperateResources() {

    const role = currentUser.organization?.role;

    return role === "OWNER" || role === "ADMIN" || role === "OPERATOR";

}


function resourceRowActions(resource) {

    const operational = getOperationalStatusLabel(resource);
    const canOperate = canOperateResources();
    const quantity = isQuantityResource(resource);

    const edit = canOperate
        ? `
            <button
                type="button"
                class="resource-edit-btn"
                data-resource-id="${escapeHtml(resource.id)}"
            >
                Edit
            </button>
          `
        : "";

    let everyday = "";

    if (canOperate) {
        if (quantity) {
            everyday = `
                <button type="button" class="resource-qty-reserve-btn" data-resource-id="${escapeHtml(resource.id)}">
                    Reserve qty
                </button>
                <button type="button" class="resource-qty-allocate-btn" data-resource-id="${escapeHtml(resource.id)}">
                    Allocate qty
                </button>
                <button type="button" class="resource-everyday-return-btn" data-resource-id="${escapeHtml(resource.id)}">
                    Return qty
                </button>
                <button type="button" class="resource-maintenance-btn" data-resource-id="${escapeHtml(resource.id)}">
                    Maintenance
                </button>
                <button type="button" class="resource-retire-btn" data-resource-id="${escapeHtml(resource.id)}">
                    Retire
                </button>
            `;
        } else if (operational === "AVAILABLE") {
            everyday = `
                <button type="button" class="resource-reserve-btn" data-resource-id="${escapeHtml(resource.id)}">
                    Reserve
                </button>
                <button type="button" class="resource-everyday-allocate-btn" data-resource-id="${escapeHtml(resource.id)}">
                    Allocate
                </button>
                <button type="button" class="resource-maintenance-btn" data-resource-id="${escapeHtml(resource.id)}">
                    Maintenance
                </button>
                <button type="button" class="resource-damage-btn" data-resource-id="${escapeHtml(resource.id)}">
                    Damage
                </button>
                <button type="button" class="resource-retire-btn" data-resource-id="${escapeHtml(resource.id)}">
                    Retire
                </button>
                <button type="button" class="resource-assign-btn" data-resource-id="${escapeHtml(resource.id)}">
                    Assign
                </button>
            `;
        } else if (operational === "RESERVED") {
            everyday = `
                <button type="button" class="resource-reservation-release-btn" data-resource-id="${escapeHtml(resource.id)}">
                    Release reservation
                </button>
                <button type="button" class="resource-everyday-allocate-btn" data-resource-id="${escapeHtml(resource.id)}">
                    Allocate
                </button>
                <button type="button" class="resource-maintenance-btn" data-resource-id="${escapeHtml(resource.id)}">
                    Maintenance
                </button>
                <button type="button" class="resource-damage-btn" data-resource-id="${escapeHtml(resource.id)}">
                    Damage
                </button>
            `;
        } else if (operational === "ALLOCATED") {
            everyday = `
                <button type="button" class="resource-everyday-return-btn" data-resource-id="${escapeHtml(resource.id)}">
                    Return
                </button>
                <button type="button" class="resource-in-use-btn" data-resource-id="${escapeHtml(resource.id)}">
                    Mark in use
                </button>
            `;
        } else if (operational === "IN_USE") {
            everyday = `
                <button type="button" class="resource-in-use-return-btn" data-resource-id="${escapeHtml(resource.id)}">
                    Return to available
                </button>
                <button type="button" class="resource-maintenance-btn" data-resource-id="${escapeHtml(resource.id)}">
                    Maintenance
                </button>
                <button type="button" class="resource-damage-btn" data-resource-id="${escapeHtml(resource.id)}">
                    Damage
                </button>
            `;
        } else if (operational === "MAINTENANCE") {
            everyday = `
                <button type="button" class="resource-maintenance-complete-btn" data-resource-id="${escapeHtml(resource.id)}">
                    Complete maintenance
                </button>
                <button type="button" class="resource-retire-btn" data-resource-id="${escapeHtml(resource.id)}">
                    Retire
                </button>
            `;
        } else if (operational === "DAMAGED") {
            everyday = `
                <button type="button" class="resource-damage-recover-btn" data-resource-id="${escapeHtml(resource.id)}">
                    Recover
                </button>
                <button type="button" class="resource-maintenance-btn" data-resource-id="${escapeHtml(resource.id)}">
                    Maintenance
                </button>
                <button type="button" class="resource-retire-btn" data-resource-id="${escapeHtml(resource.id)}">
                    Retire
                </button>
            `;
        }
    }

    const emergencyRelease =
        canOperate &&
        !isResourceAvailable(resource) &&
        operational !== "RESERVED" &&
        operational !== "MAINTENANCE" &&
        operational !== "DAMAGED" &&
        operational !== "RETIRED" &&
        operational !== "IN_USE"
            ? `
                <button
                    type="button"
                    class="release-resource-btn"
                    onclick="releaseResource('${escapeHtml(resource.id)}')"
                >
                    Emergency release
                </button>
              `
            : "";

    return `

        <button
            type="button"
            class="resource-history-btn"
            data-resource-id="${escapeHtml(resource.id)}"
        >
            History
        </button>

        ${edit}

        ${everyday}

        ${emergencyRelease}

    `;

}


function renderResourcesTable() {

    const table =
        $("resourcesTable");


    if (!table) return;


    if (
        resources.length === 0
    ) {

        table.innerHTML = `

            <tr>

                <td colspan="4">
                    No resources registered for this view.
                </td>

            </tr>

        `;

        return;

    }


    table.innerHTML =
        resources
            .map(
                resource => {

                    const status =
                        getResourceStatus(
                            resource
                        );


                    return `

                        <tr>

                            <td>

                                <div class="cell-title">
                                    ${escapeHtml(resource.name || resource.type || "Resource")}
                                </div>

                                <div class="cell-meta">
                                    ${escapeHtml(resource.id)}
                                </div>

                            </td>

                            <td>
                                ${escapeHtml(
                                    resource.type
                                )}
                            </td>

                            <td>
                                ${escapeHtml(
                                    resource.location
                                )}
                            </td>

                            <td>

                                <span
                                    class="status-badge ${
                                        status ===
                                        "AVAILABLE"
                                            ? "available"
                                            : "allocated"
                                    }"
                                >

                                    ${status}

                                </span>

                            </td>

                        </tr>

                    `;

                }
            )
            .join("");


    applyResourceDashboardFilters();

}


/* =========================================================
   RESOURCES PAGE
========================================================= */

function renderResourcesPage() {

    const table =
        $("resourcesPageTable");


    if (!table) return;
        populateResourcePageFilters();


    if (
        resources.length === 0
    ) {

        table.innerHTML = `

            <tr>

                <td colspan="7">
                    No resources registered. Add a resource for this organization to start allocation.
                </td>

            </tr>

        `;

        return;

    }


    table.innerHTML =
        resources
            .map(
                resource => {

                    const emergencyLabel =
                        getEmergencyAvailabilityLabel(resource);

                    const operational =
                        getOperationalStatusLabel(resource);


                    const action = resourceRowActions(resource);


                    return `

                        <tr data-resource-id="${escapeHtml(resource.id)}">

                            <td>

                                <div class="cell-title">
                                    ${escapeHtml(resource.name || resource.type || "Resource")}
                                </div>

                                <div class="cell-meta">
                                    ${escapeHtml(resource.id)}
                                </div>

                            </td>

                            <td>
                                ${escapeHtml(
                                    resource.type
                                )}
                            </td>

                            <td>
                                ${escapeHtml(
                                    resource.location
                                )}
                            </td>

                            <td>

                                <span
                                    class="status-badge ${
                                        emergencyLabel === "Available"
                                            ? "available"
                                            : "allocated"
                                    }"
                                >

                                    ${escapeHtml(emergencyLabel)}

                                </span>

                            </td>

                            <td>

                                <span
                                    class="status-badge ${
                                        operational === "AVAILABLE"
                                            ? "available"
                                            : "allocated"
                                    }"
                                >

                                    ${escapeHtml(operational)}

                                </span>

                            </td>

                            <td>
                                <span class="status-badge ${(resource.visibility || "PRIVATE") === "PUBLIC" ? "public" : "private"}">
                                    ${escapeHtml(resource.visibility || "PRIVATE")}
                                </span>
                            </td>

                            <td>
                                ${action}
                            </td>

                        </tr>

                    `;

                }
            )
            .join("");


    applyResourcePageFilters();

}

/* =========================================================
   RELEASE RESOURCE
========================================================= */

function showResourceHistoryModal(resourceId, history) {

    const modal =
        document.getElementById(
            "resourceHistoryModal"
        );

    const resourceIdElement =
        document.getElementById(
            "resourceHistoryResourceId"
        );

    const content =
        document.getElementById(
            "resourceHistoryContent"
        );

    if (!modal || !resourceIdElement || !content) {
        return;
    }

    resourceIdElement.textContent =
        resourceId;

    if (!Array.isArray(history) || history.length === 0) {

        content.innerHTML = `
            <div class="resource-history-empty">
                No status history available.
            </div>
        `;

    } else {

        content.innerHTML =
            history.map(item => {

                const previousStatus =
                    item.previous_status ||
                    "UNKNOWN";

                const newStatus =
                    item.new_status ||
                    "UNKNOWN";

                const reason =
                    item.reason ||
                    "STATUS_CHANGED";

                const changedAt =
                    item.changed_at
                        ? new Date(
                            item.changed_at
                          ).toLocaleString()
                        : "Unknown time";

                const requestId =
                    item.request_id ||
                    "";

                const allocationId =
                    item.allocation_id ||
                    "";

                return `
                    <div class="resource-history-item">

                        <div class="resource-history-transition">

                            <span class="history-status">
                                ${escapeHtml(previousStatus)}
                            </span>

                            <span class="history-arrow">
                                →
                            </span>

                            <span class="history-status">
                                ${escapeHtml(newStatus)}
                            </span>

                        </div>

                        <div class="resource-history-meta">

                            <strong>
                                ${escapeHtml(reason)}
                            </strong>

                            <span>
                                ${escapeHtml(changedAt)}
                            </span>

                        </div>

                        ${
                            requestId
                                ? `
                                    <div class="resource-history-reference">
                                        Request:
                                        ${escapeHtml(requestId)}
                                    </div>
                                  `
                                : ""
                        }

                        ${
                            allocationId
                                ? `
                                    <div class="resource-history-reference">
                                        Allocation:
                                        ${escapeHtml(allocationId)}
                                    </div>
                                  `
                                : ""
                        }

                    </div>
                `;

            }).join("");

    }

    modal.style.display = "flex";
}


function closeResourceHistoryModal() {

    const modal =
        document.getElementById(
            "resourceHistoryModal"
        );

    if (modal) {
        modal.style.display = "none";
    }
}


async function viewResourceHistory(resourceId) {

    try {

        const response =
            await fetch(
                `${RESOURCE_HISTORY_API_URL}?resource_id=${encodeURIComponent(resourceId)}&organization_id=${encodeURIComponent(selectedOrganizationId())}`,
                {
                    method: "GET",
                    headers: {
                        "Authorization": "Bearer " + getIdToken()
                    }
                }
            );

        if (!response.ok) {
            throw new Error(
                `History request failed: ${response.status}`
            );
        }

        const history =
            await response.json();

        showResourceHistoryModal(
            resourceId,
            history
        );

    } catch (error) {

        console.error(
            "Resource history error:",
            error
        );

        showToast(
            "Unable to load resource history."
        );

    }
}


async function postEverydayResource(url, payload) {

    const response = await fetch(url, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + getIdToken(),
        },
        body: JSON.stringify({
            ...payload,
            organization_id: selectedOrganizationId(),
        }),
    });

    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
        throw new Error(data.message || "Request failed");
    }

    return data;
}


async function reserveResource(resourceId) {

    try {
        showToast("Reserving resource...");
        await postEverydayResource(RESOURCE_RESERVE_API_URL, { resource_id: resourceId });
        showToast("Resource reserved.");
        await loadResources();
    } catch (error) {
        console.error("Reserve failed:", error);
        showToast(error.message || "Unable to reserve resource.");
    }
}


async function releaseResourceReservation(resourceId) {

    try {
        showToast("Releasing reservation...");
        await postEverydayResource(RESOURCE_RESERVATION_RELEASE_API_URL, { resource_id: resourceId });
        showToast("Reservation released.");
        await loadResources();
    } catch (error) {
        console.error("Reservation release failed:", error);
        showToast(error.message || "Unable to release reservation.");
    }
}


async function everydayAllocateResource(resourceId, quantity) {

    const body = { resource_id: resourceId };

    if (quantity) {
        body.quantity = quantity;
    }

    const purpose = window.prompt("Purpose (optional)", "") || "";

    if (purpose.trim()) {
        body.purpose = purpose.trim();
    }

    try {
        showToast("Creating everyday allocation...");
        const data = await postEverydayResource(RESOURCE_EVERYDAY_ALLOCATE_API_URL, body);

        if (data.allocation_id) {
            everydayAllocationByResource[resourceId] = data.allocation_id;
        }

        showToast(
            data.allocation_id
                ? `Allocated (${data.allocation_id}).`
                : "Everyday allocation created."
        );
        await loadResources();
    } catch (error) {
        console.error("Everyday allocate failed:", error);
        showToast(error.message || "Unable to allocate resource.");
    }
}


async function everydayReturnResource(resourceId) {

    let allocationId =
        everydayAllocationByResource[resourceId] ||
        window.prompt("Everyday allocation ID to return", "") ||
        "";

    allocationId = allocationId.trim();

    if (!allocationId) {
        showToast("Allocation ID is required to return.");
        return;
    }

    try {
        showToast("Returning everyday allocation...");
        await postEverydayResource(RESOURCE_EVERYDAY_RETURN_API_URL, {
            resource_id: resourceId,
            allocation_id: allocationId,
        });
        delete everydayAllocationByResource[resourceId];
        showToast("Everyday allocation returned.");
        await loadResources();
    } catch (error) {
        console.error("Everyday return failed:", error);
        showToast(error.message || "Unable to return allocation.");
    }
}


function promptPositiveQuantity(label) {

    const raw = window.prompt(label, "1");

    if (raw === null) {
        return null;
    }

    const value = Number.parseInt(String(raw).trim(), 10);

    if (!Number.isFinite(value) || value < 1) {
        showToast("Enter a whole number greater than zero.");
        return null;
    }

    return value;
}


async function reserveQuantityResource(resourceId) {

    const quantity = promptPositiveQuantity("Quantity to reserve");

    if (!quantity) {
        return;
    }

    try {
        showToast("Reserving quantity...");
        await postEverydayResource(RESOURCE_RESERVE_API_URL, {
            resource_id: resourceId,
            quantity,
        });
        showToast(`Reserved ${quantity} units.`);
        await loadResources();
    } catch (error) {
        console.error("Quantity reserve failed:", error);
        showToast(error.message || "Unable to reserve quantity.");
    }
}


async function everydayAllocateQuantityResource(resourceId) {

    const quantity = promptPositiveQuantity("Quantity to allocate");

    if (!quantity) {
        return;
    }

    await everydayAllocateResource(resourceId, quantity);
}


async function lifecycleResourceAction(url, resourceId, promptLabel) {

    const body = { resource_id: resourceId };

    if (promptLabel) {
        const notes = window.prompt(promptLabel, "") || "";
        if (notes.trim()) {
            body.notes = notes.trim();
        }
    }

    try {
        showToast("Updating resource...");
        await postEverydayResource(url, body);
        showToast("Resource updated.");
        await loadResources();
    } catch (error) {
        console.error("Lifecycle action failed:", error);
        showToast(error.message || "Unable to update resource.");
    }
}


async function assignResourceAction(resourceId) {

    const assignedTo = window.prompt("Assigned to (member id or name)", "") || "";
    const department = window.prompt("Department (optional)", "") || "";

    if (!assignedTo.trim() && !department.trim()) {
        showToast("Assignment target is required.");
        return;
    }

    try {
        showToast("Assigning resource...");
        await postEverydayResource(RESOURCE_ASSIGN_API_URL, {
            resource_id: resourceId,
            assigned_to: assignedTo.trim(),
            department: department.trim(),
        });
        showToast("Resource assigned.");
        await loadResources();
    } catch (error) {
        console.error("Assign failed:", error);
        showToast(error.message || "Unable to assign resource.");
    }
}


async function releaseResource(resourceId) {

    const confirmed =
        window.confirm(
            `Emergency release resource ${resourceId}?`
        );

    if (!confirmed) {
        return;
    }

    try {

        showToast(
            "Releasing resource..."
        );

        const response =
            await fetch(
                RELEASE_RESOURCE_API_URL,
                {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "Authorization": "Bearer " + getIdToken(),
                    },

                    body: JSON.stringify({
                        resource_id: resourceId,
                        organization_id: selectedOrganizationId()
                    })
                }
            );


        const data =
            await response.json();


        if (!response.ok) {

            showToast(
                data.message ||
                "Failed to release resource."
            );

            return;

        }


        showToast(
            "Resource released successfully."
        );


        addNotification(
            "Resource released",
            `${resourceId} is now available.`
        );


        await loadResources();

    }

    catch (error) {

        console.error(
            "Resource release failed:",
            error
        );


        showToast(
            "Unable to release resource. Please try again."
        );

    }

}


/* =========================================================
   DASHBOARD RESOURCE FILTER
========================================================= */

function applyResourceDashboardFilters() {

    const search =
        $("resourceSearch");


    const filter =
        $("resourceFilter");


    const table =
        $("resourcesTable");


    if (
        !search ||
        !filter ||
        !table
    ) {

        return;

    }


    const query =
        search.value
            .trim()
            .toLowerCase();


    const selected =
        filter.value;


    const filtered =
        resources.filter(
            resource => {

                const status =
                    getResourceStatus(
                        resource
                    );


                const matchesSearch =
                    !query ||

                    String(
                        resource.id
                    )
                        .toLowerCase()
                        .includes(query) ||

                    String(
                        resource.type
                    )
                        .toLowerCase()
                        .includes(query) ||

                    String(
                        resource.location
                    )
                        .toLowerCase()
                        .includes(query);


                const matchesFilter =
                    selected === "ALL" ||
                    selected === status;


                return (
                    matchesSearch &&
                    matchesFilter
                );

            }
        );


    table.innerHTML =
        filtered.length === 0

            ? `

                <tr>

                    <td colspan="4">
                        No matching resources found.
                    </td>

                </tr>

              `

            :

              filtered
                .map(
                    resource => {

                        const status =
                            getResourceStatus(
                                resource
                            );


                        return `

                            <tr>

                                <td>

                                    <strong>
                                        ${escapeHtml(
                                            resource.id
                                        )}
                                    </strong>

                                </td>

                                <td>
                                    ${escapeHtml(
                                        resource.type
                                    )}
                                </td>

                                <td>
                                    ${escapeHtml(
                                        resource.location
                                    )}
                                </td>

                                <td>

                                    <span
                                        class="status-badge ${
                                            status ===
                                            "AVAILABLE"
                                                ? "available"
                                                : "allocated"
                                        }"
                                    >

                                        ${status}

                                    </span>

                                </td>

                            </tr>

                        `;

                    }
                )
                .join("");

}
function populateResourcePageFilters() {

    const typeFilter =
        $("resourcesPageTypeFilter");

    const locationFilter =
        $("resourcesPageLocationFilter");

    if (
        !typeFilter ||
        !locationFilter
    ) {
        return;
    }


    const currentType =
        typeFilter.value;

    const currentLocation =
        locationFilter.value;


    const types = [
        ...new Set(
            resources
                .map(
                    resource =>
                        String(
                            resource.type || ""
                        ).trim()
                )
                .filter(Boolean)
        )
    ].sort();


    const locations = [
        ...new Set(
            resources
                .map(
                    resource =>
                        String(
                            resource.location || ""
                        ).trim()
                )
                .filter(Boolean)
        )
    ].sort();


    typeFilter.innerHTML = `
        <option value="ALL">
            All Types
        </option>
    `;

    types.forEach(
        type => {

            typeFilter.innerHTML += `
                <option value="${escapeHtml(type)}">
                    ${escapeHtml(type)}
                </option>
            `;

        }
    );


    locationFilter.innerHTML = `
        <option value="ALL">
            All Locations
        </option>
    `;

    locations.forEach(
        location => {

            locationFilter.innerHTML += `
                <option value="${escapeHtml(location)}">
                    ${escapeHtml(location)}
                </option>
            `;

        }
    );


    if (
        types.includes(currentType)
    ) {
        typeFilter.value =
            currentType;
    }


    if (
        locations.includes(currentLocation)
    ) {
        locationFilter.value =
            currentLocation;
    }

}

/* =========================================================
   RESOURCE PAGE FILTER
========================================================= */

function applyResourcePageFilters() {

    const search =
        $("resourcesPageSearch");

    const statusFilter =
        $("resourcesPageFilter");

    const typeFilter =
        $("resourcesPageTypeFilter");

    const locationFilter =
        $("resourcesPageLocationFilter");

    const table =
        $("resourcesPageTable");

    if (
        !search ||
        !statusFilter ||
        !typeFilter ||
        !locationFilter ||
        !table
    ) {
        return;
    }


    const query =
        search.value
            .trim()
            .toLowerCase();


    const selectedStatus =
        statusFilter.value;


    const selectedType =
        typeFilter.value;


    const selectedLocation =
        locationFilter.value;


    const filtered =
        resources.filter(
            resource => {

                const status =
                    getResourceStatus(
                        resource
                    );


                const resourceType =
                    String(
                        resource.type || ""
                    );


                const resourceLocation =
                    String(
                        resource.location || ""
                    );


                const matchesSearch =
                    !query ||

                    String(
                        resource.id || ""
                    )
                        .toLowerCase()
                        .includes(query) ||

                    resourceType
                        .toLowerCase()
                        .includes(query) ||

                    resourceLocation
                        .toLowerCase()
                        .includes(query);


                const matchesStatus =
                    selectedStatus === "ALL" ||
                    selectedStatus === status;


                const matchesType =
                    selectedType === "ALL" ||
                    resourceType === selectedType;


                const matchesLocation =
                    selectedLocation === "ALL" ||
                    resourceLocation === selectedLocation;

                const selectedVisibility =
                    $("resourcesPageVisibilityFilter")?.value || "ALL";

                const matchesVisibility =
                    selectedVisibility === "ALL" ||
                    (resource.visibility || "PRIVATE") === selectedVisibility;


                return (
                    matchesSearch &&
                    matchesStatus &&
                    matchesType &&
                    matchesLocation &&
                    matchesVisibility
                );

            }
        );


    table.innerHTML =
        filtered.length === 0

            ? `

                <tr>

                    <td colspan="6">
                        No resources match these filters.
                    </td>

                </tr>

              `

            :

              filtered
                .map(
                    resource => {

                        const status =
                            getResourceStatus(
                                resource
                            );


                        const action = resourceRowActions(resource);


                        return `

                            <tr>

                                <td>

                                    <strong>
                                        ${escapeHtml(
                                            resource.id
                                        )}
                                    </strong>

                                </td>

                                <td>
                                    ${escapeHtml(
                                        resource.type
                                    )}
                                </td>

                                <td>
                                    ${escapeHtml(
                                        resource.location
                                    )}
                                </td>

                                <td>

                                    <span
                                        class="status-badge ${
                                            status ===
                                            "AVAILABLE"
                                                ? "available"
                                                : "allocated"
                                        }"
                                    >
                                        ${status}
                                    </span>

                                </td>

                                <td>
                                    ${action}
                                </td>

                            </tr>

                        `;

                    }
                )
                .join("");

}

/* =========================================================
   ALLOCATION REQUEST
========================================================= */

async function submitAllocation(
    event
) {

    event.preventDefault();

    if (tenantContextLoading || !selectedOrganizationId()) {

        showResult(
            "Organization context is still loading.",
            "error"
        );

        return;

    }


    const form =
        $("allocationForm");


    const button =
        $("allocateBtn");


    const requestId =
        $("requestId")
            .value
            .trim();


    const resourceType =
        $("resourceType")
            .value;


    const location =
        $("location")
            .value
            .trim();


    const priority =
        Number(
            $("priority").value
        );


    hideResult();


    if (
        !requestId ||
        !resourceType ||
        !location ||
        !priority
    ) {

        showResult(
            "Please complete all request fields.",
            "error"
        );

        return;

    }


    button.disabled =
        true;


    button.classList.add(
        "loading"
    );


    try {

        const payload = {

            request_id:
                requestId,

            resource_type:
                $("resourceType")?.selectedOptions?.[0]?.textContent?.trim() || resourceType,

            request_type_id:
                resourceType,

            attributes:
                readAttributeValues("requestAttributeFields"),

            location_id:
                location,

            organization_id:
                selectedOrganizationId(),

            priority:
                priority

        };


        const response =
            await fetch(
                API_URL,
                {

                    method:
                        "POST",

                    headers: {

                        "Content-Type":
                            "application/json",

                        "Authorization":
                            "Bearer " + getIdToken()

                    },

                    body:
                        JSON.stringify(
                            payload
                        )

                }
            );


        const raw =
            await response.text();


        let data;


        try {

            data =
                JSON.parse(raw);

        }

        catch {

            data =
                raw;

        }


        if (!response.ok) {

            throw new Error(

                getApiMessage(
                    data
                ) ||

                `Request failed with status ${response.status}`

            );

        }


        let result =
            data;


        if (
            data &&
            typeof data.body ===
            "string"
        ) {

            try {

                result =
                    JSON.parse(
                        data.body
                    );

            }

            catch {

                result =
                    data.body;

            }

        }


        const message =
            getApiMessage(
                result
            ) ||

            "Resource allocation request processed successfully.";


        showResult(
            message,
            "success"
        );


        addNotification(
            "Allocation request processed",
            message
        );


        addAllocationFromResponse(
            result
        );


        form.reset();


        await loadResources();

    }

    catch (error) {

        console.error(
            "Allocation error:",
            error
        );


        showResult(
            error.message ||
            "Unable to process allocation request.",
            "error"
        );


        addNotification(
            "Allocation request failed",

            error.message ||
            "Unable to process the request."
        );

    }

    finally {

        button.disabled =
            false;


        button.classList.remove(
            "loading"
        );

    }

}


/* =========================================================
   API MESSAGE
========================================================= */

function getApiMessage(data) {

    if (!data) return "";


    if (
        typeof data ===
        "string"
    ) {

        return data;

    }


    return (

        data.message ||

        data.Message ||

        data.body?.message ||

        data.body?.Message ||

        data.error ||

        data.Error ||

        ""

    );

}


/* =========================================================
   PROFILE
========================================================= */

function openProfile() {

    const modal =
        $("profileModal");


    if (!modal) return;


    updateUserInterface();


    modal.classList.remove(
        "hidden"
    );

}


function closeProfile() {

    const modal =
        $("profileModal");


    if (!modal) return;


    modal.classList.add(
        "hidden"
    );

}

/* =========================================================
   EDIT PROFILE
========================================================= */

function openEditProfile() {

    const name =
        currentUser.name || "";

    const phone =
        currentUser.phone || "";

    const newName =
        window.prompt(
            "Enter your name:",
            name
        );

    if (newName === null) {
        return;
    }

    const newPhone =
        window.prompt(
            "Enter your mobile number:",
            phone
        );

    if (newPhone === null) {
        return;
    }

    currentUser.name =
        newName.trim() || name;

    currentUser.phone =
        newPhone.trim() || phone;

    updateUserInterface();

    showToast(
        "Profile updated for this session."
    );

}


/* =========================================================
   REGISTER RESOURCE MODAL
========================================================= */

function prepareNewResourceForm() {

    editingResourceId = "";

    if ($("registerModalTitle")) {
        $("registerModalTitle").textContent = "Register Resource";
    }

    if ($("newResourceId")) {
        $("newResourceId").value = "";
        $("newResourceId").readOnly = false;
    }

    ["newResourceName", "newResourceType", "newResourceLocation", "newResourcePublicName", "newResourcePublicDescription", "newResourcePublicContact"].forEach(id => {
        if ($(id)) {
            $(id).value = "";
        }
    });

    if ($("newResourceVisibility")) {
        $("newResourceVisibility").value = "PRIVATE";
    }

    if ($("newResourceShowAvailability")) {
        $("newResourceShowAvailability").checked = false;
    }

    showSelectedTypeAttributes();
    openRegisterModal();

}


function openRegisterModal() {

    const modal =
        $("registerModal");


    if (!modal) return;


    modal.classList.remove(
        "hidden"
    );

}


function closeRegisterModal() {

    const modal =
        $("registerModal");


    if (!modal) return;


    modal.classList.add(
        "hidden"
    );

}


/* =========================================================
   REGISTER RESOURCE
========================================================= */

async function registerResource() {
    if (tenantContextLoading || !selectedOrganizationId()) {
        showToast("Organization context is still loading.");
        return;
    }

    const id = $("newResourceId").value.trim();
    const type = $("newResourceType").value;
    const location = $("newResourceLocation").value.trim();

    if (!id || !type || !location) {
        showToast("Please complete all resource details.");
        return;
    }

    try {
        const editing = Boolean(editingResourceId);
        const response = await fetch(
            RESOURCES_API_URL,
            {
                method: editing ? "PUT" : "POST",
                headers: {
                    "Content-Type": "application/json",

                        "Authorization":
                            "Bearer " + getIdToken()

                    },

                    body: JSON.stringify({
                    resource_id: editing ? editingResourceId : id,
                    name: $("newResourceName")?.value.trim() || "",
                    resource_type_id: type,
                    location_id: location,
                    organization_id: selectedOrganizationId(),
                    visibility: $("newResourceVisibility")?.value || "PRIVATE",
                    public_name: $("newResourcePublicName")?.value.trim() || "",
                    public_description: $("newResourcePublicDescription")?.value.trim() || "",
                    public_contact: $("newResourcePublicContact")?.value.trim() || "",
                    show_availability: $("newResourceShowAvailability")?.checked === true,
                    attributes: readAttributeValues("resourceAttributeFields"),
                    ...(editing ? {} : { Available: true })
                })
            }
        );

        const data = await response.json();

        if (!response.ok) {
            showToast(
                data.message ||
                (editing ? "Failed to update resource." : "Failed to register resource.")
            );
            return;
        }

        showToast(editing ? "Resource updated." : "Resource registered successfully.");

        addNotification(
            editing ? "Resource updated" : "Resource registered",
            `${id} was ${editing ? "updated" : "added to the resource inventory"}.`
        );

        editingResourceId = "";
        closeRegisterModal();

        $("newResourceId").value = "";
        $("newResourceId").readOnly = false;
        $("newResourceType").value = "";
        $("newResourceLocation").value = "";

        await loadResources();

    } catch (error) {
        console.error(
            "Resource registration failed:",
            error
        );

        showToast(
            "Unable to register resource. Please try again."
        );
    }
}


/* =========================================================
   ALLOCATION CAPTURE
========================================================= */

function addAllocationFromResponse(
    data
) {

    if (!data) return;


    const allocation =
        data.allocation ||
        data.Allocation;


    if (!allocation) return;


    allocations.unshift(
        allocation
    );


    renderAllocations();

}


/* =========================================================
   ALLOCATION TABLE
========================================================= */

async function loadAllocations() {

    const table = $("allocationsTable");

    if (table) {

    table.innerHTML = `
        <tr>
            <td colspan="4">
                Loading allocations...
            </td>
        </tr>
    `;
    }

    try {

        const idToken = await waitForIdToken();

        if (!idToken) {

            throw new Error(
                "Cognito ID token is not available"
            );

        }

        const response = await fetch(
            ALLOCATIONS_API_URL + tenantQuery(),
            {
                method: "GET",
                headers: {
                    "Accept": "application/json",
                "Authorization": "Bearer " + idToken
                }
            }
        );

        if (!response.ok) {
            throw new Error(
                `Allocation API returned ${response.status}`
            );
        }

        const data = await response.json();

        let parsed = data;

        if (
            data &&
            typeof data.body === "string"
        ) {
            try {
                parsed = JSON.parse(data.body);
            } catch {
                parsed = data;
            }
        }

        if (Array.isArray(parsed)) {

            allocations = parsed;

        } else if (
            Array.isArray(parsed.allocations)
        ) {

            allocations = parsed.allocations;

        } else {

            allocations = [];

        }

        renderAllocations();
        updateAnalytics();
        updateAdminDashboard();

    } catch (error) {

        console.error(
            "Unable to load allocations:",
            error
        );

        allocations = [];

        table.innerHTML = `
            <tr>
                <td colspan="4">
                    Unable to load allocations from AWS.
                </td>
            </tr>
        `;
    }
}

function renderAllocations() {

    const table =
        $("allocationsTable");


    if (!table) return;


    if (
        allocations.length === 0
    ) {

        table.innerHTML = `

            <tr>

                <td colspan="8">

                    No allocations yet. Completed allocations from this session appear here.

                </td>

            </tr>

        `;

        return;

    }


    const query =
        (
            $("allocationSearch")
                ?.value ||
            ""
        )
            .trim()
            .toLowerCase();


    const filtered =
        allocations.filter(
            item => {

                return (

                    !query ||

                    String(
                        item.allocation_id ||
                        ""
                    )
                        .toLowerCase()
                        .includes(query) ||

                    String(
                        item.request_id ||
                        ""
                    )
                        .toLowerCase()
                        .includes(query) ||

                    String(
                        item.resource_id ||
                        ""
                    )
                        .toLowerCase()
                        .includes(query) ||

                    String(
                        item.resource_type ||
                        ""
                    )
                        .toLowerCase()
                        .includes(query) ||

                    String(
                        item.location ||
                        ""
                    )
                        .toLowerCase()
                        .includes(query) ||

                    String(
                        item.priority ??
                        ""
                    )
                        .toLowerCase()
                        .includes(query) ||

                    String(
                        item.status ||
                        ""
                    )
                        .toLowerCase()
                        .includes(query)

                );

            }
        );


    table.innerHTML =
        filtered.length === 0

            ? `

                <tr>

                    <td colspan="8">

                        No matching allocations found.

                    </td>

                </tr>

              `

            :

              filtered
                .map(
                    item => {

                        const status =
                            String(
                                item.status ||
                                "ALLOCATED"
                            ).toUpperCase();

                        const rowClass =
                            status === "RELEASED"
                                ? "flow-released"
                                : "flow-live";

                        const action =
                            status === "ALLOCATED"

                                ? `
                                    <button
                                        type="button"
                                        class="release-resource-btn"
                                        onclick="releaseResource('${escapeHtml(
                                            item.resource_id ||
                                            ""
                                        )}')"
                                    >
                                        Release
                                    </button>
                                  `

                                : "-";

                        return `

                            <tr class="${rowClass}">

                                <td>
                                    ${escapeHtml(
                                        item.allocation_id ||
                                        "-"
                                    )}
                                </td>

                                <td>
                                    ${escapeHtml(
                                        item.request_id ||
                                        "-"
                                    )}
                                </td>

                                <td>
                                    ${escapeHtml(
                                        item.resource_id ||
                                        "-"
                                    )}
                                </td>

                                <td>
                                    ${escapeHtml(
                                        item.resource_type ||
                                        "-"
                                    )}
                                </td>

                                <td>
                                    ${escapeHtml(
                                        item.location ||
                                        "-"
                                    )}
                                </td>

                                <td>
                                    ${escapeHtml(
                                        item.priority ??
                                        "-"
                                    )}
                                </td>

                                <td>

                                    <span
                                        class="status-badge ${
                                            status === "RELEASED"
                                                ? "released"
                                                : "allocated"
                                        }"
                                    >

                                        ${escapeHtml(status)}

                                    </span>

                                </td>

                                <td>
                                    ${action}
                                </td>

                            </tr>

                        `;

                    }
                )
                .join("");

}

/* =========================================================
   EXPORT REQUESTS
========================================================= */

function exportRequests() {

    if (
        requests.length === 0
    ) {

        showToast(
            "No requests available to export."
        );

        return;

    }

    const header =
        "Request ID,Request Type,Location,Priority,Status";

    const rows =
        requests.map(
            item => {

                return [
                    item.request_id ?? "",
                    item.resource_type ?? item.ResourceType ?? "",
                    item.location ?? item.Location ?? "",
                    item.priority ?? item.Priority ?? "",
                    item.status ?? item.Status ?? ""
                ]

                .map(
                    value =>
                        `"${String(value)
                            .replaceAll(
                                '"',
                                '""'
                            )}"`
                )

                .join(",");

            }
        );

    const csv =
        [
            header,
            ...rows
        ]
        .join("\n");

    const blob =
        new Blob(
            [csv],
            {
                type:
                    "text/csv;charset=utf-8;"
            }
        );

    const url =
        URL.createObjectURL(
            blob
        );

    const link =
        document.createElement(
            "a"
        );

    link.href =
        url;

    link.download =
        "erap-request-history.csv";

    document.body.appendChild(
        link
    );

    link.click();

    link.remove();

    URL.revokeObjectURL(
        url
    );

    showToast(
        "Request report exported."
    );

}


/* =========================================================
   EXPORT ALLOCATIONS
========================================================= */
/* =========================================================
   EXPORT ALLOCATIONS
========================================================= */

function exportAllocations() {

    if (
        allocations.length === 0
    ) {

        showToast(
            "No browser-session allocations available to export."
        );

        return;

    }


   const header =
    "Allocation ID,Request ID,Resource ID,Resource Type,Location,Priority,Status";


const rows =
    allocations.map(
        item => {

            return [

                item.allocation_id ||
                "",

                item.request_id ||
                "",

                item.resource_id ||
                "",

                item.resource_type ||
                "",

                item.location ||
                "",

                item.priority ||
                "",

                item.status ||
                ""

            ]

            .map(
                value =>
                    `"${String(value)
                        .replaceAll(
                            '"',
                            '""'
                        )}"`
            )

            .join(",");

        }
    );

    const csv =
        [
            header,
            ...rows
        ]
        .join("\n");


    const blob =
        new Blob(
            [csv],
            {
                type:
                    "text/csv;charset=utf-8;"
            }
        );


    const url =
        URL.createObjectURL(
            blob
        );


    const link =
        document.createElement(
            "a"
        );


    link.href =
        url;


    link.download =
        "erap-allocation-history.csv";


    document.body.appendChild(
        link
    );


    link.click();


    link.remove();


    URL.revokeObjectURL(
        url
    );


    showToast(
        "Allocation report exported."
    );

}


/* =========================================================
   EVENT LISTENERS
========================================================= */

function everydayResourceDelegatedClick(event) {

    const target = event.target.closest(
        ".resource-reserve-btn, .resource-reservation-release-btn, " +
        ".resource-everyday-allocate-btn, .resource-everyday-return-btn, " +
        ".resource-qty-reserve-btn, .resource-qty-allocate-btn, " +
        ".resource-maintenance-btn, .resource-maintenance-complete-btn, " +
        ".resource-damage-btn, .resource-damage-recover-btn, " +
        ".resource-retire-btn, .resource-in-use-btn, .resource-in-use-return-btn, " +
        ".resource-assign-btn"
    );

    if (!target) {
        return;
    }

    const resourceId = target.dataset.resourceId;

    if (!resourceId) {
        return;
    }

    if (target.classList.contains("resource-reserve-btn")) {
        reserveResource(resourceId);
        return;
    }

    if (target.classList.contains("resource-reservation-release-btn")) {
        releaseResourceReservation(resourceId);
        return;
    }

    if (target.classList.contains("resource-everyday-allocate-btn")) {
        everydayAllocateResource(resourceId);
        return;
    }

    if (target.classList.contains("resource-everyday-return-btn")) {
        everydayReturnResource(resourceId);
        return;
    }

    if (target.classList.contains("resource-qty-reserve-btn")) {
        reserveQuantityResource(resourceId);
        return;
    }

    if (target.classList.contains("resource-qty-allocate-btn")) {
        everydayAllocateQuantityResource(resourceId);
        return;
    }

    if (target.classList.contains("resource-maintenance-btn")) {
        lifecycleResourceAction(RESOURCE_MAINTENANCE_API_URL, resourceId, "Maintenance notes (optional)");
        return;
    }

    if (target.classList.contains("resource-maintenance-complete-btn")) {
        lifecycleResourceAction(RESOURCE_MAINTENANCE_COMPLETE_API_URL, resourceId, "Completion notes (optional)");
        return;
    }

    if (target.classList.contains("resource-damage-btn")) {
        lifecycleResourceAction(RESOURCE_DAMAGE_API_URL, resourceId, "Damage reason (optional)");
        return;
    }

    if (target.classList.contains("resource-damage-recover-btn")) {
        lifecycleResourceAction(RESOURCE_DAMAGE_RECOVER_API_URL, resourceId, "Recovery notes (optional)");
        return;
    }

    if (target.classList.contains("resource-retire-btn")) {
        if (!window.confirm(`Retire resource ${resourceId}?`)) {
            return;
        }
        lifecycleResourceAction(RESOURCE_RETIRE_API_URL, resourceId, "Retirement notes (optional)");
        return;
    }

    if (target.classList.contains("resource-in-use-btn")) {
        lifecycleResourceAction(RESOURCE_IN_USE_API_URL, resourceId, "");
        return;
    }

    if (target.classList.contains("resource-in-use-return-btn")) {
        lifecycleResourceAction(RESOURCE_IN_USE_RETURN_API_URL, resourceId, "");
        return;
    }

    if (target.classList.contains("resource-assign-btn")) {
        assignResourceAction(resourceId);
    }
}


function resourceHistoryDelegatedClick(event) {

    const button =
        event.target.closest(
            ".resource-history-btn"
        );

    if (!button) {
        return;
    }

    const resourceId =
        button.dataset.resourceId;

    if (!resourceId) {
        console.error(
            "Resource history button has no resource ID."
        );
        return;
    }

    viewResourceHistory(resourceId);

}


function resourceEditDelegatedClick(event) {

    const button = event.target.closest(".resource-edit-btn");

    if (!button) {
        return;
    }

    const resourceId = button.dataset.resourceId;

    if (!resourceId) {
        return;
    }

    const resource = resources.find(item => item.id === resourceId);

    if (!resource || !canOperateResources()) {
        showToast("You cannot edit this resource.");
        return;
    }

    editingResourceId = resource.id;

    if ($("registerModalTitle")) {
        $("registerModalTitle").textContent = "Edit Resource";
    }

    $("newResourceId").value = resource.id;
    $("newResourceId").readOnly = true;

    if ($("newResourceName")) {
        $("newResourceName").value = resource.name || "";
    }

    if ($("newResourceType")) {
        $("newResourceType").value = resource.resource_type_id || "";
    }

    if ($("newResourceLocation")) {
        $("newResourceLocation").value = resource.location_id || "";
    }

    if ($("newResourceVisibility")) {
        $("newResourceVisibility").value = resource.visibility || "PRIVATE";
    }

    if ($("newResourcePublicName")) {
        $("newResourcePublicName").value = resource.public_name || "";
    }

    if ($("newResourcePublicDescription")) {
        $("newResourcePublicDescription").value = resource.public_description || "";
    }

    if ($("newResourcePublicContact")) {
        $("newResourcePublicContact").value = resource.public_contact || "";
    }

    if ($("newResourceShowAvailability")) {
        $("newResourceShowAvailability").checked = resource.show_availability === true;
    }

    renderAttributeFields(
        "resourceAttributeFields",
        resourceTypes.find(item => item.resource_type_id === resource.resource_type_id)?.attributes_schema,
        resource.attributes || {}
    );

    openRegisterModal();

}


function initializeEvents() {

    $("inviteMemberForm")?.addEventListener("submit", submitMemberInvite);
    document.addEventListener("click", event => {

        const accept = event.target?.closest?.(".accept-invitation-btn");
        const save = event.target?.closest?.(".save-member-role");
        const remove = event.target?.closest?.(".remove-member");
        const cancelInvitation = event.target?.closest?.(".cancel-invitation");
        const reactivate = event.target?.closest?.(".reactivate-member");

        if (accept) {

            acceptInvitation(accept.dataset.organizationId);

        }

        if (save) {

            changeMemberRole(save.dataset.userSub);

        }

        if (remove) {

            openRemoveMemberDialog(remove.dataset.userSub, remove.dataset.email || "");

        }

        if (cancelInvitation) {

            cancelPendingInvitation(cancelInvitation.dataset.email || "");

        }

        if (reactivate) {

            setMemberActive(reactivate.dataset.userSub, "reactivate_member");

        }

    });


    /* Allocation form */

    $("allocationForm")
        ?.addEventListener(
            "submit",
            submitAllocation
        );


    /* Profile */

    $("profileMenu")
        ?.addEventListener(
            "click",
            openProfile
        );


    $("profileMenu")
        ?.addEventListener(
            "keydown",
            event => {

                if (
                    event.key === "Enter" ||
                    event.key === " "
                ) {

                    event.preventDefault();

                    openProfile();

                }

            }
        );


        /* Profile modal */

    $("profileModalClose")
        ?.addEventListener(
            "click",
            closeProfile
        );


    /* Edit Profile */

    $("editProfileBtn")
        ?.addEventListener(
            "click",
            openEditProfile
        );


    /* Logout */

    $("modalLogoutBtn")
        ?.addEventListener(
            "click",
            logoutFromCognito
        );


    /* Notifications */

    $("notificationBtn")
        ?.addEventListener(
            "click",
            () => {

                navigateTo(
                    "notifications"
                );

            }
        );


    /* Quick actions */

    $("registerResourceBtn")
        ?.addEventListener(
            "click",
            prepareNewResourceForm
        );


    $("refreshDataBtn")
        ?.addEventListener(
            "click",
            loadResources
        );


    $("viewAllocationsBtn")
        ?.addEventListener(
            "click",
            () => {

                navigateTo(
                    "allocations"
                );

            }
        );


    /* Resource refresh */

    $("resourceRefreshBtn")
        ?.addEventListener(
            "click",
            loadResources
        );


    $("resourcesRefreshBtn")
        ?.addEventListener(
            "click",
            loadResources
        );


    /* Allocation refresh */

    $("allocationRefreshBtn")
        ?.addEventListener(
            "click",
            loadAllocations
        );


    /* Dashboard resource search */

    $("resourceSearch")
        ?.addEventListener(
            "input",
            applyResourceDashboardFilters
        );


    $("resourceFilter")
        ?.addEventListener(
            "change",
            applyResourceDashboardFilters
        );


    /* Resource page search */

    $("resourcesPageSearch")
        ?.addEventListener(
            "input",
            applyResourcePageFilters
        );


    $("resourcesPageFilter")
        ?.addEventListener(
            "change",
            applyResourcePageFilters
        );

    $("resourcesPageTypeFilter")
        ?.addEventListener(
            "change",
            applyResourcePageFilters
        );

    $("resourcesPageLocationFilter")
        ?.addEventListener(
            "change",
            applyResourcePageFilters
        );


    /* Allocation search */

    $("allocationSearch")
        ?.addEventListener(
            "input",
            renderAllocations
        );
    $("exportRequestsBtn")?.addEventListener("click", exportRequests);

    /* Requests search + filter + refresh */

initializeRequestControls();


    /* Export */

    $("exportAllocationsBtn")
        ?.addEventListener(
            "click",
            exportAllocations
        );


    /* New request */

    $("activeOperations")?.addEventListener("click", event => {

        if (event.target.closest(".js-open-request")) {

            $("newRequestBtn")?.click();

        }

        if (event.target.closest(".js-view-requests")) {

            navigateTo("requests");

        }

    });

    $("settingsBtn")
        ?.addEventListener(
            "click",
            () => {

                navigateTo(
                    (typeof canManageCatalog === "function" && (canManageCatalog() || canManageMembers()))
                        ? "admin"
                        : "settings"
                );

            }
        );


    /* Register modal */

    $("registerModalClose")
        ?.addEventListener(
            "click",
            closeRegisterModal
        );


    $("saveResourceBtn")
        ?.addEventListener(
            "click",
            registerResource
        );


    $("createOrganizationBtn")
        ?.addEventListener(
            "click",
            createOrganization
        );


    $("organizationSwitcher")
        ?.addEventListener(
            "change",
            event => {

                switchOrganization(event.target.value);

            }
        );


    $("locationSwitcher")
        ?.addEventListener(
            "change",
            event => {

                switchLocation(event.target.value);

            }
        );


    $("createLocationBtn")
        ?.addEventListener(
            "click",
            createLocation
        );


    /* Clear notifications */

    $("clearNotificationsBtn")
        ?.addEventListener(
            "click",
            () => {

                notifications = [];

                renderNotifications();

                updateNotificationCount();

                showToast(
                    "Notifications cleared."
                );

            }
        );


    /* Profile modal background */

    $("profileModal")
        ?.addEventListener(
            "click",
            event => {

                if (
                    event.target ===
                    $("profileModal")
                ) {

                    closeProfile();

                }

            }
        );


    $("removeMemberClose")
        ?.addEventListener(
            "click",
            closeRemoveMemberDialog
        );


    $("removeMemberCancel")
        ?.addEventListener(
            "click",
            closeRemoveMemberDialog
        );


    $("removeMemberConfirm")
        ?.addEventListener(
            "click",
            confirmRemoveMember
        );


    $("removeMemberModal")
        ?.addEventListener(
            "click",
            event => {

                if (event.target === $("removeMemberModal")) {

                    closeRemoveMemberDialog();

                }

            }
        );


    $("organizationModalClose")
        ?.addEventListener(
            "click",
            dismissOrganizationOnboarding
        );


    $("organizationNotNowBtn")
        ?.addEventListener(
            "click",
            dismissOrganizationOnboarding
        );


    $("organizationOnboardingBtn")
        ?.addEventListener(
            "click",
            openOrganizationOnboarding
        );


    $("showOrganizationCreateBtn")
        ?.addEventListener(
            "click",
            () => {

                organizationCreateRevealed = true;

                syncOrganizationOnboardingView();

            }
        );


    $("organizationModal")
        ?.addEventListener(
            "click",
            event => {

                if (event.target === $("organizationModal")) {

                    dismissOrganizationOnboarding();

                }

            }
        );


    /* Register modal background */

    $("registerModal")
        ?.addEventListener(
            "click",
            event => {

                if (
                    event.target ===
                    $("registerModal")
                ) {

                    closeRegisterModal();

                }

            }
        );


    /* Escape */

    document.addEventListener(
        "keydown",
        event => {

            if (
                event.key !==
                "Escape"
            ) {

                return;

            }


            if (
                $("removeMemberModal") &&
                !$("removeMemberModal").classList.contains("hidden")
            ) {

                closeRemoveMemberDialog();

                return;

            }


            if (
                $("organizationModal") &&
                !$("organizationModal").classList.contains("hidden")
            ) {

                dismissOrganizationOnboarding();

                return;

            }


            closeProfile();

            closeRegisterModal();

        }
    );

}


/* =========================================================
   INITIALIZE NOTIFICATIONS
========================================================= */

function initializeNotifications() {

    notifications = [];

    renderNotifications();

    updateNotificationCount();

}


/* =========================================================
   ORGANIZATION CONTEXT
========================================================= */

function selectedOrganizationId() {

    return currentUser.organization?.organization_id || "";

}


function selectedLocationId() {

    const locationId = currentUser.location?.location_id || "";

    return locationId && locationId !== "ALL" ? locationId : "";

}


function tenantQuery() {

    const organizationId = selectedOrganizationId();

    if (!organizationId) {

        return "";

    }

    const params = new URLSearchParams({
        organization_id: organizationId
    });

    const locationId = selectedLocationId();

    if (locationId) {

        params.set("location_id", locationId);

    }

    return "?" + params.toString();

}


function fillLocationSelect(select) {

    if (!select) {

        return;

    }

    const current = select.value;

    select.innerHTML = `<option value="">Select location</option>`;

    (currentUser.locations || []).forEach(location => {

        const option = document.createElement("option");

        option.value = location.location_id;

        option.textContent = location.name || location.location_id;

        select.appendChild(option);

    });

    if (current && [...select.options].some(option => option.value === current)) {

        select.value = current;

    } else if (selectedLocationId()) {

        select.value = selectedLocationId();

    }

}


function renderOrganizationSwitcher() {

    const select = $("organizationSwitcher");

    if (!select) {

        return;

    }

    const organizations = currentUser.organizations || [];

    select.innerHTML = "";

    organizations.forEach(organization => {

        const option = document.createElement("option");

        option.value = organization.organization_id;

        option.textContent = organization.name || organization.organization_id;

        select.appendChild(option);

    });

    if (currentUser.organization) {

        select.value = currentUser.organization.organization_id;

    }

    select.disabled = organizations.length < 2;

}


function renderLocationSwitcher() {

    const select = $("locationSwitcher");

    if (!select) {

        return;

    }

    const locations = currentUser.locations || [];

    select.innerHTML = `<option value="ALL">All locations</option>`;

    locations.forEach(location => {

        const option = document.createElement("option");

        option.value = location.location_id;

        option.textContent = location.name || location.location_id;

        select.appendChild(option);

    });

    select.value = currentUser.location?.location_id || (locations.length === 1 ? locations[0].location_id : "ALL");

    fillLocationSelect($("location"));

    fillLocationSelect($("newResourceLocation"));

    fillLocationSelect($("modalLocation"));

}


function canViewBilling() {

    const role = currentUser.organization && currentUser.organization.role;

    return role === "OWNER" || role === "ADMIN";

}


function canManageBilling() {

    return Boolean(currentUser.organization && currentUser.organization.role === "OWNER");

}


function canManageCatalog() {

    const role = currentUser.organization?.role;

    return role === "OWNER" || role === "ADMIN";

}


function parseAttributeSchema(text) {

    const fields = [];

    String(text || "").split(/\n/).forEach(line => {

        const parts = line.split(":").map(item => item.trim());

        if (!parts[0]) {

            return;

        }

        fields.push({
            key: parts[0],
            type: parts[1] || "string",
            required: parts[2] === "required",
            label: parts[0]
        });

    });

    return { fields };

}


function renderAttributeFields(containerId, schema, values) {

    const container = $(containerId);

    if (!container) {

        return;

    }

    const fields = schema?.fields || [];

    container.innerHTML = fields.map(field => `

        <label>
            ${escapeHtml(field.label || field.key)}
            ${field.required ? "*" : ""}
        </label>
        <input
            data-attribute-key="${escapeHtml(field.key)}"
            data-attribute-type="${escapeHtml(field.type)}"
            value="${escapeHtml(values?.[field.key] ?? "")}"
        >

    `).join("");

}


function readAttributeValues(containerId) {

    const attributes = {};

    $(containerId)?.querySelectorAll("[data-attribute-key]").forEach(input => {

        const key = input.dataset.attributeKey;
        const type = input.dataset.attributeType;
        const raw = input.value.trim();

        if (!raw) {

            return;

        }

        if (type === "number") {

            attributes[key] = Number(raw);

        } else if (type === "boolean") {

            attributes[key] = raw.toLowerCase() === "true";

        } else {

            attributes[key] = raw;

        }

    });

    return attributes;

}


function fillCatalogSelects() {

    const resourceSelect = $("newResourceType");
    const requestSelect = $("resourceType");
    const modalRequestSelect = $("modalRequestType");

    if (resourceSelect) {

        const current = resourceSelect.value;

        resourceSelect.innerHTML = `<option value="">Select resource type</option>` +
            resourceTypes.filter(item => item.status === "ACTIVE").map(item =>
                `<option value="${escapeHtml(item.resource_type_id)}">${escapeHtml(item.name)}</option>`
            ).join("");

        if (current) {

            resourceSelect.value = current;

        }

    }

    const compatible = $("requestTypeResources");

    if (compatible) {

        compatible.innerHTML = resourceTypes.filter(item => item.status === "ACTIVE").map(item =>
            `<option value="${escapeHtml(item.resource_type_id)}">${escapeHtml(item.name)}</option>`
        ).join("");

    }

    if (requestSelect) {

        const current = requestSelect.value;

        requestSelect.innerHTML = `<option value="">Select request type</option>` +
            requestTypes.filter(item => item.status === "ACTIVE").map(item =>
                `<option value="${escapeHtml(item.request_type_id)}">${escapeHtml(item.name)}</option>`
            ).join("");

        if (current) {

            requestSelect.value = current;

        }

    }

    if (modalRequestSelect) {

        const current = modalRequestSelect.value;

        modalRequestSelect.innerHTML = `<option value="">Select request type</option>` +
            requestTypes.filter(item => item.status === "ACTIVE").map(item =>
                `<option value="${escapeHtml(item.request_type_id)}">${escapeHtml(item.name)}</option>`
            ).join("");

        if (current) {

            modalRequestSelect.value = current;

        }

    }

    showSelectedTypeAttributes();

}


function showSelectedTypeAttributes() {

    const resourceSelect = $("newResourceType");
    const requestSelect = $("resourceType");
    const modalRequestSelect = $("modalRequestType");

    renderAttributeFields(
        "resourceAttributeFields",
        resourceTypes.find(item => item.resource_type_id === resourceSelect?.value)?.attributes_schema,
        {}
    );

    renderAttributeFields(
        "requestAttributeFields",
        requestTypes.find(item => item.request_type_id === requestSelect?.value)?.attributes_schema,
        {}
    );

    renderAttributeFields(
        "modalRequestAttributes",
        requestTypes.find(item => item.request_type_id === modalRequestSelect?.value)?.attributes_schema,
        {}
    );

}


function renderCatalogAdmin() {

    const resourceNode = $("adminResourceTypes");
    const requestNode = $("adminRequestTypes");
    const manageable = canManageCatalog();

    ["saveResourceTypeBtn", "saveRequestTypeBtn"].forEach(id => {

        const button = $(id);

        if (button) {

            button.hidden = !manageable;

        }

    });

    if (resourceNode) {

        resourceNode.innerHTML = resourceTypes.map(item => `
            <article class="config-row">
                <strong>${escapeHtml(item.name)}</strong>
                <span>${escapeHtml([item.description, item.category, item.status].filter(Boolean).join(" · "))}</span>
                ${manageable ? `<button type="button" class="text-action" data-deactivate-resource-type="${escapeHtml(item.resource_type_id)}">Deactivate</button>` : ""}
            </article>
        `).join("") || "<div class=\"empty-state\"><h3>No resource types</h3><p>Add a type before registering resources.</p></div>";

    }

    if (requestNode) {

        requestNode.innerHTML = requestTypes.map(item => `
            <article class="config-row">
                <strong>${escapeHtml(item.name)}</strong>
                <span>${escapeHtml([item.description, item.category, item.status].filter(Boolean).join(" · "))}</span>
                ${manageable ? `<button type="button" class="text-action" data-deactivate-request-type="${escapeHtml(item.request_type_id)}">Deactivate</button>` : ""}
            </article>
        `).join("") || "<div class=\"empty-state\"><h3>No request types</h3><p>Add a request type before creating requests.</p></div>";

    }

}


async function loadCatalog(url) {

    const response = await fetch(url + tenantQuery(), {
        headers: { "Authorization": "Bearer " + getIdToken() }
    });

    if (!response.ok) {

        return [];

    }

    const data = await response.json();

    return data.types || [];

}


async function loadResourceTypes() {

    resourceTypes = selectedOrganizationId() ? await loadCatalog(RESOURCE_TYPES_API_URL) : [];

    fillCatalogSelects();

    renderCatalogAdmin();

}


async function loadRequestTypes() {

    if (!selectedOrganizationId()) {

        requestTypes = [];
        requestTypesLoadFailed = false;

    } else {

        const response = await fetch(REQUEST_TYPES_API_URL + tenantQuery(), {
            headers: { "Authorization": "Bearer " + getIdToken() }
        });

        if (!response.ok) {

            requestTypes = [];
            requestTypesLoadFailed = true;

        } else {

            const data = await response.json();

            requestTypes = data.types || [];
            requestTypesLoadFailed = false;

        }

    }

    fillCatalogSelects();

    renderCatalogAdmin();

}


function prepareRequestModal() {

    fillCatalogSelects();

    fillLocationSelect($("modalLocation"));

    const result = $("requestModalResult");

    if (!result) {

        return;

    }

    result.textContent = "";
    result.className = "request-modal-result";

    if (requestTypesLoadFailed) {

        result.textContent = "Request types could not be loaded. Refresh and try again.";
        result.className = "request-modal-result error";

    } else if (!requestTypes.some(item => item.status === "ACTIVE")) {

        result.textContent = "No active request types are available.";
        result.className = "request-modal-result error";

    }

}


async function submitRequestModal(event) {

    event.preventDefault();

    const requestId = $("modalRequestId")?.value.trim() || "";
    const requestTypeId = $("modalRequestType")?.value || "";
    const locationId = $("modalLocation")?.value || "";
    const priority = Number($("modalPriority")?.value);
    const result = $("requestModalResult");
    const button = $("submitRequestModal");

    const show = (message, kind) => {

        if (!result) {

            return;

        }

        result.textContent = message;
        result.className = "request-modal-result" + (kind ? " " + kind : "");

    };

    if (!selectedOrganizationId()) {

        show("Organization context is still loading.", "error");

        return;

    }

    if (!requestId || !requestTypeId || !locationId || !priority) {

        show("Please complete all request fields.", "error");

        return;

    }

    if (button) {

        button.disabled = true;
        button.textContent = "Submitting...";

    }

    show("");

    try {

        const response = await fetch(REQUESTS_API_URL, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": "Bearer " + getIdToken()
            },
            body: JSON.stringify({
                request_id: requestId,
                request_type_id: requestTypeId,
                location_id: locationId,
                organization_id: selectedOrganizationId(),
                priority: priority,
                attributes: readAttributeValues("modalRequestAttributes")
            })
        });

        const raw = await response.text();
        let data = raw;

        try {

            data = JSON.parse(raw);

        } catch (error) {

            data = raw;

        }

        if (!response.ok) {

            show(getApiMessage(data) || "Request could not be created.", "error");

            return;

        }

        show(getApiMessage(data) || "Request created successfully.", "success");

        if (typeof loadRequests === "function") {

            loadRequests();

        }

        setTimeout(() => {

            const modal = $("requestModal");

            if (modal) {

                modal.style.display = "none";

            }

            $("requestModalForm")?.reset();
            show("");

        }, 1200);

    } catch (error) {

        show("Request could not be created.", "error");

    } finally {

        if (button) {

            button.disabled = false;
            button.textContent = "Submit Request →";

        }

    }

}


async function saveResourceType() {

    if (!canManageCatalog()) {

        showToast("You cannot manage resource types.");

        return;

    }

    const response = await fetch(RESOURCE_TYPES_API_URL, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + getIdToken()
        },
        body: JSON.stringify({
            organization_id: selectedOrganizationId(),
            name: $("resourceTypeName")?.value.trim() || "",
            description: $("resourceTypeDescription")?.value.trim() || "",
            category: $("resourceTypeCategory")?.value.trim() || "",
            attributes_schema: parseAttributeSchema($("resourceTypeSchema")?.value || ""),
            visibility_default: "PRIVATE"
        })
    });

    const data = await response.json();

    if (!response.ok) {

        showToast(data.message || "Unable to save resource type.");

        return;

    }

    showToast("Resource type saved.");

    await loadResourceTypes();

}


async function saveRequestType() {

    if (!canManageCatalog()) {

        showToast("You cannot manage request types.");

        return;

    }

    const compatible = Array.from($("requestTypeResources")?.selectedOptions || []).map(option => option.value);

    const response = await fetch(REQUEST_TYPES_API_URL, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + getIdToken()
        },
        body: JSON.stringify({
            organization_id: selectedOrganizationId(),
            name: $("requestTypeName")?.value.trim() || "",
            description: $("requestTypeDescription")?.value.trim() || "",
            category: $("requestTypeCategory")?.value.trim() || "",
            attributes_schema: parseAttributeSchema($("requestTypeSchema")?.value || ""),
            matching_config: {
                compatible_resource_type_ids: compatible,
                same_location_preferred: true
            }
        })
    });

    const data = await response.json();

    if (!response.ok) {

        showToast(data.message || "Unable to save request type.");

        return;

    }

    showToast("Request type saved.");

    await loadRequestTypes();

}


async function deactivateCatalogType(url, typeId, idName) {

    const response = await fetch(url + "/" + encodeURIComponent(typeId) + tenantQuery(), {
        method: "DELETE",
        headers: { "Authorization": "Bearer " + getIdToken() }
    });

    const data = await response.json();

    if (!response.ok) {

        showToast(data.message || "Unable to deactivate type.");

        return;

    }

    if (idName === "resource") {

        await loadResourceTypes();

    } else {

        await loadRequestTypes();

    }

}


function clearTenantData() {

    resources = [];

    requests = [];

    allocations = [];

    resourceTypes = [];

    requestTypes = [];

    fillCatalogSelects();

    renderCatalogAdmin();

    const history = $("resourceHistoryContent");

    if (history) {

        history.innerHTML = "";

    }

    $("resourceHistoryModal")?.classList.add("hidden");

    renderResourcesTable();

    renderRequests();

    renderAllocations();

    updateDashboardStats();

    updateAnalytics();

}


function setOperationalActionsEnabled(enabled) {

    ["allocateBtn", "saveResourceBtn"].forEach(id => {

        const button = $(id);

        if (button) {

            button.disabled = !enabled;

        }

    });

}


function renderLocationWorkspace() {

    const node = $("locationWorkspace");

    if (!node) {

        return;

    }

    const rows = Array.isArray(currentUser.locations) ? currentUser.locations : [];

    if (!rows.length) {

        node.innerHTML = `
            <div class="empty-state">
                <h3>No locations yet</h3>
                <p>Add a location before registering resources or creating requests.</p>
            </div>
        `;

        return;

    }

    node.innerHTML = rows.map(location => `
        <article class="config-row">
            <strong>${escapeHtml(location.name || "Location")}</strong>
            <span>${escapeHtml([location.city, location.state, location.status].filter(Boolean).join(" · "))}</span>
        </article>
    `).join("");

}


async function loadLocations() {

    const organizationId = selectedOrganizationId();

    if (!organizationId) {

        currentUser.locations = [];

        currentUser.location = null;

        renderLocationSwitcher();

        return;

    }

    const idToken = await waitForIdToken();

    if (!idToken) {

        return;

    }

    const response = await fetch(
        `${LOCATIONS_API_URL}?organization_id=${encodeURIComponent(organizationId)}`,
        {
            method: "GET",
            headers: {
                "Authorization": "Bearer " + idToken
            }
        }
    );

    const data = await readJsonResponse(response);

    if (!response.ok) {

        showToast(organizationErrorMessage(response.status, data));

        currentUser.locations = [];

        currentUser.location = null;

        renderLocationSwitcher();

        return;

    }

    currentUser.locations = Array.isArray(data.locations) ? data.locations : [];

    const saved = sessionStorage.getItem("erap_location_" + organizationId);

    const savedLocation = currentUser.locations.find(
        location => location.location_id === saved
    );

    if (savedLocation) {

        currentUser.location = savedLocation;

    } else if (currentUser.locations.length === 1) {

        currentUser.location = currentUser.locations[0];

    } else {

        currentUser.location = { location_id: "ALL", name: "All locations" };

    }

    if (currentUser.location?.location_id && currentUser.location.location_id !== "ALL") {

        sessionStorage.setItem(
            "erap_location_" + organizationId,
            currentUser.location.location_id
        );

    }

    renderLocationSwitcher();

    renderLocationWorkspace();

    if (currentUser.locations.length === 0) {

        $("locationModal")?.classList.remove("hidden");

    } else {

        $("locationModal")?.classList.add("hidden");

    }

}


async function refreshTenantData() {

    tenantContextLoading = true;

    setOperationalActionsEnabled(false);

    clearTenantData();

    try {

        await loadLocations();

        await loadResourceTypes();

        await loadRequestTypes();

        await loadResources();

        await loadRequests();

        await loadAllocations();

    } finally {

        tenantContextLoading = false;

        setOperationalActionsEnabled(Boolean(selectedOrganizationId()));

    }

}


async function switchOrganization(organizationId) {

    const selected = (currentUser.organizations || []).find(
        organization => organization.organization_id === organizationId
    );

    if (!selected) {

        return;

    }

    currentUser.organization = selected;

    sessionStorage.setItem("erap_selected_organization_id", organizationId);

    currentUser.locations = [];

    currentUser.location = null;

    sessionStorage.removeItem("erap_location_" + organizationId);

    if ($("billing") && $("billing").classList.contains("active-section")) {

        billingCancelArmed = false;
        loadBilling();

    }

    tenantContextLoading = true;

    setOperationalActionsEnabled(false);

    clearTenantData();

    updateUserInterface();

    renderOrganizationSwitcher();

    renderLocationSwitcher();

    await refreshTenantData();

}


async function switchLocation(locationId) {

    if (locationId === "ALL") {

        currentUser.location = { location_id: "ALL", name: "All locations" };

        sessionStorage.removeItem("erap_location_" + selectedOrganizationId());

    } else {

        currentUser.location = (currentUser.locations || []).find(
            location => location.location_id === locationId
        ) || null;

        if (currentUser.location) {

            sessionStorage.setItem(
                "erap_location_" + selectedOrganizationId(),
                currentUser.location.location_id
            );

        }

    }

    clearTenantData();

    await loadResources();

    await loadRequests();

    await loadAllocations();

}


async function createLocation() {

    const name = $("locationName")?.value.trim() || "";

    if (!name) {

        showToast("Location name is required.");

        return;

    }

    const idToken = await waitForIdToken();

    if (!idToken) {

        showToast("Cognito ID token is not available.");

        return;

    }

    const response = await fetch(LOCATIONS_API_URL, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + idToken
        },
        body: JSON.stringify({
            name: name,
            city: $("locationCity")?.value.trim() || "",
            organization_id: selectedOrganizationId()
        })
    });

    const data = await readJsonResponse(response);

    if (!response.ok) {

        showToast(organizationErrorMessage(response.status, data));

        return;

    }

    $("locationModal")?.classList.add("hidden");

    $("locationName").value = "";

    $("locationCity").value = "";

    showToast("Location added.");

    await refreshTenantData();

}


function selectCurrentOrganization(organizations) {

    if (!organizations || organizations.length === 0) {

        return null;

    }


    const active = organizations.filter(
        organization => organization.status === "ACTIVE"
    );

    const pool = active.length ? active : organizations;

    return pool
        .slice()
        .sort((left, right) =>
            String(left.organization_id).localeCompare(
                String(right.organization_id)
            )
        )[0];

}


function applyOrganizationContext(organizations) {

    const list = Array.isArray(organizations) ? organizations : [];

    currentUser.organizations = list;

    const savedId = sessionStorage.getItem("erap_selected_organization_id");

    currentUser.organization = list.find(
        organization => organization.organization_id === savedId
    ) || selectCurrentOrganization(list);

    if (currentUser.organization) {

        sessionStorage.setItem(
            "erap_selected_organization_id",
            currentUser.organization.organization_id
        );

    }

    updateUserInterface();

    renderOrganizationSwitcher();

    updateOrganizationOnboardingEntry();

}


function createOrganizationRequestId() {

    if (window.crypto && crypto.randomUUID) {

        return crypto.randomUUID();

    }

    return "req-" + Date.now().toString(36) + "-" +
        Math.random().toString(36).slice(2, 12);

}


function canManageMembers() {

    const role = currentUser.organization && currentUser.organization.role;

    return role === "OWNER" || role === "ADMIN";

}


function memberOrganizationId() {

    return currentUser.organization && currentUser.organization.organization_id || "";

}


function renderInvitationList(container) {

    if (!container) {

        return;

    }

    if (!pendingInvitations.length) {

        container.innerHTML = "";

        syncOrganizationOnboardingView();

        return;

    }

    container.innerHTML = pendingInvitations.map(invitation => {

        const name = escapeHtml(invitation.name || "an organization");
        const role = escapeHtml(invitation.role || "");
        const organizationId = escapeHtml(invitation.organization_id || "");

        return `
            <section class="invitation-card">
                <p>You've been invited to join <strong>${name}</strong></p>
                <p class="invitation-role-label">Role</p>
                <p><strong>${role}</strong></p>
                <button type="button" class="primary-btn full-width accept-invitation-btn" data-organization-id="${organizationId}">
                    Accept Invitation
                </button>
            </section>
        `;

    }).join("");

    syncOrganizationOnboardingView();

}


function updateOrganizationOnboardingEntry() {

    const button = $("organizationOnboardingBtn");

    if (!button) {

        return;

    }

    const hasOrganization = Boolean(currentUser.organization);

    button.hidden = hasOrganization;

    if (!hasOrganization) {

        button.textContent = pendingInvitations.length
            ? "Invitations"
            : "Create organization";

    }

}


function syncOrganizationOnboardingView() {

    const invited = pendingInvitations.length > 0;
    const title = $("organizationModalTitle");
    const description = $("organizationModalDescription");
    const createFields = $("organizationCreateFields");
    const notNow = $("organizationNotNowBtn");
    const createInstead = $("showOrganizationCreateBtn");
    const single = pendingInvitations.length === 1 ? pendingInvitations[0] : null;

    if (title) {

        title.textContent = invited
            ? "You've been invited to join " + (single && single.name ? single.name : "an organization")
            : "Create your organization";

    }

    if (description) {

        description.textContent = invited
            ? "Accept the invitation to join the existing organization. You can close this and decide later."
            : "Set up your organization to start managing resources, requests, locations, and team members in ERAP.";

    }

    if (createFields) {

        createFields.hidden = invited && !organizationCreateRevealed;

    }

    if (notNow) {

        notNow.hidden = !invited;

    }

    if (createInstead) {

        createInstead.hidden = !invited || organizationCreateRevealed;

    }

    updateOrganizationOnboardingEntry();

}


function isInvitationRecord(member) {

    return String(member && member.user_sub || "").startsWith("invite-");

}


function signedInSubject() {

    const claims = decodeJwt(getIdToken() || "");
    const subject = claims && claims.sub;

    return typeof subject === "string" ? subject : "";

}


function renderPendingOrganizationInvitations(invitations) {

    const container = $("organizationPendingInvitations");

    if (!container) {

        return;

    }

    const pending = (Array.isArray(invitations) ? invitations : [])
        .filter(invitation => invitation && invitation.status === "PENDING");

    if (!pending.length) {

        container.innerHTML = "";

        return;

    }

    container.innerHTML = `
        <h4>Pending Invitations</h4>
        <table>
            <thead>
                <tr>
                    <th>Email</th>
                    <th>Role</th>
                    <th>Status</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>
                ${pending.map(invitation => `
                    <tr>
                        <td>${escapeHtml(invitation.email || "")}</td>
                        <td>${escapeHtml(invitation.role || "")}</td>
                        <td>PENDING</td>
                        <td>
                            <button type="button" class="primary-btn cancel-invitation" data-email="${escapeHtml(invitation.email || "")}">
                                Cancel invitation
                            </button>
                        </td>
                    </tr>
                `).join("")}
            </tbody>
        </table>
    `;

}


function renderMemberRows(members) {

    const container = $("organizationMembers");

    if (!container) {

        return;

    }

    const actualMembers = (Array.isArray(members) ? members : [])
        .filter(member => member && !isInvitationRecord(member) && member.status !== "PENDING");
    const actorSub = signedInSubject();
    const actorIsOwner = Boolean(currentUser.organization && currentUser.organization.role === "OWNER");
    const activeOwnerCount = actualMembers.filter(member =>
        member.role === "OWNER" && (member.status || "ACTIVE") === "ACTIVE"
    ).length;

    if (!actualMembers.length) {

        container.innerHTML = "<p>No members are recorded for this organization.</p>";

        return;

    }

    container.innerHTML = `
        <table>
            <thead>
                <tr>
                    <th>Email</th>
                    <th>Role</th>
                    <th>Status</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>
                ${actualMembers.map(member => {
                    const inactive = member.status === "INACTIVE";
                    const email = escapeHtml(member.email || "");
                    const userSub = escapeHtml(member.user_sub || "");
                    const isSelf = Boolean(actorSub) && member.user_sub === actorSub;
                    const ownerRow = member.role === "OWNER";
                    const canManageOwner = ownerRow && actorIsOwner && !isSelf && (inactive || activeOwnerCount >= 2);
                    const manageable = ownerRow ? canManageOwner : !isSelf;
                    const roleControl = !manageable || inactive
                        ? escapeHtml(member.role || "")
                        : `
                            <select data-member-role="${userSub}">
                                ${["ADMIN", "OPERATOR", "MEMBER"].map(role => `
                                    <option value="${role}" ${member.role === role ? "selected" : ""}>${role}</option>
                                `).join("")}
                            </select>
                            <button type="button" class="primary-btn save-member-role" data-user-sub="${userSub}">Save role</button>
                        `;
                    const activation = !manageable
                        ? ""
                        : inactive
                            ? `<button type="button" class="primary-btn reactivate-member" data-user-sub="${userSub}">Reactivate</button>`
                            : `<button type="button" class="primary-btn remove-member" data-user-sub="${userSub}" data-email="${email}">Remove</button>`;

                    return `
                        <tr>
                            <td>${escapeHtml(member.email || "Verified member")}</td>
                            <td>${roleControl}</td>
                            <td>${escapeHtml(member.status || "ACTIVE")}</td>
                            <td>${activation}</td>
                        </tr>
                    `;
                }).join("")}
            </tbody>
        </table>
    `;

}


async function postMemberOperation(body) {

    const idToken = await waitForIdToken();

    if (!idToken) {

        showToast("Cognito ID token is not available.");

        return null;

    }

    const response = await fetch(ORGANIZATION_API_URL, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + idToken
        },
        body: JSON.stringify(body)
    });
    const data = await readJsonResponse(response);

    if (!response.ok) {

        showToast(organizationErrorMessage(response.status, data));

        return null;

    }

    return data;

}


async function loadOrganizationMembers() {

    const panel = $("organizationMembersPanel");

    if (!panel || !canManageMembers()) {

        if (panel) {

            panel.hidden = true;

        }

        return;

    }

    panel.hidden = false;
    renderInvitationList($("myInvitations"));

    const organizationId = memberOrganizationId();

    if (!organizationId) {

        return;

    }

    const idToken = await waitForIdToken();

    if (!idToken) {

        return;

    }

    const response = await fetch(
        ORGANIZATION_API_URL + "?view=members&organization_id=" + encodeURIComponent(organizationId),
        {
            method: "GET",
            headers: {
                "Accept": "application/json",
                "Authorization": "Bearer " + idToken
            }
        }
    );
    const data = await readJsonResponse(response);

    if (!response.ok) {

        showToast(organizationErrorMessage(response.status, data));

        return;

    }

    renderMemberRows(Array.isArray(data.members) ? data.members : []);
    renderPendingOrganizationInvitations(data.pending_invitations);

}


async function submitMemberInvite(event) {

    event.preventDefault();

    const organizationId = memberOrganizationId();
    const email = $("inviteMemberEmail")?.value.trim() || "";
    const role = $("inviteMemberRole")?.value || "";

    if (!organizationId || !email) {

        showToast("Email address is required.");

        return;

    }

    const result = await postMemberOperation({
        operation: "invite_member",
        organization_id: organizationId,
        email: email,
        role: role
    });

    if (!result) {

        return;

    }

    if ($("inviteMemberEmail")) {

        $("inviteMemberEmail").value = "";

    }

    showToast(result.message || "Invitation created.");
    await loadOrganizationMembers();

}


async function changeMemberRole(userSub) {

    if (!userSub || userSub === signedInSubject()) {

        return;

    }

    const organizationId = memberOrganizationId();
    const select = document.querySelector(`[data-member-role="${CSS.escape(userSub)}"]`);
    const role = select ? select.value : "";

    if (!organizationId || !window.confirm("Change this member's role to " + role + "?")) {

        return;

    }

    const result = await postMemberOperation({
        operation: "change_role",
        organization_id: organizationId,
        target_user_sub: userSub,
        role: role
    });

    if (result) {

        showToast(result.message || "Role updated.");
        await loadOrganizationMembers();

    }

}


function closeRemoveMemberDialog() {

    pendingMemberRemoval = null;
    $("removeMemberModal")?.classList.add("hidden");

}


function openRemoveMemberDialog(userSub, email) {

    if (!userSub || String(userSub).startsWith("invite-") || userSub === signedInSubject()) {

        return;

    }

    pendingMemberRemoval = {
        userSub: userSub,
        email: email || ""
    };

    const emailLabel = $("removeMemberEmail");
    const organizationLabel = $("removeMemberOrganization");
    const organizationName = currentUser.organization && currentUser.organization.name
        ? currentUser.organization.name
        : "this organization";

    if (emailLabel) {

        emailLabel.textContent = email || "this member";

    }

    if (organizationLabel) {

        organizationLabel.textContent = organizationName;

    }

    $("removeMemberModal")?.classList.remove("hidden");
    $("removeMemberClose")?.focus();

}


async function confirmRemoveMember() {

    const removal = pendingMemberRemoval;
    const organizationId = memberOrganizationId();

    if (!removal || !organizationId) {

        closeRemoveMemberDialog();

        return;

    }

    closeRemoveMemberDialog();

    const result = await postMemberOperation({
        operation: "deactivate_member",
        organization_id: organizationId,
        target_user_sub: removal.userSub
    });

    if (result) {

        showToast(result.message || "Member removed.");
        await loadOrganizationMembers();

    }

}


async function cancelPendingInvitation(email) {

    const organizationId = memberOrganizationId();

    if (!organizationId || !email || !window.confirm("Cancel this invitation?")) {

        return;

    }

    const result = await postMemberOperation({
        operation: "deactivate_member",
        organization_id: organizationId,
        email: email
    });

    if (result) {

        showToast(result.message || "Invitation cancelled.");
        await loadOrganizationMembers();

    }

}


async function setMemberActive(userSub, operation) {

    const organizationId = memberOrganizationId();

    if (operation !== "reactivate_member" || !organizationId || !window.confirm("Reactivate this member?")) {

        return;

    }

    const result = await postMemberOperation({
        operation: operation,
        organization_id: organizationId,
        target_user_sub: userSub
    });

    if (result) {

        showToast(result.message || "Membership updated.");
        await loadOrganizationMembers();

    }

}


async function acceptInvitation(organizationId) {

    const result = await postMemberOperation({
        operation: "accept_invitation",
        organization_id: organizationId
    });

    if (!result) {

        return;

    }

    showToast(result.message || "Invitation accepted.");
    const organizations = await loadOrganizationMembership();

    if (organizations && organizations.length) {

        closeOrganizationOnboarding();
        applyOrganizationContext(organizations);
        await startDashboardData();

    }

}


function openOrganizationOnboarding() {

    organizationOnboardingRequired = true;

    organizationCreateRevealed = false;

    if (!organizationRequestId) {

        organizationRequestId = createOrganizationRequestId();

    }

    $("organizationModal")?.classList.remove("hidden");
    renderInvitationList($("pendingInvitations"));
    $("organizationModalClose")?.focus();

}


function closeOrganizationOnboarding() {

    organizationOnboardingRequired = false;

    $("organizationModal")?.classList.add("hidden");

    updateOrganizationOnboardingEntry();

}


function dismissOrganizationOnboarding() {

    closeOrganizationOnboarding();

}


function organizationErrorMessage(status, data) {

    const message = String(getApiMessage(data) || "");
    const safe = message &&
        message.length <= 160 &&
        !message.includes("Traceback") &&
        !message.includes("\n");

    if (status === 400 && safe) {

        return message;

    }

    if (status === 401) {

        return "Sign in is required.";

    }

    if (status === 403) {

        return "You are not allowed to perform this organization action.";

    }

    if (status === 409 && safe) {

        return message;

    }

    if (status === 409) {

        return "This organization request conflicts with an existing organization.";

    }

    return "Unable to complete the organization request. Please try again.";

}


async function readJsonResponse(response) {

    try {

        return await response.json();

    }

    catch (error) {

        return {};

    }

}


async function loadOrganizationMembership() {

    const idToken = await waitForIdToken();

    if (!idToken) {

        showToast("Cognito ID token is not available.");

        return null;

    }


    try {

        const response = await fetch(ORGANIZATION_API_URL, {
            method: "GET",
            headers: {
                "Accept": "application/json",
                "Authorization": "Bearer " + idToken
            }
        });

        const data = await readJsonResponse(response);

        if (!response.ok) {

            showToast(
                organizationErrorMessage(response.status, data)
            );

            return null;

        }

        pendingInvitations = Array.isArray(data.pending_invitations) ? data.pending_invitations : [];
        renderInvitationList($("pendingInvitations"));
        renderInvitationList($("myInvitations"));

        return Array.isArray(data.organizations) ? data.organizations : [];

    }

    catch (error) {

        console.error("Organization lookup failed:", error);

        showToast(
            "Unable to load your organization. Please refresh."
        );

        return null;

    }

}


async function createOrganization() {

    if (!organizationOnboardingRequired) {

        return;

    }

    const name = $("organizationName")?.value.trim() || "";

    if (!name) {

        showToast("Organization name is required.");

        return;

    }

    if (name.length > 100) {

        showToast("Organization name is too long.");

        return;

    }

    const button = $("createOrganizationBtn");

    if (button) {

        button.disabled = true;

    }

    try {

        const idToken = await waitForIdToken();

        if (!idToken) {

            showToast("Cognito ID token is not available.");

            return;

        }

        const response = await fetch(ORGANIZATION_API_URL, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": "Bearer " + idToken
            },
            body: JSON.stringify({
                name: name,
                client_request_id: organizationRequestId
            })
        });

        const data = await readJsonResponse(response);

        if (response.status !== 201 && response.status !== 200) {

            showToast(
                organizationErrorMessage(response.status, data)
            );

            return;

        }

        const created = data.organization || {};

        applyOrganizationContext([
            {
                organization_id: created.organization_id,
                name: created.name || name,
                role: created.role || "OWNER",
                status: created.status || "ACTIVE"
            }
        ]);

        closeOrganizationOnboarding();

        showToast(
            data.message || "Organization created successfully."
        );

        addNotification(
            "Organization ready",
            (created.name || name) + " is ready in ERAP."
        );

        await startDashboardData();

    }

    catch (error) {

        console.error("Organization creation failed:", error);

        showToast(
            "Unable to create organization. Please try again."
        );

    }

    finally {

        if (organizationOnboardingRequired && button) {

            button.disabled = false;

        }

    }

}


async function resolveOrganization() {

    const organizations = await loadOrganizationMembership();

    if (organizations === null) {

        return;

    }

    if (organizations.length === 0) {

        openOrganizationOnboarding();

        return;

    }

    applyOrganizationContext(organizations);

    await startDashboardData();

}


async function startDashboardData() {

    if (dashboardDataStarted) {

        return;

    }

    dashboardDataStarted = true;

    await refreshTenantData();

    console.log(
        "ERAP initialized successfully."
    );

}


/* =========================================================
   BILLING
========================================================= */

const BILLING_STATUS_LABELS = {
    TRIALING: "Free trial",
    ACTIVE: "Active",
    PAST_DUE: "Payment required",
    CANCELLED: "Cancelled",
    EXPIRED: "Subscription expired",
    GRANDFATHERED: "Legacy access"
};

let billingLoad = 0;
let billingCancelArmed = false;
let billingSnapshot = null;


function noteBillingRequired(response) {

    if (!response || response.status !== 403 || typeof response.clone !== "function") {

        return;

    }

    response.clone().json().then(payload => {

        const body = unwrapApiPayload(payload);

        if (!body || !body.error || body.error.code !== "BILLING_REQUIRED") {

            return;

        }

        showToast("Subscription required for this operation.");

        const toast = $("toast");

        if (toast) {

            toast.onclick = () => {

                if ($("billing")) {

                    navigateTo("billing");

                }

            };

        }

    }).catch(() => {});

}


function unwrapApiPayload(payload) {

    if (!payload || typeof payload !== "object") {

        return {};

    }

    if (typeof payload.body === "string") {

        try {

            return JSON.parse(payload.body);

        } catch (error) {

            return {};

        }

    }

    return payload;

}


function billingQuery() {

    const organizationId = selectedOrganizationId();

    if (!organizationId) {

        return "";

    }

    return "?organization_id=" + encodeURIComponent(organizationId);

}


function billingSelector() {

    const organizationId = selectedOrganizationId();

    return organizationId ? { organization_id: organizationId } : {};

}


function formatBillingStamp(value) {

    if (!value) {

        return "—";

    }

    const date = new Date(value);

    if (Number.isNaN(date.getTime())) {

        return String(value);

    }

    return new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short"
    }).format(date);

}


function formatPlanAmount(amountMinor, currency) {

    if (typeof amountMinor !== "number" || !Number.isFinite(amountMinor) || !currency) {

        return "";

    }

    try {

        const format = new Intl.NumberFormat(undefined, {
            style: "currency",
            currency: currency
        });
        const digits = format.resolvedOptions().maximumFractionDigits;
        return format.format(amountMinor / (10 ** digits));

    } catch (error) {

        return "";

    }

}


function billingIntervalLabel(interval) {

    if (interval === "month") {

        return "Monthly";

    }

    if (interval === "year") {

        return "Yearly";

    }

    if (interval === "none" || !interval) {

        return "No billing interval";

    }

    return String(interval);

}


function trialRemainingText(trialEnd) {

    if (!trialEnd) {

        return "";

    }

    const end = new Date(trialEnd);

    if (Number.isNaN(end.getTime())) {

        return "";

    }

    const remaining = end.getTime() - Date.now();

    if (remaining <= 0) {

        return "The trial end time has passed. The status above is still the one reported by ERAP.";

    }

    const days = Math.ceil(remaining / 86400000);

    return days === 1 ? "1 day remaining in the trial." : days + " days remaining in the trial.";

}


function apiFailureText(payload, fallback) {

    if (payload && payload.error && payload.error.code === "BILLING_REQUIRED") {

        return "Subscription required for this operation.";

    }

    if (payload && payload.message) {

        return String(payload.message);

    }

    return fallback;

}


async function billingRequest(path, method, body) {

    const options = {
        method: method,
        headers: {
            "Authorization": "Bearer " + getIdToken()
        }
    };

    if (body) {

        options.headers["Content-Type"] = "application/json";
        options.body = JSON.stringify(body);

    }

    try {

        const response = await fetch(BILLING_API_URL + path + (method === "GET" ? billingQuery() : ""), options);
        const payload = unwrapApiPayload(await response.json().catch(() => ({})));

        return {
            ok: response.ok,
            status: response.status,
            payload: payload
        };

    } catch (error) {

        return {
            ok: false,
            status: 0,
            payload: { message: "Unable to reach billing." }
        };

    }

}


function initializeBilling() {

    const root = $("billing");

    if (!root || root.dataset.bound === "1") {

        return;

    }

    root.dataset.bound = "1";

    $("billingRefreshBtn")?.addEventListener("click", () => {

        billingCancelArmed = false;
        loadBilling();

    });

    root.addEventListener("click", onBillingClick);

}


async function loadBilling() {

    const workspace = $("billingWorkspace");

    if (!workspace) {

        return;

    }

    const ticket = ++billingLoad;
    workspace.innerHTML = `<p class="billing-note">Loading billing…</p>`;

    const [summary, plans, events] = await Promise.all([
        billingRequest("", "GET"),
        billingRequest("/plans", "GET"),
        billingRequest("/events", "GET")
    ]);

    if (ticket !== billingLoad) {

        return;

    }

    renderBilling(summary, plans, events);

}


function onBillingClick(event) {

    const button = event.target.closest("[data-billing-action]");

    if (!button) {

        return;

    }

    const action = button.dataset.billingAction;

    if (action === "retry") {

        billingCancelArmed = false;
        loadBilling();
        return;

    }

    if (!billingSnapshot) {

        return;

    }

    if (action === "cancel-ask") {

        billingCancelArmed = true;
        renderBilling(billingSnapshot.summary, billingSnapshot.plans, billingSnapshot.events);
        return;

    }

    if (action === "cancel-dismiss") {

        billingCancelArmed = false;
        renderBilling(billingSnapshot.summary, billingSnapshot.plans, billingSnapshot.events);
        return;

    }

    if (action === "cancel-confirm") {

        confirmBillingCancellation();
        return;

    }

    if (action === "checkout") {

        startBillingCheckout(button.dataset.planId, button.dataset.purchasable === "true");

    }

}


async function startBillingCheckout(planId, purchasable) {

    if (!purchasable || !planId || !canManageBilling()) {

        return;

    }

    const result = await billingRequest("/checkout", "POST", {
        ...billingSelector(),
        plan_id: planId
    });

    if (!result.ok) {

        showToast(apiFailureText(result.payload, "Checkout could not be started."));
        return;

    }

    showToast("Checkout reference saved. Payment is confirmed only after the provider notifies ERAP.");
    billingCancelArmed = false;
    loadBilling();

}


async function confirmBillingCancellation() {

    const result = await billingRequest("/cancel", "POST", billingSelector());

    if (!result.ok) {

        showToast(apiFailureText(result.payload, "Cancellation could not be scheduled."));
        return;

    }

    billingCancelArmed = false;
    showToast("Cancellation request sent. The subscription status will update from ERAP.");
    loadBilling();

}


function renderBilling(summary, plans, events) {

    billingSnapshot = { summary, plans, events };
    const workspace = $("billingWorkspace");

    if (!workspace) {

        return;

    }

    if (!summary.ok && (summary.status === 401 || summary.status === 403)) {

        workspace.innerHTML = `
            <p class="billing-error">${escapeHtml(apiFailureText(summary.payload, "You do not have access to billing for this organization."))}</p>
            <button class="secondary-btn" type="button" data-billing-action="retry">Retry</button>
        `;
        return;

    }

    if (!summary.ok && !plans.ok && !events.ok) {

        workspace.innerHTML = `
            <p class="billing-error">${escapeHtml(apiFailureText(summary.payload, "Billing could not be loaded."))}</p>
            <button class="secondary-btn" type="button" data-billing-action="retry">Retry</button>
        `;
        return;

    }

    const subscription = summary.ok && summary.payload.subscription ? summary.payload.subscription : null;
    const status = subscription ? subscription.subscription_status : "";
    const planList = plans.ok && Array.isArray(plans.payload.plans) ? plans.payload.plans : [];
    const planName = planDisplayName(planList, subscription);
    const eventList = events.ok && Array.isArray(events.payload.events) ? events.payload.events : [];

    workspace.innerHTML = `
        ${summary.ok && subscription ? billingOverview(subscription, summary.payload.next_action, planName) : summary.ok ? `<p class="billing-note">No subscription details were returned.</p>` : `<p class="billing-error">${escapeHtml(apiFailureText(summary.payload, "Subscription details could not be loaded."))}</p>`}
        ${plans.ok ? billingPlans(planList, subscription) : `<p class="billing-error">${escapeHtml(apiFailureText(plans.payload, "Plans could not be loaded."))}</p>`}
        ${billingActions(subscription, summary.payload && summary.payload.next_action)}
        ${events.ok ? billingEvents(eventList) : `<p class="billing-error">${escapeHtml(apiFailureText(events.payload, "Billing history could not be loaded."))}</p><button class="secondary-btn" type="button" data-billing-action="retry">Retry</button>`}
    `;

}


function planDisplayName(planList, subscription) {

    if (!subscription) {

        return "";

    }

    const match = planList.find(plan => plan.plan_id === subscription.plan_id);

    return match && match.display_name ? match.display_name : (subscription.plan_id || "—");

}


function billingOverview(subscription, nextAction, planName) {

    const status = subscription.subscription_status || "";
    const label = BILLING_STATUS_LABELS[status] || status || "Unknown";
    const lines = [
        ["Status code", status || "—"],
        ["Plan", planName || "—"],
        ["Billing interval", billingIntervalLabel(subscription.billing_interval)]
    ];

    if (status === "TRIALING") {

        lines.push(["Trial start", formatBillingStamp(subscription.trial_start)]);
        lines.push(["Trial end", formatBillingStamp(subscription.trial_end)]);

    }

    if (subscription.current_period_start || subscription.current_period_end) {

        lines.push(["Period start", formatBillingStamp(subscription.current_period_start)]);
        lines.push(["Period end", formatBillingStamp(subscription.current_period_end)]);

    }

    if (subscription.cancelled_at) {

        lines.push(["Cancelled at", formatBillingStamp(subscription.cancelled_at)]);

    }

    if (subscription.pending_plan_id) {

        lines.push(["Selected plan", subscription.pending_plan_id + " awaiting confirmation"]);

    }

    return `
        <p class="billing-kicker">Current subscription</p>
        <div class="billing-status">
            <strong>${escapeHtml(label)}</strong>
            <span>${escapeHtml(status)}</span>
        </div>
        <p>${escapeHtml(billingNarrative(subscription, nextAction))}</p>
        <div class="billing-facts">
            ${lines.map(line => `<div><span>${escapeHtml(line[0])}</span><span>${escapeHtml(line[1])}</span></div>`).join("")}
        </div>
    `;

}


function billingNarrative(subscription, nextAction) {

    const status = subscription.subscription_status;

    if (status === "TRIALING") {

        return trialRemainingText(subscription.trial_end) || "This organization is in a trial. The dates above come from ERAP.";

    }

    if (status === "ACTIVE" && subscription.cancel_at_period_end) {

        return "Cancellation scheduled. Access continues through the current billing period. This is not an immediate cancellation.";

    }

    if (status === "ACTIVE") {

        return "The subscription is active for the current billing period.";

    }

    if (status === "PAST_DUE") {

        return "Payment required. The organization is not marked expired. Operational access follows the current ERAP rules.";

    }

    if (status === "EXPIRED") {

        return "Your organization is currently read-only. Renew your subscription to restore operational changes.";

    }

    if (status === "CANCELLED") {

        return "The subscription is cancelled. Records remain with the organization.";

    }

    if (status === "GRANDFATHERED") {

        return "This organization currently has legacy access. Viewing this page does not create a subscription.";

    }

    if (nextAction === "subscribe") {

        return "A subscription can be started when a plan is available for purchase.";

    }

    return "The subscription state shown here is the one returned by ERAP.";

}


function billingPlans(planList, subscription) {

    const owner = canManageBilling();
    const items = planList.map(plan => {

        const price = formatPlanAmount(plan.amount_minor, plan.currency);
        const purchasable = plan.purchasable === true;
        const label = purchasable ? "Start checkout" : "Not currently available";
        const detail = [
            plan.display_name || plan.plan_id,
            billingIntervalLabel(plan.billing_interval),
            price
        ].filter(Boolean).join(" · ");

        return `
            <div class="billing-plan">
                <p>${escapeHtml(detail)}${purchasable ? "" : ". Coming soon."}</p>
                <button
                    class="secondary-btn"
                    type="button"
                    data-billing-action="checkout"
                    data-plan-id="${escapeHtml(plan.plan_id || "")}"
                    data-purchasable="${purchasable ? "true" : "false"}"
                    ${purchasable && owner ? "" : "disabled"}
                >${escapeHtml(label)}</button>
            </div>
        `;

    }).join("");

    const note = owner
        ? ""
        : `<p class="billing-note">Only the organization owner can start checkout or schedule cancellation.</p>`;

    return `
        <p class="billing-kicker">Plans</p>
        <div class="billing-plans">${items || `<p class="billing-note">No plans were returned.</p>`}</div>
        ${note}
    `;

}


function billingActions(subscription, nextAction) {

    if (!subscription || !canManageBilling()) {

        return "";

    }

    if (subscription.cancel_at_period_end) {

        return `<p class="billing-note">A cancellation is already scheduled. Another request will not be sent.</p>`;

    }

    if (subscription.subscription_status !== "ACTIVE" || nextAction !== "manage_subscription") {

        return "";

    }

    if (!billingCancelArmed) {

        return `
            <p class="billing-kicker">Account action</p>
            <button class="secondary-btn" type="button" data-billing-action="cancel-ask">Schedule cancellation</button>
        `;

    }

    return `
        <div class="billing-confirm">
            <p>Cancellation is scheduled at the end of the current billing period. Access continues until then. This does not delete the organization or its operational records. Billing history stays with the organization.</p>
            <div class="text-actions">
                <button class="secondary-btn" type="button" data-billing-action="cancel-confirm">Confirm cancellation</button>
                <button class="secondary-btn" type="button" data-billing-action="cancel-dismiss">Keep subscription</button>
            </div>
        </div>
    `;

}


function billingEvents(eventList) {

    if (!eventList.length) {

        return `
            <p class="billing-kicker">History</p>
            <p class="billing-note">No billing events yet.</p>
        `;

    }

    const rows = eventList.map(item => {

        const parts = [
            item.event_type,
            item.processing_status,
            item.received_at ? formatBillingStamp(item.received_at) : ""
        ].filter(Boolean);

        if (item.provider_payment_id) {

            parts.push(item.provider_payment_id);

        }

        return `<div class="billing-event"><p>${escapeHtml(parts.join(" · "))}</p></div>`;

    }).join("");

    return `
        <p class="billing-kicker">History</p>
        <div class="billing-events">${rows}</div>
    `;

}


/* =========================================================
   RESOURCE EXCHANGE
========================================================= */

let exchangeView = "network";
let exchangeNetworkItems = [];
let exchangeMineItems = [];
let exchangeMyOffers = [];
let exchangeSelectedId = "";
let exchangeDetailRequest = null;
let exchangeDetailOffers = [];
let exchangeBusy = false;

function exchangeIdempotencyKey(prefix) {
    return prefix + "-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
}

function exchangeStatusLabel(status) {
    const value = String(status || "").toUpperCase();
    if (value === "OPEN") return "Open";
    if (value === "ACCEPTED") return "Accepted / Resource Held";
    if (value === "TRANSFER_PENDING") return "Transfer Pending";
    if (value === "COMPLETED") return "Completed";
    if (value === "CANCELLED") return "Cancelled";
    if (value === "EXPIRED") return "Expired";
    if (value === "SUPERSEDED") return "Superseded";
    if (value === "REJECTED") return "Rejected";
    if (value === "WITHDRAWN") return "Withdrawn";
    return value || "Unknown";
}

function exchangeStatusClass(status) {
    const value = String(status || "").toUpperCase();
    if (value === "OPEN") return "exchange-open";
    if (value === "ACCEPTED") return "exchange-accepted";
    if (value === "TRANSFER_PENDING") return "exchange-pending";
    if (value === "COMPLETED") return "exchange-completed";
    return "exchange-terminal";
}

function canWriteExchange() {
    return canOperateResources();
}

function exchangeConflictMessage(payload, fallback) {
    const message = apiFailureText(payload, fallback || "This exchange action could not be completed.");
    const lower = String(message).toLowerCase();
    if (lower.includes("quantity") && lower.includes("deferred")) {
        return "Quantity-resource handover is not currently supported for this exchange.";
    }
    if (lower.includes("quantity") && lower.includes("available")) {
        return "The requested quantity is no longer available. Refresh and choose another resource.";
    }
    if (lower.includes("offer") && (lower.includes("open") || lower.includes("available"))) {
        return "The offer is no longer available.";
    }
    if (lower.includes("conflict") || lower.includes("already") || lower.includes("held") || lower.includes("allocated")) {
        return "The resource was already committed by another operation. Refresh and review the current state.";
    }
    return message;
}

async function exchangeRequest(path, options = {}) {
    const organizationId = selectedOrganizationId();
    if (!organizationId) {
        throw new Error("Select an organization first.");
    }

    const method = String(options.method || "GET").toUpperCase();
    const params = new URLSearchParams(options.query || {});
    params.set("organization_id", organizationId);

    const url = EXCHANGE_API_URL + path + "?" + params.toString();
    const headers = {
        "Authorization": "Bearer " + (await waitForIdToken() || getIdToken() || "")
    };

    if (method !== "GET" && method !== "HEAD") {
        headers["Content-Type"] = "application/json";
    }

    const response = await fetch(url, {
        method: method,
        headers: headers,
        body: options.body ? JSON.stringify(options.body) : undefined
    });

    let payload = {};
    try {
        payload = unwrapApiPayload(await response.json());
    } catch (_error) {
        payload = {};
    }

    if (!response.ok) {
        const error = new Error(exchangeConflictMessage(payload, "Unable to complete exchange request."));
        error.status = response.status;
        error.payload = payload;
        throw error;
    }

    return payload;
}

function setExchangeBusy(button, busy, busyLabel, idleLabel) {
    exchangeBusy = Boolean(busy);
    if (!button) {
        return;
    }
    button.disabled = exchangeBusy;
    if (busyLabel || idleLabel) {
        button.textContent = exchangeBusy ? busyLabel : idleLabel;
    }
}

function renderExchangeTabs() {
    document.querySelectorAll("[data-exchange-view]").forEach(button => {
        const active = button.dataset.exchangeView === exchangeView;
        button.classList.toggle("active", active);
        button.setAttribute("aria-selected", active ? "true" : "false");
    });

    const labels = {
        network: ["Network", "Open network requests", "Visible to verified ERAP organizations. Not public internet discovery."],
        mine: ["My requests", "My exchange requests", "Requests created by the selected organization."],
        offers: ["My offers", "Offers I submitted", "Offers your organization made on network requests."]
    };
    const current = labels[exchangeView] || labels.network;
    if ($("exchangeListLabel")) $("exchangeListLabel").textContent = current[0];
    if ($("exchangeListTitle")) $("exchangeListTitle").textContent = current[1];
    if ($("exchangeListHint")) $("exchangeListHint").textContent = current[2];
}

function exchangeRequestCard(item, selected) {
    const status = item.status || "OPEN";
    const title = item.resource_type_name || "Resource request";
    const qty = item.quantity_requested != null ? item.quantity_requested : "";
    const mode = item.tracking_mode || "";
    const where = [item.destination_city, item.destination_state].filter(Boolean).join(", ");
    const orgName = item.requester_organization_display_name || "";
    return `
        <button type="button" class="exchange-card ${selected ? "active" : ""}" data-exchange-select="${escapeHtml(item.exchange_request_id || "")}">
            <div class="exchange-card-top">
                <strong>${escapeHtml(title)}</strong>
                <span class="status-badge ${exchangeStatusClass(status)}">${escapeHtml(exchangeStatusLabel(status))}</span>
            </div>
            <div class="exchange-card-meta">
                <span>${escapeHtml(mode)}${qty !== "" ? " · qty " + escapeHtml(String(qty)) : ""}</span>
                <span>${escapeHtml(where || item.destination_location_name || "")}</span>
            </div>
            ${orgName ? `<div class="exchange-muted">${escapeHtml(orgName)}</div>` : ""}
        </button>
    `;
}

function exchangeOfferCard(item) {
    const status = item.status || "OPEN";
    return `
        <button type="button" class="exchange-card" data-exchange-select="${escapeHtml(item.exchange_request_id || "")}">
            <div class="exchange-card-top">
                <strong>${escapeHtml(item.resource_snapshot?.name || item.resource_id || "Offer")}</strong>
                <span class="status-badge ${exchangeStatusClass(status)}">${escapeHtml(exchangeStatusLabel(status))}</span>
            </div>
            <div class="exchange-card-meta">
                <span>Request ${escapeHtml(item.exchange_request_id || "")}</span>
                <span>Qty ${escapeHtml(String(item.quantity_offered != null ? item.quantity_offered : 1))}</span>
            </div>
        </button>
    `;
}

function renderExchangeList() {
    const host = $("exchangeList");
    if (!host) {
        return;
    }

    if (!selectedOrganizationId()) {
        host.innerHTML = `<div class="empty-state"><h3>Select an organization</h3><p>Exchange loads after an organization is selected.</p></div>`;
        return;
    }

    if (exchangeView === "network") {
        if (!exchangeNetworkItems.length) {
            host.innerHTML = `<div class="empty-state"><h3>No open network requests</h3><p>No open resource exchange requests are currently available.</p></div>`;
            return;
        }
        host.innerHTML = exchangeNetworkItems.map(item => exchangeRequestCard(item, item.exchange_request_id === exchangeSelectedId)).join("");
        return;
    }

    if (exchangeView === "mine") {
        if (!exchangeMineItems.length) {
            host.innerHTML = `<div class="empty-state"><h3>No exchange requests yet</h3><p>You haven't created any exchange requests yet.</p></div>`;
            return;
        }
        host.innerHTML = exchangeMineItems.map(item => exchangeRequestCard(item, item.exchange_request_id === exchangeSelectedId)).join("");
        return;
    }

    if (!exchangeMyOffers.length) {
        host.innerHTML = `<div class="empty-state"><h3>No offers yet</h3><p>You haven't offered a resource on any exchange request yet.</p></div>`;
        return;
    }
    host.innerHTML = exchangeMyOffers.map(exchangeOfferCard).join("");
}

function eligibleOfferResources() {
    return (resources || []).filter(resource => {
        const status = String(resource.operational_status || (resource.Available === false ? "ALLOCATED" : "AVAILABLE")).toUpperCase();
        if (isQuantityResource(resource)) {
            return Number(resource.quantity_available || 0) > 0;
        }
        return status === "AVAILABLE" && resource.Available !== false;
    });
}

function fillExchangeCreateForm() {
    const typeSelect = $("exchangeResourceType");
    const locationSelect = $("exchangeDestinationLocation");
    if (typeSelect) {
        const options = (resourceTypes || []).filter(item => String(item.status || "ACTIVE").toUpperCase() === "ACTIVE");
        typeSelect.innerHTML = options.map(item =>
            `<option value="${escapeHtml(item.resource_type_id)}">${escapeHtml(item.name || item.resource_type_id)}</option>`
        ).join("") || `<option value="">No resource types</option>`;
    }
    if (locationSelect) {
        fillLocationSelect(locationSelect);
    }
    const tracking = $("exchangeTrackingMode");
    const qty = $("exchangeQuantityRequested");
    if (tracking && qty) {
        qty.value = tracking.value === "QUANTITY" ? Math.max(1, Number(qty.value) || 1) : "1";
        qty.readOnly = tracking.value !== "QUANTITY";
    }
}

function renderExchangeOfferComposer(request) {
    if (!canWriteExchange()) {
        return "";
    }
    if (String(request.status || "").toUpperCase() !== "OPEN") {
        return "";
    }
    if (request.requester_organization_id && request.requester_organization_id === selectedOrganizationId()) {
        return "";
    }

    const candidates = eligibleOfferResources();
    const options = candidates.map(resource => {
        const label = `${resource.name || resource.id} · ${resource.Type || ""} · ${resource.location_id || ""}`;
        return `<option value="${escapeHtml(resource.id)}" data-location-id="${escapeHtml(resource.location_id || "")}" data-tracking="${escapeHtml(resource.tracking_mode || "INDIVIDUAL")}" data-available="${escapeHtml(String(resource.quantity_available != null ? resource.quantity_available : 1))}">${escapeHtml(label)}</option>`;
    }).join("");

    return `
        <div class="exchange-action-block">
            <h3>Offer a resource</h3>
            <p class="exchange-muted">Creating an offer does NOT reserve or allocate this resource. The resource stays available until the requester accepts.</p>
            <div class="form-group">
                <label for="exchangeOfferResource">Eligible resource</label>
                <select id="exchangeOfferResource">${options || `<option value="">No eligible resources</option>`}</select>
            </div>
            <div class="form-group">
                <label for="exchangeOfferQuantity">Quantity offered</label>
                <input id="exchangeOfferQuantity" type="number" min="1" step="1" value="1">
            </div>
            <div class="form-group">
                <label for="exchangeOfferNotes">Notes (optional)</label>
                <textarea id="exchangeOfferNotes" rows="2" maxlength="500"></textarea>
            </div>
            <button class="primary-btn" type="button" id="exchangeSubmitOfferBtn" ${candidates.length ? "" : "disabled"}>Submit Offer</button>
        </div>
    `;
}

function renderExchangeOffers(request) {
    const org = selectedOrganizationId();
    const isRequester = request.requester_organization_id === org;
    const visibleOffers = isRequester
        ? exchangeDetailOffers
        : exchangeDetailOffers.filter(offer => offer.provider_organization_id === org);
    if (!isRequester && !visibleOffers.length) {
        return "";
    }
    if (!visibleOffers.length) {
        return `<div class="exchange-offer-block"><h3>Offers</h3><div class="empty-state"><h3>No offers yet</h3><p>No offers have been submitted for this request yet.</p></div></div>`;
    }

    const requestOpen = String(request.status || "").toUpperCase() === "OPEN";
    return `
        <div class="exchange-offer-block">
            <h3>Offers</h3>
            ${visibleOffers.map(offer => {
                const status = String(offer.status || "").toUpperCase();
                const canAccept = canWriteExchange() && isRequester && status === "OPEN" && requestOpen;
                const canReject = canWriteExchange() && isRequester && status === "OPEN" && requestOpen
                    && offer.provider_organization_id !== org;
                const canWithdraw = canWriteExchange() && status === "OPEN"
                    && offer.provider_organization_id === org;
                return `
                    <div class="exchange-card">
                        <div class="exchange-card-top">
                            <strong>${escapeHtml(offer.resource_snapshot?.name || offer.resource_id || offer.offer_id || "Offer")}</strong>
                            <span class="status-badge ${exchangeStatusClass(status)}">${escapeHtml(exchangeStatusLabel(status))}</span>
                        </div>
                        <div class="exchange-card-meta">
                            <span>${escapeHtml(offer.provider_organization_id || "")}</span>
                            <span>Qty ${escapeHtml(String(offer.quantity_offered != null ? offer.quantity_offered : 1))}</span>
                        </div>
                        ${offer.source_location_id ? `<div class="exchange-muted">Provider location ${escapeHtml(offer.source_location_id)}</div>` : ""}
                        ${offer.expires_at ? `<div class="exchange-muted">Expires ${escapeHtml(String(offer.expires_at))}</div>` : ""}
                        ${offer.notes ? `<div class="exchange-muted">${escapeHtml(offer.notes)}</div>` : ""}
                        <div class="exchange-actions">
                            ${canAccept ? `<button class="primary-btn" type="button" data-exchange-accept="${escapeHtml(offer.offer_id)}">Accept Offer</button>` : ""}
                            ${canReject ? `<button class="secondary-btn" type="button" data-exchange-reject="${escapeHtml(offer.offer_id)}">Reject Offer</button>` : ""}
                            ${canWithdraw ? `<button class="secondary-btn" type="button" data-exchange-withdraw="${escapeHtml(offer.offer_id)}">Withdraw Offer</button>` : ""}
                        </div>
                    </div>
                `;
            }).join("")}
        </div>
    `;
}

function renderExchangeLifecycleActions(request) {
    const status = String(request.status || "").toUpperCase();
    const org = selectedOrganizationId();
    const isRequester = request.requester_organization_id === org;
    const isProvider = request.accepted_provider_organization_id === org;
    let actions = "";

    if (canWriteExchange() && isRequester && (status === "OPEN" || status === "ACCEPTED" || status === "TRANSFER_PENDING")) {
        actions += `
            <div class="exchange-action-block">
                <h3>Cancel request</h3>
                <p class="exchange-muted">Cancels this exchange. If a resource is held, the hold is released and ownership is not transferred.</p>
                <button class="secondary-btn" type="button" id="exchangeCancelRequestBtn">Cancel Request</button>
            </div>
        `;
    }

    if (request.expires_at || request.handover_expires_at) {
        actions += `
            <div class="exchange-action-block">
                <h3>Expiry</h3>
                <p class="exchange-muted">
                    ${request.expires_at ? `Request expires ${escapeHtml(String(request.expires_at))}. ` : ""}
                    ${request.handover_expires_at ? `Handover due ${escapeHtml(String(request.handover_expires_at))}.` : ""}
                </p>
            </div>
        `;
    }

    if (canWriteExchange() && isProvider && status === "ACCEPTED") {
        actions += `
            <div class="exchange-action-block">
                <h3>Start transfer</h3>
                <p class="exchange-muted">Marks the held resource as ready for handover. Ownership is not transferred yet.</p>
                <button class="primary-btn" type="button" id="exchangeStartTransferBtn">Start Transfer</button>
            </div>
        `;
    }

    if (canWriteExchange() && isRequester && status === "TRANSFER_PENDING") {
        const locationOptions = (currentUser.locations || [])
            .filter(item => String(item.status || "ACTIVE").toUpperCase() === "ACTIVE")
            .map(item => `<option value="${escapeHtml(item.location_id)}" ${item.location_id === request.destination_location_id ? "selected" : ""}>${escapeHtml(item.name || item.location_id)}</option>`)
            .join("");
        actions += `
            <div class="exchange-action-block">
                <h3>Confirm handover</h3>
                <p class="exchange-muted">Confirming handover transfers ownership of this resource to your organization and moves it to the selected destination location.</p>
                <div class="form-group">
                    <label for="exchangeHandoverLocation">Destination location</label>
                    <select id="exchangeHandoverLocation">${locationOptions}</select>
                </div>
                <button class="primary-btn" type="button" id="exchangeConfirmHandoverBtn">Confirm Handover</button>
            </div>
        `;
    }

    if (status === "COMPLETED") {
        actions += `
            <div class="exchange-action-block">
                <h3>Completed</h3>
                <p class="exchange-muted">Ownership and location were transferred. The resource should appear under the requester organization as AVAILABLE / PRIVATE.</p>
            </div>
        `;
    }

    if (status === "CANCELLED" || status === "EXPIRED") {
        actions += `
            <div class="exchange-action-block">
                <h3>${escapeHtml(exchangeStatusLabel(status))}</h3>
                <p class="exchange-muted">This exchange is closed. No further lifecycle actions are available.</p>
            </div>
        `;
    }

    return actions;
}

function renderExchangeDetail() {
    const host = $("exchangeDetail");
    if (!host) {
        return;
    }

    if (!exchangeDetailRequest) {
        host.innerHTML = `<div class="empty-state"><h3>No request selected</h3><p>Choose an item from the list.</p></div>`;
        if ($("exchangeDetailTitle")) $("exchangeDetailTitle").textContent = "Select a request";
        if ($("exchangeDetailStatusLine")) $("exchangeDetailStatusLine").textContent = "Open a network or organization request to review offers and actions.";
        return;
    }

    const request = exchangeDetailRequest;
    const status = request.status || "OPEN";
    if ($("exchangeDetailTitle")) {
        $("exchangeDetailTitle").textContent = request.resource_type_name || request.exchange_request_id || "Exchange request";
    }
    if ($("exchangeDetailStatusLine")) {
        $("exchangeDetailStatusLine").textContent = exchangeStatusLabel(status);
    }

    const fields = [
        ["Status", exchangeStatusLabel(status)],
        ["Request ID", request.exchange_request_id],
        ["Resource type", request.resource_type_name],
        ["Tracking", request.tracking_mode],
        ["Quantity", request.quantity_requested],
        ["Destination", request.destination_location_name || request.destination_location_id],
        ["Accepted offer", request.accepted_offer_id],
        ["Held resource", request.accepted_resource_id],
        ["Provider organization", request.accepted_provider_organization_id],
        ["Request expires", request.expires_at],
        ["Handover due", request.handover_expires_at],
        ["Notes", request.notes]
    ].filter(pair => pair[1] !== undefined && pair[1] !== null && pair[1] !== "");

    host.innerHTML = `
        <div class="exchange-card-top">
            <span class="status-badge ${exchangeStatusClass(status)}">${escapeHtml(exchangeStatusLabel(status))}</span>
        </div>
        <div class="exchange-detail-fields">
            ${fields.map(([label, value]) => `
                <div class="form-group">
                    <label>${escapeHtml(label)}</label>
                    <div>${escapeHtml(String(value))}</div>
                </div>
            `).join("")}
        </div>
        ${renderExchangeOffers(request)}
        ${renderExchangeOfferComposer(request)}
        ${renderExchangeLifecycleActions(request)}
    `;
}

async function loadExchangeWorkspace() {
    renderExchangeTabs();
    fillExchangeCreateForm();
    if (!selectedOrganizationId()) {
        exchangeNetworkItems = [];
        exchangeMineItems = [];
        exchangeMyOffers = [];
        exchangeDetailRequest = null;
        renderExchangeList();
        renderExchangeDetail();
        return;
    }

    if ($("exchangeCreateRequestBtn")) {
        $("exchangeCreateRequestBtn").hidden = !canWriteExchange();
    }

    try {
        if (!resourceTypes.length) {
            await loadResourceTypes();
        }
        if (!resources.length) {
            await loadResources();
        }

        const [network, mine, offers] = await Promise.all([
            exchangeRequest("/requests", { query: { scope: "network" } }).catch(error => {
                showToast(error.message || "Unable to load network requests.");
                return { items: [] };
            }),
            exchangeRequest("/requests", { query: { scope: "mine" } }).catch(error => {
                showToast(error.message || "Unable to load my exchange requests.");
                return { items: [] };
            }),
            exchangeRequest("/offers", { query: { scope: "mine" } }).catch(error => {
                showToast(error.message || "Unable to load my offers.");
                return { items: [] };
            })
        ]);

        exchangeNetworkItems = network.items || [];
        exchangeMineItems = mine.items || [];
        exchangeMyOffers = offers.items || [];
        renderExchangeList();

        if (exchangeSelectedId) {
            await openExchangeRequest(exchangeSelectedId, { quiet: true });
        } else {
            renderExchangeDetail();
        }
    } catch (error) {
        showToast(error.message || "Unable to load Exchange.");
    }
}

async function openExchangeRequest(requestId, options = {}) {
    if (!requestId) {
        return;
    }
    exchangeSelectedId = requestId;
    renderExchangeList();

    try {
        const detail = await exchangeRequest("/requests/" + encodeURIComponent(requestId));
        exchangeDetailRequest = detail.request || detail;
        try {
            const offers = await exchangeRequest(
                "/requests/" + encodeURIComponent(requestId) + "/offers"
            );
            exchangeDetailOffers = offers.items || [];
        } catch (_error) {
            exchangeDetailOffers = [];
        }
        renderExchangeDetail();
    } catch (error) {
        exchangeDetailRequest = null;
        exchangeDetailOffers = [];
        renderExchangeDetail();
        if (!options.quiet) {
            showToast(error.message || "Unable to open exchange request.");
        }
    }
}

async function createExchangeRequest(event) {
    event.preventDefault();
    if (exchangeBusy || !canWriteExchange()) {
        showToast("You cannot create exchange requests with this role.");
        return;
    }

    const tracking = ($("exchangeTrackingMode")?.value || "INDIVIDUAL").toUpperCase();
    const quantity = Number($("exchangeQuantityRequested")?.value || 1);
    if (!Number.isInteger(quantity) || quantity < 1) {
        showToast("Enter a whole-number quantity of at least 1.");
        return;
    }
    if (tracking === "INDIVIDUAL" && quantity !== 1) {
        showToast("Individual exchange requests require quantity 1.");
        return;
    }

    const body = {
        destination_location_id: $("exchangeDestinationLocation")?.value || "",
        resource_type_id: $("exchangeResourceType")?.value || "",
        tracking_mode: tracking,
        quantity_requested: quantity,
        notes: $("exchangeRequestNotes")?.value || "",
        visibility: "NETWORK",
        idempotency_key: exchangeIdempotencyKey("exreq")
    };

    const button = $("exchangeCreateSubmitBtn");
    setExchangeBusy(button, true, "Creating...", "Create Request");
    try {
        await exchangeRequest("/requests", { method: "POST", body: body });
        showToast("Exchange request created.");
        if ($("exchangeCreatePanel")) $("exchangeCreatePanel").hidden = true;
        if ($("exchangeCreateForm")) $("exchangeCreateForm").reset();
        $("exchangeQuantityRequested").value = "1";
        exchangeView = "mine";
        await loadExchangeWorkspace();
    } catch (error) {
        showToast(error.message || "Unable to create exchange request.");
        if (error.status === 409) {
            await loadExchangeWorkspace();
        }
    } finally {
        setExchangeBusy(button, false, "Creating...", "Create Request");
    }
}

async function submitExchangeOffer() {
    if (exchangeBusy || !exchangeDetailRequest || !canWriteExchange()) {
        return;
    }
    const select = $("exchangeOfferResource");
    const option = select?.selectedOptions?.[0];
    const resourceId = select?.value || "";
    if (!resourceId) {
        showToast("Select an eligible resource to offer.");
        return;
    }
    const tracking = String(option?.dataset?.tracking || "INDIVIDUAL").toUpperCase();
    const quantity = Number($("exchangeOfferQuantity")?.value || 1);
    if (!Number.isInteger(quantity) || quantity < 1) {
        showToast("Enter a whole-number offered quantity of at least 1.");
        return;
    }
    if (tracking === "INDIVIDUAL" && quantity !== 1) {
        showToast("Individual offers require quantity 1.");
        return;
    }

    const button = $("exchangeSubmitOfferBtn");
    setExchangeBusy(button, true, "Submitting Offer...", "Submit Offer");
    try {
        await exchangeRequest(
            "/requests/" + encodeURIComponent(exchangeDetailRequest.exchange_request_id) + "/offers",
            {
                method: "POST",
                body: {
                    resource_id: resourceId,
                    provider_location_id: option?.dataset?.locationId || "",
                    quantity_offered: quantity,
                    notes: $("exchangeOfferNotes")?.value || "",
                    idempotency_key: exchangeIdempotencyKey("exoff")
                }
            }
        );
        showToast("Offer submitted. The resource was not reserved.");
        await loadExchangeWorkspace();
        await openExchangeRequest(exchangeDetailRequest.exchange_request_id);
    } catch (error) {
        showToast(error.message || "Unable to submit offer.");
        if (error.status === 409) {
            await loadExchangeWorkspace();
            await openExchangeRequest(exchangeSelectedId, { quiet: true });
        }
    } finally {
        setExchangeBusy(button, false, "Submitting Offer...", "Submit Offer");
    }
}

async function acceptExchangeOffer(offerId) {
    if (exchangeBusy || !exchangeDetailRequest || !offerId || !canWriteExchange()) {
        return;
    }
    if (!window.confirm("Accepting this offer will commit the offered resource to this exchange. Ownership is not transferred yet.")) {
        return;
    }

    const button = document.querySelector(`[data-exchange-accept="${offerId}"]`);
    setExchangeBusy(button, true, "Accepting...", "Accept Offer");
    try {
        await exchangeRequest(
            "/requests/" + encodeURIComponent(exchangeDetailRequest.exchange_request_id)
                + "/offers/" + encodeURIComponent(offerId) + "/accept",
            { method: "POST", body: {} }
        );
        showToast("Offer accepted. The resource is held for this exchange.");
        await loadExchangeWorkspace();
        await openExchangeRequest(exchangeDetailRequest.exchange_request_id);
        await loadResources();
    } catch (error) {
        showToast(error.message || "Unable to accept offer.");
        await loadExchangeWorkspace();
        await openExchangeRequest(exchangeSelectedId, { quiet: true });
    } finally {
        setExchangeBusy(button, false, "Accepting...", "Accept Offer");
    }
}

async function rejectExchangeOffer(offerId) {
    if (exchangeBusy || !exchangeDetailRequest || !offerId || !canWriteExchange()) {
        return;
    }
    if (!window.confirm("Reject this offer? The request will stay open for other offers.")) {
        return;
    }
    const button = document.querySelector(`[data-exchange-reject="${offerId}"]`);
    setExchangeBusy(button, true, "Rejecting...", "Reject Offer");
    try {
        await exchangeRequest(
            "/requests/" + encodeURIComponent(exchangeDetailRequest.exchange_request_id)
                + "/offers/" + encodeURIComponent(offerId) + "/reject",
            { method: "POST", body: {} }
        );
        showToast("Offer rejected.");
        await loadExchangeWorkspace();
        await openExchangeRequest(exchangeDetailRequest.exchange_request_id);
    } catch (error) {
        showToast(error.message || "Unable to reject offer.");
        await loadExchangeWorkspace();
        await openExchangeRequest(exchangeSelectedId, { quiet: true });
    } finally {
        setExchangeBusy(button, false, "Rejecting...", "Reject Offer");
    }
}

async function withdrawExchangeOffer(offerId) {
    if (exchangeBusy || !exchangeDetailRequest || !offerId || !canWriteExchange()) {
        return;
    }
    if (!window.confirm("Withdraw this offer? Accepted offers cannot be withdrawn.")) {
        return;
    }
    const button = document.querySelector(`[data-exchange-withdraw="${offerId}"]`);
    setExchangeBusy(button, true, "Withdrawing...", "Withdraw Offer");
    try {
        await exchangeRequest(
            "/requests/" + encodeURIComponent(exchangeDetailRequest.exchange_request_id)
                + "/offers/" + encodeURIComponent(offerId) + "/withdraw",
            { method: "POST", body: {} }
        );
        showToast("Offer withdrawn.");
        await loadExchangeWorkspace();
        await openExchangeRequest(exchangeDetailRequest.exchange_request_id);
    } catch (error) {
        showToast(error.message || "Unable to withdraw offer.");
        await loadExchangeWorkspace();
        await openExchangeRequest(exchangeSelectedId, { quiet: true });
    } finally {
        setExchangeBusy(button, false, "Withdrawing...", "Withdraw Offer");
    }
}

async function cancelExchangeRequest() {
    if (exchangeBusy || !exchangeDetailRequest || !canWriteExchange()) {
        return;
    }
    if (!window.confirm("Cancel this exchange request? If a resource is held, the hold will be released and ownership will not transfer.")) {
        return;
    }
    const button = $("exchangeCancelRequestBtn");
    setExchangeBusy(button, true, "Cancelling...", "Cancel Request");
    try {
        await exchangeRequest(
            "/requests/" + encodeURIComponent(exchangeDetailRequest.exchange_request_id) + "/cancel",
            { method: "POST", body: {} }
        );
        showToast("Exchange request cancelled.");
        await loadExchangeWorkspace();
        await openExchangeRequest(exchangeDetailRequest.exchange_request_id);
        await loadResources();
    } catch (error) {
        showToast(error.message || "Unable to cancel request.");
        await loadExchangeWorkspace();
        await openExchangeRequest(exchangeSelectedId, { quiet: true });
    } finally {
        setExchangeBusy(button, false, "Cancelling...", "Cancel Request");
    }
}

async function startExchangeTransfer() {
    if (exchangeBusy || !exchangeDetailRequest || !canWriteExchange()) {
        return;
    }
    const button = $("exchangeStartTransferBtn");
    setExchangeBusy(button, true, "Starting Transfer...", "Start Transfer");
    try {
        await exchangeRequest(
            "/requests/" + encodeURIComponent(exchangeDetailRequest.exchange_request_id) + "/transfer/start",
            { method: "POST", body: {} }
        );
        showToast("The provider has marked the resource as ready for handover.");
        await loadExchangeWorkspace();
        await openExchangeRequest(exchangeDetailRequest.exchange_request_id);
    } catch (error) {
        showToast(error.message || "Unable to start transfer.");
        await loadExchangeWorkspace();
        await openExchangeRequest(exchangeSelectedId, { quiet: true });
    } finally {
        setExchangeBusy(button, false, "Starting Transfer...", "Start Transfer");
    }
}

async function confirmExchangeHandover() {
    if (exchangeBusy || !exchangeDetailRequest || !canWriteExchange()) {
        return;
    }
    if (!window.confirm("Confirming handover transfers ownership of this resource to your organization and moves it to the selected destination location.")) {
        return;
    }

    const button = $("exchangeConfirmHandoverBtn");
    setExchangeBusy(button, true, "Confirming Handover...", "Confirm Handover");
    try {
        await exchangeRequest(
            "/requests/" + encodeURIComponent(exchangeDetailRequest.exchange_request_id) + "/handover/confirm",
            {
                method: "POST",
                body: {
                    destination_location_id: $("exchangeHandoverLocation")?.value || exchangeDetailRequest.destination_location_id || ""
                }
            }
        );
        showToast("Handover completed. The resource is now owned by your organization.");
        await loadExchangeWorkspace();
        await openExchangeRequest(exchangeDetailRequest.exchange_request_id);
        await loadResources();
        await loadAllocations();
    } catch (error) {
        showToast(error.message || "Unable to confirm handover.");
        await loadExchangeWorkspace();
        await openExchangeRequest(exchangeSelectedId, { quiet: true });
    } finally {
        setExchangeBusy(button, false, "Confirming Handover...", "Confirm Handover");
    }
}

function initializeExchange() {
    document.querySelectorAll("[data-exchange-view]").forEach(button => {
        button.addEventListener("click", () => {
            exchangeView = button.dataset.exchangeView || "network";
            exchangeSelectedId = "";
            exchangeDetailRequest = null;
            exchangeDetailOffers = [];
            renderExchangeTabs();
            renderExchangeList();
            renderExchangeDetail();
        });
    });

    $("exchangeRefreshBtn")?.addEventListener("click", () => {
        loadExchangeWorkspace();
    });

    $("exchangeCreateRequestBtn")?.addEventListener("click", () => {
        if (!canWriteExchange()) {
            showToast("You cannot create exchange requests with this role.");
            return;
        }
        fillExchangeCreateForm();
        if ($("exchangeCreatePanel")) {
            $("exchangeCreatePanel").hidden = false;
            $("exchangeCreatePanel").scrollIntoView({ behavior: "smooth", block: "start" });
        }
    });

    $("exchangeCreateCancelBtn")?.addEventListener("click", () => {
        if ($("exchangeCreatePanel")) $("exchangeCreatePanel").hidden = true;
    });

    $("exchangeCreateForm")?.addEventListener("submit", createExchangeRequest);

    $("exchangeTrackingMode")?.addEventListener("change", () => {
        const qty = $("exchangeQuantityRequested");
        const tracking = $("exchangeTrackingMode")?.value || "INDIVIDUAL";
        if (!qty) return;
        if (tracking === "INDIVIDUAL") {
            qty.value = "1";
            qty.readOnly = true;
        } else {
            qty.readOnly = false;
        }
    });

    document.addEventListener("click", event => {
        const selectId = event.target?.closest?.("[data-exchange-select]")?.dataset?.exchangeSelect;
        if (selectId) {
            openExchangeRequest(selectId);
            return;
        }
        const acceptId = event.target?.closest?.("[data-exchange-accept]")?.dataset?.exchangeAccept;
        if (acceptId) {
            acceptExchangeOffer(acceptId);
            return;
        }
        const rejectId = event.target?.closest?.("[data-exchange-reject]")?.dataset?.exchangeReject;
        if (rejectId) {
            rejectExchangeOffer(rejectId);
            return;
        }
        const withdrawId = event.target?.closest?.("[data-exchange-withdraw]")?.dataset?.exchangeWithdraw;
        if (withdrawId) {
            withdrawExchangeOffer(withdrawId);
            return;
        }
        if (event.target?.id === "exchangeSubmitOfferBtn") {
            submitExchangeOffer();
            return;
        }
        if (event.target?.id === "exchangeCancelRequestBtn") {
            cancelExchangeRequest();
            return;
        }
        if (event.target?.id === "exchangeStartTransferBtn") {
            startExchangeTransfer();
            return;
        }
        if (event.target?.id === "exchangeConfirmHandoverBtn") {
            confirmExchangeHandover();
        }
    });
}


/* =========================================================
   APPLICATION INITIALIZATION
========================================================= */

async function initializeApp() {

    console.log(
        "ERAP starting..."
    );


    /*
       Authentication MUST happen first.
    */

    const authenticated =
        await initializeAuthentication();


    /*
       If not authenticated,
       loginWithCognito() has redirected
       the browser. Stop here.
    */

    if (!authenticated) {

        return;

    }


    /*
       User is authenticated.
       Keep the existing dashboard shell, then
       resolve organization membership before live data.
    */

    initializeNavigation();

    initializeMobileMenu();

    initializeAdminDesk();

    initializeResourceDesk();

    initializeEvents();

    initializeNotifications();

    initializeBilling();

    initializeExchange();


    document.addEventListener(
        "click",
        resourceHistoryDelegatedClick
    );

    document.addEventListener(
        "click",
        everydayResourceDelegatedClick
    );

    document.addEventListener(
        "click",
        resourceEditDelegatedClick
    );

    document.addEventListener("click", event => {

        const resourceTypeId = event.target?.dataset?.deactivateResourceType;
        const requestTypeId = event.target?.dataset?.deactivateRequestType;

        if (resourceTypeId) {

            deactivateCatalogType(RESOURCE_TYPES_API_URL, resourceTypeId, "resource");

        }

        if (requestTypeId) {

            deactivateCatalogType(REQUEST_TYPES_API_URL, requestTypeId, "request");

        }

    });

    $("newResourceType")?.addEventListener("change", showSelectedTypeAttributes);
    $("resourceType")?.addEventListener("change", showSelectedTypeAttributes);
    $("modalRequestType")?.addEventListener("change", showSelectedTypeAttributes);
    $("saveResourceTypeBtn")?.addEventListener("click", saveResourceType);
    $("saveRequestTypeBtn")?.addEventListener("click", saveRequestType);
    $("resourcesPageVisibilityFilter")?.addEventListener("change", applyResourcePageFilters);



    const resourceHistoryModalClose =
        document.getElementById(
            "resourceHistoryModalClose"
        );

    if (resourceHistoryModalClose) {

        resourceHistoryModalClose.addEventListener(
            "click",
            closeResourceHistoryModal
        );

    }


    await resolveOrganization();

}


/* =========================================================
   START APPLICATION
========================================================= */

document.addEventListener(
    "DOMContentLoaded",
    initializeApp
);

