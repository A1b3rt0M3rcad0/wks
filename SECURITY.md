# Reporting security issues

Use GitHub's private vulnerability reporting for this repository when enabled.
Otherwise contact the repository owner privately through their GitHub profile;
do not put credentials, private documents or exploit payloads in a public issue.
Include the version/commit, affected endpoint or worker profile, prerequisites,
reproduction on synthetic data and expected versus observed authorization.

Upgrade to the latest release before reporting an already-fixed defect. Security
fixes target the latest release; older versions have no promised support window.
An acknowledgement deadline or production availability guarantee is not implied.

Production requires HTTPS, a dedicated PostgreSQL role, a strong cursor secret,
explicit origin, private object and privacy-ledger volumes, bounded input budgets
and the required decoder isolation. See `docs/stabilization/OPERATIONS.md`.
The privacy ledger must survive database restores. Losing it can resurrect
revoked credentials or deleted content; restore refuses a missing signed ledger.

No public persistent download URLs are issued. Delegations expire, restrict
actions and are revalidated during streaming. The maximum stream revocation
envelope is advertised by `/v1/capabilities`; already-delivered bytes cannot be
withdrawn. Keep worker model/input directories separate from broker secrets.
