# Living Development Status & Handover Log (`UPDATE.md`)

> **Document Purpose**: This living document tracks the active development branch, architectural decisions, completed modules, test results, and integration requirements for **Half 1: Requests and Speed**. It serves as a continuous async handover for my teammate (responsible for **Half 2: Verification and Integrity**) and documents all changes made in response to PR #1 review feedback.

---

## A. Project Overview

* **The Problem**: Social media intermediaries handle high volumes of lawful takedown orders and metadata assistance requests from law enforcement agencies (e.g. prospective Kerala Police cyber units). Measuring true response times, identifying delays, and auditing compliance requires an independent, transparent, reproducible, and privacy-preserving accountability system.
* **Architecture Division**:
  * **Half 1 (Our Workstream — Requests and Speed)**: Implements reproducible synthetic data generation (using fictional platforms `Platform A` .. `Platform J`), ingestion and safe validation of police request logs (CSV/JSON), statistical calculation of removal and data-request turnaround times with right-censoring to eliminate survivorship bias, `as_of` measurement cutoffs, $k$-anonymity privacy filtering with small-count redaction, and public dashboard presentation.
  * **Half 2 (Teammate's Workstream — Verification and Integrity)**: Implements pluggable external verifier simulators, scheduled URL availability polling, PDQ perceptual hash re-upload detection, false removal claim detection, dispute review workflows, and authenticated victim progress queries.
* **Shared Data Contracts**: Both halves operate on shared data models:
  1. `schemas/request_log.schema.json`: Ingestion schema with `content_url_hmac` and `legal_route`.
  2. `schemas/observation.schema.json`: Independent verification observations contract.
  3. `schemas/scoreboard.schema.json`: Public aggregate transparency contract with censored percentile support, per-platform integrity breakdowns, and `as_of` metadata.

---

## B. Current Development Status

| Property | Current Value |
| :--- | :--- |
| **Assigned Workstream** | **Half 1 — Requests and Speed** |
| **Git Feature Branch** | `feat/requests-speed-metrics` |
| **Target Merge Branch** | `main` |
| **Pull Request** | **Open — PR [#1](https://github.com/iamnih4l/takedown-tracking/pull/1)** |
| **Current Phase** | **Review Feedback Addressed — Tested & Documented** |
| **Overall Progress** | Milestone 6 of 6 (All review findings resolved with tested regression suites) |
| **Most Recent Milestone** | Full regression test suite passed (86/86 tests in 0.80s) |
| **Next Immediate Task** | Pair with teammate on Half 2 verifier integration and measurement cutoff calibration |
| **Active Blockers** | **None** |

### Workstream Feature Status Matrix

| Component | Status | Test Status | Review Item Addressed |
| :--- | :--- | :--- | :--- |
| **Fictional Platform Identities** (`Platform A` .. `Platform J`) | `IMPLEMENTED` | `TESTED` | Item 2 (Zero real platforms in synthetic data) |
| **2026 IT Rules Statutory Deadlines** (`config/deadlines.json`) | `IMPLEMENTED` | `TESTED` | Items 3, 9, 12 (Rule 3(1)(d)=3h, Rule 3(2)(b)=2h, strictest resolution) |
| **Right-Censored Survival Percentiles** (`metrics/engine.py`) | `IMPLEMENTED` | `TESTED` | Item 4 (Eliminated survivorship bias; Platform A vs B verified) |
| **Measurement Cutoff & Cohort Maturity** (`as_of`) | `IMPLEMENTED` | `TESTED` | Items 5, 10 (Immature takedowns & 72h data requests excluded) |
| **Strengthened Privacy Filter** (`privacy/filter.py`) | `IMPLEMENTED` | `TESTED` | Items 6, 12, 13, 14 (Scope filtering, per-metric k-check, small-count redaction) |
| **Shared Observations Contract** (`schemas/observation.schema.json`) | `IMPLEMENTED` | `TESTED` | Items 7, 11 (Verifier priority over platform status; false claim detection) |
| **HMAC-SHA-256 URL Identifiers** (`models/request.py`) | `IMPLEMENTED` | `TESTED` | Item 8 (Keyed HMAC, fail-closed production safety) |
| **Separate Platform Rejections Metric** (`metrics/engine.py`) | `IMPLEMENTED` | `TESTED` | Item 13 (Rejections isolated from still-available) |
| **Narrowed Gitleaks Allowlist** (`.gitleaks.toml`) | `IMPLEMENTED` | `TESTED` | Item 14 (Removed `tests/.*` broad exemption) |
| **Documentation Link & Security Fixes** (`README.md`, `SECURITY.md`) | `IMPLEMENTED` | `TESTED` | Items 10, 11 (Relative paths, GitHub private advisory link) |

---

## C. PR #1 Code Review Items Addressed

### 1. Fictional Platform Identities in Synthetic Data (Item 2)
* **Problem**: Generator and fixtures previously attributed invented numbers to real platform names (YouTube, Meta, Telegram), risking misleading public screenshots.
* **Resolution**: Replaced with fictional platforms `Platform A` through `Platform J` and generic behavioral profiles in `SyntheticDataGenerator`. Real platform identifiers (`Meta`, `YouTube`, etc.) are retained strictly for authorized production deployment mode.
* **Affected Files**: `src/takedown_tracking/models/request.py`, `schemas/request_log.schema.json`, `src/takedown_tracking/generator/synthetic_generator.py`, `data/demo_synthetic_requests.json`, `data/demo_public_scoreboard.json`.

### 2. 2026 IT Rules Statutory Deadlines & Strictest Resolution (Items 3, 9)
* **Problem**: Deadlines were outdated (24h/36h) and urgent priority (3h) could be overridden by category defaults.
* **Resolution**: Updated `config/deadlines.json` to 2026 IT Rules amendments (effective 20 February 2026; Gazette 269993):
  - Rule 3(1)(d): 3 hours for specified court orders or authorized government intimations (`court_order_or_govt_intimation`).
  - Rule 3(2)(b): 2 hours for qualifying complaints (`CSAM`, `NonConsensualImagery`, `Impersonation`) under `grievance_complaint_3_2_b`.
  - Conflict resolution strictly evaluates $\min(\text{route}, \text{priority}, \text{category})$.
  - Urgent priority (3h) is never overridden by a category deadline.
* **Affected Files**: `config/deadlines.json`, `src/takedown_tracking/metrics/deadline.py`, `schemas/request_log.schema.json`.

### 3. Removal-Time Percentiles & Right-Censoring (Item 4)
* **Problem**: Calculating medians using only completed removals created survivorship bias (e.g. Platform B with 10 removed in 1h and 10 unremoved appeared 10x faster than Platform A with 20 removed in 10h).
* **Resolution**: Implemented survival analysis percentiles in `calculate_censored_percentile`. Unresolved requests past deadline are placed after finite durations ($+\infty$). If rank touches unremoved items, the metric is right-censored (`is_censored: true`, display: `"> measurement window"`). Zero `Infinity` or `NaN` serialized into JSON.
* **Affected Files**: `src/takedown_tracking/metrics/engine.py`, `src/takedown_tracking/models/metrics.py`, `src/takedown_tracking/models/scoreboard.py`.

### 4. Explicit Measurement Cutoff (`as_of`) and Cohort Maturity (Item 5)
* **Problem**: Pending requests dispatched shortly before report generation were unfairly counted as missed deadlines.
* **Resolution**: Added `--as-of` ISO 8601 UTC timestamp to CLI and `MetricsEngine`. Requests whose deadline has not elapsed by `as_of` are classified as immature and excluded from deadline-miss denominators. Early completed removals enter the cohort immediately.
* **Affected Files**: `src/takedown_tracking/metrics/engine.py`, `src/takedown_tracking/cli.py`, `schemas/scoreboard.schema.json`.

### 5. Strengthened Privacy Filter ($k$-Anonymity) (Items 6, 12)
* **Problem**: `suppression_reason` leaked exact counts; $k$-anonymity only checked overall platform total; small incomplete request counts were exposed.
* **Resolution**:
  - `suppression_reason` now uses a static message: `"Suppressed because the group is below the publication threshold."` (strictly zero counts leaked).
  - Applied $k$-anonymity independently per metric denominator (takedown cohort, data cohort, integrity cohort).
  - Small counts of incomplete requests and rejections ($< k$) are redacted to `None`.
  - Default public reporting scope narrowed to high-impact victim protection (`CSAM`, `NonConsensualImagery`, `Impersonation`, `FinancialFraud`).
* **Affected Files**: `src/takedown_tracking/privacy/filter.py`, `schemas/scoreboard.schema.json`.

### 6. Shared Verification Observation Contract (Item 7)
* **Problem**: Need an independent verification contract for Half 2 without circular dependencies.
* **Resolution**: Designed `schemas/observation.schema.json` and `ObservationRecord` domain model:
  - Supports `request_id`, `observation_status`, `verified_removed_at`, `reupload_detected_at`, `last_checked_at`, `verifier_agent_id`.
  - Metrics engine prioritizes `verified_removed_at` when present.
  - Conflicting observations (claimed removed, but observed available) are detected and flagged as **False Removal Claims**.
  - Public scoreboard extended with per-platform integrity breakdown.
* **Affected Files**: `schemas/observation.schema.json`, `src/takedown_tracking/models/observation.py`, `src/takedown_tracking/metrics/engine.py`, `schemas/scoreboard.schema.json`.

### 7. HMAC-SHA-256 URL Identifiers (Item 8)
* **Problem**: Plain SHA-256 is guessable against known URLs.
* **Resolution**: Replaced plain SHA-256 with HMAC-SHA-256 using `TAKEDOWN_HMAC_SECRET`. Fails closed if secret is missing in non-synthetic production mode. Permitted non-production test key in synthetic test mode.
* **Affected Files**: `src/takedown_tracking/models/request.py`, `schemas/request_log.schema.json`.

### 8. Separate Platform Rejection Reporting (Item 13)
* **Problem**: Rejections were counted as "content still available", punishing platforms for refusing overbroad or unlawful demands.
* **Resolution**: Rejections tracked in dedicated `rejections_count` and `rejection_rate_pct`. Not equated with removal or availability.
* **Affected Files**: `src/takedown_tracking/metrics/engine.py`, `src/takedown_tracking/models/metrics.py`, `src/takedown_tracking/models/scoreboard.py`.

### 9. Gitleaks Allowlist & Documentation Fixes (Items 10, 11, 14)
* **Problem**: Broad `tests/.*` allowlist in `.gitleaks.toml`, `file:///` links in `README.md`, placeholder email in `SECURITY.md`.
* **Resolution**:
  - Removed `tests/.*` from `.gitleaks.toml`; retained only specific synthetic JSON fixtures.
  - Replaced all local `file:///` links in `README.md` with relative repository paths.
  - Linked `SECURITY.md` directly to GitHub Private Vulnerability Reporting (`https://github.com/iamnih4l/takedown-tracking/security/advisories/new`).
* **Affected Files**: `.gitleaks.toml`, `README.md`, `SECURITY.md`.

### 10. Data Requests Cutoff (`as_of`) and Cohort Maturity
* **Problem**: A data assistance request submitted 30 minutes before cutoff (with a 72-hour statutory deadline) was counted in the compliance denominator, unfairly penalizing platforms (e.g. dropping compliance from 100% to 95%).
* **Resolution**: Implemented cohort maturity for data requests in `MetricsEngine`. Data requests only enter the mature compliance cohort if completed/rejected before `as_of` or if their 72h deadline has elapsed by `as_of`. Immature pending requests are excluded from denominators. Added `mature_data_requests` metric.
* **Affected Files**: `src/takedown_tracking/metrics/engine.py`, `src/takedown_tracking/models/metrics.py`, `src/takedown_tracking/models/scoreboard.py`, `schemas/scoreboard.schema.json`.

### 11. Verifier Precedence over Platform Status
* **Problem**: If an independent verifier observation confirmed content was removed (`confirmed_removed`) with `verified_removed_at`, but the platform status was still `pending` or `acknowledged`, the engine previously marked the content as still available.
* **Resolution**: Independent verification strictly takes precedence over platform self-reporting. An observation with `confirmed_removed` and `verified_removed_at <= cutoff` marks the request as completed and mature, increments `verified_takedowns_count`, and calculates duration using `verified_removed_at`.
* **Affected Files**: `src/takedown_tracking/metrics/engine.py`, `tests/test_observations_contract.py`.

### 12. Statutory Police Intimations & 2026 Gazette Citations
* **Problem**: Pre-amendment Oct 2025 MeitY URL, and unclear statutory basis for `routine_police_inquiry: 36h`.
* **Resolution**:
  - Updated `config/deadlines.json` source URL to official Gazette Notification 269993 (effective 20 February 2026).
  - Clarified in `METHODOLOGY.md` that statutory police notices under Section 79(3)(b) fall under Rule 3(1)(d) (3.0 hours), while 36.0h represents an operational assumption for informal communications. Documented fallback resolution when `legal_route` is missing.
* **Affected Files**: `config/deadlines.json`, `METHODOLOGY.md`.

### 13. Reporting Scope Filtering in PrivacyFilter
* **Problem**: `published_categories` hid breakdown rows, but out-of-scope categories (e.g., Defamation, HateSpeech) still leaked into platform totals and overall summary counts.
* **Resolution**: `PrivacyFilter` now filters input records to `published_categories` upfront before computing overall metrics, platform breakdowns, and category rows. Synthetic data generator categories were also aligned with the public reporting scope.
* **Affected Files**: `src/takedown_tracking/privacy/filter.py`, `src/takedown_tracking/generator/synthetic_generator.py`.

### 14. Refined Per-Metric $k$-Check Denominators
* **Problem**: Removal speed $k$-check evaluated mature takedowns including rejections, and data turnaround $k$-check evaluated total data requests rather than completed responses.
* **Resolution**:
  - Speed $k$-check strictly evaluates the speed cohort size ($N - \text{rejections} = \text{completed} + \text{still\_available}$).
  - Median data response time $k$-check strictly evaluates completed data responses ($\text{completed\_data\_requests} \ge k$).
* **Affected Files**: `src/takedown_tracking/privacy/filter.py`, `tests/test_privacy.py`.

---

## D. Testing and Verification Results

* **Automated Test Suite**:
  ```powershell
  $env:PYTHONPATH="src"; python -m pytest -v tests
  ```
  - **Total Tests**: **86**
  - **Passed**: **86** (100% pass rate)
  - **Failed**: **0**
  - **Execution Time**: **0.80 seconds**
* **Test Suite Breakdown**:
  - `tests/test_deadlines_2026.py` (9 tests): Rule 3(1)(d)=3h, Rule 3(2)(b)=2h, qualifying categories, priority override prevention, 3-way conflict resolution.
  - `tests/test_censored_percentiles.py` (7 tests): Platform A vs Platform B survivorship bias avoidance, right-censoring boundary ranks, zero `Infinity`/`NaN`.
  - `tests/test_measurement_cutoff.py` (6 tests): Immature takedowns, immature data requests (30m before cutoff), overdue transitions, early completed inclusions, determinism.
  - `tests/test_hmac_urls.py` (6 tests): HMAC determinism, key variations, fail-closed production safety, zero secret leakage.
  - `tests/test_observations_contract.py` (6 tests): ObservationRecord validation, verifier precedence over platform status, verified removal override, false removal claim detection, per-platform integrity.
  - `tests/test_rejections.py` (3 tests): Rejection isolation from still-available, exclusion from removal speeds, small-count privacy redaction.
  - `tests/test_secret_scanning.py` (3 tests): Secret scanning of all source and test files, `.gitleaks.toml` restriction validation.
  - `tests/test_generator.py` (8 tests): Fictional platform guarantee (`Platform A` .. `Platform J`), seed determinism, observation generation.
  - `tests/test_privacy.py` (9 tests): Generic reason validation, scope filtering without leakage, speed $k$-check without rejections, completed data $k$-check, small count redactions.
  - `tests/test_importer.py` (8 tests): Validation, temporal order, quarantine logging.
  - `tests/test_metrics.py` (11 tests): Percentile interpolation, compliance math, data request turnaround.
  - `tests/test_web.py` (6 tests): Server endpoints, scoreboard API, static assets.
  - `tests/test_cli.py` (5 tests): Subcommands (`generate`, `import`, `metrics`, `export-scoreboard`, quarantine).

---

## E. Handover Notes for Teammate (Half 2 Collaboration)

### 1. Workstream Boundary Alignment
As agreed in review feedback:
* **Teammate (Half 2) Ownership**:
  - Writing and extending the observation contract (`schemas/observation.schema.json` and `ObservationRecord`).
  - Adding `last_seen_up_at` and `first_checked_at` (to detect 1h claims when content remained live at 20h).
  - Replacing unstructured notes with a typed error-code enum.
  - Owning integrity metrics calculation: false removal claims, re-upload window and rate, and verification coverage.
* **Half 1 (Our Workstream) Ownership**:
  - Request ingestion, validation, and HMAC-SHA-256 URL pseudonymization.
  - Statutory 2026 IT Rules SLA deadline resolution ($\min(\text{route}, \text{priority}, \text{category})$).
  - Cohort maturity under explicit `as_of` measurement cutoff (for both takedowns and data requests).
  - Right-censored survival percentiles for removal duration.
  - $k$-Anonymity privacy filtering with small-count redaction and reporting scope restriction.
  - Public scoreboard schemas and web presentation.

### 2. How to Feed Observations into Half 1
When the Verifier Simulator runs, emit observations adhering to `schemas/observation.schema.json`. Half 1 binds them via the `--observations` CLI option or `filter_and_publish(records, observations=obs_map)`.
If content is verified removed, `verified_removed_at` will automatically override platform status and compute empirical turnaround times.
