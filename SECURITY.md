# Security Policy

## 1. Core Security Principles

The Social Media Takedown Accountability Platform is designed for law enforcement transparency and public accountability. Maintaining strict privacy, credential safety, and data integrity is paramount:

* **No Hardcoded Credentials**: Never commit or embed authentication tokens, API keys, passwords, or private certificates in code, scripts, configs, documentation, or commit history.
* **Authorized Authentication**: Use official, properly scoped authentication mechanisms (such as Git Credential Manager, GitHub CLI `gh auth`, or environment-injected secrets in CI/CD). Never print credentials in terminal logs or save them in temporary files.
* **Immediate Revocation on Exposure**: Treat any credential exposed in terminal logs, transcripts, or code as immediately compromised. Revoke it via GitHub security settings without delay.
* **Strict Privacy for Police Data**: No real victim data, complainant identities, actual case references, or real content URLs may ever be entered or committed to this repository. All public demos and automated tests must exclusively use synthetic fixtures with fictional platform identities (`Platform A` .. `Platform J`).
* **Cryptographic URL Pseudonymization**: Content URLs must never be stored in raw format or plain SHA-256 hashes. Real-world deployments require an operator-configured `TAKEDOWN_HMAC_SECRET` environment variable to compute HMAC-SHA-256 identifiers. The system fails closed if this secret is missing in non-synthetic execution.

---

## 2. Supported Versions

| Version | Supported |
| :--- | :--- |
| `0.2.x` | :white_check_mark: |
| `0.1.x` | :white_check_mark: |

---

## 3. Reporting a Vulnerability

If you discover a security vulnerability or credential leak within this repository:

1. **Do not open a public issue or discussion.** Never publish exploitable vulnerability details, proof-of-concept exploits, or sensitive case information in public forums.
2. Submit an advisory report via GitHub's Private Vulnerability Reporting page:
   [https://github.com/iamnih4l/takedown-tracking/security/advisories/new](https://github.com/iamnih4l/takedown-tracking/security/advisories/new)
3. If Private Vulnerability Reporting is disabled or unavailable in repository settings, contact the repository maintainer through an established private communication channel.
4. Include in your advisory:
   * A clear summary of the issue.
   * Specific steps to reproduce without including sensitive production secrets or victim data.
   * The affected component, file, or endpoint.
   * Potential impact assessment.

Security reports are acknowledged promptly and prioritized for remediation.
