# Solar telemetry and owner security tools

Research date: 2026-09-16. This is a product feasibility assessment, not a device certification or a production security audit. No customer systems were accessed.

## Recommendation

Build an opt-in, read-only telemetry path alongside validated VNB metering. Start with a documented local inverter API and a narrowly scoped bridge for existing meter readers. Show production, import/export, data age and device-reported faults. Add owner security records and sourced advisories without adding remote equipment control.

Local access can reduce dependence on a vendor cloud for monitoring. It does not disable that cloud's existing control channel. A collector that only issues reads is also not a hardware-enforced security boundary: a compromised collector may reach writable device interfaces. Network isolation and endpoint restrictions remain necessary.

## Current OpenLEG boundary

- `sdat_datahub.py` retrieves files; `sdat_e66.py` and `store/metering.py` ingest validated meter readings. `billing_readings.py` rejects gaps before billing.
- `docs/data-pipeline.md` identifies E66 readings as billing input. Fast device telemetry must have its own storage and provenance and must not silently replace those readings.
- `docs/self-host-appliance.md` already proposes P1/MUC, MQTT and Home Assistant connectors. `docs/private-network.md` describes member access; this does not establish isolation from household equipment.
- #600 already covers scheduled SDAT retrieval and recovery. #555 covers validation of existing E66 anomaly thresholds. #611 covers the operator API. These remain separate work.
- Fine-grained household readings can expose occupancy. Existing neighbour-sharing consent is not sufficient authorization to share live readings. Add explicit telemetry permissions, retention and deletion behavior.

These are observations of the repository code; production behavior was not inspected.

## Access matrix

| Source | Verified access and timing | Security and unknowns | Proposed priority |
|---|---|---|---|
| Fronius Solar API | Official local REST/JSON interface reads inverter power, voltage and current, plus attached Smart Meter, storage and other components. [Fronius overview](https://www.fronius.com/en/help-center/solar-energy/products/monitoring-control/solutions/open-interfaces/fronius-solar-api-json-). Home Assistant polls power flow every 10 seconds and inverter/meter details every minute. This is client behavior, not an inverter SLA. [HA integration](https://www.home-assistant.io/integrations/fronius) | The manufacturer manual says API access is unauthenticated on the local network once enabled; disabled by default on applicable devices. Compatibility and activation vary by model/firmware. Keep access inside the installation network. [Solar API V1 manual](https://www.fronius.com/~/downloads/Solar%20Energy/Operating%20Instructions/42%2C0410%2C2012.pdf) | First inverter pilot, contingent on actual site hardware. |
| SMA local Modbus/SunSpec | Official documented local interface; activation required. SMA describes worst-case interface response times of 5-10 seconds, which is not a freshness guarantee. [SMA interface](https://www.sma.de/en/products/product-features-interfaces/modbus-protocol-interface) | SMA supports read and write functions. A read-only client does not make the device interface read-only. SMA warns against port forwarding and recommends firewall/VPN. Match device-specific registers, firmware and roles. [Function/security guidance](https://manuals.sma.de/SHPxxxUS21/en-US/10413994123.html), [register access documentation](https://files.sma.de/downloads/EDMx-Modbus-TI-en-16.pdf) | Second connector after a device profile and read-function allowlist. |
| Huawei FusionSolar/SmartPVMS cloud | Northbound API access requires administrator authorization. One official device convergence endpoint allows 100 inverters per query, exposes 5-minute data, and permits one call per hour. Another documented device-data endpoint has per-user 5-minute call limits based on device counts. [Convergence API](https://support.huawei.com/enterprise/en/doc/EDOC1100307213/ec40b249/device-convergence-data-interface), [device-data API](https://support.huawei.com/enterprise/mx/doc/EDOC1100306384/2630ae1b/device-data-interfaces) | These are specific SmartPVMS versions and endpoints. Do not generalize their quotas to every FusionSolar account. Establish region, API generation, plant grants, expiry and applicable quota before implementation. Exact current Swiss commercial onboarding and least-privilege grant capabilities remain unverified. | Feasibility spike if pilots have Huawei. No promise of second-level freshness. |
| Huawei local | Huawei publishes SUN2000L Modbus interface definitions for third-party development. Older official SmartLogger instructions document local Modbus TCP and warn it has no authentication and transmits unencrypted data. [SUN2000L release documents](https://support.huawei.cn/enterprise/ru/doc/EDOC1100421492?currentPartNo=k002&togo=content), [SmartLogger manual](https://solar.huawei.com/-/media/Solar/attachment/pdf/eu/service/download/SUN2000%20APP%20User%20Manual%20iOS.pdf) | The older logger document does not establish current SUN2000/SDongle/EMMA compatibility or safe read-only settings. Obtain exact model, dongle, firmware and official current register map. Do not adopt community instructions that enable unrestricted access. | Hardware-specific feasibility spike. |
| Sungrow iSolarCloud | Official developer platform advertises OAuth 2.0, monitoring, MQTT live data and distinct grid-control APIs. It offers basic and paid developer packages; a customer example describes minute-level access. [Sungrow developer portal](https://developer-api.isolarcloud.com/) | Portal content does not establish a generally available latency SLA, exact rate limits, Swiss pricing, device coverage or read-only OAuth scopes. Monitoring and control coexist. Validate grants, revocation, data residency and package terms before coding. | Feasibility spike if pilots have Sungrow. |
| SunSpec Modbus | Common manufacturer-independent information models cover monitoring and control, including export limiting and power-factor settings. [SunSpec](https://sunspec.org/modbus/) | SunSpec is a data model, not a guarantee of read-only access or installed secure transport. Secure SunSpec specifications now exist; hardware support must be verified separately. [Specifications](https://sunspec.org/specifications/) | Shared parser for documented compatible profiles. |
| EKZ meter customer interface | EKZ explicitly offers 10-second consumption/feed-in readings to eligible customers and ZEV/LEG/storage operators. Requires customer application, enabled communicative meter and reader hardware/software. [EKZ FAQ](https://www.ekz.ch/de/angebote/strom/gut-zu-wissen/smart-meter-fragen-und-antworten.html) | Physical access, operator authorization and exact meter/reader compatibility required. Does not expose inverter firmware or vendor cloud permissions. | First meter candidate if pilot is in EKZ area. |
| BKW KS2 | BKW enables KS2 on request; customer supplies compatible additional hardware. [BKW](https://www.bkw.ch/de/strom-in-der-grundversorgung/strom-beziehen/stromkosten-rechnung/stromzaehler-ablesung/smart-meter). OMNIPOWER documentation specifies 10-second encrypted DLMS or unencrypted HAN-P1 push, including power, energy, phase voltage/current. [OMNIPOWER](https://www.bkw.ch/fileadmin/user_upload/03_Energie/03_04_Stromnetz/Smart-Meter/BKW_Smart_Meter_Kamstrup_OMNIPOWER_R__Information_Kundenschnittstelle__KS2_.pdf) | eRS301 differs: unencrypted DSMR-P1 at one-second cadence, event counters/logs, some model-dependent fields. Both documents describe transformer-factor handling. [eRS301](https://www.bkw.ch/fileadmin/user_upload/03_Energie/03_04_Stromnetz/Smart-Meter/BKW_Smart_Meter_Ensor-Semax_eRS301_Information_Kundenschnittstelle__KS2_.pdf). Do not treat Swiss P1/HAN as one universal protocol profile. | First meter candidate if pilot is in BKW area. |
| Existing Home Assistant/MQTT | HA can supply already integrated local data. MQTT supports broker authentication, TLS certificate validation and client certificates. [HA MQTT](https://www.home-assistant.io/integrations/mqtt) | HA Fronius now also supports Modbus inverter/battery controls when enabled. Use a dedicated telemetry export, scoped topics and broker ACLs. Do not give OpenLEG a general HA service-call token or a bridge to command topics. [HA Fronius](https://www.home-assistant.io/integrations/fronius) | Optional adapter to existing equipment, not an unrestricted control bridge. |

## Other data worth using

| Source | Verified access and cadence | Product use and limits |
| --- | --- | --- |
| MeteoSwiss local forecasts | Public STAC downloads; hourly updates, including radiation. Files use UTC and parameter-specific aggregation intervals. | Explain likely solar windows. Treat derived generation as an estimate, not measured output or guaranteed surplus. Cite MeteoSwiss and use permitted icons. |
| ElCom tariffs | Official LINDAS tariff datasets and machine-readable tariffs. | Price context. Published comparison tariffs do not establish a household's actual contract or a live spot price. |
| BFE energy dashboard | Daily national energy overview; open data available. | Optional national context, not a live household reading or a local outage detector. Lower priority than local readings. |
| VNB E66 | Existing authenticated delivery/import path; actual delivery delay depends on VNB. | Billing and retrospective comparison. Scheduling fetches more often cannot make the VNB publish earlier. |

Sources: [MeteoSwiss local forecasts](https://opendatadocs.meteoswiss.ch/e-forecast-data/e4-local-forecast-data), [ElCom tariff data](https://www.elcom.admin.ch/en/tariff-data-and-visualisations), [BFE dashboard](https://www.bfe.admin.ch/en/energy-dashboard), and repository modules above.

## Security and safety tools

1. An owner-controlled equipment record: manufacturer, exact model, firmware, rated AC capacity, optional PV DC capacity, cloud dependency, installer access and evidence dates. Keep serials, addresses and remote endpoints private. Separate declared facts from observations and unknowns.
2. A checklist with recorded completion and review dates: changed default credentials, account MFA where supported, obsolete installer access, firmware support, internet exposure and network isolation. Generate instructions for the actual device; do not claim a universal secure configuration. BACS supports these basic controls in its [device guidance](https://www.bacs.admin.ch/en/device-security).
3. Curated advisory matching by model and affected firmware range, linking to vendor or NTC evidence. Unknown firmware means unknown applicability. An empty advisory list does not certify safety. NTC describes ongoing inverter and EMS investigation in its [PV research announcement](https://en.ntc.swiss/news/2025-photovoltaics); this source does not supply a universal affected-model list.
4. Device fault and missing-data notices with clear uncertainty. Loss of telemetry is not proof of a blackout or cyberattack. Acknowledgement, cooldown and recovery states should prevent alert floods.
5. A private installer handoff containing findings and proposed actions. Cloud disconnection, firmware updates and firewall changes may affect maintenance or required grid services; review those per installation before applying them.
6. A backup-capability record supported by installer evidence. Ordinary PV ownership does not establish backup capability. [Swissolar](https://www.swissolar.ch/de/wissen/anlagenbetrieb/solarstrom-bei-netzausfall) explains the equipment and grid-separation requirements. OpenLEG should not alter grid protection, power limits, battery dispatch or electrical settings.

Security recommendations above are proposed product controls, not claims that OpenLEG already implements them. No public IP scanning, credential guessing, exploit checks, automated firmware rollout or fleet shutdown is proposed.

## Proposed architecture and acceptance boundaries

- An owner-configured local collector polls only documented, allowlisted read endpoints or receives explicitly selected telemetry topics. No general-purpose command channel, arbitrary URL fetcher or device-management proxy.
- Device credentials stay at the installation. A collector may deliver over authenticated TLS to a LEG's chosen host with explicit consent; a local-only mode remains available. No inbound internet access to the inverter is required.
- Give each collector a revocable credential restricted to one installation. Validate payload sizes, rates, units, timestamps, source identity and replay behavior. Restrict collector network access and do not permit network discovery or remote configuration commands through the ingestion credential.
- Keep observation time, receipt time, source cadence, quality and provenance. Preserve unknown values; never turn a communication failure into zero production. Avoid summing duplicate inverter and meter observations.
- Proposed local pilot target: readings within 30 seconds under normal conditions, measured end to end. This is an engineering target, not a vendor promise. Cloud connectors must show their actual cadence.
- Proposed privacy default for implementation review: keep raw telemetry seven days and 15-minute operational aggregates 90 days; allow shorter periods and explicit deletion. These are product defaults, not statutory retention claims. Billing retention remains separate.
- Household details are private by default. Community summaries require permission and coverage labels, including missing installations. A small group aggregate can still identify a household.

## Delivery order

First establish telemetry storage, permissions and a collector boundary. Then prove a local inverter adapter and a meter bridge on authorized pilot hardware. Deliver freshness-aware views and device fault notices. Equipment records and security checklists can proceed independently. Add curated advisories after model identification exists; add weather context after the display distinguishes estimates from observations.

Vendor-cloud connectors and broader Modbus support need a bounded compatibility investigation before an implementation promise. Confirm regional account access, permissions, terms, exact model/firmware support and observed update delays. Keep real installation inventories, credentials and network layouts outside this public repository.

## Open questions

- Which devices and VNB customer interfaces are available from consenting pilot owners?
- Can each vendor account be restricted to data access, and what regional/API approval is required?
- Does enabling a local interface also enable control operations on the same port?
- Which owners need hosted ingestion, and what data-sharing/retention choices will they accept?
- Which advisory sources can be maintained reliably without claiming comprehensive coverage?

Hardware and account verification remain required before describing any connector as supported.

## GitHub follow-up

Parent: [#613](https://github.com/Open-LEG-ch/openleg/issues/613).

- [#614](https://github.com/Open-LEG-ch/openleg/issues/614): telemetry: add isolated ingestion for owner-authorized live readings
- [#615](https://github.com/Open-LEG-ch/openleg/issues/615): connectors: pilot a read-only Fronius Solar API collector
- [#616](https://github.com/Open-LEG-ch/openleg/issues/616): connectors: ingest selected MQTT telemetry from local meter readers
- [#617](https://github.com/Open-LEG-ch/openleg/issues/617): dashboard: show fresh solar readings and actionable device notices
- [#618](https://github.com/Open-LEG-ch/openleg/issues/618): security: add private solar equipment records and hardening checklists
- [#619](https://github.com/Open-LEG-ch/openleg/issues/619): security: match reviewed solar advisories to models and firmware
- [#620](https://github.com/Open-LEG-ch/openleg/issues/620): dashboard: add sourced solar forecast context to measured readings
- [#621](https://github.com/Open-LEG-ch/openleg/issues/621): research: validate Swiss inverter and meter access on authorized pilot hardware
