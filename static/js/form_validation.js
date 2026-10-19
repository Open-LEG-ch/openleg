/* Native constraints with the page's language, independent of browser locale. */
(() => {
    "use strict";
    const configuration = document.getElementById("form-validation-messages");
    if (!configuration) return;
    const messages = JSON.parse(configuration.textContent);
    const numeric = value => Number.isFinite(Number(value))
        ? new Intl.NumberFormat(document.documentElement.lang).format(Number(value)) : value;
    function message(input) {
        const validity = input.validity;
        if (validity.valueMissing) return messages.required;
        if (validity.typeMismatch) return input.type === "email" ? messages.email : messages.url;
        if (validity.badInput) return ["date", "datetime-local", "month", "week", "time"].includes(input.type) ? messages.date : messages.number;
        if (validity.rangeUnderflow) return messages.minimum.replace("%(value)s", numeric(input.min));
        if (validity.rangeOverflow) return messages.maximum.replace("%(value)s", numeric(input.max));
        if (validity.tooShort) return messages.tooShort.replace("%(value)s", numeric(input.minLength));
        if (validity.tooLong) return messages.tooLong.replace("%(value)s", numeric(input.maxLength));
        if (validity.stepMismatch) return messages.step;
        if (validity.patternMismatch) return messages.pattern;
        return "";
    }
    document.addEventListener("invalid", event => {
        const input = event.target;
        if (!(input instanceof HTMLInputElement || input instanceof HTMLTextAreaElement || input instanceof HTMLSelectElement)) return;
        input.setCustomValidity("");
        input.setCustomValidity(message(input));
    }, true);
    document.addEventListener("input", event => {
        if (typeof event.target.setCustomValidity === "function") event.target.setCustomValidity("");
    }, true);
    document.addEventListener("change", event => {
        if (typeof event.target.setCustomValidity === "function") event.target.setCustomValidity("");
    }, true);
    // Keep an existing application file picker; replace only the bare native UI.
    let fileIndex = 0;
    for (const input of document.querySelectorAll('input[type="file"]:not(.hidden)')) {
        if (input.hidden || input.style.display === "none") continue;
        const wrapper = document.createElement("div");
        wrapper.className = "flex flex-wrap items-center gap-3 text-sm";
        const button = document.createElement("button");
        button.type = "button";
        button.className = "rounded border border-line px-3 py-2 font-semibold";
        button.textContent = messages.chooseFile;
        const name = document.createElement("span");
        name.textContent = messages.noFile;
        name.setAttribute("role", "status");
        name.id = "localized-file-status-" + (++fileIndex);
        const labelledBy = input.getAttribute("aria-labelledby");
        const fieldName = input.getAttribute("aria-label") || [...(input.labels || [])].map(label => label.textContent.trim()).join(" ");
        if (labelledBy) button.setAttribute("aria-labelledby", labelledBy);
        else button.setAttribute("aria-label", fieldName ? messages.chooseFile + " : " + fieldName : messages.chooseFile);
        button.setAttribute("aria-describedby", [input.getAttribute("aria-describedby"), name.id].filter(Boolean).join(" "));
        button.addEventListener("click", () => input.click());
        input.addEventListener("change", () => { name.textContent = [...input.files].map(file => file.name).join(", ") || messages.noFile; });
        input.before(wrapper);
        wrapper.append(button, name, input);
        input.classList.add("sr-only");
        input.tabIndex = -1;
        input.addEventListener("invalid", () => button.focus());
    }
})();
