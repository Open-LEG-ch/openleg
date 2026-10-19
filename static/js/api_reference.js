/* Local, gettext-labelled API reference. Protocol names and JSON stay intact. */
window.initializeApiReference = function (root, spec, labels) {
    "use strict";
    function element(tag, text, className) {
        const node = document.createElement(tag);
        if (text !== undefined) node.textContent = text;
        if (className) node.className = className;
        return node;
    }
    function exampleValue(schema) {
        if (schema.example !== undefined) return schema.example;
        if (schema.default !== undefined) return schema.default;
        if (schema.enum?.length) return schema.enum[0];
        if (schema.type === "object" || schema.properties) {
            return Object.fromEntries(Object.entries(schema.properties || {}).map(([key, value]) => [key, exampleValue(value)]));
        }
        if (schema.type === "array") return Array.from({length: Math.min(3, Math.max(1, schema.minItems || 1))}, () => exampleValue(schema.items || {}));
        if (["integer", "number"].includes(schema.type)) return schema.minimum ?? 0;
        if (schema.type === "boolean") return false;
        return "";
    }
    const fieldClass = "mt-1 block w-full rounded border border-line px-3 py-2 font-mono text-sm";
    root.replaceChildren();
    root.append(element("h2", spec.info.title, "mt-8 text-2xl font-bold"));
    root.append(element("p", spec.info.description, "mt-3 whitespace-pre-line text-sm text-ink-muted"));
    for (const [path, operations] of Object.entries(spec.paths)) {
        for (const [method, operation] of Object.entries(operations)) {
            if (!["get", "post", "put", "patch", "delete"].includes(method)) continue;
            const details = element("details", undefined, "mt-4 rounded-xl border border-line bg-white p-4");
            const summary = element("summary", undefined, "cursor-pointer font-semibold");
            summary.append(element("code", method.toUpperCase() + " " + path, "mr-3 text-brand"));
            summary.append(document.createTextNode(operation.summary || ""));
            details.append(summary);
            const form = element("form", undefined, "mt-4 space-y-4");
            const inputs = [];
            if (operation.parameters?.length) form.append(element("h3", labels.parameters, "font-semibold"));
            for (const parameter of operation.parameters || []) {
                const label = element("label", undefined, "block text-sm");
                label.append(element("span", parameter.name + (parameter.required ? " *" : ""), "font-mono"));
                if (parameter.description) label.append(element("span", " · " + parameter.description, "text-ink-muted"));
                const schema = parameter.schema || {};
                const input = element(schema.enum ? "select" : "input", undefined, fieldClass);
                if (schema.enum) {
                    if (!parameter.required) input.append(new Option("", ""));
                    for (const value of schema.enum) input.append(new Option(String(value), String(value)));
                } else {
                    input.type = ["integer", "number"].includes(schema.type) ? "number" : "text";
                    if (schema.type === "integer") input.step = "1";
                    if (schema.type === "number") input.step = "any";
                    if (schema.minimum !== undefined) input.min = schema.minimum;
                    if (schema.maximum !== undefined) input.max = schema.maximum;
                }
                input.name = parameter.name;
                input.required = Boolean(parameter.required);
                if (schema.default !== undefined) input.value = schema.default;
                label.append(input);
                form.append(label);
                inputs.push({parameter, input});
            }
            let bodyInput;
            if (operation.requestBody) {
                const bodyLabel = element("label", labels.requestBody, "block text-sm font-semibold");
                bodyInput = element("textarea", undefined, fieldClass);
                bodyInput.rows = 7;
                bodyInput.required = Boolean(operation.requestBody.required);
                const content = operation.requestBody.content?.["application/json"] || {};
                bodyInput.value = JSON.stringify(content.example ?? exampleValue(content.schema || {}), null, 2);
                bodyLabel.append(bodyInput);
                form.append(bodyLabel);
                const schemaDetails = element("details", undefined, "mt-3 text-sm");
                schemaDetails.append(element("summary", labels.schema, "cursor-pointer font-semibold"));
                schemaDetails.append(element("pre", JSON.stringify(content.schema || {}, null, 2), "mt-2 overflow-auto whitespace-pre-wrap break-words rounded bg-paper p-4 text-xs"));
                form.append(schemaDetails);
            }
            const run = element("button", labels.execute, "rounded-lg bg-brand px-4 py-2 text-white font-semibold");
            run.type = "submit";
            const status = element("p", "", "text-sm font-semibold");
            status.setAttribute("role", "status");
            const output = element("pre", "", "mt-3 overflow-auto whitespace-pre-wrap break-words rounded bg-paper p-4 text-xs");
            output.hidden = true;
            form.append(run, status, output);
            form.addEventListener("submit", async function (event) {
                event.preventDefault();
                if (!form.reportValidity()) return;
                let target = path;
                const query = new URLSearchParams();
                for (const {parameter, input} of inputs) {
                    if (!input.value) continue;
                    if (parameter.in === "path") target = target.replace("{" + parameter.name + "}", encodeURIComponent(input.value));
                    else if (parameter.in === "query") query.append(parameter.name, input.value);
                }
                query.set("lang", document.documentElement.lang);
                const options = {method: method.toUpperCase(), headers: {Accept: "application/json"}};
                if (bodyInput) {
                    try { options.body = JSON.stringify(JSON.parse(bodyInput.value)); }
                    catch (_) { status.textContent = labels.invalidJson; return; }
                    options.headers["Content-Type"] = "application/json";
                }
                const url = (spec.servers[0]?.url || "/api/v1") + target + "?" + query;
                run.disabled = true;
                status.textContent = labels.loading;
                try {
                    const response = await fetch(url, options);
                    const text = await response.text();
                    status.textContent = labels.response + " · HTTP " + response.status;
                    try {
                        const payload = JSON.parse(text);
                        output.textContent = JSON.stringify(payload, null, 2);
                        if (response.status >= 400) {
                            const error = payload && typeof payload === 'object' && !Array.isArray(payload) ? payload.error : undefined;
                            const explanation = typeof error === 'string' && Object.hasOwn(labels.errors || {}, error)
                                ? labels.errors[error] : labels.requestFailed;
                            status.textContent += " · " + explanation;
                        }
                    }
                    catch (_) { output.textContent = text; }
                    output.hidden = false;
                } catch (_) {
                    status.textContent = labels.networkError;
                } finally { run.disabled = false; }
            });
            details.append(form);
            const responseList = element("dl", undefined, "mt-5 space-y-2 text-sm");
            responseList.append(element("dt", labels.responses, "font-semibold"));
            for (const [code, response] of Object.entries(operation.responses || {})) {
                responseList.append(element("dd", code + " · " + (response.description || ""), "text-ink-muted"));
            }
            details.append(responseList);
            root.append(details);
        }
    }
};
