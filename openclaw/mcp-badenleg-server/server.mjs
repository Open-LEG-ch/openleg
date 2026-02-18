import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import pg from 'pg';

const { Pool } = pg;

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: { rejectUnauthorized: false },
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
  name: 'badenleg',
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

// Start server
const transport = new StdioServerTransport();
await server.connect(transport);
