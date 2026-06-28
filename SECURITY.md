# Security Policy

## Reporting

Please do not open public issues for secrets, credential exposure, or customer
data concerns. Report security concerns to the repository maintainers through a
private GitHub Security Advisory.

Use GitHub's private vulnerability reporting / Security Advisory flow when it is
available for this repository. If GitHub Security Advisory is unavailable,
contact the repository maintainers through a private channel and do not disclose
details publicly until WACA has had a reasonable opportunity to investigate.

## Scope

- Source code and installation scripts in this repository.
- Sample data and documentation shipped for public installation.

## Out Of Scope

- Questions about trademarks, certification, endorsement, or commercial terms.
- Requests for WACA to debug a third-party production environment unless a
  separate support agreement exists.
- Vulnerabilities caused by user-managed cloud configuration outside the
  repository's documented setup. The minimal public backend has no built-in
  authentication and is intended for local use behind your own access control;
  see `INSTALL.md` for the constraint.

## Japanese

secret、credential、customer data に関する懸念は public issue に書かず、maintainer
へ GitHub Security Advisory などの非公開 channel で連絡してください。

GitHub Security Advisory が利用できる場合は、その private vulnerability reporting /
Security Advisory flow を使ってください。利用できない場合も、WACA が調査する合理的な
機会を得る前に public disclosure しないでください。
