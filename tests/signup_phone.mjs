import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const context = {
    console,
    URLSearchParams,
    navigator: { language: "en" },
};
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(root, "frontend", "country-dial-codes.js"), "utf8"), context);
vm.runInContext(fs.readFileSync(path.join(root, "frontend", "signup-phone.js"), "utf8"), context);

const codes = context.ERAP_DIAL_CODES;
assert.ok(codes.length > 200, "country list should be international");

function byIso(iso) {
    return codes.find(country => country.iso === iso);
}

assert.equal(byIso("IN").dial, "+91");
assert.equal(byIso("US").dial, "+1");
assert.equal(byIso("GB").dial, "+44");
assert.equal(byIso("AE").dial, "+971");

const india = context.searchDialCodes("India");
assert.ok(india.some(country => country.iso === "IN" && country.dial === "+91"));
const byDial = context.searchDialCodes("+91");
assert.ok(byDial.some(country => country.iso === "IN"));
const byDigits = context.searchDialCodes("91");
assert.ok(byDigits.some(country => country.iso === "IN"));
const united = context.searchDialCodes("United").map(country => country.iso);
assert.ok(united.includes("US"));
assert.ok(united.includes("GB"));
assert.equal(context.searchDialCodes("india")[0].iso, "IN");

assert.equal(context.defaultDialCountry(codes, "en-US").iso, "US");
assert.equal(context.defaultDialCountry(codes, "en"), null);

const indiaPhone = context.normalizeSignupPhone("+91", "9876543210");
assert.equal(indiaPhone.ok, true);
assert.equal(indiaPhone.value, "+919876543210");
assert.equal(context.normalizeSignupPhone("+1", "2025550123").value, "+12025550123");
assert.equal(context.normalizeSignupPhone("+44", "7911 123 456").value, "+447911123456");
assert.equal(context.normalizeSignupPhone("", "9876543210").ok, false);
assert.equal(context.normalizeSignupPhone("+91", "").ok, false);
assert.equal(context.normalizeSignupPhone("+91", "98AB543210").ok, false);
assert.equal(context.normalizeSignupPhone("+91", "+919876543210").ok, false);
assert.equal(context.normalizeSignupPhone("+91", "919876543210").ok, false);
assert.equal(context.normalizeSignupPhone("+91", "123").ok, false);

const account = context.validateSignupAccount({
    name: "Beta Tester",
    email: "beta.tester@example.com",
    password: "Example1!",
    confirmPassword: "Example1!",
    dial: "+91",
    national: "9876543210",
});
assert.equal(account.phone, "+919876543210");
const body = context.buildSignUpBody("client", account);
assert.equal(body.Username, "beta.tester@example.com");
assert.equal(body.UserAttributes.find(item => item.Name === "phone_number").Value, "+919876543210");
assert.equal(context.validateSignupAccount({ ...account, confirmPassword: "Other1!" }).ok, false);

const confirm = context.buildConfirmSignUpBody("client", "beta.tester@example.com", "123456");
assert.equal(confirm.ConfirmationCode, "123456");
assert.equal("phone_number" in confirm, false);
assert.equal(JSON.stringify(confirm).includes("9876543210"), false);

const elements = {};
function makeElement(id) {
    const element = {
        id,
        hidden: true,
        dataset: {},
        value: "",
        textContent: "",
        children: [],
        attributes: {},
        classList: { remove() {}, add() {} },
        focus() { element.focused = true; },
        setAttribute(name, value) { element.attributes[name] = value; },
        getAttribute(name) { return element.attributes[name]; },
        removeAttribute(name) { delete element.attributes[name]; },
        addEventListener() {},
        append(...nodes) { element.children.push(...nodes); },
        appendChild(child) { element.children.push(child); return child; },
        replaceChildren() { element.children = []; },
        querySelector() { return element.children.find(child => child.attributes && child.attributes["aria-selected"] === "true") || null; },
    };
    elements[id] = element;
    return element;
}
context.document = {
    getElementById(id) { return elements[id] || null; },
    createElement(tag) { return makeElement(""); },
    createTextNode(text) { return { textContent: text }; },
    addEventListener() {},
};
["signupScreen", "signupDialButton", "signupDialFlag", "signupDialLabel", "signupDialList", "signupDialPanel", "signupDialSearch", "signupMessage"].forEach(makeElement);
elements.signupDialButton.setAttribute = function (name, value) { this.attributes[name] = value; };
elements.signupDialPanel.hidden = true;

vm.runInContext(fs.readFileSync(path.join(root, "frontend", "signup-ui.js"), "utf8"), context);
context.applyDialCountry(byIso("IN"));
assert.equal(elements.signupDialLabel.textContent, "India +91");
assert.match(elements.signupDialButton.attributes["aria-label"], /India \+91/);
context.openDialPanel();
assert.equal(elements.signupDialPanel.hidden, false);
assert.equal(elements.signupDialButton.attributes["aria-expanded"], "true");
assert.ok(elements.signupDialList.children.length > 200);
context.closeDialPanel();
assert.equal(elements.signupDialPanel.hidden, true);
