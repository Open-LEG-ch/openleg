// SPDX-License-Identifier: AGPL-3.0-or-later
(() => {
  document.querySelectorAll('[data-address-intake]').forEach(root => {
    if (root.dataset.initialized) return;
    root.dataset.initialized = 'true';
    const config = JSON.parse(root.querySelector('[data-address-config]').textContent);
    const messages = config.messages;
    const get = id => root.querySelector('#' + id);
  // Map (Leaflet kommt von einem CDN; der Funnel muss auch ohne Karte funktionieren)
  let map = null;
  try {
      if (typeof L === 'undefined') throw new Error('Map unavailable');
      map = L.map(get('map'), {zoomControl: false}).setView(config.map.center, config.map.zoom);
      L.control.zoom({zoomInTitle: messages.zoomIn, zoomOutTitle: messages.zoomOut}).addTo(map);
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
          maxZoom: 18, attribution: '&copy; OpenStreetMap'
      }).addTo(map);
  } catch (_) {
      map = null;
      get('map').classList.add('hidden');
  }

  let selectedProfile = null;
  let addressTimeout = null;
  let suggestionVersion = 0;
  const referralCode = new URLSearchParams(window.location.search).get('ref');

  // Address autocomplete
  const addressInput = get('address-input');
  const suggestionsDiv = get('suggestions');
  const btnCheck = get('btn-check');
  const addressStatus = get('address-status');
  const addressStateEls = Array.from(root.querySelectorAll('[data-address-state]'));

  function selectedValues(name) {
      return Array.from(root.querySelectorAll(`input[name="${name}"]:checked`)).map((input) => input.value);
  }

  function optionalBoolean(id) {
      const value = get(id).value;
      return value === '' ? null : value === 'true';
  }

  function showInterestFallback() {
      get('step-address').classList.add('hidden');
      get('step-register').classList.add('hidden');
      const fallback = get('interest-fallback');
      fallback.classList.remove('hidden');
      get('interest-email').focus();
      get('interest-address').value = addressInput.value;
      const match = addressInput.value.match(/\b(\d{4})\s+([^,]+)$/);
      if (match) {
          get('interest-plz').value = match[1];
          get('interest-municipality').value = match[2].trim();
      }
  }

  function setAddressState(state, message) {
      addressStateEls.forEach((el) => {
          el.classList.toggle('hidden', el.dataset.addressState !== state);
      });
      if (message) addressStatus.textContent = message;
  }

  addressInput.addEventListener('input', function() {
      clearTimeout(addressTimeout);
      const version = ++suggestionVersion;
      suggestionsDiv.classList.add('hidden');
      const q = this.value.trim();
      if (q.length < 3) {
          suggestionsDiv.classList.add('hidden');
          btnCheck.disabled = true;
          setAddressState('empty', messages.inputHint);
          return;
      }
      addressTimeout = setTimeout(async () => {
          try {
              const res = await fetch(`/api/suggest_addresses?q=${encodeURIComponent(q)}`);
              if (!res.ok) throw new Error(`Address suggestions failed: ${res.status}`);
              const data = await res.json();
              if (version !== suggestionVersion) return;
              btnCheck.disabled = false;
              if (data.suggestions && data.suggestions.length > 0) {
                  suggestionsDiv.replaceChildren();
                  data.suggestions.forEach(s => {
                      const el = document.createElement('button');
                      el.type = 'button';
                      el.className = 'px-3 py-2 hover:bg-slate-50 cursor-pointer text-sm border-b';
                      el.textContent = s.label;
                      el.dataset.lat = s.lat;
                      el.dataset.lon = s.lon;
                      el.dataset.plz = s.plz;
                      el.dataset.label = s.label;
                      el.addEventListener('click', function() {
                          ++suggestionVersion;
                          addressInput.value = this.dataset.label;
                          suggestionsDiv.classList.add('hidden');
                          btnCheck.disabled = false;
                          setAddressState('empty', messages.selectedHint);
                      });
                      suggestionsDiv.appendChild(el);
                  });
                  suggestionsDiv.classList.remove('hidden');
              } else {
                  suggestionsDiv.classList.add('hidden');
                  btnCheck.disabled = false;
                  setAddressState('not-found', messages.noSuggestion);
              }
          } catch(e) {
              if (version !== suggestionVersion) return;
              btnCheck.disabled = false;
              setAddressState('empty', messages.suggestionUnavailable);
              console.warn('Address autocomplete failed:', e);
              suggestionsDiv.classList.add('hidden');
          }
      }, 300);
  });

  // Check potential
  btnCheck.addEventListener('click', async function() {
      ++suggestionVersion;
      clearTimeout(addressTimeout);
      suggestionsDiv.classList.add('hidden');
      this.disabled = true;
      this.textContent = messages.checking;
      setAddressState('loading', messages.checkingHint);
      get('form-error').classList.add('hidden');
      try {
          const res = await fetch('/api/check_potential', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({address: addressInput.value})
          });
          if (res.status === 429) {
              get('form-error').textContent = messages.rateLimit;
              get('form-error').classList.remove('hidden');
              this.disabled = false;
              this.textContent = messages.checkAddress;
              setAddressState('empty', messages.rateLimitHint);
              return;
          }
          const data = await res.json();
          if (!res.ok || data.error) {
              if (data.can_register_interest) {
                  showInterestFallback();
                  return;
              }
              get('form-error').textContent = data.error || messages.checkFailed;
              get('form-error').classList.remove('hidden');
              this.disabled = false;
              this.textContent = messages.checkAddress;
              setAddressState('empty', messages.checkFailed);
              return;
          }
          selectedProfile = data.profile_summary;
          try {
              if (map && selectedProfile.lat && selectedProfile.lon) {
                  map.setView([selectedProfile.lat, selectedProfile.lon], 15);
                  L.marker([selectedProfile.lat, selectedProfile.lon]).addTo(map);
              }
          } catch (_) { /* Intake remains usable if map rendering fails. */ }
          const matchInfo = get('match-info');
          if (data.potential) {
              const ci = data.cluster_info;
              const matched = document.createElement('strong');
              const countTemplate = new Intl.PluralRules(config.locale).select(ci.num_members) === 'one'
                  ? messages.memberOne
                  : messages.memberMany;
              matched.textContent = countTemplate.replace('%(count)s', ci.num_members);
              const detail = messages.potentialDetail.replace('%(percent)s', ci.autarky_percent.toFixed(0));
              matchInfo.replaceChildren(matched, document.createTextNode(detail));
              setAddressState('found', messages.found);
          } else {
              matchInfo.textContent = messages.noNeighbours.replace(/<[^>]*>/g, '');
              setAddressState('not-found', messages.noNeighboursHint);
          }
          get('step-address').classList.add('hidden');
          get('step-register').classList.remove('hidden');
          get('email-input').focus();
      } catch(e) {
          get('form-error').textContent = messages.networkRetry;
          get('form-error').classList.remove('hidden');
          this.disabled = false;
          this.textContent = messages.checkAddress;
          setAddressState('empty', messages.networkRetry);
      }
  });

  // Register
  get('btn-register').addEventListener('click', async function() {
      const email = get('email-input').value;
      const phone = get('phone-input').value;
      const cn = get('consent-neighbors').checked;
      const co = get('consent-updates').checked;
      if (!email) {
          get('form-error').textContent = messages.emailRequired;
          get('form-error').classList.remove('hidden');
          return;
      }
      try {
          const payload = {
              email, phone, profile: selectedProfile,
              preferred_locale: config.locale,
              roles: selectedValues('interest-role'),
              has_solar: optionalBoolean('has-solar'),
              consents: {share_with_neighbors: cn, updates_opt_in: co}
          };
          if (referralCode) payload.referral_code = referralCode;
          const res = await fetch('/api/register_anonymous', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify(payload)
          });
          let data = {};
          try {
              data = await res.json();
          } catch(e) {
              data = {};
          }
          if (res.ok) {
              get('form-error').classList.add('hidden');
              get('step-register').classList.add('hidden');
              get('step-success').classList.remove('hidden');
              get('step-success').focus();
              if (data.referral_link) {
                  get('referral-link').value = data.referral_link;
                  get('referral-link-box').classList.remove('hidden');
              }
          } else {
              get('form-error').textContent = res.status === 429
                  ? messages.rateLimit
                  : (data.error || messages.registrationFailed);
              get('form-error').classList.remove('hidden');
          }
      } catch(e) {
          get('form-error').textContent = messages.networkError;
          get('form-error').classList.remove('hidden');
      }
  });

  get('btn-interest').addEventListener('click', async function() {
      const payload = {
          email: get('interest-email').value,
          address: get('interest-address').value,
          plz: get('interest-plz').value,
          municipality_name: get('interest-municipality').value,
          roles: selectedValues('fallback-role'),
          has_solar: optionalBoolean('fallback-has-solar'),
          preferred_locale: config.locale
      };
      try {
          const res = await fetch('/api/register_interest', {
              method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) {
              get('form-error').textContent = res.status === 429
                  ? messages.rateLimit : (data.error || messages.interestFailed);
              get('form-error').classList.remove('hidden');
              return;
          }
          get('interest-fallback').classList.add('hidden');
          get('step-success').classList.remove('hidden');
              get('step-success').focus();
          get('form-error').classList.add('hidden');
      } catch (error) {
          get('form-error').textContent = messages.networkRetry;
          get('form-error').classList.remove('hidden');
      }
  });

  });
})();
