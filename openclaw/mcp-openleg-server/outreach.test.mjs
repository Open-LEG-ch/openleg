import { describe, it, expect } from 'vitest';
import { buildOutreachBrief } from './outreach.mjs';

describe('buildOutreachBrief', () => {
  const municipalityRow = {
    bfs_number: 4021,
    name: 'Baden',
    kanton: 'AG',
    population: 19200,
    solar_potential_pct: 42.5,
    ev_share_pct: 8.3,
    renewable_heating_pct: 22.1,
    energy_transition_score: 67,
    leg_value_gap_chf: 185
  };

  const tariffRow = {
    total_rp_kwh: 27.3,
    grid_rp_kwh: 9.8,
    operator_name: 'Regionalwerke AG Baden'
  };

  const cantonalStats = {
    avg_tariff_rp_kwh: 25.1,
    rank: 12,
    total_in_canton: 213
  };

  it('handles missing tariff data gracefully', () => {
    const result = buildOutreachBrief(municipalityRow, null, cantonalStats);

    expect(result.brief.tariff_total_rp_kwh).toBeUndefined();
    expect(result.brief.operator_name).toBeUndefined();
    expect(result.brief.above_cantonal_avg).toBeNull();
    expect(result.brief.municipality_name).toBe('Baden');
  });

  it('handles missing municipality profile gracefully', () => {
    const result = buildOutreachBrief(null, tariffRow, cantonalStats);

    expect(result.brief.municipality_name).toBeUndefined();
    expect(result.brief.tariff_total_rp_kwh).toBe(27.3);
    expect(result.urls.profile).toContain('undefined');
  });

  it('includes feedback when provided', () => {
    const feedback = 'Zu formal. Mehr wie ein Mensch schreiben.';
    const result = buildOutreachBrief(municipalityRow, tariffRow, cantonalStats, feedback);

    expect(result.feedback).toBe(feedback);
  });

  it('returns empty feedback when none provided', () => {
    const result = buildOutreachBrief(municipalityRow, tariffRow, cantonalStats);

    expect(result.feedback).toBe('');
  });

  it('returns non-empty style guide', () => {
    const result = buildOutreachBrief(municipalityRow, tariffRow, cantonalStats);

    expect(result.style_guide).toBeTruthy();
    expect(result.style_guide).toContain('Schweizer Hochdeutsch');
    expect(result.style_guide).toContain('5 Sätze');
  });

  it('returns correct URLs with BFS number', () => {
    const result = buildOutreachBrief(municipalityRow, tariffRow, cantonalStats);

    expect(result.urls.profile).toBe('https://openleg.ch/gemeinde/profil/4021');
    expect(result.urls.onboarding).toBe('https://openleg.ch/gemeinde/onboarding');
  });

  it('includes cantonal average tariff and rank', () => {
    const result = buildOutreachBrief(municipalityRow, tariffRow, cantonalStats);

    expect(result.brief.cantonal_avg_tariff_rp_kwh).toBe(25.1);
    expect(result.brief.cantonal_rank).toBe('12/213');
    expect(result.brief.above_cantonal_avg).toBe(true);
  });

  it('returns tariff data and operator name from elcom row', () => {
    const result = buildOutreachBrief(municipalityRow, tariffRow, cantonalStats);

    expect(result.brief.tariff_total_rp_kwh).toBe(27.3);
    expect(result.brief.grid_rp_kwh).toBe(9.8);
    expect(result.brief.operator_name).toBe('Regionalwerke AG Baden');
  });

  it('returns brief with all municipality data fields', () => {
    const result = buildOutreachBrief(municipalityRow, tariffRow, cantonalStats);

    expect(result.brief.municipality_name).toBe('Baden');
    expect(result.brief.bfs_number).toBe(4021);
    expect(result.brief.kanton).toBe('AG');
    expect(result.brief.population).toBe(19200);
    expect(result.brief.solar_potential_pct).toBe(42.5);
    expect(result.brief.ev_share_pct).toBe(8.3);
    expect(result.brief.renewable_heating_pct).toBe(22.1);
    expect(result.brief.energy_transition_score).toBe(67);
    expect(result.brief.leg_value_gap_chf).toBe(185);
  });
});
