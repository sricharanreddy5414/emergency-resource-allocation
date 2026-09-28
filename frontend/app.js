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

const REQUESTS_API_URL =
    "https://4c6dni17l3.execute-api.eu-north-1.amazonaws.com/dev/requests";

const ALLOCATIONS_API_URL =
    "https://4c6dni17l3.execute-api.eu-north-1.amazonaws.com/dev/allocate/allocations";

const ORGANIZATION_API_URL =
    "https://4c6dni17l3.execute-api.eu-north-1.amazonaws.com/dev/organization";

const LOCATIONS_API_URL =
    "https://4c6dni17l3.execute-api.eu-north-1.amazonaws.com/dev/locations";

const RESOURCE_TYPES_API_URL =
    "https://4c6dni17l3.execute-api.eu-north-1.amazonaws.com/dev/resource-types";

const REQUEST_TYPES_API_URL =
    "https://4c6dni17l3.execute-api.eu-north-1.amazonaws.com/dev/request-types";


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

        return response;

    }

    const refreshed = await refreshSession();

    if (!refreshed) {

        return response;

    }

    next.headers.Authorization = "Bearer " + getIdToken();

    return originalFetch(input, next);

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

    notifications:
        "Notifications",

    settings:
        "Platform",

    help:
        "Help"

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

    if (
        sectionId ===
        "admin"
    ) {

        loadAllocations();
        loadOrganizationMembers();

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
                getResourceStatus(resource),
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
                        resource.show_availability === true

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

    return isResourceAvailable(
        resource
    )

        ? "AVAILABLE"

        : "ALLOCATED";

}


/* =========================================================
   RESOURCE TABLE
========================================================= */

function canOperateResources() {

    const role = currentUser.organization?.role;

    return role === "OWNER" || role === "ADMIN" || role === "OPERATOR";

}


function resourceRowActions(resource) {

    const status = getResourceStatus(resource);

    const edit = canOperateResources()
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

    const release = status === "ALLOCATED"
        ? `
            <button
                type="button"
                class="release-resource-btn"
                onclick="releaseResource('${escapeHtml(resource.id)}')"
            >
                Release
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

        ${release}

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

                <td colspan="6">
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

                    const status =
                        getResourceStatus(
                            resource
                        );


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


async function releaseResource(resourceId) {

    const confirmed =
        window.confirm(
            `Release resource ${resourceId}?`
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


    document.addEventListener(
        "click",
        resourceHistoryDelegatedClick
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

