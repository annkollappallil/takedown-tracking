# Security Policy

## 1. Core Security Principles

The Social Media Takedown Accountability Platform is designed for law enforcement transparency and public accountability. Maintaining strict privacy, credential safety, and data integrity is paramount:

* **No Hardcoded Credentials**: Never commit or embed authentication tokens, API keys, passwords, or private certificates in code, scripts, configs, documentation, or commit history.
* **Authorized Authentication**: Use official, properly scoped authentication mechanisms (such as Git Credential Manager, GitHub CLI `gh auth`, or environment-injected secrets in CI/CD). Never print credentials in terminal logs or save them in temporary files.
* **Immediate Revocation on Exposure**: Treat any credential exposed in terminal logs, transcripts, or code as immediately compromised. Revoke it via GitHub security settings without delay.
* **Strict Privacy for Police Data**: No real victim data, complainant identities, actual case references, or real content URLs may ever be entered or committed to this repository. All public demos and automated tests must exclusively use synthetic fixtures.

---

## 2. Supported Versions

| Version | Supported |
| :--- | :--- |
| `0.1.x` | :white_check_mark: |

---

## 3. Reporting a Vulnerability

If you discover a security vulnerability or credential leak within this repository:

1. **Do not open a public issue.**
2. Send an advisory report directly to the repository maintainer via GitHub Private Vulnerability Reporting or email `maintainers@takedown-tracking.internal`.
3. Include:
   * A description of the vulnerability.
   * Steps to reproduce without attaching real sensitive credentials or victim data.
   * Potential impact and affected files or components.

We acknowledge security reports within 24 hours and prioritize rapid remediation.
