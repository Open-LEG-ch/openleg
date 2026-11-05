// SPDX-License-Identifier: AGPL-3.0-or-later
(() => {
  const form = document.getElementById('onboarding');
  if (!form) return;
  const fields = form.elements;
  const button = form.querySelector('button[type="submit"]');
  const result = document.getElementById('onboarding-result');
  fields.fixture.addEventListener('change', () => {
    fields.address.value = `Musterweg ${fields.fixture.value}, Sonnwil`;
  });
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    button.disabled = true;
    result.textContent = '';
    const offset = Number(fields.fixture.value) / 10000;
    try {
      const response = await fetch('/api/register_full', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          email: fields.email.value,
          profile: {
            building_id: form.dataset.buildingId,
            address: fields.address.value,
            annual_consumption_kwh: Number(fields.annual_consumption_kwh.value),
            potential_pv_kwp: Number(fields.potential_pv_kwp.value),
            lat: 47.1 + offset,
            lon: 8.1 + offset,
            bfs_number: 999999,
            municipality_name: 'Sonnwil (fiktiv)',
            plz: '0000',
            canton: 'ZH',
          },
          has_solar: Number(fields.potential_pv_kwp.value) > 0,
          consents: {share_with_neighbors: fields.share_with_neighbors.checked},
          preferred_locale: document.documentElement.lang.replace('-', '_'),
        }),
      });
      const data = await response.json();
      result.textContent = response.ok ? form.dataset.success : (data.error || form.dataset.error);
    } catch (_) {
      result.textContent = form.dataset.error;
    } finally {
      button.disabled = false;
    }
  });
  button.disabled = false;
})();
