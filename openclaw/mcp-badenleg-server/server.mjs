import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import pg from 'pg';

const { Pool } = pg;

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: false,
  max: 5
});

const BRAVE_API_KEY = process.env.BRAVE_API_KEY || '';
const READONLY = (process.env.OPENCLAW_READONLY || 'false').toLowerCase() === 'true';

function readonlyGuard() {
  if (READONLY) return { content: [{ type: 'text', text: 'Write access disabled (OPENCLAW_READONLY=true)' }] };
  return null;
}

async function query(sql, params = []) {
  const start = Date.now();
  const result = await pool.query(sql, params);
  console.error(`[${new Date().toISOString()}] SQL ${Date.now() - start}ms rows=${result.rowCount}`);
  return result;
}

function txt(data) {
  return { content: [{ type: 'text', text: JSON.stringify(data, null, 2) }] };
}

const server = new McpServer({
  name: 'openleg',
  version: '1.0.0'
});

// ============================================================
// Read Tools
// ============================================================

server.tool(
  'search_registrations',
  'Search confirmed buildings/registrations by address, email, name, PLZ, or building type. Returns matching records with cluster info.',
  {
    query: z.string().optional().describe('Free text search (address, email)'),
    plz: z.string().optional().describe('Filter by postal code'),
    building_type: z.string().optional().describe('Filter: house, apartment'),
    verified_only: z.boolean().default(true).describe('Only verified registrations'),
    limit: z.number().default(20).describe('Max results')
  },
  async ({ query: q, plz, building_type, verified_only, limit }) => {
    let sql = `
      SELECT b.building_id, b.email, b.phone, b.address, b.plz, b.building_type,
             b.annual_consumption_kwh, b.potential_pv_kwp, b.registered_at, b.verified,
             b.user_type, b.referral_code, c.cluster_id,
             co.share_with_neighbors, co.updates_opt_in
      FROM buildings b
      LEFT JOIN clusters c ON c.building_id = b.building_id
      LEFT JOIN consents co ON co.building_id = b.building_id
      WHERE 1=1
    `;
    const params = [];
    let idx = 1;

    if (verified_only) {
      sql += ` AND b.verified = true`;
    }
    if (q) {
      sql += ` AND (b.address ILIKE $${idx} OR b.email ILIKE $${idx})`;
      params.push(`%${q}%`);
      idx++;
    }
    if (plz) {
      sql += ` AND b.plz = $${idx}`;
      params.push(plz);
      idx++;
    }
    if (building_type) {
      sql += ` AND b.building_type = $${idx}`;
      params.push(building_type);
      idx++;
    }
    sql += ` ORDER BY b.registered_at DESC LIMIT $${idx}`;
    params.push(limit);

    const result = await query(sql, params);
    return txt(result.rows);
  }
);

server.tool(
  'get_registration',
  'Get full details of a single building/registration by building_id, including consents, cluster, referrals.',
  { building_id: z.string().describe('Building ID') },
  async ({ building_id }) => {
    const bRes = await query(`
      SELECT b.*, c.cluster_id, ci.autarky_percent, ci.num_members,
             co.share_with_neighbors, co.share_with_utility, co.updates_opt_in
      FROM buildings b
      LEFT JOIN clusters c ON c.building_id = b.building_id
      LEFT JOIN cluster_info ci ON ci.cluster_id = c.cluster_id
      LEFT JOIN consents co ON co.building_id = b.building_id
      WHERE b.building_id = $1
    `, [building_id]);

    if (bRes.rows.length === 0) return txt({ error: 'Not found' });

    const refRes = await query(`
      SELECT r.id, b.address, b.email, r.created_at
      FROM referrals r JOIN buildings b ON b.building_id = r.referred_id
      WHERE r.referrer_id = $1
    `, [building_id]);

    const cmRes = await query(`
      SELECT cm.community_id, com.name, com.status, cm.role, cm.status as member_status
      FROM community_members cm
      JOIN communities com ON com.community_id = cm.community_id
      WHERE cm.building_id = $1
    `, [building_id]);

    return txt({
      building: bRes.rows[0],
      referrals_made: refRes.rows,
      communities: cmRes.rows
    });
  }
);

server.tool(
  'list_communities',
  'List all communities with status, member count, admin info.',
  {
    status: z.string().optional().describe('Filter: interested, formation_started, dso_submitted, dso_approved, active, rejected'),
    limit: z.number().default(50)
  },
  async ({ status, limit }) => {
    let sql = `
      SELECT com.*, b.address as admin_address, b.email as admin_email,
             (SELECT COUNT(*) FROM community_members cm WHERE cm.community_id = com.community_id) as member_count
      FROM communities com
      LEFT JOIN buildings b ON b.building_id = com.admin_building_id
      WHERE 1=1
    `;
    const params = [];
    let idx = 1;

    if (status) {
      sql += ` AND com.status = $${idx}`;
      params.push(status);
      idx++;
    }
    sql += ` ORDER BY com.created_at DESC LIMIT $${idx}`;
    params.push(limit);

    const result = await query(sql, params);
    return txt(result.rows);
  }
);

server.tool(
  'get_community',
  'Get community details with all members.',
  { community_id: z.string().describe('Community ID') },
  async ({ community_id }) => {
    const comRes = await query(`SELECT * FROM communities WHERE community_id = $1`, [community_id]);
    if (comRes.rows.length === 0) return txt({ error: 'Not found' });

    const memRes = await query(`
      SELECT cm.*, b.address, b.email, b.phone, b.building_type,
             b.annual_consumption_kwh, b.potential_pv_kwp
      FROM community_members cm
      JOIN buildings b ON b.building_id = cm.building_id
      WHERE cm.community_id = $1
      ORDER BY cm.role DESC, cm.joined_at
    `, [community_id]);

    const docRes = await query(`
      SELECT * FROM community_documents WHERE community_id = $1
    `, [community_id]);

    return txt({
      community: comRes.rows[0],
      members: memRes.rows,
      documents: docRes.rows[0] || null
    });
  }
);

server.tool(
  'get_stats',
  'Dashboard stats: total registrations, verified count, communities, clusters, referrals, email stats.',
  {},
  async () => {
    const stats = {};

    const bRes = await query(`
      SELECT
        COUNT(*) as total_buildings,
        COUNT(*) FILTER (WHERE verified = true) as verified,
        COUNT(*) FILTER (WHERE user_type = 'registered') as registered,
        COUNT(*) FILTER (WHERE user_type = 'anonymous') as anonymous
      FROM buildings
    `);
    stats.buildings = bRes.rows[0];

    const cRes = await query(`
      SELECT COUNT(DISTINCT cluster_id) as total_clusters,
             AVG(num_members) as avg_members,
             AVG(autarky_percent) as avg_autarky
      FROM cluster_info
    `);
    stats.clusters = cRes.rows[0];

    const comRes = await query(`
      SELECT status, COUNT(*) as count FROM communities GROUP BY status
    `);
    stats.communities = comRes.rows;

    const refRes = await query(`SELECT COUNT(*) as total FROM referrals`);
    stats.referrals = { total: parseInt(refRes.rows[0].total) };

    const eRes = await query(`
      SELECT status, COUNT(*) as count FROM scheduled_emails GROUP BY status
    `);
    stats.emails = eRes.rows;

    const recentRes = await query(`
      SELECT building_id, address, email, registered_at, verified
      FROM buildings ORDER BY registered_at DESC LIMIT 5
    `);
    stats.recent_registrations = recentRes.rows;

    return txt(stats);
  }
);

server.tool(
  'get_referrals',
  'Referral leaderboard: top referrers with count of successful referrals.',
  { limit: z.number().default(10) },
  async ({ limit }) => {
    const result = await query(`
      SELECT b.building_id, b.address, b.email, b.referral_code,
             COUNT(r.id) as referral_count
      FROM buildings b
      JOIN referrals r ON r.referrer_id = b.building_id
      GROUP BY b.building_id, b.address, b.email, b.referral_code
      ORDER BY referral_count DESC
      LIMIT $1
    `, [limit]);
    return txt(result.rows);
  }
);

server.tool(
  'list_scheduled_emails',
  'List scheduled/pending emails with status and template info.',
  {
    status: z.string().optional().describe('Filter: pending, sent, failed, cancelled'),
    building_id: z.string().optional(),
    limit: z.number().default(20)
  },
  async ({ status, building_id, limit }) => {
    let sql = `
      SELECT se.*, b.address
      FROM scheduled_emails se
      LEFT JOIN buildings b ON b.building_id = se.building_id
      WHERE 1=1
    `;
    const params = [];
    let idx = 1;

    if (status) {
      sql += ` AND se.status = $${idx}`;
      params.push(status);
      idx++;
    }
    if (building_id) {
      sql += ` AND se.building_id = $${idx}`;
      params.push(building_id);
      idx++;
    }
    sql += ` ORDER BY se.send_at DESC LIMIT $${idx}`;
    params.push(limit);

    const result = await query(sql, params);
    return txt(result.rows);
  }
);

server.tool(
  'get_formation_status',
  'Get formation wizard progress for a community: current status, timestamps, member confirmations.',
  { community_id: z.string().describe('Community ID') },
  async ({ community_id }) => {
    const comRes = await query(`
      SELECT community_id, name, status, distribution_model,
             created_at, formation_started_at, dso_submitted_at, dso_approved_at, activated_at
      FROM communities WHERE community_id = $1
    `, [community_id]);

    if (comRes.rows.length === 0) return txt({ error: 'Not found' });

    const memRes = await query(`
      SELECT building_id, role, status, confirmed_at
      FROM community_members WHERE community_id = $1
    `, [community_id]);

    const confirmed = memRes.rows.filter(m => m.status === 'confirmed').length;
    const total = memRes.rows.length;

    return txt({
      community: comRes.rows[0],
      members: memRes.rows,
      progress: { confirmed, total, percent: total > 0 ? Math.round(confirmed / total * 100) : 0 }
    });
  }
);

server.tool(
  'get_cluster_details',
  'Get cluster info with all member buildings.',
  { cluster_id: z.number().describe('Cluster ID') },
  async ({ cluster_id }) => {
    const ciRes = await query(`SELECT * FROM cluster_info WHERE cluster_id = $1`, [cluster_id]);
    const mRes = await query(`
      SELECT b.building_id, b.address, b.email, b.building_type,
             b.annual_consumption_kwh, b.potential_pv_kwp, b.verified
      FROM clusters c JOIN buildings b ON b.building_id = c.building_id
      WHERE c.cluster_id = $1
    `, [cluster_id]);
    return txt({
      cluster: ciRes.rows[0] || null,
      members: mRes.rows
    });
  }
);

server.tool(
  'get_street_leaderboard',
  'Street leaderboard: streets ranked by building count, communities, referrals.',
  { limit: z.number().default(10) },
  async ({ limit }) => {
    const result = await query(`
      SELECT * FROM street_stats ORDER BY building_count DESC LIMIT $1
    `, [limit]);
    return txt(result.rows);
  }
);

// ============================================================
// Write Tools
// ============================================================

server.tool(
  'update_registration',
  'Update building/registration fields. Confirm with user before executing.',
  {
    building_id: z.string().describe('Building ID'),
    email: z.string().optional(),
    phone: z.string().optional(),
    building_type: z.string().optional(),
    annual_consumption_kwh: z.number().optional(),
    potential_pv_kwp: z.number().optional()
  },
  async ({ building_id, ...fields }) => {
    const blocked = readonlyGuard(); if (blocked) return blocked;
    const entries = Object.entries(fields).filter(([, v]) => v !== undefined);
    if (entries.length === 0) return txt({ error: 'No fields to update' });

    const sets = entries.map(([k], i) => `${k} = $${i + 2}`);
    const params = [building_id, ...entries.map(([, v]) => v)];
    sets.push(`updated_at = NOW()`);

    const result = await query(
      `UPDATE buildings SET ${sets.join(', ')} WHERE building_id = $1 RETURNING *`,
      params
    );
    return txt(result.rows[0] || { error: 'Not found' });
  }
);

server.tool(
  'add_note',
  'Add an analytics event as internal note for a building.',
  {
    building_id: z.string().describe('Building ID'),
    note: z.string().describe('Note content')
  },
  async ({ building_id, note }) => {
    const blocked = readonlyGuard(); if (blocked) return blocked;
    const result = await query(
      `INSERT INTO analytics_events (event_type, building_id, data, created_at)
       VALUES ('internal_note', $1, $2, NOW()) RETURNING *`,
      [building_id, JSON.stringify({ note, author: 'LEA' })]
    );
    return txt(result.rows[0]);
  }
);

server.tool(
  'create_community',
  'Manually create a new community. Confirm with user before executing.',
  {
    name: z.string().describe('Community name'),
    admin_building_id: z.string().describe('Building ID of community admin'),
    distribution_model: z.string().default('proportional').describe('simple, proportional, or custom'),
    description: z.string().optional()
  },
  async ({ name, admin_building_id, distribution_model, description }) => {
    const blocked = readonlyGuard(); if (blocked) return blocked;
    const community_id = `com_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;

    const result = await query(
      `INSERT INTO communities (community_id, name, admin_building_id, distribution_model, description, status, created_at, updated_at)
       VALUES ($1, $2, $3, $4, $5, 'interested', NOW(), NOW()) RETURNING *`,
      [community_id, name, admin_building_id, distribution_model, description || '']
    );

    await query(
      `INSERT INTO community_members (community_id, building_id, role, status, joined_at, confirmed_at)
       VALUES ($1, $2, 'admin', 'confirmed', NOW(), NOW())`,
      [community_id, admin_building_id]
    );

    return txt(result.rows[0]);
  }
);

server.tool(
  'update_community_status',
  'Move community through formation stages. Confirm with user before executing.',
  {
    community_id: z.string().describe('Community ID'),
    status: z.string().describe('interested, formation_started, dso_submitted, dso_approved, active, rejected')
  },
  async ({ community_id, status }) => {
    const blocked = readonlyGuard(); if (blocked) return blocked;
    const timestampCol = {
      formation_started: 'formation_started_at',
      dso_submitted: 'dso_submitted_at',
      dso_approved: 'dso_approved_at',
      active: 'activated_at'
    }[status];

    let sql = `UPDATE communities SET status = $2, updated_at = NOW()`;
    if (timestampCol) sql += `, ${timestampCol} = NOW()`;
    sql += ` WHERE community_id = $1 RETURNING *`;

    const result = await query(sql, [community_id, status]);
    return txt(result.rows[0] || { error: 'Not found' });
  }
);

server.tool(
  'trigger_email',
  'Schedule an email to a building. Confirm with user before executing.',
  {
    building_id: z.string().describe('Building ID'),
    template_key: z.string().describe('Email template key'),
    send_at: z.string().optional().describe('ISO timestamp, defaults to now')
  },
  async ({ building_id, template_key, send_at }) => {
    const blocked = readonlyGuard(); if (blocked) return blocked;
    const bRes = await query(`SELECT email FROM buildings WHERE building_id = $1`, [building_id]);
    if (bRes.rows.length === 0) return txt({ error: 'Building not found' });

    const result = await query(
      `INSERT INTO scheduled_emails (building_id, email, template_key, send_at, status, created_at)
       VALUES ($1, $2, $3, $4, 'pending', NOW()) RETURNING *`,
      [building_id, bRes.rows[0].email, template_key, send_at || new Date().toISOString()]
    );
    return txt(result.rows[0]);
  }
);

server.tool(
  'update_formation_step',
  'Update community member status (invited/confirmed/rejected). Confirm with user before executing.',
  {
    community_id: z.string().describe('Community ID'),
    building_id: z.string().describe('Building ID'),
    status: z.string().describe('invited, confirmed, rejected')
  },
  async ({ community_id, building_id, status }) => {
    const blocked = readonlyGuard(); if (blocked) return blocked;
    let sql = `UPDATE community_members SET status = $3`;
    if (status === 'confirmed') sql += `, confirmed_at = NOW()`;
    sql += ` WHERE community_id = $1 AND building_id = $2 RETURNING *`;

    const result = await query(sql, [community_id, building_id, status]);
    return txt(result.rows[0] || { error: 'Not found' });
  }
);

server.tool(
  'add_community_member',
  'Add a building to a community. Confirm with user before executing.',
  {
    community_id: z.string().describe('Community ID'),
    building_id: z.string().describe('Building ID to add'),
    role: z.string().default('member').describe('member or admin'),
    invited_by: z.string().optional().describe('Building ID of inviter')
  },
  async ({ community_id, building_id, role, invited_by }) => {
    const blocked = readonlyGuard(); if (blocked) return blocked;
    const result = await query(
      `INSERT INTO community_members (community_id, building_id, role, status, invited_by, joined_at)
       VALUES ($1, $2, $3, 'invited', $4, NOW())
       ON CONFLICT (community_id, building_id) DO NOTHING
       RETURNING *`,
      [community_id, building_id, role, invited_by || null]
    );
    if (result.rows.length === 0) return txt({ error: 'Already a member' });
    return txt(result.rows[0]);
  }
);

server.tool(
  'update_consent',
  'Update consent flags for a building. Confirm with user before executing.',
  {
    building_id: z.string().describe('Building ID'),
    share_with_neighbors: z.boolean().optional(),
    share_with_utility: z.boolean().optional(),
    updates_opt_in: z.boolean().optional()
  },
  async ({ building_id, ...fields }) => {
    const blocked = readonlyGuard(); if (blocked) return blocked;
    const entries = Object.entries(fields).filter(([, v]) => v !== undefined);
    if (entries.length === 0) return txt({ error: 'No fields to update' });

    const sets = entries.map(([k], i) => `${k} = $${i + 2}`);
    const params = [building_id, ...entries.map(([, v]) => v)];

    const result = await query(
      `UPDATE consents SET ${sets.join(', ')} WHERE building_id = $1 RETURNING *`,
      params
    );
    return txt(result.rows[0] || { error: 'Not found' });
  }
);

// ============================================================
// Tenant Management Tools
// ============================================================

server.tool(
  'list_tenants',
  'List all tenant configs (active and inactive) from white_label_configs.',
  { active_only: z.boolean().default(true).describe('Only show active tenants') },
  async ({ active_only }) => {
    let sql = `SELECT id, territory, utility_name, primary_color, contact_email, active, config, created_at, updated_at FROM white_label_configs`;
    if (active_only) sql += ` WHERE active = TRUE`;
    sql += ` ORDER BY territory`;
    const result = await query(sql);
    return txt(result.rows);
  }
);

server.tool(
  'get_tenant',
  'Get full tenant config by territory slug.',
  { territory: z.string().describe('Territory slug (e.g. "baden", "schaffhausen")') },
  async ({ territory }) => {
    const result = await query(
      `SELECT * FROM white_label_configs WHERE territory = $1`, [territory]
    );
    if (result.rows.length === 0) return txt({ error: 'Tenant not found' });
    return txt(result.rows[0]);
  }
);

server.tool(
  'upsert_tenant',
  'Create or update a tenant/city config. Confirm with user before executing. Config JSONB stores: city_name, kanton, kanton_code, platform_name, brand_prefix, map_center_lat, map_center_lon, map_zoom, map_bounds_sw, map_bounds_ne, plz_ranges, solar_kwh_per_kwp, site_url, ga4_id.',
  {
    territory: z.string().describe('Territory slug (lowercase, no spaces, e.g. "schaffhausen")'),
    utility_name: z.string().describe('Local utility/VNB name'),
    primary_color: z.string().default('#c7021a').describe('Hex color'),
    secondary_color: z.string().default('#f59e0b').describe('Hex color'),
    contact_email: z.string().default('').describe('Contact email'),
    legal_entity: z.string().default('').describe('Legal entity name'),
    dso_contact: z.string().default('').describe('DSO contact name'),
    active: z.boolean().default(true),
    config: z.object({
      city_name: z.string().describe('Display name of the city'),
      kanton: z.string().describe('Canton name'),
      kanton_code: z.string().describe('Canton code (2 letters)'),
      platform_name: z.string().describe('Platform name (e.g. "SchaffhausenLEG")'),
      brand_prefix: z.string().describe('Brand prefix'),
      map_center_lat: z.number().describe('Map center latitude'),
      map_center_lon: z.number().describe('Map center longitude'),
      map_zoom: z.number().default(14).describe('Map zoom level'),
      plz_ranges: z.array(z.array(z.number())).describe('PLZ ranges [[from, to], ...]'),
      solar_kwh_per_kwp: z.number().default(950).describe('Regional solar yield kWh/kWp')
    }).describe('JSONB config object')
  },
  async ({ territory, config: configObj, ...fields }) => {
    const blocked = readonlyGuard(); if (blocked) return blocked;
    const result = await query(
      `INSERT INTO white_label_configs (territory, utility_name, primary_color, secondary_color, contact_email, legal_entity, dso_contact, active, config, created_at, updated_at)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW(), NOW())
       ON CONFLICT (territory) DO UPDATE SET
         utility_name = EXCLUDED.utility_name,
         primary_color = EXCLUDED.primary_color,
         secondary_color = EXCLUDED.secondary_color,
         contact_email = EXCLUDED.contact_email,
         legal_entity = EXCLUDED.legal_entity,
         dso_contact = EXCLUDED.dso_contact,
         active = EXCLUDED.active,
         config = EXCLUDED.config,
         updated_at = NOW()
       RETURNING *`,
      [territory, fields.utility_name, fields.primary_color, fields.secondary_color,
       fields.contact_email, fields.legal_entity, fields.dso_contact, fields.active,
       JSON.stringify(configObj)]
    );
    return txt(result.rows[0]);
  }
);

server.tool(
  'get_tenant_stats',
  'Get registration stats per tenant/city_id.',
  {},
  async () => {
    const result = await query(`
      SELECT city_id, COUNT(*) as total, COUNT(*) FILTER (WHERE verified = true) as verified
      FROM buildings
      GROUP BY city_id
      ORDER BY total DESC
    `);
    return txt(result.rows);
  }
);

server.tool(
  'research_vnb',
  'Research a Swiss VNB/utility: search web for their LEG offerings, check if on LEGHub, find PLZ coverage. Use this to evaluate expansion targets.',
  {
    utility_name: z.string().describe('Name of the utility/VNB to research'),
    municipality: z.string().optional().describe('Municipality name for context')
  },
  async ({ utility_name, municipality }) => {
    if (!BRAVE_API_KEY) return txt({ error: 'BRAVE_API_KEY not configured' });
    const searches = [
      `"${utility_name}" LEG Lokale Elektrizitätsgemeinschaft`,
      `"${utility_name}" leghub.ch`,
      municipality ? `"${utility_name}" ${municipality} PLZ Versorgungsgebiet` : null
    ].filter(Boolean);

    const results = {};
    for (const q of searches) {
      const params = new URLSearchParams({ q, count: 5 });
      try {
        const res = await fetch(`https://api.search.brave.com/res/v1/web/search?${params}`, {
          headers: { 'Accept': 'application/json', 'X-Subscription-Token': BRAVE_API_KEY }
        });
        if (res.ok) {
          const data = await res.json();
          results[q] = (data.web?.results || []).map(r => ({ title: r.title, url: r.url, description: r.description }));
        }
      } catch (e) {
        results[q] = { error: e.message };
      }
    }
    return txt(results);
  }
);

// ============================================================
// Web Search
// ============================================================

server.tool(
  'search_web',
  'Search the web via Brave Search API. Use for regulations, energy law, DSO info, etc.',
  {
    query: z.string().describe('Search query'),
    count: z.number().default(5).describe('Number of results (1-10)')
  },
  async ({ query: q, count }) => {
    if (!BRAVE_API_KEY) return { content: [{ type: 'text', text: 'BRAVE_API_KEY not configured' }] };
    const params = new URLSearchParams({ q, count: Math.min(Math.max(count || 5, 1), 10) });
    const res = await fetch(`https://api.search.brave.com/res/v1/web/search?${params}`, {
      headers: { 'Accept': 'application/json', 'X-Subscription-Token': BRAVE_API_KEY }
    });
    if (!res.ok) throw new Error(`Brave API ${res.status}: ${await res.text()}`);
    const data = await res.json();
    const results = (data.web?.results || []).map(r => ({ title: r.title, url: r.url, description: r.description }));
    return txt(results);
  }
);

// ============================================================
// Public Data Tools
// ============================================================

server.tool(
  'fetch_elcom_tariffs',
  'Fetch ElCom electricity tariffs for a municipality from LINDAS SPARQL endpoint. Caches results in elcom_tariffs table.',
  {
    bfs_number: z.number().describe('BFS municipality number (e.g. 261 for Dietikon)'),
    year: z.number().default(2026).describe('Tariff year')
  },
  async ({ bfs_number, year }) => {
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
      } ORDER BY ?operator ?category
    `;
    try {
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

      // Cache in DB
      for (const t of tariffs) {
        await query(
          `INSERT INTO elcom_tariffs (bfs_number, operator_name, year, category, total_rp_kwh, energy_rp_kwh, grid_rp_kwh, municipality_fee_rp_kwh, kev_rp_kwh)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
           ON CONFLICT (bfs_number, operator_name, year, category) DO UPDATE SET
             total_rp_kwh = EXCLUDED.total_rp_kwh, energy_rp_kwh = EXCLUDED.energy_rp_kwh,
             grid_rp_kwh = EXCLUDED.grid_rp_kwh, municipality_fee_rp_kwh = EXCLUDED.municipality_fee_rp_kwh,
             kev_rp_kwh = EXCLUDED.kev_rp_kwh, fetched_at = NOW()`,
          [t.bfs_number, t.operator_name, t.year, t.category, t.total_rp_kwh, t.energy_rp_kwh, t.grid_rp_kwh, t.municipality_fee_rp_kwh, t.kev_rp_kwh]
        );
      }
      return txt({ bfs_number, year, tariffs_count: tariffs.length, tariffs });
    } catch (e) {
      return txt({ error: e.message, bfs_number, year });
    }
  }
);

server.tool(
  'fetch_energie_reporter',
  'Download Energie Reporter CSV from opendata.swiss. Parse per-municipality energy transition data. Upsert into municipality_profiles.',
  {
    kanton: z.string().default('ZH').describe('Canton code to filter')
  },
  async ({ kanton }) => {
    try {
      const metaRes = await fetch('https://opendata.swiss/api/3/action/package_show?id=energie-reporter');
      const meta = await metaRes.json();
      const csvResource = meta.result?.resources?.find(r => r.format?.toUpperCase() === 'CSV');
      if (!csvResource) return txt({ error: 'No CSV resource found' });

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

        await query(
          `INSERT INTO municipality_profiles (bfs_number, name, kanton, solar_potential_pct, ev_share_pct, renewable_heating_pct, electricity_consumption_mwh, renewable_production_mwh, data_sources)
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
           JSON.stringify({ energie_reporter: true, fetched_at: new Date().toISOString() })]
        );
        count++;
      }
      return txt({ kanton, municipalities_upserted: count });
    } catch (e) {
      return txt({ error: e.message });
    }
  }
);

server.tool(
  'fetch_sonnendach_data',
  'Fetch solar potential from BFE Sonnendach (opendata.swiss) for municipalities. Cache in sonnendach_municipal table.',
  {
    bfs_number: z.number().optional().describe('Specific BFS number, or omit for all')
  },
  async ({ bfs_number }) => {
    try {
      const metaRes = await fetch('https://opendata.swiss/api/3/action/package_show?id=sonnendach-ch');
      const meta = await metaRes.json();
      const csvResource = meta.result?.resources?.find(r =>
        r.format?.toUpperCase() === 'CSV' && (r.name?.toLowerCase().includes('gemeinde') || r.name?.toLowerCase().includes('municipal'))
      ) || meta.result?.resources?.find(r => r.format?.toUpperCase() === 'CSV');

      if (!csvResource) return txt({ error: 'No CSV resource found' });

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
        if (bfs_number && bfs !== bfs_number) continue;

        await query(
          `INSERT INTO sonnendach_municipal (bfs_number, total_roof_area_m2, suitable_roof_area_m2, potential_kwh_year, potential_kwp, utilization_pct)
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
           parseFloat(row.auslastung_pct || 0) || null]
        );
        count++;
      }
      return txt({ municipalities_upserted: count, bfs_number: bfs_number || 'all' });
    } catch (e) {
      return txt({ error: e.message });
    }
  }
);

server.tool(
  'scan_vnb_leg_offerings',
  'Search web for a VNB/utility LEG product pages. Extract: offers_leg, pricing_model, platform, partnership.',
  {
    utility_name: z.string().describe('Name of the VNB/utility'),
  },
  async ({ utility_name }) => {
    if (!BRAVE_API_KEY) return txt({ error: 'BRAVE_API_KEY not configured' });
    const searches = [
      `"${utility_name}" LEG Lokale Elektrizitätsgemeinschaft Angebot`,
      `"${utility_name}" GemeinsamStrom OR LEGhub`,
      `"${utility_name}" Netzgebühren LEG Reduktion`
    ];

    const results = {};
    for (const q of searches) {
      try {
        const res = await fetch(`https://api.search.brave.com/res/v1/web/search?${new URLSearchParams({ q, count: 5 })}`, {
          headers: { 'Accept': 'application/json', 'X-Subscription-Token': BRAVE_API_KEY }
        });
        if (res.ok) {
          const data = await res.json();
          results[q] = (data.web?.results || []).map(r => ({ title: r.title, url: r.url, description: r.description }));
        }
      } catch (e) {
        results[q] = { error: e.message };
      }
    }
    return txt({ utility_name, searches: results });
  }
);

server.tool(
  'monitor_leghub_partners',
  'Scrape leghub.ch partner list. Compare with previous scan to detect new/removed partners.',
  {},
  async () => {
    if (!BRAVE_API_KEY) return txt({ error: 'BRAVE_API_KEY not configured' });
    try {
      const res = await fetch(`https://api.search.brave.com/res/v1/web/search?${new URLSearchParams({ q: 'site:leghub.ch partner', count: 10 })}`, {
        headers: { 'Accept': 'application/json', 'X-Subscription-Token': BRAVE_API_KEY }
      });
      if (!res.ok) throw new Error(`Brave API ${res.status}`);
      const data = await res.json();
      const partners = (data.web?.results || []).map(r => ({ title: r.title, url: r.url, description: r.description }));

      // Check previous scan
      const prevRes = await query(`SELECT data FROM insights_cache WHERE insight_type = 'leghub_partners' ORDER BY computed_at DESC LIMIT 1`);
      const previous = prevRes.rows[0]?.data?.partners || [];

      const prevUrls = new Set(previous.map(p => p.url));
      const newPartners = partners.filter(p => !prevUrls.has(p.url));

      // Save current scan
      await query(
        `INSERT INTO insights_cache (insight_type, scope, period, data, expires_at)
         VALUES ('leghub_partners', 'CH', 'current', $1, NOW() + INTERVAL '30 days')
         ON CONFLICT (insight_type, scope, period) DO UPDATE SET data = EXCLUDED.data, computed_at = NOW(), expires_at = EXCLUDED.expires_at`,
        [JSON.stringify({ partners, scanned_at: new Date().toISOString() })]
      );

      return txt({ total_partners: partners.length, new_since_last_scan: newPartners.length, new_partners: newPartners, all_partners: partners });
    } catch (e) {
      return txt({ error: e.message });
    }
  }
);

server.tool(
  'refresh_municipality_data',
  'Orchestrate all fetches for a municipality: ElCom tariffs, compute scores, update profile. End-to-end refresh.',
  {
    bfs_number: z.number().describe('BFS municipality number')
  },
  async ({ bfs_number }) => {
    const result = { bfs_number, steps: {} };

    // 1. Fetch ElCom tariffs
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
        }
      `;
      const sparqlRes = await fetch('https://lindas.admin.ch/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/sparql-results+json' },
        body: `query=${encodeURIComponent(sparql)}`
      });
      const sparqlData = await sparqlRes.json();
      const bindings = sparqlData.results?.bindings || [];
      result.steps.elcom = { tariffs: bindings.length };

      // Cache tariffs
      for (const b of bindings) {
        await query(
          `INSERT INTO elcom_tariffs (bfs_number, operator_name, year, category, total_rp_kwh, energy_rp_kwh, grid_rp_kwh, municipality_fee_rp_kwh, kev_rp_kwh)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
           ON CONFLICT (bfs_number, operator_name, year, category) DO UPDATE SET
             total_rp_kwh = EXCLUDED.total_rp_kwh, energy_rp_kwh = EXCLUDED.energy_rp_kwh,
             grid_rp_kwh = EXCLUDED.grid_rp_kwh, municipality_fee_rp_kwh = EXCLUDED.municipality_fee_rp_kwh,
             kev_rp_kwh = EXCLUDED.kev_rp_kwh, fetched_at = NOW()`,
          [bfs_number, b.operator?.value || '', 2026, b.category?.value || '',
           parseFloat(b.total?.value || 0), parseFloat(b.energy?.value || 0),
           parseFloat(b.grid?.value || 0), parseFloat(b.municipality_fee?.value || 0),
           parseFloat(b.kev?.value || 0)]
        );
      }

      // 2. Compute value gap from H4 tariff
      const h4 = bindings.find(b => (b.category?.value || '').startsWith('H4'));
      if (h4) {
        const gridFee = parseFloat(h4.grid?.value || 0);
        const savingsRp = gridFee * 0.4;
        const annualSavings = savingsRp * 4500 / 100;
        result.steps.value_gap = { grid_fee_rp: gridFee, annual_savings_chf: Math.round(annualSavings * 100) / 100 };

        // 3. Update profile with value gap and score
        const profileRes = await query(`SELECT * FROM municipality_profiles WHERE bfs_number = $1`, [bfs_number]);
        const existing = profileRes.rows[0] || {};

        const solar = parseFloat(existing.solar_potential_pct || 0) / 100;
        const ev = Math.min(parseFloat(existing.ev_share_pct || 0), 30) / 30;
        const heating = parseFloat(existing.renewable_heating_pct || 0) / 100;
        const consumption = parseFloat(existing.electricity_consumption_mwh || 0);
        const production = parseFloat(existing.renewable_production_mwh || 0);
        const prodRatio = consumption > 0 ? Math.min(production / consumption, 1) : 0;
        const score = Math.round((solar * 30 + ev * 20 + heating * 25 + prodRatio * 25) * 10) / 10;

        await query(
          `UPDATE municipality_profiles SET leg_value_gap_chf = $2, energy_transition_score = $3,
           data_sources = data_sources || $4, updated_at = NOW() WHERE bfs_number = $1`,
          [bfs_number, annualSavings, score,
           JSON.stringify({ elcom: true, last_refresh: new Date().toISOString() })]
        );
        result.steps.profile = { score, value_gap_chf: annualSavings };
      }
    } catch (e) {
      result.steps.error = e.message;
    }

    return txt(result);
  }
);

// ============================================================
// Sales Pipeline Tools
// ============================================================

server.tool(
  'get_vnb_pipeline',
  'Get VNB sales pipeline entries. Optional status filter.',
  {
    status: z.string().optional().describe('Filter: lead, contacted, demo, trial, paid, churned'),
    limit: z.number().default(50).describe('Max results')
  },
  async ({ status, limit }) => {
    let sql = 'SELECT * FROM vnb_pipeline';
    const params = [];
    if (status) {
      sql += ' WHERE status = $1';
      params.push(status);
    }
    sql += ' ORDER BY score DESC NULLS LAST LIMIT $' + (params.length + 1);
    params.push(limit);
    const res = await query(sql, params);
    return txt(res.rows);
  }
);

server.tool(
  'update_vnb_status',
  'Update VNB pipeline entry status and notes.',
  {
    vnb_id: z.number().describe('Pipeline entry ID'),
    status: z.string().describe('New status: lead, contacted, demo, trial, paid, churned'),
    notes: z.string().optional().describe('Notes about the status change')
  },
  async ({ vnb_id, status, notes }) => {
    const guard = readonlyGuard();
    if (guard) return guard;
    const validStages = ['lead', 'contacted', 'demo', 'trial', 'paid', 'churned'];
    if (!validStages.includes(status)) return txt({ error: 'Invalid status' });
    const res = await query(
      `UPDATE vnb_pipeline SET status = $2, notes = COALESCE($3, notes), updated_at = NOW() WHERE id = $1 RETURNING *`,
      [vnb_id, status, notes || null]
    );
    return txt(res.rows[0] || { error: 'Not found' });
  }
);

server.tool(
  'add_vnb_lead',
  'Add a new VNB to the sales pipeline.',
  {
    vnb_name: z.string().describe('Name of the VNB/utility'),
    municipality: z.string().optional().describe('Primary municipality'),
    bfs_number: z.number().optional().describe('BFS municipality number'),
    population: z.number().optional().describe('Population served'),
    score: z.number().optional().describe('Lead score 0-100'),
    notes: z.string().optional().describe('Research notes')
  },
  async ({ vnb_name, municipality, bfs_number, population, score, notes }) => {
    const guard = readonlyGuard();
    if (guard) return guard;
    const res = await query(
      `INSERT INTO vnb_pipeline (vnb_name, municipality, bfs_number, population, score, notes, status, created_at, updated_at)
       VALUES ($1, $2, $3, $4, $5, $6, 'lead', NOW(), NOW()) RETURNING *`,
      [vnb_name, municipality || null, bfs_number || null, population || null, score || null, notes || null]
    );
    return txt(res.rows[0]);
  }
);

server.tool(
  'get_pipeline_dashboard',
  'Get pipeline funnel metrics: counts per stage, avg score, conversion rate.',
  {},
  async () => {
    const res = await query(`
      SELECT status, COUNT(*)::int as count, ROUND(AVG(score)::numeric, 1) as avg_score
      FROM vnb_pipeline GROUP BY status ORDER BY
        CASE status WHEN 'lead' THEN 1 WHEN 'contacted' THEN 2 WHEN 'demo' THEN 3
        WHEN 'trial' THEN 4 WHEN 'paid' THEN 5 WHEN 'churned' THEN 6 END
    `);
    const total = res.rows.reduce((s, r) => s + r.count, 0);
    const paid = res.rows.find(r => r.status === 'paid')?.count || 0;
    return txt({
      funnel: res.rows,
      total,
      conversion_rate: total > 0 ? Math.round(paid / total * 1000) / 10 : 0
    });
  }
);

// ============================================================
// LEG Document & Billing Tools
// ============================================================

server.tool(
  'generate_leg_document',
  'Generate a LEG document (Gründungsvertrag, Reglement, Tarifblatt) for a community. Calls Flask endpoint.',
  {
    community_id: z.string().describe('Community UUID'),
    doc_type: z.enum(['gruendungsvertrag', 'reglement', 'tarifblatt']).describe('Document type')
  },
  async ({ community_id, doc_type }) => {
    const blocked = readonlyGuard(); if (blocked) return blocked;
    try {
      const flaskUrl = process.env.FLASK_URL || 'http://flask:5000';
      const res = await fetch(`${flaskUrl}/api/formation/generate-document`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ community_id, doc_type })
      });
      const data = await res.json();
      return txt(data);
    } catch (e) {
      return txt({ error: e.message });
    }
  }
);

server.tool(
  'list_documents',
  'List all LEG documents for a community from leg_documents table.',
  {
    community_id: z.string().describe('Community UUID')
  },
  async ({ community_id }) => {
    const result = await query(
      `SELECT id, community_id, doc_type, status, file_path, created_at, signed_at
       FROM leg_documents WHERE community_id = $1 ORDER BY created_at DESC`,
      [community_id]
    );
    return txt(result.rows);
  }
);

server.tool(
  'run_billing_period',
  'Query billing periods for a community within a date range.',
  {
    community_id: z.string().describe('Community UUID'),
    start_date: z.string().describe('Start date (YYYY-MM-DD)'),
    end_date: z.string().describe('End date (YYYY-MM-DD)')
  },
  async ({ community_id, start_date, end_date }) => {
    const result = await query(
      `SELECT id, community_id, period_start, period_end, status, total_consumption_kwh,
              total_production_kwh, self_consumption_kwh, grid_import_kwh, created_at
       FROM billing_periods
       WHERE community_id = $1 AND period_start >= $2 AND period_end <= $3
       ORDER BY period_start`,
      [community_id, start_date, end_date]
    );
    return txt(result.rows);
  }
);

server.tool(
  'get_billing_summary',
  'Get billing line items summary for a community in a given month.',
  {
    community_id: z.string().describe('Community UUID'),
    month: z.string().describe('Month (YYYY-MM)')
  },
  async ({ community_id, month }) => {
    const result = await query(
      `SELECT bli.building_id, bli.consumption_kwh, bli.production_kwh,
              bli.self_supply_kwh, bli.grid_import_kwh, bli.amount_chf,
              bli.self_supply_ratio, bp.period_start, bp.period_end
       FROM billing_line_items bli
       JOIN billing_periods bp ON bli.billing_period_id = bp.id
       WHERE bp.community_id = $1 AND to_char(bp.period_start, 'YYYY-MM') = $2
       ORDER BY bli.building_id`,
      [community_id, month]
    );
    const total = result.rows.reduce((acc, r) => ({
      consumption: acc.consumption + parseFloat(r.consumption_kwh || 0),
      production: acc.production + parseFloat(r.production_kwh || 0),
      amount: acc.amount + parseFloat(r.amount_chf || 0),
      count: acc.count + 1
    }), { consumption: 0, production: 0, amount: 0, count: 0 });
    return txt({ month, community_id, line_items: result.rows, totals: total });
  }
);

server.tool(
  'score_vnb',
  'Score a VNB/utility as expansion target. Pure computation, no DB needed.',
  {
    population: z.number().describe('Municipality population'),
    solar_potential_pct: z.number().describe('Solar potential percentage (0-100)'),
    has_leghub: z.boolean().describe('Whether VNB is on LEGHub platform'),
    smart_meter_rollout_pct: z.number().describe('Smart meter rollout percentage (0-100)')
  },
  async ({ population, solar_potential_pct, has_leghub, smart_meter_rollout_pct }) => {
    let score = 0;
    // Population: larger = more potential (max 30pts)
    if (population > 50000) score += 30;
    else if (population > 20000) score += 25;
    else if (population > 10000) score += 20;
    else if (population > 5000) score += 15;
    else score += 10;
    // Solar potential (max 25pts)
    score += Math.min(25, Math.round(solar_potential_pct * 0.25));
    // LEGHub readiness (max 20pts)
    score += has_leghub ? 20 : 0;
    // Smart meter rollout (max 25pts)
    score += Math.min(25, Math.round(smart_meter_rollout_pct * 0.25));

    const tier = score >= 80 ? 'hot' : score >= 60 ? 'warm' : score >= 40 ? 'medium' : 'cold';
    return txt({ score, tier, breakdown: { population_pts: score > 80 ? 30 : 'varies', solar_pts: Math.min(25, Math.round(solar_potential_pct * 0.25)), leghub_pts: has_leghub ? 20 : 0, smart_meter_pts: Math.min(25, Math.round(smart_meter_rollout_pct * 0.25)) } });
  }
);

server.tool(
  'draft_outreach',
  'Draft a municipality outreach email informing about free LEG infrastructure. Returns German text.',
  {
    municipality_name: z.string().describe('Name of the Gemeinde'),
    bfs_number: z.number().describe('BFS number of the municipality'),
    value_gap_chf: z.number().describe('Annual savings potential per household in CHF'),
    solar_potential_pct: z.number().describe('Solar potential percentage of suitable roofs')
  },
  async ({ municipality_name, bfs_number, value_gap_chf, solar_potential_pct }) => {
    const profileUrl = `https://openleg.ch/gemeinde/${bfs_number}/profil`;
    const onboardingUrl = `https://openleg.ch/gemeinde/${bfs_number}/onboarding`;
    const email = `Betreff: Kostenlose LEG-Infrastruktur für ${municipality_name}\n\nSehr geehrte Gemeindeverantwortliche\n\nDie Gemeinde ${municipality_name} verfügt über ein hohes Potenzial für Lokale Elektrizitätsgemeinschaften (LEG).\n\nKennzahlen:\n- Solarpotenzial: ${solar_potential_pct}% der Dachflächen geeignet\n- Einsparpotenzial: ca. CHF ${value_gap_chf} pro Haushalt und Jahr\n\nOpenLEG stellt kostenlose, quelloffene Infrastruktur für die Gründung und Verwaltung von LEGs bereit. Kein Datenverkauf, keine Gebühren.\n\nGemeindeprofil: ${profileUrl}\nOnboarding starten: ${onboardingUrl}\n\nFreundliche Grüsse\nOpenLEG\nhallo@openleg.ch`;
    return txt({ email, metadata: { municipality_name, bfs_number, value_gap_chf, solar_potential_pct } });
  }
);

// ============================================================
// Seeding Tools
// ============================================================

server.tool(
  'get_unseeded_municipalities',
  'List municipalities without tenant config, ranked by value gap and solar potential. Use for weekly seeding runs.',
  {
    kanton: z.string().optional().describe('Filter by kanton code (e.g. ZH, BE, AG)'),
    limit: z.number().default(50).describe('Max results')
  },
  async ({ kanton, limit }) => {
    let sql = `
      SELECT mp.bfs_number, mp.name, mp.kanton, mp.solar_potential_pct, mp.leg_value_gap_chf
      FROM municipality_profiles mp
      LEFT JOIN white_label_configs wlc ON LOWER(mp.name) = wlc.territory
      WHERE wlc.territory IS NULL
    `;
    const params = [];
    if (kanton) {
      params.push(kanton);
      sql += ` AND mp.kanton = $${params.length}`;
    }
    sql += ` ORDER BY mp.leg_value_gap_chf DESC NULLS LAST, mp.solar_potential_pct DESC NULLS LAST`;
    params.push(limit);
    sql += ` LIMIT $${params.length}`;
    const result = await query(sql, params);
    return txt({ count: result.rowCount, municipalities: result.rows });
  }
);

server.tool(
  'get_all_swiss_municipalities',
  'Query LINDAS SPARQL endpoint for all Swiss municipalities. Returns BFS number, name, kanton, population.',
  {
    kanton: z.string().optional().describe('Filter by kanton URI suffix (e.g. ZH, BE)')
  },
  async ({ kanton }) => {
    const kantonFilter = kanton ? `FILTER(STRENDS(STR(?canton), "${kanton}"))` : '';
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
      }
      ORDER BY ?name
    `;
    try {
      const resp = await fetch('https://lindas.admin.ch/query', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
          'Accept': 'application/sparql-results+json'
        },
        body: `query=${encodeURIComponent(sparqlQuery)}`
      });
      if (!resp.ok) return txt({ error: `SPARQL query failed: ${resp.status}` });
      const data = await resp.json();
      const rows = (data.results?.bindings || []).map(b => ({
        bfs_number: parseInt(b.bfs?.value || '0'),
        name: b.name?.value || '',
        kanton: (b.canton?.value || '').split('/').pop(),
        population: parseInt(b.population?.value || '0')
      }));
      return txt({ count: rows.length, municipalities: rows });
    } catch (e) {
      return txt({ error: `SPARQL fetch error: ${e.message}` });
    }
  }
);

// ============================================================
// Formation Monitoring Tools
// ============================================================

server.tool(
  'get_stuck_formations',
  'Find communities stuck at the same status for N days. Returns community details with admin contact for nudging.',
  {
    days_threshold: z.number().default(7).describe('Minimum days stuck at same status')
  },
  async ({ days_threshold }) => {
    const result = await query(`
      SELECT c.community_id, c.name, c.status,
        EXTRACT(DAY FROM NOW() - c.updated_at)::int as days_stuck,
        b.email as admin_email
      FROM communities c
      JOIN buildings b ON c.admin_building_id = b.building_id
      WHERE c.status NOT IN ('active', 'rejected')
      AND c.updated_at < NOW() - make_interval(days => $1)
      ORDER BY c.updated_at ASC
    `, [days_threshold]);
    return txt({ count: result.rowCount, stuck_formations: result.rows });
  }
);

server.tool(
  'get_outreach_candidates',
  'Find seeded municipalities with no registrations and no contact email. Candidates for outreach.',
  {
    limit: z.number().default(50).describe('Max results')
  },
  async ({ limit }) => {
    const result = await query(`
      SELECT wlc.territory, wlc.config->>'city_name' as city_name, wlc.config->>'kanton' as kanton
      FROM white_label_configs wlc
      LEFT JOIN buildings b ON b.city_id = wlc.territory
      WHERE wlc.active = true
      GROUP BY wlc.id, wlc.territory, wlc.config
      HAVING COUNT(b.building_id) = 0
      AND (wlc.contact_email IS NULL OR wlc.contact_email = '')
      ORDER BY wlc.created_at ASC
      LIMIT $1
    `, [limit]);
    return txt({ count: result.rowCount, candidates: result.rows });
  }
);

// Start server
const transport = new StdioServerTransport();
await server.connect(transport);
