# Commercial readiness

What separates this project from a product a facility manager pays for, based on how building
analytics and fault detection products work today. Each gap points to a roadmap item; the open
questions at the end need a product decision.

## Market fit

- **Category**: energy management information systems and fault detection and diagnostics
  for buildings. Established products (for example SkySpark, Siemens Navigator, Schneider
  EcoStruxure Resource Advisor) sell rules, dashboards and reports on top of BMS and meter data.
  The opening for a small team is a product that works from meter data alone, explains every
  alarm in money and needs no BMS integration.
- **Buyers**: facility managers of offices, shopping centers, hotels, schools and hospitals;
  energy service companies (ESCOs) that guarantee savings; energy managers that large
  buildings must appoint under the Turkish Energy Efficiency Law (No. 5627).
- **Standards customers ask about**: ISO 50001 (energy management systems), IPMVP for
  measuring savings, ASHRAE Guideline 14 for baseline model accuracy.

## Data access in practice

| Source | Typical resolution | Effort |
|---|---|---|
| Distribution company OSOS (automatic meter reading for large consumers) | hourly or 15 min, next day | export or portal access; fits the CSV import |
| Submeters with a Modbus or M-Bus gateway | 1-15 min | gateway publishes to MQTT; fits the ingest service |
| BMS (BACnet, Modbus) | minutes | integration project; out of scope for a first version |
| Monthly bills | monthly | too coarse for hourly anomalies; useful for onboarding |

Consequences: the import must accept many export formats (done), and two weeks of history
before go-live is realistic for most customers (warm-up already assumes it).

## Gaps to close

### Trust in alarms

- **Alarm budget**: operators stop reading alerts that fire too often. The alarm budget table
  exists; the threshold per building must become a setting agreed with the customer.
- **Feedback loop**: verdicts are stored and used for training; the next step is showing
  precision per building and per rule so false alarms are tuned away.
- **Explanations in Turkish** with the hour, the excess kWh, the cost and a suggested action.

### Money that adds up

- **Time-of-use tariffs**: Turkish commercial customers often pay day, peak and night rates.
  A single TL/kWh per building overstates night waste and understates peak waste.
- **Baseline accuracy**: report CV(RMSE) and NMBE of the expected model per building, the
  ASHRAE Guideline 14 measures customers and ESCOs recognize.
- **Savings verification**: compare confirmed events with the following weeks to show
  realized savings, not only estimated waste.

### Model robustness

- Drift detection for slow increases (weakest scenario today).
- School terms, Ramadan working hours and Bayram periods per building, not only public
  holidays.
- Retraining on customer data with feedback, compared with the current model before switching.
- Monitoring of data gaps and stuck meters per customer, shown before any anomaly result.

### Operations

- Multi-tenant accounts, roles and audit (see [security.md](security.md)).
- Notifications by e-mail, WhatsApp or Teams with a daily digest.
- Weekly and monthly PDF or e-mail reports per building.
- Hosting in Turkey or the EU, backups, monitoring and an uptime target.
- KVKK: data processing agreement, retention period, deletion on request.

## Business model options

| Model | How it is priced | Fits |
|---|---|---|
| Subscription | per building or per meter per month | facility managers, chains |
| Savings share | a share of verified savings | ESCOs, energy performance contracts |
| Partner | white label for meter, BMS or energy companies | distribution and service companies |

## Open questions

1. Which segment is the first target? The list a building can take is in
   `src/data/segments.py` (office, mall, retail, hotel, hospital, education, factory, ...).
2. Where does the first customer data come from (OSOS export, submeter gateway, BMS)?
3. Which pricing model and sales channel (direct, ESCO, partner)?
4. Where is the system hosted (Turkish cloud, EU cloud, on premises)?
5. Which alert channels, and how many alerts per building per week are acceptable?
6. Is a single tariff per building enough, or are time-of-use tariffs needed from the start?
7. Read-only analysis, or will the product ever write set points to a BMS?
8. Who confirms alarms at the customer (facility manager, technician, energy manager)?
9. Are savings reported as estimates, or verified and guaranteed?
10. What is the minimum building size or consumption worth serving?
