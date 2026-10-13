# Methodology & Metric Specifications

This document outlines the scientific definitions, mathematical formulas, data filtering logic, privacy controls, and audit limitations governing **Half 1: Requests and Speed** and the shared observation integration with **Half 2: Verification and Integrity** within the Social Media Takedown Accountability Platform.

> **Demonstration Notice**:
> In demonstration mode, this platform uses 100% synthetic records with **fictional platform identities** (`Platform A` through `Platform J`) and simulated profiles. Figures generated from synthetic data do not represent genuine intermediary compliance, actual platform responsiveness, or real police investigations. Real platform names (`Meta`, `YouTube`, etc.) are reserved strictly for authorized, isolated law enforcement deployment.

---

## 1. System Dataflow & Processing Architecture

```mermaid
flowchart TD
    subgraph Ingestion["1. Ingestion & Validation"]
        A[Police Log CSV / JSON] --> B[RequestValidator]
        B -->|Valid Records| C[Valid Request Store]
        B -->|Defective Rows| D[Quarantine Log / Failure Audit]
    end

    subgraph CutoffAndMaturity["2. Cutoff & Cohort Maturity (as_of)"]
        C --> E{Measurement Cutoff as_of}
        E -->|Mature Records| F[Mature Analysis Cohort]
        E -->|Immature Pending Records| G[Excluded from SLA Denominators]
    end

    subgraph Processing["3. Strictest SLA Resolution"]
        F --> H[DeadlineConfig: min Route, Priority, Category]
        H --> I[Eligible Takedown Split]
        H --> J[Data Request SLA: 72h]
    end

    subgraph VerifierIntegration["4. Half 2 Observation Binding"]
        OBS[ObservationRecord Contract] --> K{Status Verification}
        K -->|Confirmed Removed| L[Verified Removal Duration]
        K -->|Confirmed Available| M[False Removal Claim Detected]
        K -->|Uncertain / Failed| N[Unverified Fallback]
    end

    subgraph StatisticalEngine["5. Statistical Metrics"]
        L & M & N --> O[Survival Percentiles: Right-Censoring]
        O --> P[Median & p90 Removal Speed]
        O --> Q[Deadline Compliance Rate %]
        O --> R[Still Available Rate %]
        O --> S[Platform Rejection Rate %]
    end

    subgraph PrivacyLayer["6. Privacy & Disclosure Control"]
        P & Q & R & S --> T[PrivacyFilter: Per-Metric k-Anonymity]
        T -->|Cohort Denominator < k| U[Suppress Group / Metric]
        T -->|Cohort Denominator >= k| V[Publish Metric]
        T -->|Purge Microdata & Redact Small Counts| W[Public Scoreboard JSON & Web Dashboard]
    end
```

---

## 2. Statutory Deadlines & Strictest Resolution (2026 IT Rules)

### 2.1. Statutory Timelines
Amended timelines under the Information Technology (Intermediary Guidelines and Digital Media Ethics Code) Rules, 2021 (amended effective 20 February 2026; Gazette notification 269993):

1. **Rule 3(1)(d) (Specified Court Orders or Authorized Government Intimations)**:
   - **Deadline**: **3.0 hours** from formal communication.
   - **Applicable Route**: `court_order_or_govt_intimation`.
2. **Rule 3(2)(b) (Grievance Complaints on Specified Harm Categories)**:
   - **Deadline**: **2.0 hours** from receipt of complaint.
   - **Qualifying Categories**: Child Sexual Abuse Material (`CSAM`), Non-Consensual Intimate Imagery and morphed content (`NonConsensualImagery`), and `Impersonation`.
   - **Critical Rule**: The 2-hour deadline does **not** automatically apply to every request labeled NCII; the legal route must be `grievance_complaint_3_2_b`.
3. **Operational Priorities**:
   - `emergency_life_threat`: **2.0 hours** (imminent danger to human life).
   - `urgent`: **3.0 hours** (priority operational escalation).
   - `routine`: **36.0 hours** (standard operational default).
4. **Data Assistance Requests**:
   - **Deadline**: **72.0 hours** under statutory data-assistance provisions. Content-removal SLAs are **never** applied to data requests.

### 2.2. Conflict Resolution Order
When multiple deadlines are applicable (e.g., category default, legal route, and operational priority), the engine selects the **strictest applicable deadline**:

$$\text{Effective Deadline} = \min(\text{Applicable Candidates})$$

*Example*: If Harm Category = `FinancialFraud` (36h default), Operational Priority = `urgent` (3h), and Legal Route = `court_order_or_govt_intimation` (3h):
$$\text{Selected Deadline} = \min(36.0, 3.0, 3.0) = 3.0\text{ hours}$$

An urgent priority is never silently ignored or relaxed by a category default.

### 2.3. Statutory Police Intimations vs. Operational Assumptions
1. **Rule 3(1)(d) Police Intimations (3.0 Hours)**:
   Formal police takedown notices issued under Section 79(3)(b) by an authorized law enforcement officer or agency constitute authorized government intimations under Rule 3(1)(d). Under the 2026 amendments (Gazette No. 269993), these carry a mandatory **3.0-hour** compliance deadline.
2. **Operational Fallback Assumptions**:
   Non-statutory communications or informal inquiries (`routine_police_inquiry`) fall back to operational defaults (36.0 hours).
3. **Ambiguous or Missing Route Information**:
   If `legal_route` is absent or `other`, the engine relies strictly on applicable category and priority rules. It does not fabricate a statutory 2h or 3h compliance route without supporting evidence. Law enforcement logs must record `court_order_or_govt_intimation` or `grievance_complaint_3_2_b` to trigger statutory SLA tracking.

---

## 3. Measurement Cutoff (`as_of`) and Cohort Maturity

To prevent temporal bias where pending requests submitted shortly before report generation are unfairly penalized:

1. **Explicit Reporting Cutoff (`as_of`)**:
   Every calculation accepts an ISO 8601 UTC timestamp `as_of`. If not provided, it defaults to a single UTC timestamp captured at report generation start.
2. **Takedown Cohort Maturity Rule**:
   A takedown enters the mature analysis cohort if and only if:
   - It was verified removed before `as_of` ($\text{verified\_removed\_at} \le \text{as\_of}$); OR
   - It was claimed removed before `as_of` ($\text{claimed\_removal\_at} \le \text{as\_of}$); OR
   - It was rejected before `as_of` ($\text{sent\_at} \le \text{as\_of}$); OR
   - Its applicable deadline has elapsed by `as_of` ($\text{sent\_at} + \text{deadline\_hours} \le \text{as\_of}$).
3. **Data Request Cohort Maturity Rule**:
   A data assistance request enters the mature data cohort if and only if:
   - Data was provided before `as_of` ($\text{data\_provided\_at} \le \text{as\_of}$); OR
   - It was rejected before `as_of` ($\text{sent\_at} \le \text{as\_of}$); OR
   - Its statutory 72-hour deadline has elapsed by `as_of` ($\text{sent\_at} + 72.0\text{h} \le \text{as\_of}$).
4. **Immature Pending Exclusions**:
   Requests whose deadlines have not yet elapsed by `as_of` are **immature**. They are strictly excluded from compliance denominators and speed percentiles. A data request sent 30 minutes before export cannot drop compliance from 100% to 95%.

---

## 4. Removal-Time Percentiles & Right-Censoring

### 4.1. The Survivorship Bias Problem
Calculating median and p90 removal times using only requests marked `claimed_removed` creates severe survivorship bias. For example, if Platform A removes 20 requests in 10 hours, while Platform B removes 10 in 1 hour and leaves 10 unresolved indefinitely, an un-censored calculation would report Platform B as 10x faster (1.0h vs 10.0h).

### 4.2. Survival Analysis Formulation
We calculate removal-time percentiles over the entire eligible, mature takedown cohort of size $N$:

$$N = k + U$$
where:
- $k$: count of confirmed removals with finite observed durations $t_0 \le t_1 \le \dots \le t_{k-1}$.
- $U$: count of mature unresolved requests (content still available past deadline), treated as right-censored outcomes with duration $> \text{deadline}$ (conceptually $+\infty$).

For any target percentile $p \in [0, 100]$:
$$\text{Rank} = \frac{p}{100} \times (N - 1)$$
$$i_{\text{low}} = \lfloor \text{Rank} \rfloor, \quad i_{\text{high}} = \lceil \text{Rank} \rceil$$

1. **Finite Rank ($i_{\text{high}} < k$)**:
   Both bounds fall within the finite durations:
   $$\text{Percentile} = t_{i_{\text{low}}} + (\text{Rank} - i_{\text{low}}) \times (t_{i_{\text{high}}} - t_{i_{\text{low}}})$$
   Serialized as finite number `numeric_hours`, `is_censored = false`.
2. **Right-Censored Rank ($i_{\text{high}} \ge k$)**:
   The percentile rank touches or falls into the unremoved, censored outcomes.
   Serialized safely as:
   - `numeric_hours: null` (never `Infinity` or `NaN`)
   - `is_censored: true`
   - `display: "> measurement window"`

In the Platform A vs Platform B example:
- **Platform A**: $N=20, k=20$. Rank for median = 9.5 ($< 20$). Median = **10.0 hours**.
- **Platform B**: $N=20, k=10$. Rank for median = 9.5. Upper index $10 \ge k (10)$. Median is **right-censored** (`> measurement window`), not 1.0 hour.

---

## 5. Separate Platform Rejection Reporting

1. **Rejection Outcome Isolation**:
   Platform refusals (`status == 'rejected'`) are audited in separate metrics (`rejections_count` and `rejection_rate_pct`).
2. **Non-Equivalence Rules**:
   - A rejection is **not** counted as content removal.
   - A rejection is **not** automatically counted as confirmed content availability (unless verified by an independent observation).
   - Rejections do not distort the removal-time speed distribution.
3. **Privacy Protection**:
   Small rejection counts ($< k$) are redacted from public output to prevent disclosure.

---

## 6. Shared Verification Observation Contract (Half 1 / Half 2)

Independent external checks performed by Half 2 (Verifier Simulator) interface through `schemas/observation.schema.json` and `ObservationRecord`:

1. **Verifier Priority Over Platform Status**:
   If `observation_status == 'confirmed_removed'` and `verified_removed_at` is present, the verifier observation strictly overrides platform status. Even if the platform status is still `pending` or `acknowledged`, the content is marked as verified removed with duration calculated from `verified_removed_at`.
2. **False Removal Claims**:
   If a platform claims `claimed_removed`, but an independent observation confirms `confirmed_available`:
   - Flagged as a **False Removal Claim**;
   - Increments `false_removal_claims_count`;
   - Treated as non-compliant still-available content.
3. **Uncertain and Failed Checks**:
   Checks with status `uncertain` or `failed_check` are never equated with successful removal.
4. **Per-Platform Integrity Breakdown**:
   Public scoreboards report per-platform false removal claims, observed re-uploads, and verified takedowns, subject to $k$-anonymity suppression.

---

## 7. URL Cryptographic Pseudonymization (HMAC-SHA-256)

1. **Vulnerability of Plain SHA-256**:
   Plain SHA-256 hashes of known candidate URLs are vulnerable to dictionary pre-computation and re-identification attacks.
2. **HMAC-SHA-256 Standard**:
   All URL identifiers use HMAC-SHA-256 keyed with a deployment secret (`TAKEDOWN_HMAC_SECRET`):
   $$\text{content\_url\_hmac} = \text{HMAC-SHA-256}(\text{SecretKey}, \text{CanonicalURL})$$
3. **Fail-Closed Protection**:
   In non-synthetic production execution, if `TAKEDOWN_HMAC_SECRET` is unset, the validator immediately fails closed.
4. **Boundary Distinction**:
   HMAC URL identifiers enable privacy-preserving record correlation across workstreams. PDQ perceptual image matching remains strictly inside Half 2's isolated verifier environment.

---

## 8. Strengthened Privacy Controls ($k$-Anonymity)

1. **Generic Suppression Reasons**:
   Public suppression reasons never include exact cohort sizes. The standardized explanation is:
   `"Suppressed because the group is below the publication threshold."`
2. **Independent Per-Metric Denominators**:
   The $k$-anonymity threshold ($k=10$) is enforced independently for each published metric:
   - Takedown removal speed: requires speed cohort size $\ge k$ ($(\text{completed\_takedowns} + \text{still\_available\_takedowns}) \ge k$, excluding rejections).
   - Takedown compliance & availability rates: requires mature actionable takedowns $\ge k$.
   - Data request turnaround (median/p90): requires completed responses $\ge k$ ($\text{completed\_data\_requests} \ge k$).
   - Data request compliance rate: requires $\text{mature\_data\_requests} \ge k$.
   - Integrity breakdowns: requires $\text{integrity\_cohort} \ge k$.
3. **Small-Count Redaction**:
   Small counts of incomplete police requests ($< k$) and platform rejections ($< k$) are redacted (`null`) to eliminate indirect re-identification through subtraction.
4. **Scope Filtering by Default Reporting Scope**:
   Default public reports are restricted to high-impact victim-protection categories (`CSAM`, `NonConsensualImagery`, `Impersonation`, `FinancialFraud`). The privacy filter scopes input records to `published_categories` before computing overall performance, platform breakdowns, and category rows, guaranteeing mutual consistency and eliminating out-of-scope counts.
