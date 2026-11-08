# SPDX-License-Identifier: AGPL-3.0-or-later
"""Execute the rendered upload script through file and consent changes."""

import json
import re
import shutil
import subprocess

from tests import security_review_fixtures
from tests.test_dashboard_access_routes import _set_session

app_module = security_review_fixtures.app_module


def test_upload_requires_both_a_file_and_consent(app_module):
    client = app_module.web.test_client()
    _set_session(client, "synthetic-building")
    response = client.get("/meter-upload")
    assert response.status_code == 200
    script = next(
        source
        for source in re.findall(
            r"<script[^>]*>(.*?)</script>", response.text, re.DOTALL
        )
        if "const dropZone" in source
    )
    node = shutil.which("node")
    assert node, "Node is required to verify the rendered upload behaviour"
    harness = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
const elements = {};
function element(id) {
    return elements[id] ||= {
        files: [], checked: false, disabled: true, listeners: {},
        classList: {add() {}, remove() {}},
        addEventListener(event, callback) {this.listeners[event] = callback;}
    };
}
const document = {getElementById: element};
vm.runInNewContext(SCRIPT, {document});
const input = element('csv-file');
const consent = element('consent-confirmed');
const button = element('btn-upload');
input.files = [{name: 'synthetic.csv'}];
input.listeners.change();
assert.equal(button.disabled, true, 'file alone cannot authorize upload');
consent.checked = true;
consent.listeners.change();
assert.equal(button.disabled, false);
consent.checked = false;
consent.listeners.change();
assert.equal(button.disabled, true, 'revoking consent disables upload');
input.files = [];
input.listeners.change();
consent.checked = true;
consent.listeners.change();
assert.equal(button.disabled, true, 'consent alone cannot authorize upload');
element('drop-zone').listeners.drop({preventDefault() {}, dataTransfer: {files: [{name: 'dropped.csv'}]}});
assert.equal(button.disabled, false, 'drop applies the same consent gate');
""".replace("SCRIPT", json.dumps(script))
    result = subprocess.run(
        [node, "-e", harness], capture_output=True, text=True, timeout=10, check=False
    )
    assert result.returncode == 0, result.stderr
