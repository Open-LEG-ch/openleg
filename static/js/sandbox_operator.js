// SPDX-License-Identifier: AGPL-3.0-or-later
(() => {
    "use strict";
    const root = document.getElementById("operator-console");
    if (!root) return;
    const community = root.dataset.community;
    const credentialsPath = `/leg/community/${community}/operator-api/credentials`;
    const api = `/api/operator/v1/communities/${community}`;
    const token = document.getElementById("api-token");
    const output = document.getElementById("operator-response");
    const clients = document.getElementById("credential-id");
    let previousToken = "";
    function show(status, payload) {
        output.textContent = `HTTP ${status}\n` + JSON.stringify(payload, null, 2);
    }
    async function call(path, method, payload, headers = {}) {
        const response = await fetch(path, {
            method, credentials: "same-origin", redirect: "error",
            headers: {"Content-Type": "application/json", ...headers},
            ...(payload === undefined ? {} : {body: JSON.stringify(payload)})
        });
        const text = await response.text();
        let data;
        try {data = JSON.parse(text);} catch {data = {body: text};}
        return {status: response.status, data};
    }
    function issue(result) {
        const row = result.data.credential;
        if (row) {
            let option = Array.from(clients.options).find(option => option.value === row.id);
            if (!option) {option = new Option(); clients.add(option);}
            option.value = row.id;
            option.textContent = row.name + " · " + row.active;
            clients.value = row.id;
        }
        if (result.data.token) {
            previousToken = token.value;
            token.value = result.data.token;
            document.getElementById("webhook-secret").textContent = result.data.webhook_secret;
            show(result.status, {credential: row});
        } else show(result.status, result.data);
    }
    function onForm(id, action) {
        document.getElementById(id).addEventListener("submit", async event => {
            event.preventDefault();
            const button = event.submitter;
            if (button) button.disabled = true;
            try {await action(event);} catch (error) {show("error", {error: error.message});}
            finally {if (button) button.disabled = false;}
        });
    }
    onForm("credential-create", async event => {
        const fields = new FormData(event.target);
        issue(await call(credentialsPath, "POST", {
            name: fields.get("name"), capabilities: fields.getAll("capabilities"),
            webhook_url: fields.get("webhook_url")
        }, {"X-CSRF-Token": root.dataset.csrf}));
    });
    onForm("credential-manage", async event => {
        const operation = event.submitter?.value;
        if (!["rotate", "revoke"].includes(operation)) return;
        issue(await call(`${credentialsPath}/${encodeURIComponent(clients.value)}/${operation}`, "POST", {}, {"X-CSRF-Token": root.dataset.csrf}));
    });
    onForm("operator-request", async event => {
        const fields = new FormData(event.target);
        const path = String(fields.get("path"));
        const method = String(fields.get("method"));
        if (!/^\/api\/operator\/v1\/communities\/[A-Za-z0-9_-]{1,64}\/(formation(?:\/submissions)?|membership-mutations)$/.test(path)
            || !["GET", "POST"].includes(method)) throw new Error(root.dataset.pathError);
        const payload = method === "POST" ? JSON.parse(String(fields.get("body"))) : undefined;
        const result = await call(path, method, payload, {Authorization: "Bearer " + token.value});
        show(result.status, result.data);
    });
    const requestForm = document.getElementById("operator-request");
    root.querySelectorAll("[data-example]").forEach(button => button.addEventListener("click", () => {
        const example = button.dataset.example;
        requestForm.elements.method.value = ["membership", "forbidden"].includes(example) ? "POST" : "GET";
        const paths = {formation: api + "/formation", membership: api + "/membership-mutations", mutations: api + "/membership-mutations", forbidden: api + "/formation/submissions", foreign: "/api/operator/v1/communities/sonnwil-private-leg/formation"};
        requestForm.elements.path.value = paths[example];
        requestForm.elements.body.value = JSON.stringify(example === "membership" ? {
            mutation_id: "sonnwil-demo-mutation-1", participant_id: "sonnwil-resident",
            mutation_type: "change", effective_date: "2026-11-10",
            source_agreement_id: "SIMULATION-SONNWIL-AGREEMENT-1", after: {access_roles: ["metering"]}
        } : {}, null, 2);
    }));
    document.getElementById("try-old-key").addEventListener("click", async () => {
        if (!previousToken) {show("error", {error: root.dataset.keyError}); return;}
        try {const result = await call(api + "/formation", "GET", undefined, {Authorization: "Bearer " + previousToken}); show(result.status, result.data);}
        catch (error) {show("error", {error: error.message});}
    });
    root.querySelectorAll("[data-retry]").forEach(button => button.addEventListener("click", async () => {
        button.disabled = true;
        try {
            const result = await call(`/leg/community/${community}/operator-api/deliveries/${encodeURIComponent(button.dataset.retry)}/retry`, "POST", {}, {"X-CSRF-Token": root.dataset.csrf});
            if (result.status === 200) location.reload(); else show(result.status, result.data);
        } catch (error) {show("error", {error: error.message});}
        finally {button.disabled = false;}
    }));
})();
