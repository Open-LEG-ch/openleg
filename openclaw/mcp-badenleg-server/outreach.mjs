export const OUTREACH_STYLE_GUIDE = `Schreib in Schweizer Hochdeutsch (kein ß, kein Genitiv-s Missbrauch).
Maximal 5 Sätze. Aktive Stimme. Kein Marketing-Deutsch.
Struktur: 1) Einstieg mit konkreter Zahl zur Gemeinde (Tarif, Rang, Ersparnis). 2) Ein Satz Nutzenversprechen. 3) Link zum Gemeindeprofil. 4) Eine offene Frage als Abschluss.
Unterschrift: LEA, OpenLEG / lea@mail.openleg.ch
Vermeide: "Sehr geehrte Damen und Herren", "Wir möchten Sie informieren", "Wir erlauben uns", Aufzählungen mit Bullet Points, Superlative.
Tonfall: sachlich, respektvoll, auf Augenhöhe. Wie eine kompetente Fachperson, nicht wie ein Verkäufer.
Betreff: kurz, mit einer konkreten Zahl (z.B. Tarif oder Ersparnis).`;

/**
 * Pure function: assembles outreach data brief from DB query results.
 * No side effects, no DB access. Testable in isolation.
 */
export function buildOutreachBrief(municipalityRow, tariffRow, cantonalStats, feedback) {
  const m = municipalityRow || {};
  const t = tariffRow || {};
  const c = cantonalStats || {};
  const aboveAvg = (t.total_rp_kwh && c.avg_tariff_rp_kwh)
    ? t.total_rp_kwh > c.avg_tariff_rp_kwh
    : null;
  return {
    brief: {
      municipality_name: m.name,
      bfs_number: m.bfs_number,
      kanton: m.kanton,
      population: m.population,
      solar_potential_pct: m.solar_potential_pct,
      ev_share_pct: m.ev_share_pct,
      renewable_heating_pct: m.renewable_heating_pct,
      energy_transition_score: m.energy_transition_score,
      leg_value_gap_chf: m.leg_value_gap_chf,
      tariff_total_rp_kwh: t.total_rp_kwh,
      grid_rp_kwh: t.grid_rp_kwh,
      operator_name: t.operator_name,
      cantonal_avg_tariff_rp_kwh: c.avg_tariff_rp_kwh,
      cantonal_rank: (c.rank != null && c.total_in_canton != null)
        ? `${c.rank}/${c.total_in_canton}` : null,
      above_cantonal_avg: aboveAvg,
    },
    urls: {
      profile: `https://openleg.ch/gemeinde/profil/${m.bfs_number}`,
      onboarding: 'https://openleg.ch/gemeinde/onboarding',
    },
    style_guide: OUTREACH_STYLE_GUIDE,
    feedback: feedback || ''
  };
}
