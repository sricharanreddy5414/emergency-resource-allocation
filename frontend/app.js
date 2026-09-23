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

let allocations = [];

let notifications = [];

let currentUser = {

    name: "Admin",

    email: "Not signed in",

    phone: "Not available"

};


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
                "Not available"

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
            "Phone unavailable"

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

    if (!getAccessToken()) {

        await loginWithCognito();

        return false;

    }


    /*
       Existing authenticated session.
    */

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
        "Resource Dashboard",

    resources:
        "Resource Management",

    requests:
        "Resource Requests",

    allocations:
        "Allocation History",

    notifications:
        "Notifications",

    settings:
        "System Settings",

    help:
        "Help & FAQ"

};


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


    const target =
        $(sectionId);


    if (!target) return;


    target.classList.add(
        "active-section"
    );


    const navItem =
        document.querySelector(
            `.nav-item[data-section="${sectionId}"]`
        );


    if (navItem) {

        navItem.classList.add(
            "active"
        );

    }


    if ($("pageTitle")) {

        $("pageTitle").textContent =
            pageTitles[sectionId] ||
            "ERAP";

    }


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

}


/* =========================================================
   NAVIGATION INITIALIZATION
========================================================= */

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


    button.addEventListener(
        "click",
        () => {

            sidebar.classList.toggle(
                "mobile-open"
            );

        }
    );

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

                <div class="empty-icon">
                    ✓
                </div>

                <h3>
                    No notifications
                </h3>

                <p>
                    New platform events will appear here.
                </p>

            </div>

        `;

        return;

    }


    list.innerHTML =
        notifications
            .map(
                notification => `

                    <div class="notification-item">

                        <div class="notification-icon">
                            ✓
                        </div>

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

                        </div>

                    </div>

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


/* =========================================================
   LOAD RESOURCES FROM API GATEWAY
========================================================= */

async function loadResources() {

    try {

        showToast(
            "Loading AWS resources..."
        );


        /*
           IMPORTANT:
           No Content-Type header on GET.
           This avoids unnecessary CORS preflight.
        */

        const response =
            await fetch(
                RESOURCES_API_URL,
                {
                    method:
                        "GET"
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

        const response = await fetch(REQUESTS_API_URL, {
            method: "GET",
            headers: {
                "Accept": "application/json"
            }
        });


        if (!response.ok) {

            throw new Error(
                `Request API returned ${response.status}`
            );

        }


        const data = await response.json();


        console.log("Live requests received:", data);


        if (Array.isArray(data)) {

            requests = data;

        } else if (Array.isArray(data.requests)) {

            requests = data.requests;

        } else {

            requests = [];

        }


        updateAnalytics();

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
                    No matching requests found.
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
            statusLabel = "✓ ALLOCATED";

        } else if (status === "PENDING") {

            statusClass += " status-pending";
            statusLabel = "◷ PENDING";

        } else if (status === "WAITING") {

            statusClass += " status-waiting";
            statusLabel = "⌛ WAITING";

        }

        return `
            <tr>

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
                        false

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
                No data available yet.
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
                        <div class="analytics-bar-row">

                            <span class="analytics-bar-label">
                                ${item.label}
                            </span>

                            <div class="analytics-bar-track">

                                <div
                                    class="analytics-bar-fill"
                                    style="width: ${percentage}%"
                                ></div>

                            </div>

                            <span class="analytics-bar-value">
                                ${item.value}
                            </span>

                        </div>
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

function renderResourcesTable() {

    const table =
        $("resourcesTable");


    if (!table) return;


    if (
        resources.length === 0
    ) {

        table.innerHTML = `

            <tr>

                <td colspan="5">
                    No resource data available.
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

                <td colspan="5">
                    No resource data available.
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


                    const action = `

                        <button
                            type="button"
                            class="resource-history-btn"
                            data-resource-id="${escapeHtml(resource.id)}"
                        >
                            History
                        </button>

                        ${
                            status === "ALLOCATED"
                                ? `
                                    <button
                                        type="button"
                                        class="release-resource-btn"
                                        onclick="releaseResource('${escapeHtml(resource.id)}')"
                                    >
                                        Release
                                    </button>
                                  `
                                : ""
                        }

                    `;


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
                `${RESOURCE_HISTORY_API_URL}?resource_id=${encodeURIComponent(resourceId)}`,
                {
                    method: "GET"
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
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify({
                        resource_id: resourceId
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


                return (
                    matchesSearch &&
                    matchesStatus &&
                    matchesType &&
                    matchesLocation
                );

            }
        );


    table.innerHTML =
        filtered.length === 0

            ? `

                <tr>

                    <td colspan="5">
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


                        const action = `

                            <button
                                type="button"
                                class="resource-history-btn"
                                data-resource-id="${escapeHtml(resource.id)}"
                            >
                                History
                            </button>

                            ${
                                status === "ALLOCATED"
                                    ? `
                                        <button
                                            type="button"
                                            class="release-resource-btn"
                                            onclick="releaseResource('${escapeHtml(resource.id)}')"
                                        >
                                            Release
                                        </button>
                                      `
                                    : ""
                            }

                        `;


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
                resourceType,

            location:
                location,

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
                            "application/json"

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
    const id = $("newResourceId").value.trim();
    const type = $("newResourceType").value;
    const location = $("newResourceLocation").value.trim();

    if (!id || !type || !location) {
        showToast("Please complete all resource details.");
        return;
    }

    try {
        const response = await fetch(
            RESOURCES_API_URL,
            {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    resource_id: id,
                    Type: type,
                    Location: location,
                    Available: true
                })
            }
        );

        const data = await response.json();

        if (!response.ok) {
            showToast(
                data.message ||
                "Failed to register resource."
            );
            return;
        }

        showToast("Resource registered successfully.");

        addNotification(
            "Resource registered",
            `${id} was added to the resource inventory.`
        );

        const modal = $("registerResourceModal");

        if (modal) {
            modal.classList.remove("open");
        }

        $("newResourceId").value = "";
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

    if (!table) return;

    table.innerHTML = `
        <tr>
            <td colspan="4">
                Loading allocations from AWS...
            </td>
        </tr>
    `;

    try {

        const response = await fetch(
            ALLOCATIONS_API_URL,
            {
                method: "GET",
                headers: {
                    "Accept": "application/json"
                }
            }
        );

        if (!response.ok) {
            throw new Error(
                `Allocation API returned ${response.status}`
            );
        }

        const data = await response.json();

        console.log(
            "Live allocations received:",
            data
        );

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

                <td colspan="7">

                    No allocation records captured
                    in this browser session.

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

                    <td colspan="7">

                        No matching allocations found.

                    </td>

                </tr>

              `

            :

              filtered
                .map(
                    item => `

                        <tr>

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
            class="status-badge allocated"
        >

            ✓ ${escapeHtml(
                item.status ||
                "ALLOCATED"
            )}

        </span>

    </td>

</tr>

                    `
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
        "Request ID,Resource Type,Location,Priority,Status";

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


function initializeEvents() {


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
            openRegisterModal
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


    $("settingsBtn")
        ?.addEventListener(
            "click",
            () => {

                navigateTo(
                    "settings"
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

    $("newRequestBtn")
        ?.addEventListener(
            "click",
            () => {

                navigateTo(
                    "dashboard"
                );


                setTimeout(
                    () => {

                        $("requestId")
                            ?.focus();

                    },
                    250
                );

            }
        );


    $("createRequestFromPageBtn")
        ?.addEventListener(
            "click",
            () => {

                navigateTo(
                    "dashboard"
                );


                setTimeout(
                    () => {

                        $("requestId")
                            ?.focus();

                    },
                    250
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
       Now initialize the dashboard.
    */

    initializeNavigation();

    initializeMobileMenu();

    initializeEvents();

    initializeNotifications();


    document.addEventListener(
        "click",
        resourceHistoryDelegatedClick
    );



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


    renderAllocations();

    updateDashboardStats();
        updateAnalytics();


    /*
       Load live AWS data.
    */

    await loadResources();

    await loadRequests();


    console.log(
        "ERAP initialized successfully."
    );

}


/* =========================================================
   START APPLICATION
========================================================= */

document.addEventListener(
    "DOMContentLoaded",
    initializeApp
);
