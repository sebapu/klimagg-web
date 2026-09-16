# Security Policy

## Scope

This policy applies to the public `klimagg-web` source repository.

A deployment of the software is a separate security boundary. Production databases, `.env` files, SMTP credentials, JWT secrets and other installation-specific data are not part of the public repository.

## Reporting a vulnerability

Please do **not** publish exploit details, credentials, personal data or a working proof of concept in a public issue.

Preferred reporting channel:

1. use GitHub Private Vulnerability Reporting / a private security advisory for this repository, if enabled;
2. if that is not available, contact the repository maintainer privately through the contact method listed on the repository owner profile.

Include, where possible:

- affected version or Git commit;
- affected endpoint or component;
- prerequisites;
- concise reproduction steps;
- expected and actual behavior;
- impact;
- suggested mitigation, if known.

Do not include real user data. Use synthetic accounts and a local test database.

There is no guaranteed response-time SLA for this community project.

## High-impact areas

Changes in the following areas deserve additional review:

- Magic-Link generation and consumption;
- JWT creation, storage and authorization;
- `User.is_admin` checks;
- account deletion and anonymization;
- comment publication boundaries;
- MiniMD rendering and HTML escaping;
- Patch v2 anchoring and merge logic;
- public/private article version access;
- snapshot and export generation;
- admin file downloads;
- Git / process-management functions in admin operations;
- SMTP handling;
- CORS, proxy headers and security headers.

## Authentication model

The supported authentication model is deliberately narrow:

```text
Magic Link -> JWT -> User.is_admin
```

The project does not use:

- HTTP Basic Auth for the admin panel;
- a second admin password;
- a development authentication bypass.

Please treat any change that introduces an additional authentication path as security-sensitive.

## Deployment assumptions

The reference production deployment assumes:

- nginx terminates HTTPS;
- Uvicorn listens only on `127.0.0.1`;
- nginx overwrites forwarding headers rather than trusting arbitrary client-provided values;
- one application process is used with SQLite;
- `.env` is readable only by the service account;
- the SQLite database and backups are not web-accessible;
- production configuration passes `tools/check_env.py --mode production`;
- explicit reviewed Git tags are deployed.

Running Uvicorn directly on a public interface is outside the recommended deployment model.

## Secrets

Never commit:

- `.env`;
- JWT secrets;
- SMTP passwords;
- GitHub/API tokens;
- private keys;
- production SQLite databases;
- SQLite WAL/SHM files;
- database backups;
- account exports containing personal data.

`.gitignore` is only a convenience and is not a security boundary.

Before making a repository public, scan both the current tree and Git history. Removing a secret in a later commit does not remove it from earlier Git objects.

If a secret has ever been committed, rotate the secret even if the Git history is rewritten.

## Personal data

Security reports and test fixtures must not contain real account data.

The included demo profiles use synthetic identities and `.invalid` email domains.

Public snapshot/export code is expected to keep account-level personal data out of public artifacts. A change that exposes email addresses, user ids, login tokens or private review data should be treated as a security issue.

## Supported versions

Security fixes are expected to target the current public release first.

Older tags are historical snapshots and may not receive backports.

## Safe research

Good-faith testing against a local installation using synthetic data is welcome.

Do not:

- access data that is not yours;
- degrade a public service;
- attempt persistence on a production host;
- send bulk email;
- publish secrets or personal data discovered accidentally.

If sensitive information is encountered, stop further access and report it privately.
