/* Signup phone helpers. They do not call Cognito and do not log account data. */

function dialFlag(iso) {

    const code = String(iso || "").toUpperCase();

    if (!/^[A-Z]{2}$/.test(code)) {

        return "";

    }

    return String.fromCodePoint(
        ...[...code].map(character => 127397 + character.charCodeAt(0))
    );

}


function dialCodeList() {

    return typeof ERAP_DIAL_CODES === "undefined" ? [] : ERAP_DIAL_CODES;

}


function searchDialCodes(query, codes) {

    const source = Array.isArray(codes) ? codes : dialCodeList();
    const text = String(query || "").trim().toLowerCase();

    if (!text) {

        return source.slice();

    }

    const compact = text.replace(/\s+/g, "");

    return source
        .map(country => {

            const name = String(country.name || "").toLowerCase();
            const iso = String(country.iso || "").toLowerCase();
            const dial = String(country.dial || "").toLowerCase();
            let score = 4;

            if (name === text || iso === compact || dial === compact || dial.slice(1) === compact) {

                score = 0;

            } else if (name.startsWith(text)) {

                score = 1;

            } else if (name.includes(text)) {

                score = 2;

            }

            return { country, score };

        })
        .filter(item => item.score < 4)
        .sort((left, right) => left.score - right.score || left.country.name.localeCompare(right.country.name))
        .map(item => item.country);

}


function defaultDialCountry(codes, locale) {

    const source = Array.isArray(codes) ? codes : dialCodeList();
    const match = String(locale || "").match(/[-_]([A-Za-z]{2})\b/);

    if (!match) {

        return null;

    }

    const iso = match[1].toUpperCase();

    return source.find(country => country.iso === iso) || null;

}


function normalizeSignupPhone(dial, national) {

    const code = String(dial || "").trim();

    if (!/^\+\d{1,4}$/.test(code)) {

        return { ok: false, message: "Select a country code." };

    }

    const raw = String(national == null ? "" : national).trim();

    if (!raw) {

        return { ok: false, message: "Enter a mobile number." };

    }

    if (raw.includes("+")) {

        return { ok: false, message: "Enter the mobile number without the country code." };

    }

    if (/[A-Za-z]/.test(raw) || /[^0-9\s().-]/.test(raw)) {

        return { ok: false, message: "Enter the mobile number using digits only." };

    }

    const digits = raw.replace(/[\s().-]/g, "");

    if (!/^\d+$/.test(digits)) {

        return { ok: false, message: "Enter the mobile number using digits only." };

    }

    const dialDigits = code.slice(1);

    if (digits.startsWith(dialDigits) && digits.length - dialDigits.length >= 6) {

        return { ok: false, message: "Enter the mobile number without the country code." };

    }

    if (digits.length < 4 || digits.length > 14) {

        return { ok: false, message: "Enter a valid mobile number." };

    }

    const combined = dialDigits + digits;

    if (combined.length < 8 || combined.length > 15) {

        return { ok: false, message: "Enter a valid mobile number." };

    }

    return { ok: true, value: "+" + combined };

}


function validateSignupAccount(fields) {

    const name = String((fields && fields.name) || "").trim();
    const email = String((fields && fields.email) || "").trim();
    const password = String((fields && fields.password) || "");
    const confirmPassword = String((fields && fields.confirmPassword) || "");

    if (!name || name.length > 128) {

        return { ok: false, message: "Enter your name." };

    }

    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {

        return { ok: false, message: "Enter a valid email address." };

    }

    if (
        password.length < 8
        || !/[a-z]/.test(password)
        || !/[A-Z]/.test(password)
        || !/\d/.test(password)
        || !/[^A-Za-z0-9]/.test(password)
    ) {

        return {
            ok: false,
            message: "Password must be at least 8 characters and include upper and lower case letters, a number, and a symbol."
        };

    }

    if (password !== confirmPassword) {

        return { ok: false, message: "Passwords do not match." };

    }

    const phone = normalizeSignupPhone(fields.dial, fields.national);

    if (!phone.ok) {

        return phone;

    }

    return { ok: true, name: name, email: email, phone: phone.value, password: password };

}


function buildSignUpBody(clientId, account) {

    return {
        ClientId: clientId,
        Username: account.email,
        Password: account.password,
        UserAttributes: [
            { Name: "email", Value: account.email },
            { Name: "name", Value: account.name },
            { Name: "phone_number", Value: account.phone }
        ]
    };

}


function buildConfirmSignUpBody(clientId, email, code) {

    return {
        ClientId: clientId,
        Username: String(email || "").trim(),
        ConfirmationCode: String(code || "").trim()
    };

}
