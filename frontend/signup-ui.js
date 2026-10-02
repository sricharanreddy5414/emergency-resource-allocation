/* ERAP signup screen. Cognito SignUp and ConfirmSignUp use the existing app client. */

let signupDialIndex = -1;
let signupSelectedCountry = null;


function signupRequested() {

    if (typeof window.location === "undefined" || !window.location.search) {

        return false;

    }

    return new URLSearchParams(window.location.search).get("signup") === "1";

}


function hideAccountScreen(id) {

    const screen = document.getElementById(id);

    if (!screen) {

        return;

    }

    screen.hidden = true;

}


function showAccountEntry() {

    hideAccountScreen("signupScreen");
    const screen = document.getElementById("accountEntry");

    if (!screen) {

        return;

    }

    screen.hidden = false;
    screen.classList.remove("hidden");

}


function showSignupScreen() {

    const screen = document.getElementById("signupScreen");

    if (!screen) {

        return;

    }

    hideAccountScreen("accountEntry");
    screen.hidden = false;
    screen.classList.remove("hidden");

    if (!signupSelectedCountry) {

        applyDialCountry(defaultDialCountry(dialCodeList(), navigator.language || ""));

    }

    renderDialOptions(searchDialCodes(""));

}


function signupMessage(text) {

    const node = document.getElementById("signupMessage");

    if (node) {

        node.textContent = text || "";

    }

}


function applyDialCountry(country) {

    signupSelectedCountry = country || null;
    const button = document.getElementById("signupDialButton");
    const flag = document.getElementById("signupDialFlag");
    const label = document.getElementById("signupDialLabel");

    if (!button || !flag || !label) {

        return;

    }

    if (!country) {

        flag.textContent = "";
        label.textContent = "Country code";
        button.setAttribute("aria-label", "Country code. No country selected.");
        return;

    }

    flag.textContent = dialFlag(country.iso);
    label.textContent = country.name + " " + country.dial;
    button.setAttribute("aria-label", "Country code. " + country.name + " " + country.dial);

}


function renderDialOptions(countries) {

    const list = document.getElementById("signupDialList");

    if (!list) {

        return;

    }

    list.replaceChildren();
    countries.forEach((country, index) => {

        const item = document.createElement("li");
        const flag = document.createElement("span");
        const text = document.createElement("span");

        item.setAttribute("role", "option");
        item.id = "signup-dial-option-" + index;
        item.dataset.iso = country.iso;
        item.dataset.dial = country.dial;
        item.dataset.name = country.name;
        item.setAttribute("aria-selected", index === signupDialIndex ? "true" : "false");
        flag.setAttribute("aria-hidden", "true");
        flag.textContent = dialFlag(country.iso);
        text.textContent = country.name + " " + country.dial;
        item.append(flag, document.createTextNode(" "), text);
        item.addEventListener("click", () => selectDialCountry(country));
        list.appendChild(item);

    });

    const active = list.querySelector("[aria-selected='true']");

    if (active) {

        list.setAttribute("aria-activedescendant", active.id);

    } else {

        list.removeAttribute("aria-activedescendant");

    }

}


function visibleDialCountries() {

    const search = document.getElementById("signupDialSearch");

    return searchDialCodes(search ? search.value : "");

}


function openDialPanel() {

    const panel = document.getElementById("signupDialPanel");
    const button = document.getElementById("signupDialButton");
    const search = document.getElementById("signupDialSearch");

    if (!panel || !button) {

        return;

    }

    signupDialIndex = 0;
    panel.hidden = false;
    button.setAttribute("aria-expanded", "true");
    renderDialOptions(visibleDialCountries());

    if (search) {

        search.focus();

    }

}


function closeDialPanel() {

    const panel = document.getElementById("signupDialPanel");
    const button = document.getElementById("signupDialButton");

    if (panel) {

        panel.hidden = true;

    }

    if (button) {

        button.setAttribute("aria-expanded", "false");
        button.focus();

    }

}


function selectDialCountry(country) {

    applyDialCountry(country);
    closeDialPanel();

}


function moveDialHighlight(step) {

    const countries = visibleDialCountries();

    if (!countries.length) {

        signupDialIndex = -1;
        renderDialOptions(countries);
        return;

    }

    const next = signupDialIndex < 0 ? 0 : (signupDialIndex + step + countries.length) % countries.length;

    signupDialIndex = next;
    renderDialOptions(countries);
    document.getElementById("signup-dial-option-" + next)?.scrollIntoView({ block: "nearest" });

}


function signupErrorText(error) {

    const code = error && (error.__type || error.code || error.name || "");
    const name = String(code).split("#").pop();

    if (name === "UsernameExistsException") {

        return "An account with this email already exists. Sign in instead.";

    }

    if (name === "InvalidPasswordException") {

        return "Password must be at least 8 characters and include upper and lower case letters, a number, and a symbol.";

    }

    if (name === "CodeMismatchException") {

        return "That confirmation code is not valid.";

    }

    if (name === "ExpiredCodeException") {

        return "That confirmation code has expired.";

    }

    if (name === "UserNotConfirmedException") {

        return "Confirm the account before signing in.";

    }

    return "Account creation could not be completed. Check the details and try again.";

}


/* Keep this name off app.js. app.js loads later and would replace a shared function. */
async function cognitoIdentityCall(action, body) {

    const response = await fetch("https://cognito-idp.eu-north-1.amazonaws.com/", {
        method: "POST",
        headers: {
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": "AWSCognitoIdentityProviderService." + action
        },
        body: JSON.stringify(body)
    });
    const payload = await response.json().catch(() => ({}));

    if (!response.ok) {

        const error = new Error(signupErrorText(payload));
        error.code = payload.__type || payload.code || "";
        throw error;

    }

    return payload;

}


function signupAccountFromForm() {

    return validateSignupAccount({
        name: document.getElementById("signupName")?.value,
        email: document.getElementById("signupEmail")?.value,
        password: document.getElementById("signupPassword")?.value,
        confirmPassword: document.getElementById("signupConfirmPassword")?.value,
        dial: signupSelectedCountry ? signupSelectedCountry.dial : "",
        national: document.getElementById("signupNational")?.value
    });

}


async function submitSignupAccount(event) {

    event.preventDefault();
    const account = signupAccountFromForm();

    if (!account.ok) {

        signupMessage(account.message);
        return;

    }

    const button = document.getElementById("signupSubmit");

    if (button) {

        button.disabled = true;

    }

    try {

        await cognitoIdentityCall("SignUp", buildSignUpBody(window.ERAP_COGNITO_CLIENT_ID || "", account));
        const password = document.getElementById("signupPassword");
        const confirm = document.getElementById("signupConfirmPassword");

        if (password) {

            password.value = "";

        }

        if (confirm) {

            confirm.value = "";

        }

        document.getElementById("signupDetails")?.setAttribute("hidden", "");
        document.getElementById("signupConfirm")?.removeAttribute("hidden");
        signupMessage("Enter the confirmation code sent to your email.");
        document.getElementById("signupCode")?.focus();

    } catch (error) {

        signupMessage(error.message || "Account creation could not be completed.");

    } finally {

        if (button) {

            button.disabled = false;

        }

    }

}


async function submitSignupConfirmation(event) {

    event.preventDefault();
    const email = document.getElementById("signupEmail")?.value.trim() || "";
    const code = document.getElementById("signupCode")?.value.trim() || "";

    if (!code) {

        signupMessage("Enter the confirmation code.");
        return;

    }

    try {

        await cognitoIdentityCall(
            "ConfirmSignUp",
            buildConfirmSignUpBody(window.ERAP_COGNITO_CLIENT_ID || "", email, code)
        );
        const codeInput = document.getElementById("signupCode");

        if (codeInput) {

            codeInput.value = "";

        }

        if (typeof loginWithCognito === "function") {

            await loginWithCognito();

        }

    } catch (error) {

        signupMessage(error.message || "That confirmation code is not valid.");

    }

}


function initializeSignupScreen() {

    const button = document.getElementById("signupDialButton");
    const search = document.getElementById("signupDialSearch");
    const form = document.getElementById("signupForm");
    const confirm = document.getElementById("signupConfirmForm");
    const signIn = document.getElementById("signupSignIn");

    if (!button) {

        return;

    }

    button.addEventListener("click", () => {

        const panel = document.getElementById("signupDialPanel");

        if (panel && !panel.hidden) {

            closeDialPanel();
            return;

        }

        openDialPanel();

    });

    search?.addEventListener("input", () => {

        signupDialIndex = 0;
        renderDialOptions(visibleDialCountries());

    });

    search?.addEventListener("keydown", event => {

        if (event.key === "Escape") {

            event.preventDefault();
            closeDialPanel();
            return;

        }

        if (event.key === "ArrowDown") {

            event.preventDefault();
            moveDialHighlight(1);
            return;

        }

        if (event.key === "ArrowUp") {

            event.preventDefault();
            moveDialHighlight(-1);
            return;

        }

        if (event.key === "Enter") {

            event.preventDefault();
            const countries = visibleDialCountries();
            const country = countries[signupDialIndex] || countries[0];

            if (country) {

                selectDialCountry(country);

            }

        }

    });

    form?.addEventListener("submit", submitSignupAccount);
    confirm?.addEventListener("submit", submitSignupConfirmation);
    document.getElementById("accountSignIn")?.addEventListener("click", () => {

        if (typeof loginWithCognito === "function") {

            loginWithCognito();

        }

    });
    document.getElementById("accountCreate")?.addEventListener("click", () => {

        showSignupScreen();

    });
    signIn?.addEventListener("click", () => {

        if (typeof loginWithCognito === "function") {

            loginWithCognito();

        }

    });

}


document.addEventListener("DOMContentLoaded", initializeSignupScreen);
