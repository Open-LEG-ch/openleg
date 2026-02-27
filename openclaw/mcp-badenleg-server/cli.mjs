#!/usr/bin/env node
import pg from 'pg';
const { Pool } = pg;

const pool = new Pool({ connectionString: process.env.DATABASE_URL, ssl: false, max: 5 });
const BRAVE_API_KEY = process.env.BRAVE_API_KEY || '';
const READONLY = (process.env.OPENCLAW_READONLY || 'false').toLowerCase() === 'true';

async function q(sql, params = []) {
  const result = await pool.query(sql, params);
  return result;
}

function readonlyGuard() {
  if (READONLY) throw new Error('Write access disabled (OPENCLAW_READONLY=true)');
}

async function braveSearch(query, count = 5) {
  if (!BRAVE_API_KEY) throw new Error('BRAVE_API_KEY not configured');
  const params = new URLSearchParams({ q: query, count: Math.min(Math.max(count, 1), 10) });
  const res = await fetch(`https://api.search.brave.com/res/v1/web/search?${params}`, {
    headers: { 'Accept': 'application/json', 'X-Subscription-Token': BRAVE_API_KEY }
  });
  if (!res.ok) throw new Error(`Brave API ${res.status}: ${await res.text()}`);
  const data = await res.json();
  return (data.web?.results || []).map(r => ({ title: r.title, url: r.url, description: r.description }));
}

const TOOLS = {

  // === Read Tools ===

  search_registrations: {
    desc: 'Search buildings/registrations by address, email, PLZ, building type',
    params: { query: 'string?', plz: 'string?', building_type: 'string?', verified_only: 'bool=true', limit: 'num=20' },
    run: async (a) => {
      let sql = `SELECT b.building_id, b.email, b.phone, b.address, b.plz, b.building_type,
             b.annual_consumption_kwh, b.potential_pv_kwp, b.registered_at, b.verified,
             b.user_type, b.referral_code, c.cluster_id,
             co.share_with_neighbors, co.updates_opt_in
      FROM buildings b
      LEFT JOIN clusters c ON c.building_id = b.building_id
      LEFT JOIN consents co ON co.building_id = b.building_id
      WHERE 1=1`;
      const params = [];
      let idx = 1;
      if (a.verified_only !== false) sql += ` AND b.verified = true`;
      if (a.query) { sql += ` AND (b.address ILIKE $${idx} OR b.email ILIKE $${idx})`; params.push(`%${a.query}%`); idx++; }
      if (a.plz) { sql += ` AND b.plz = $${idx}`; params.push(a.plz); idx++; }
      if (a.building_type) { sql += ` AND b.building_type = $${idx}`; params.push(a.building_type); idx++; }
      sql += ` ORDER BY b.registered_at DESC LIMIT $${idx}`;
      params.push(a.limit || 20);
      return (await q(sql, params)).rows;
    }
  },

  get_registration: {
    desc: 'Get full details of a building by building_id',
    params: { building_id: 'string' },
    run: async (a) => {
      const bRes = await q(`SELECT b.*, c.cluster_id, ci.autarky_percent, ci.num_members,
             co.share_with_neighbors, co.share_with_utility, co.updates_opt_in
      FROM buildings b
      LEFT JOIN clusters c ON c.building_id = b.building_id
      LEFT JOIN cluster_info ci ON ci.cluster_id = c.cluster_id
      LEFT JOIN consents co ON co.building_id = b.building_id
      WHERE b.building_id = $1`, [a.building_id]);
      if (!bRes.rows.length) return { error: 'Not found' };
      const refRes = await q(`SELECT r.id, b.address, b.email, r.created_at FROM referrals r JOIN buildings b ON b.building_id = r.referred_id WHERE r.referrer_id = $1`, [a.building_id]);
      const cmRes = await q(`SELECT cm.community_id, com.name, com.status, cm.role, cm.status as member_status FROM community_members cm JOIN communities com ON com.community_id = cm.community_id WHERE cm.building_id = $1`, [a.building_id]);
      return { building: bRes.rows[0], referrals_made: refRes.rows, communities: cmRes.rows };
    }
  },

  list_communities: {
    desc: 'List communities with status, member count, admin info',
    params: { status: 'string?', limit: 'num=50' },
    run: async (a) => {
      let sql = `SELECT com.*, b.address as admin_address, b.email as admin_email,
             (SELECT COUNT(*) FROM community_members cm WHERE cm.community_id = com.community_id) as member_count
      FROM communities com LEFT JOIN buildings b ON b.building_id = com.admin_building_id WHERE 1=1`;
      const params = []; let idx = 1;
      if (a.status) { sql += ` AND com.status = $${idx}`; params.push(a.status); idx++; }
      sql += ` ORDER BY com.created_at DESC LIMIT $${idx}`; params.push(a.limit || 50);
      return (await q(sql, params)).rows;
    }
  },

  get_community: {
    desc: 'Get community details with all members',
    params: { community_id: 'string' },
    run: async (a) => {
      const comRes = await q(`SELECT * FROM communities WHERE community_id = $1`, [a.community_id]);
      if (!comRes.rows.length) return { error: 'Not found' };
      const memRes = await q(`SELECT cm.*, b.address, b.email, b.phone, b.building_type, b.annual_consumption_kwh, b.potential_pv_kwp FROM community_members cm JOIN buildings b ON b.building_id = cm.building_id WHERE cm.community_id = $1 ORDER BY cm.role DESC, cm.joined_at`, [a.community_id]);
      const docRes = await q(`SELECT * FROM community_documents WHERE community_id = $1`, [a.community_id]);
      return { community: comRes.rows[0], members: memRes.rows, documents: docRes.rows[0] || null };
    }
  },

  get_stats: {
    desc: 'Dashboard stats: registrations, communities, clusters, referrals, emails',
    params: {},
    run: async () => {
      const stats = {};
      const bRes = await q(`SELECT COUNT(*) as total_buildings, COUNT(*) FILTER (WHERE verified = true) as verified, COUNT(*) FILTER (WHERE user_type = 'registered') as registered, COUNT(*) FILTER (WHERE user_type = 'anonymous') as anonymous FROM buildings`);
      stats.buildings = bRes.rows[0];
      const cRes = await q(`SELECT COUNT(DISTINCT cluster_id) as total_clusters, AVG(num_members) as avg_members, AVG(autarky_percent) as avg_autarky FROM cluster_info`);
      stats.clusters = cRes.rows[0];
      const comRes = await q(`SELECT status, COUNT(*) as count FROM communities GROUP BY status`);
      stats.communities = comRes.rows;
      const refRes = await q(`SELECT COUNT(*) as total FROM referrals`);
      stats.referrals = { total: parseInt(refRes.rows[0].total) };
      const eRes = await q(`SELECT status, COUNT(*) as count FROM scheduled_emails GROUP BY status`);
      stats.emails = eRes.rows;
      const recentRes = await q(`SELECT building_id, address, email, registered_at, verified FROM buildings ORDER BY registered_at DESC LIMIT 5`);
      stats.recent_registrations = recentRes.rows;
      return stats;
    }
  },

  get_referrals: {
    desc: 'Referral leaderboard: top referrers',
    params: { limit: 'num=10' },
    run: async (a) => {
      return (await q(`SELECT b.building_id, b.address, b.email, b.referral_code, COUNT(r.id) as referral_count FROM buildings b JOIN referrals r ON r.referrer_id = b.building_id GROUP BY b.building_id, b.address, b.email, b.referral_code ORDER BY referral_count DESC LIMIT $1`, [a.limit || 10])).rows;
    }
  },

  list_scheduled_emails: {
    desc: 'List scheduled/pending emails',
    params: { status: 'string?', building_id: 'string?', limit: 'num=20' },
    run: async (a) => {
      let sql = `SELECT se.*, b.address FROM scheduled_emails se LEFT JOIN buildings b ON b.building_id = se.building_id WHERE 1=1`;
      const params = []; let idx = 1;
      if (a.status) { sql += ` AND se.status = $${idx}`; params.push(a.status); idx++; }
      if (a.building_id) { sql += ` AND se.building_id = $${idx}`; params.push(a.building_id); idx++; }
      sql += ` ORDER BY se.send_at DESC LIMIT $${idx}`; params.push(a.limit || 20);
      return (await q(sql, params)).rows;
    }
  },

  get_formation_status: {
    desc: 'Formation wizard progress for a community',
    params: { community_id: 'string' },
    run: async (a) => {
      const comRes = await q(`SELECT community_id, name, status, distribution_model, created_at, formation_started_at, dso_submitted_at, dso_approved_at, activated_at FROM communities WHERE community_id = $1`, [a.community_id]);
      if (!comRes.rows.length) return { error: 'Not found' };
      const memRes = await q(`SELECT building_id, role, status, confirmed_at FROM community_members WHERE community_id = $1`, [a.community_id]);
      const confirmed = memRes.rows.filter(m => m.status === 'confirmed').length;
      const total = memRes.rows.length;
      return { community: comRes.rows[0], members: memRes.rows, progress: { confirmed, total, percent: total > 0 ? Math.round(confirmed / total * 100) : 0 } };
    }
  },

  get_cluster_details: {
    desc: 'Get cluster info with member buildings',
    params: { cluster_id: 'num' },
    run: async (a) => {
      const ciRes = await q(`SELECT * FROM cluster_info WHERE cluster_id = $1`, [a.cluster_id]);
      const mRes = await q(`SELECT b.building_id, b.address, b.email, b.building_type, b.annual_consumption_kwh, b.potential_pv_kwp, b.verified FROM clusters c JOIN buildings b ON b.building_id = c.building_id WHERE c.cluster_id = $1`, [a.cluster_id]);
      return { cluster: ciRes.rows[0] || null, members: mRes.rows };
    }
  },

  get_street_leaderboard: {
    desc: 'Streets ranked by building count',
    params: { limit: 'num=10' },
    run: async (a) => (await q(`SELECT * FROM street_stats ORDER BY building_count DESC LIMIT $1`, [a.limit || 10])).rows
  },

  // === Write Tools ===

  update_registration: {
    desc: 'Update building/registration fields',
    params: { building_id: 'string', email: 'string?', phone: 'string?', building_type: 'string?', annual_consumption_kwh: 'num?', potential_pv_kwp: 'num?' },
    run: async (a) => {
      readonlyGuard();
      const { building_id, ...fields } = a;
      const entries = Object.entries(fields).filter(([, v]) => v !== undefined);
      if (!entries.length) return { error: 'No fields to update' };
      const sets = entries.map(([k], i) => `${k} = $${i + 2}`);
      sets.push(`updated_at = NOW()`);
      const params = [building_id, ...entries.map(([, v]) => v)];
      return (await q(`UPDATE buildings SET ${sets.join(', ')} WHERE building_id = $1 RETURNING *`, params)).rows[0] || { error: 'Not found' };
    }
  },

  add_note: {
    desc: 'Add internal note for a building',
    params: { building_id: 'string', note: 'string' },
    run: async (a) => {
      readonlyGuard();
      return (await q(`INSERT INTO analytics_events (event_type, building_id, data, created_at) VALUES ('internal_note', $1, $2, NOW()) RETURNING *`, [a.building_id, JSON.stringify({ note: a.note, author: 'CLI' })])).rows[0];
    }
  },

  create_community: {
    desc: 'Create a new community',
    params: { name: 'string', admin_building_id: 'string', distribution_model: 'string=proportional', description: 'string?' },
    run: async (a) => {
      readonlyGuard();
      const community_id = `com_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
      const result = await q(`INSERT INTO communities (community_id, name, admin_building_id, distribution_model, description, status, created_at, updated_at) VALUES ($1, $2, $3, $4, $5, 'interested', NOW(), NOW()) RETURNING *`, [community_id, a.name, a.admin_building_id, a.distribution_model || 'proportional', a.description || '']);
      await q(`INSERT INTO community_members (community_id, building_id, role, status, joined_at, confirmed_at) VALUES ($1, $2, 'admin', 'confirmed', NOW(), NOW())`, [community_id, a.admin_building_id]);
      return result.rows[0];
    }
  },

  update_community_status: {
    desc: 'Move community through formation stages',
    params: { community_id: 'string', status: 'string' },
    run: async (a) => {
      readonlyGuard();
      const timestampCol = { formation_started: 'formation_started_at', dso_submitted: 'dso_submitted_at', dso_approved: 'dso_approved_at', active: 'activated_at' }[a.status];
      let sql = `UPDATE communities SET status = $2, updated_at = NOW()`;
      if (timestampCol) sql += `, ${timestampCol} = NOW()`;
      sql += ` WHERE community_id = $1 RETURNING *`;
      return (await q(sql, [a.community_id, a.status])).rows[0] || { error: 'Not found' };
    }
  },

  trigger_email: {
    desc: 'Schedule an email to a building',
    params: { building_id: 'string', template_key: 'string', send_at: 'string?' },
    run: async (a) => {
      readonlyGuard();
      const bRes = await q(`SELECT email FROM buildings WHERE building_id = $1`, [a.building_id]);
      if (!bRes.rows.length) return { error: 'Building not found' };
      return (await q(`INSERT INTO scheduled_emails (building_id, email, template_key, send_at, status, created_at) VALUES ($1, $2, $3, $4, 'pending', NOW()) RETURNING *`, [a.building_id, bRes.rows[0].email, a.template_key, a.send_at || new Date().toISOString()])).rows[0];
    }
  },

  update_formation_step: {
    desc: 'Update community member status (invited/confirmed/rejected)',
    params: { community_id: 'string', building_id: 'string', status: 'string' },
    run: async (a) => {
      readonlyGuard();
      let sql = `UPDATE community_members SET status = $3`;
      if (a.status === 'confirmed') sql += `, confirmed_at = NOW()`;
      sql += ` WHERE community_id = $1 AND building_id = $2 RETURNING *`;
      return (await q(sql, [a.community_id, a.building_id, a.status])).rows[0] || { error: 'Not found' };
    }
  },

  add_community_member: {
    desc: 'Add a building to a community',
    params: { community_id: 'string', building_id: 'string', role: 'string=member', invited_by: 'string?' },
    run: async (a) => {
      readonlyGuard();
      const result = await q(`INSERT INTO community_members (community_id, building_id, role, status, invited_by, joined_at) VALUES ($1, $2, $3, 'invited', $4, NOW()) ON CONFLICT (community_id, building_id) DO NOTHING RETURNING *`, [a.community_id, a.building_id, a.role || 'member', a.invited_by || null]);
      if (!result.rows.length) return { error: 'Already a member' };
      return result.rows[0];
    }
  },

  update_consent: {
    desc: 'Update consent flags for a building',
    params: { building_id: 'string', share_with_neighbors: 'bool?', share_with_utility: 'bool?', updates_opt_in: 'bool?' },
    run: async (a) => {
      readonlyGuard();
      const { building_id, ...fields } = a;
      const entries = Object.entries(fields).filter(([, v]) => v !== undefined);
      if (!entries.length) return { error: 'No fields to update' };
      const sets = entries.map(([k], i) => `${k} = $${i + 2}`);
      const params = [building_id, ...entries.map(([, v]) => v)];
      return (await q(`UPDATE consents SET ${sets.join(', ')} WHERE building_id = $1 RETURNING *`, params)).rows[0] || { error: 'Not found' };
    }
  },

  // === Tenant Tools ===

  list_tenants: {
    desc: 'List all tenant configs',
    params: { active_only: 'bool=true' },
    run: async (a) => {
      let sql = `SELECT id, territory, utility_name, primary_color, contact_email, active, config, created_at, updated_at FROM white_label_configs`;
      if (a.active_only !== false) sql += ` WHERE active = TRUE`;
      sql += ` ORDER BY territory`;
      return (await q(sql)).rows;
    }
  },

  get_tenant: {
    desc: 'Get full tenant config by territory slug',
    params: { territory: 'string' },
    run: async (a) => {
      const result = await q(`SELECT * FROM white_label_configs WHERE territory = $1`, [a.territory]);
      return result.rows[0] || { error: 'Tenant not found' };
    }
  },

  upsert_tenant: {
    desc: 'Create or update a tenant/city config',
    params: { territory: 'string', utility_name: 'string', primary_color: 'string=#c7021a', secondary_color: 'string=#f59e0b', contact_email: 'string=', legal_entity: 'string=', dso_contact: 'string=', active: 'bool=true', config: 'json' },
    run: async (a) => {
      readonlyGuard();
      const configObj = typeof a.config === 'string' ? JSON.parse(a.config) : a.config;
      return (await q(
        `INSERT INTO white_label_configs (territory, utility_name, primary_color, secondary_color, contact_email, legal_entity, dso_contact, active, config, created_at, updated_at)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW(), NOW())
         ON CONFLICT (territory) DO UPDATE SET
           utility_name = EXCLUDED.utility_name, primary_color = EXCLUDED.primary_color,
           secondary_color = EXCLUDED.secondary_color, contact_email = EXCLUDED.contact_email,
           legal_entity = EXCLUDED.legal_entity, dso_contact = EXCLUDED.dso_contact,
           active = EXCLUDED.active, config = EXCLUDED.config, updated_at = NOW()
         RETURNING *`,
        [a.territory, a.utility_name, a.primary_color || '#c7021a', a.secondary_color || '#f59e0b',
         a.contact_email || '', a.legal_entity || '', a.dso_contact || '', a.active !== false,
         JSON.stringify(configObj)]
      )).rows[0];
    }
  },

  get_tenant_stats: {
    desc: 'Registration stats per tenant/city_id',
    params: {},
    run: async () => (await q(`SELECT city_id, COUNT(*) as total, COUNT(*) FILTER (WHERE verified = true) as verified FROM buildings GROUP BY city_id ORDER BY total DESC`)).rows
  },

  // === Web Search Tools ===

  research_vnb: {
    desc: 'Research a Swiss VNB/utility via web search',
    params: { utility_name: 'string', municipality: 'string?' },
    run: async (a) => {
      const searches = [
        `"${a.utility_name}" LEG Lokale Elektrizitätsgemeinschaft`,
        `"${a.utility_name}" leghub.ch`,
        a.municipality ? `"${a.utility_name}" ${a.municipality} PLZ Versorgungsgebiet` : null
      ].filter(Boolean);
      const results = {};
      for (const sq of searches) {
        try { results[sq] = await braveSearch(sq, 5); } catch (e) { results[sq] = { error: e.message }; }
      }
      return results;
    }
  },

  search_web: {
    desc: 'Search the web via Brave Search API',
    params: { query: 'string', count: 'num=5' },
    run: async (a) => braveSearch(a.query, a.count || 5)
  },

  scan_vnb_leg_offerings: {
    desc: 'Search web for VNB LEG product pages',
    params: { utility_name: 'string' },
    run: async (a) => {
      const searches = [
        `"${a.utility_name}" LEG Lokale Elektrizitätsgemeinschaft Angebot`,
        `"${a.utility_name}" GemeinsamStrom OR LEGhub`,
        `"${a.utility_name}" Netzgebühren LEG Reduktion`
      ];
      const results = {};
      for (const sq of searches) {
        try { results[sq] = await braveSearch(sq, 5); } catch (e) { results[sq] = { error: e.message }; }
      }
      return { utility_name: a.utility_name, searches: results };
    }
  },

  monitor_leghub_partners: {
    desc: 'Scrape leghub.ch partner list, compare with previous scan',
    params: {},
    run: async () => {
      const partners = await braveSearch('site:leghub.ch partner', 10);
      const prevRes = await q(`SELECT data FROM insights_cache WHERE insight_type = 'leghub_partners' ORDER BY computed_at DESC LIMIT 1`);
      const previous = prevRes.rows[0]?.data?.partners || [];
      const prevUrls = new Set(previous.map(p => p.url));
      const newPartners = partners.filter(p => !prevUrls.has(p.url));
      await q(`INSERT INTO insights_cache (insight_type, scope, period, data, expires_at) VALUES ('leghub_partners', 'CH', 'current', $1, NOW() + INTERVAL '30 days') ON CONFLICT (insight_type, scope, period) DO UPDATE SET data = EXCLUDED.data, computed_at = NOW(), expires_at = EXCLUDED.expires_at`, [JSON.stringify({ partners, scanned_at: new Date().toISOString() })]);
      return { total_partners: partners.length, new_since_last_scan: newPartners.length, new_partners: newPartners, all_partners: partners };
    }
  },

  // === Public Data Tools ===

  fetch_elcom_tariffs: {
    desc: 'Fetch ElCom electricity tariffs from LINDAS SPARQL, cache in DB',
    params: { bfs_number: 'num', year: 'num=2026' },
    run: async (a) => {
      const bfs_number = a.bfs_number;
      const year = a.year || 2026;
      const sparql = `
        PREFIX schema: <http://schema.org/>
        PREFIX cube: <https://cube.link/>
        PREFIX elcom: <https://energy.ld.admin.ch/elcom/electricityprice/dimension/>
        SELECT ?operator ?category ?total ?energy ?grid ?municipality_fee ?kev
        WHERE {
          ?obs a cube:Observation ;
               elcom:municipality <https://ld.admin.ch/municipality/${bfs_number}> ;
               elcom:period "${year}"^^<http://www.w3.org/2001/XMLSchema#gYear> ;
               elcom:operator ?operatorUri ;
               elcom:category ?categoryUri ;
               elcom:total ?total .
          OPTIONAL { ?obs elcom:gridusage ?grid }
          OPTIONAL { ?obs elcom:energy ?energy }
          OPTIONAL { ?obs elcom:charge ?municipality_fee }
          OPTIONAL { ?obs elcom:aidfee ?kev }
          ?operatorUri schema:name ?operator .
          ?categoryUri schema:name ?category .
        } ORDER BY ?operator ?category`;
      const res = await fetch('https://lindas.admin.ch/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/sparql-results+json' },
        body: `query=${encodeURIComponent(sparql)}`
      });
      if (!res.ok) throw new Error(`SPARQL ${res.status}`);
      const data = await res.json();
      const bindings = data.results?.bindings || [];
      const tariffs = bindings.map(b => ({
        bfs_number, year,
        operator_name: b.operator?.value || '',
        category: b.category?.value || '',
        total_rp_kwh: parseFloat(b.total?.value || 0),
        energy_rp_kwh: parseFloat(b.energy?.value || 0),
        grid_rp_kwh: parseFloat(b.grid?.value || 0),
        municipality_fee_rp_kwh: parseFloat(b.municipality_fee?.value || 0),
        kev_rp_kwh: parseFloat(b.kev?.value || 0)
      }));
      for (const t of tariffs) {
        await q(`INSERT INTO elcom_tariffs (bfs_number, operator_name, year, category, total_rp_kwh, energy_rp_kwh, grid_rp_kwh, municipality_fee_rp_kwh, kev_rp_kwh)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
           ON CONFLICT (bfs_number, operator_name, year, category) DO UPDATE SET
             total_rp_kwh = EXCLUDED.total_rp_kwh, energy_rp_kwh = EXCLUDED.energy_rp_kwh,
             grid_rp_kwh = EXCLUDED.grid_rp_kwh, municipality_fee_rp_kwh = EXCLUDED.municipality_fee_rp_kwh,
             kev_rp_kwh = EXCLUDED.kev_rp_kwh, fetched_at = NOW()`,
          [t.bfs_number, t.operator_name, t.year, t.category, t.total_rp_kwh, t.energy_rp_kwh, t.grid_rp_kwh, t.municipality_fee_rp_kwh, t.kev_rp_kwh]);
      }
      return { bfs_number, year, tariffs_count: tariffs.length, tariffs };
    }
  },

  fetch_energie_reporter: {
    desc: 'Download Energie Reporter CSV, upsert municipality profiles',
    params: { kanton: 'string=ZH' },
    run: async (a) => {
      const kanton = a.kanton || 'ZH';
      const metaRes = await fetch('https://opendata.swiss/api/3/action/package_show?id=energie-reporter');
      const meta = await metaRes.json();
      const csvResource = meta.result?.resources?.find(r => r.format?.toUpperCase() === 'CSV');
      if (!csvResource) return { error: 'No CSV resource found' };
      const csvRes = await fetch(csvResource.url);
      const csvText = await csvRes.text();
      const lines = csvText.split('\n');
      const headers = lines[0]?.split(';').map(h => h.trim()) || [];
      let count = 0;
      for (let i = 1; i < lines.length; i++) {
        const vals = lines[i]?.split(';');
        if (!vals || vals.length < headers.length) continue;
        const row = {};
        headers.forEach((h, idx) => { row[h] = vals[idx]?.trim(); });
        const bfs = parseInt(row.BFS_NR || row.bfs_nr || row.gemeinde_bfs);
        const k = row.KANTON || row.kanton || '';
        if (!bfs || (kanton && k.toUpperCase() !== kanton.toUpperCase())) continue;
        await q(`INSERT INTO municipality_profiles (bfs_number, name, kanton, solar_potential_pct, ev_share_pct, renewable_heating_pct, electricity_consumption_mwh, renewable_production_mwh, data_sources)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
           ON CONFLICT (bfs_number) DO UPDATE SET
             name = EXCLUDED.name, solar_potential_pct = EXCLUDED.solar_potential_pct,
             ev_share_pct = EXCLUDED.ev_share_pct, renewable_heating_pct = EXCLUDED.renewable_heating_pct,
             electricity_consumption_mwh = EXCLUDED.electricity_consumption_mwh,
             renewable_production_mwh = EXCLUDED.renewable_production_mwh,
             data_sources = municipality_profiles.data_sources || EXCLUDED.data_sources,
             updated_at = NOW()`,
          [bfs, row.GEMEINDENAME || row.gemeindename || '', k,
           parseFloat(row.anteil_dachflaechen_solar || 0) || null,
           parseFloat(row.anteil_ev || 0) || null,
           parseFloat(row.anteil_erneuerbar_heizen || 0) || null,
           parseFloat(row.stromverbrauch_mwh || 0) || null,
           parseFloat(row.erneuerbare_produktion_mwh || 0) || null,
           JSON.stringify({ energie_reporter: true, fetched_at: new Date().toISOString() })]);
        count++;
      }
      return { kanton, municipalities_upserted: count };
    }
  },

  fetch_sonnendach_data: {
    desc: 'Fetch solar potential from BFE Sonnendach, cache in DB',
    params: { bfs_number: 'num?' },
    run: async (a) => {
      const metaRes = await fetch('https://opendata.swiss/api/3/action/package_show?id=sonnendach-ch');
      const meta = await metaRes.json();
      const csvResource = meta.result?.resources?.find(r =>
        r.format?.toUpperCase() === 'CSV' && (r.name?.toLowerCase().includes('gemeinde') || r.name?.toLowerCase().includes('municipal'))
      ) || meta.result?.resources?.find(r => r.format?.toUpperCase() === 'CSV');
      if (!csvResource) return { error: 'No CSV resource found' };
      const csvRes = await fetch(csvResource.url);
      const csvText = await csvRes.text();
      const lines = csvText.split('\n');
      const headers = lines[0]?.split(';').map(h => h.trim()) || [];
      let count = 0;
      for (let i = 1; i < lines.length; i++) {
        const vals = lines[i]?.split(';');
        if (!vals || vals.length < headers.length) continue;
        const row = {};
        headers.forEach((h, idx) => { row[h] = vals[idx]?.trim(); });
        const bfs = parseInt(row.BFS_NR || row.bfs_nr || row.gemeinde_bfs);
        if (!bfs) continue;
        if (a.bfs_number && bfs !== a.bfs_number) continue;
        await q(`INSERT INTO sonnendach_municipal (bfs_number, total_roof_area_m2, suitable_roof_area_m2, potential_kwh_year, potential_kwp, utilization_pct)
           VALUES ($1, $2, $3, $4, $5, $6)
           ON CONFLICT (bfs_number) DO UPDATE SET
             total_roof_area_m2 = EXCLUDED.total_roof_area_m2, suitable_roof_area_m2 = EXCLUDED.suitable_roof_area_m2,
             potential_kwh_year = EXCLUDED.potential_kwh_year, potential_kwp = EXCLUDED.potential_kwp,
             utilization_pct = EXCLUDED.utilization_pct, fetched_at = NOW()`,
          [bfs,
           parseFloat(row.dachflaeche_total_m2 || 0) || null,
           parseFloat(row.dachflaeche_geeignet_m2 || 0) || null,
           parseFloat(row.potenzial_kwh_jahr || 0) || null,
           parseFloat(row.potenzial_kwp || 0) || null,
           parseFloat(row.auslastung_pct || 0) || null]);
        count++;
      }
      return { municipalities_upserted: count, bfs_number: a.bfs_number || 'all' };
    }
  },

  refresh_municipality_data: {
    desc: 'End-to-end refresh: ElCom tariffs, scores, profile update',
    params: { bfs_number: 'num' },
    run: async (a) => {
      const bfs_number = a.bfs_number;
      const result = { bfs_number, steps: {} };
      try {
        const sparql = `
          PREFIX schema: <http://schema.org/>
          PREFIX cube: <https://cube.link/>
          PREFIX elcom: <https://energy.ld.admin.ch/elcom/electricityprice/dimension/>
          SELECT ?operator ?category ?total ?energy ?grid ?municipality_fee ?kev
          WHERE {
            ?obs a cube:Observation ;
                 elcom:municipality <https://ld.admin.ch/municipality/${bfs_number}> ;
                 elcom:period "2026"^^<http://www.w3.org/2001/XMLSchema#gYear> ;
                 elcom:operator ?operatorUri ;
                 elcom:category ?categoryUri ;
                 elcom:total ?total .
            OPTIONAL { ?obs elcom:gridusage ?grid }
            OPTIONAL { ?obs elcom:energy ?energy }
            OPTIONAL { ?obs elcom:charge ?municipality_fee }
            OPTIONAL { ?obs elcom:aidfee ?kev }
            ?operatorUri schema:name ?operator .
            ?categoryUri schema:name ?category .
          }`;
        const sparqlRes = await fetch('https://lindas.admin.ch/query', {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/sparql-results+json' },
          body: `query=${encodeURIComponent(sparql)}`
        });
        const sparqlData = await sparqlRes.json();
        const bindings = sparqlData.results?.bindings || [];
        result.steps.elcom = { tariffs: bindings.length };
        for (const b of bindings) {
          await q(`INSERT INTO elcom_tariffs (bfs_number, operator_name, year, category, total_rp_kwh, energy_rp_kwh, grid_rp_kwh, municipality_fee_rp_kwh, kev_rp_kwh)
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
             ON CONFLICT (bfs_number, operator_name, year, category) DO UPDATE SET
               total_rp_kwh = EXCLUDED.total_rp_kwh, energy_rp_kwh = EXCLUDED.energy_rp_kwh,
               grid_rp_kwh = EXCLUDED.grid_rp_kwh, municipality_fee_rp_kwh = EXCLUDED.municipality_fee_rp_kwh,
               kev_rp_kwh = EXCLUDED.kev_rp_kwh, fetched_at = NOW()`,
            [bfs_number, b.operator?.value || '', 2026, b.category?.value || '',
             parseFloat(b.total?.value || 0), parseFloat(b.energy?.value || 0),
             parseFloat(b.grid?.value || 0), parseFloat(b.municipality_fee?.value || 0),
             parseFloat(b.kev?.value || 0)]);
        }
        const h4 = bindings.find(b => (b.category?.value || '').startsWith('H4'));
        if (h4) {
          const gridFee = parseFloat(h4.grid?.value || 0);
          const savingsRp = gridFee * 0.4;
          const annualSavings = savingsRp * 4500 / 100;
          result.steps.value_gap = { grid_fee_rp: gridFee, annual_savings_chf: Math.round(annualSavings * 100) / 100 };
          const profileRes = await q(`SELECT * FROM municipality_profiles WHERE bfs_number = $1`, [bfs_number]);
          const existing = profileRes.rows[0] || {};
          const solar = parseFloat(existing.solar_potential_pct || 0) / 100;
          const ev = Math.min(parseFloat(existing.ev_share_pct || 0), 30) / 30;
          const heating = parseFloat(existing.renewable_heating_pct || 0) / 100;
          const consumption = parseFloat(existing.electricity_consumption_mwh || 0);
          const production = parseFloat(existing.renewable_production_mwh || 0);
          const prodRatio = consumption > 0 ? Math.min(production / consumption, 1) : 0;
          const score = Math.round((solar * 30 + ev * 20 + heating * 25 + prodRatio * 25) * 10) / 10;
          await q(`UPDATE municipality_profiles SET leg_value_gap_chf = $2, energy_transition_score = $3, data_sources = data_sources || $4, updated_at = NOW() WHERE bfs_number = $1`,
            [bfs_number, annualSavings, score, JSON.stringify({ elcom: true, last_refresh: new Date().toISOString() })]);
          result.steps.profile = { score, value_gap_chf: annualSavings };
        }
      } catch (e) { result.steps.error = e.message; }
      return result;
    }
  },

  // === Sales Pipeline Tools ===

  get_vnb_pipeline: {
    desc: 'Get VNB sales pipeline entries',
    params: { status: 'string?', limit: 'num=50' },
    run: async (a) => {
      let sql = 'SELECT * FROM vnb_pipeline';
      const params = [];
      if (a.status) { sql += ' WHERE status = $1'; params.push(a.status); }
      sql += ' ORDER BY score DESC NULLS LAST LIMIT $' + (params.length + 1);
      params.push(a.limit || 50);
      return (await q(sql, params)).rows;
    }
  },

  update_vnb_status: {
    desc: 'Update VNB pipeline entry status and notes',
    params: { vnb_id: 'num', status: 'string', notes: 'string?' },
    run: async (a) => {
      readonlyGuard();
      const valid = ['lead', 'contacted', 'demo', 'trial', 'paid', 'churned'];
      if (!valid.includes(a.status)) return { error: 'Invalid status' };
      return (await q(`UPDATE vnb_pipeline SET status = $2, notes = COALESCE($3, notes), updated_at = NOW() WHERE id = $1 RETURNING *`, [a.vnb_id, a.status, a.notes || null])).rows[0] || { error: 'Not found' };
    }
  },

  add_vnb_lead: {
    desc: 'Add a new VNB to the sales pipeline',
    params: { vnb_name: 'string', municipality: 'string?', bfs_number: 'num?', population: 'num?', score: 'num?', notes: 'string?' },
    run: async (a) => {
      readonlyGuard();
      return (await q(`INSERT INTO vnb_pipeline (vnb_name, municipality, bfs_number, population, score, notes, status, created_at, updated_at) VALUES ($1, $2, $3, $4, $5, $6, 'lead', NOW(), NOW()) RETURNING *`,
        [a.vnb_name, a.municipality || null, a.bfs_number || null, a.population || null, a.score || null, a.notes || null])).rows[0];
    }
  },

  get_pipeline_dashboard: {
    desc: 'Pipeline funnel metrics: counts per stage, conversion rate',
    params: {},
    run: async () => {
      const res = await q(`SELECT status, COUNT(*)::int as count, ROUND(AVG(score)::numeric, 1) as avg_score FROM vnb_pipeline GROUP BY status ORDER BY CASE status WHEN 'lead' THEN 1 WHEN 'contacted' THEN 2 WHEN 'demo' THEN 3 WHEN 'trial' THEN 4 WHEN 'paid' THEN 5 WHEN 'churned' THEN 6 END`);
      const total = res.rows.reduce((s, r) => s + r.count, 0);
      const paid = res.rows.find(r => r.status === 'paid')?.count || 0;
      return { funnel: res.rows, total, conversion_rate: total > 0 ? Math.round(paid / total * 1000) / 10 : 0 };
    }
  },

  // === Document & Billing Tools ===

  generate_leg_document: {
    desc: 'Generate LEG document via Flask endpoint',
    params: { community_id: 'string', doc_type: 'string' },
    run: async (a) => {
      readonlyGuard();
      const flaskUrl = process.env.FLASK_URL || 'http://flask:5000';
      const res = await fetch(`${flaskUrl}/api/formation/generate-document`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ community_id: a.community_id, doc_type: a.doc_type })
      });
      return await res.json();
    }
  },

  list_documents: {
    desc: 'List LEG documents for a community',
    params: { community_id: 'string' },
    run: async (a) => (await q(`SELECT id, community_id, doc_type, status, file_path, created_at, signed_at FROM leg_documents WHERE community_id = $1 ORDER BY created_at DESC`, [a.community_id])).rows
  },

  run_billing_period: {
    desc: 'Query billing periods for a community in date range',
    params: { community_id: 'string', start_date: 'string', end_date: 'string' },
    run: async (a) => (await q(`SELECT id, community_id, period_start, period_end, status, total_consumption_kwh, total_production_kwh, self_consumption_kwh, grid_import_kwh, created_at FROM billing_periods WHERE community_id = $1 AND period_start >= $2 AND period_end <= $3 ORDER BY period_start`, [a.community_id, a.start_date, a.end_date])).rows
  },

  get_billing_summary: {
    desc: 'Billing line items summary for a community in a month',
    params: { community_id: 'string', month: 'string' },
    run: async (a) => {
      const result = await q(`SELECT bli.building_id, bli.consumption_kwh, bli.production_kwh, bli.self_supply_kwh, bli.grid_import_kwh, bli.amount_chf, bli.self_supply_ratio, bp.period_start, bp.period_end FROM billing_line_items bli JOIN billing_periods bp ON bli.billing_period_id = bp.id WHERE bp.community_id = $1 AND to_char(bp.period_start, 'YYYY-MM') = $2 ORDER BY bli.building_id`, [a.community_id, a.month]);
      const total = result.rows.reduce((acc, r) => ({
        consumption: acc.consumption + parseFloat(r.consumption_kwh || 0),
        production: acc.production + parseFloat(r.production_kwh || 0),
        amount: acc.amount + parseFloat(r.amount_chf || 0),
        count: acc.count + 1
      }), { consumption: 0, production: 0, amount: 0, count: 0 });
      return { month: a.month, community_id: a.community_id, line_items: result.rows, totals: total };
    }
  },

  // === Scoring Tools ===

  score_vnb: {
    desc: 'Score a VNB as expansion target (pure computation)',
    params: { population: 'num', solar_potential_pct: 'num', has_leghub: 'bool', smart_meter_rollout_pct: 'num' },
    run: async (a) => {
      let score = 0;
      if (a.population > 50000) score += 30;
      else if (a.population > 20000) score += 25;
      else if (a.population > 10000) score += 20;
      else if (a.population > 5000) score += 15;
      else score += 10;
      score += Math.min(25, Math.round(a.solar_potential_pct * 0.25));
      score += a.has_leghub ? 20 : 0;
      score += Math.min(25, Math.round(a.smart_meter_rollout_pct * 0.25));
      const tier = score >= 80 ? 'hot' : score >= 60 ? 'warm' : score >= 40 ? 'medium' : 'cold';
      return { score, tier, breakdown: { population_pts: score > 80 ? 30 : 'varies', solar_pts: Math.min(25, Math.round(a.solar_potential_pct * 0.25)), leghub_pts: a.has_leghub ? 20 : 0, smart_meter_pts: Math.min(25, Math.round(a.smart_meter_rollout_pct * 0.25)) } };
    }
  },

  draft_outreach: {
    desc: 'Draft municipality outreach email (German)',
    params: { municipality_name: 'string', bfs_number: 'num', value_gap_chf: 'num', solar_potential_pct: 'num' },
    run: async (a) => {
      const profileUrl = `https://openleg.ch/gemeinde/${a.bfs_number}/profil`;
      const onboardingUrl = `https://openleg.ch/gemeinde/${a.bfs_number}/onboarding`;
      const email = `Betreff: Kostenlose LEG-Infrastruktur für ${a.municipality_name}\n\nSehr geehrte Gemeindeverantwortliche\n\nDie Gemeinde ${a.municipality_name} verfügt über ein hohes Potenzial für Lokale Elektrizitätsgemeinschaften (LEG).\n\nKennzahlen:\n- Solarpotenzial: ${a.solar_potential_pct}% der Dachflächen geeignet\n- Einsparpotenzial: ca. CHF ${a.value_gap_chf} pro Haushalt und Jahr\n\nOpenLEG stellt kostenlose, quelloffene Infrastruktur für die Gründung und Verwaltung von LEGs bereit. Kein Datenverkauf, keine Gebühren.\n\nGemeindeprofil: ${profileUrl}\nOnboarding starten: ${onboardingUrl}\n\nFreundliche Grüsse\nOpenLEG\nhallo@openleg.ch`;
      return { email, metadata: { municipality_name: a.municipality_name, bfs_number: a.bfs_number, value_gap_chf: a.value_gap_chf, solar_potential_pct: a.solar_potential_pct } };
    }
  },

  // === Seeding Tools ===

  get_unseeded_municipalities: {
    desc: 'List municipalities without tenant config, ranked by value gap',
    params: { kanton: 'string?', limit: 'num=50' },
    run: async (a) => {
      let sql = `SELECT mp.bfs_number, mp.name, mp.kanton, mp.solar_potential_pct, mp.leg_value_gap_chf FROM municipality_profiles mp LEFT JOIN white_label_configs wlc ON LOWER(mp.name) = wlc.territory WHERE wlc.territory IS NULL`;
      const params = [];
      if (a.kanton) { params.push(a.kanton); sql += ` AND mp.kanton = $${params.length}`; }
      sql += ` ORDER BY mp.leg_value_gap_chf DESC NULLS LAST, mp.solar_potential_pct DESC NULLS LAST`;
      params.push(a.limit || 50);
      sql += ` LIMIT $${params.length}`;
      const result = await q(sql, params);
      return { count: result.rowCount, municipalities: result.rows };
    }
  },

  get_all_swiss_municipalities: {
    desc: 'Query LINDAS SPARQL for all Swiss municipalities',
    params: { kanton: 'string?' },
    run: async (a) => {
      const kantonFilter = a.kanton ? `FILTER(STRENDS(STR(?canton), "${a.kanton}"))` : '';
      const sparqlQuery = `
        PREFIX schema: <http://schema.org/>
        PREFIX admin: <https://schema.ld.admin.ch/>
        SELECT ?bfs ?name ?canton ?population WHERE {
          ?municipality a admin:Municipality ;
            schema:identifier ?bfs ;
            schema:name ?name ;
            admin:canton ?canton .
          OPTIONAL { ?municipality schema:population ?population }
          ${kantonFilter}
        } ORDER BY ?name`;
      const resp = await fetch('https://lindas.admin.ch/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/sparql-results+json' },
        body: `query=${encodeURIComponent(sparqlQuery)}`
      });
      if (!resp.ok) return { error: `SPARQL query failed: ${resp.status}` };
      const data = await resp.json();
      const rows = (data.results?.bindings || []).map(b => ({
        bfs_number: parseInt(b.bfs?.value || '0'),
        name: b.name?.value || '',
        kanton: (b.canton?.value || '').split('/').pop(),
        population: parseInt(b.population?.value || '0')
      }));
      return { count: rows.length, municipalities: rows };
    }
  },

  // === Formation Monitoring ===

  get_stuck_formations: {
    desc: 'Find communities stuck at same status for N days',
    params: { days_threshold: 'num=7' },
    run: async (a) => {
      const result = await q(`SELECT c.community_id, c.name, c.status, EXTRACT(DAY FROM NOW() - c.updated_at)::int as days_stuck, b.email as admin_email FROM communities c JOIN buildings b ON c.admin_building_id = b.building_id WHERE c.status NOT IN ('active', 'rejected') AND c.updated_at < NOW() - make_interval(days => $1) ORDER BY c.updated_at ASC`, [a.days_threshold || 7]);
      return { count: result.rowCount, stuck_formations: result.rows };
    }
  },

  get_outreach_candidates: {
    desc: 'Seeded municipalities with no registrations, no contact email',
    params: { limit: 'num=50' },
    run: async (a) => {
      const result = await q(`SELECT wlc.territory, wlc.config->>'city_name' as city_name, wlc.config->>'kanton' as kanton FROM white_label_configs wlc LEFT JOIN buildings b ON b.city_id = wlc.territory WHERE wlc.active = true GROUP BY wlc.id, wlc.territory, wlc.config HAVING COUNT(b.building_id) = 0 AND (wlc.contact_email IS NULL OR wlc.contact_email = '') ORDER BY wlc.created_at ASC LIMIT $1`, [a.limit || 50]);
      return { count: result.rowCount, candidates: result.rows };
    }
  },
};

// === Arg parsing ===

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i++) {
    if (argv[i].startsWith('--')) {
      const key = argv[i].slice(2);
      const val = argv[i + 1];
      if (val === undefined || val.startsWith('--')) {
        args[key] = true;
      } else {
        // Auto-convert numbers and booleans
        if (val === 'true') args[key] = true;
        else if (val === 'false') args[key] = false;
        else if (/^-?\d+(\.\d+)?$/.test(val)) args[key] = Number(val);
        else args[key] = val;
        i++;
      }
    }
  }
  return args;
}

// === Main ===

async function main() {
  const [command, ...rest] = process.argv.slice(2);

  if (!command || command === 'help') {
    const lines = ['Usage: node cli.mjs <command> [--key value ...]\n', 'Commands:'];
    for (const [name, tool] of Object.entries(TOOLS)) {
      const paramKeys = Object.keys(tool.params);
      const paramStr = paramKeys.length ? paramKeys.map(k => `--${k}`).join(' ') : '(no params)';
      lines.push(`  ${name.padEnd(30)} ${tool.desc}`);
      lines.push(`    ${paramStr}`);
    }
    console.log(lines.join('\n'));
    process.exit(0);
  }

  const tool = TOOLS[command];
  if (!tool) {
    console.error(`Unknown command: ${command}\nRun "node cli.mjs help" for available commands.`);
    process.exit(1);
  }

  const args = parseArgs(rest);

  try {
    const result = await tool.run(args);
    console.log(JSON.stringify(result, null, 2));
  } catch (e) {
    console.error(JSON.stringify({ error: e.message }));
    process.exit(1);
  } finally {
    await pool.end();
  }
}

main();
