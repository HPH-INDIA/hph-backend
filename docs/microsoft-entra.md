# Microsoft Entra and password login coexistence

This release adds Microsoft sign-in alongside the existing HPH email/password login.
Linking a Microsoft identity does **not** remove, reset, or disable an existing HPH password.
There is no automatic password-retirement date or Entra-only cutover switch.

## Deployment checklist

1. Install `requirements.txt` and back up the database. Apply `flask --app run:app db upgrade`.
   Revision `c3e7a9012b46` adds identity links, audit records, encrypted OIDC transactions and a
   session authentication-method column; existing sessions default to `password`.
   Merge revision `f6c3d7a90128` joins it with the pre-existing office-holiday migration
   `d7c8e2f1a609`, leaving a single migration head. Revision `a8e2c4f60139` then enables RLS and
   removes browser-role grants on the three backend-only authentication tables.
   The backend connection must use its privileged database role; these tables have no browser policies.
2. Deploy the frontend with `/auth/` routing and CSRF-aware API transport before deploying the
   backend's default-on CSRF enforcement. Existing open tabs need a refresh. The new frontend
   also tolerates old backend session responses, so this ordering keeps password login working.
3. Merge `.env.entra.example` with the existing backend environment. Keep `ENTRA_ENABLED=false`
   while configuring the tenant; ordinary password login continues to work.
4. Register separate Entra **Web** apps for local/staging/production in the company workforce
   tenant. Use the tenant GUID, not `common` or `organizations`. Register the exact URL
   `${PUBLIC_APP_ORIGIN}/auth/callback`. The documented production hostname is
   `https://digital-cockpit.archpocs.in`; verify it with the deployment owner.
5. Require Enterprise Application assignment, grant required admin consent, and assign only
   approved employees and explicitly invited guests. Configure MFA/Conditional Access through
   IT. Use individual assignments if group licensing is unavailable. Single-tenant sign-in
   alone does not prove employment or guest approval.
6. Upload the production certificate and mount its private key from secret storage. Set
   `ENTRA_PRIVATE_KEY_FILE`, `ENTRA_CERT_THUMBPRINT`, `ENTRA_CLIENT_ID`, `ENTRA_TENANT_ID`, and
   `PUBLIC_APP_ORIGIN`. A server-side `ENTRA_CLIENT_SECRET` is supported for development or staging.
   Generate a separate key with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
   and store it as `ENTRA_TRANSACTION_KEY`. Never put credentials in `VITE_*` variables.
7. Link pilot accounts using **Edit user → Microsoft Entra sign-in**, or the operator CLI below.
   Use company-tenant object IDs from an IT-approved directory export; never infer IDs or link
   by email. Guest IDs must be their objects in the company tenant, not their home tenant.
   HPH admins remain responsible for accurate member/guest classification; configuring Entra's
   optional `acct` ID-token claim supplies an additional guest classification check.
8. Set `ENTRA_ENABLED=true`, restart web workers, and test both methods with pilot accounts.
   Complete employee, guest Client, privileged-account, MFA, offboarding, and recovery checks in
   the actual tenant before widening assignment. No Graph/directory/mail permissions are requested.

## Account provisioning and lifecycle

- Existing accounts retain their password hash, employee identifier, roles, projects, hierarchy,
  and all historical references. Microsoft sign-in requires an active explicitly linked HPH account.
- **New user → Login methods** defaults to creating an HPH password; an Entra link can be added
  afterwards. Selecting Microsoft only creates no password and sends no credential email.
  The administrator must then edit the saved user and link its identity before sign-in can work.
- Guests must use the Client role with an assigned project. Both identity and role editors enforce
  this. Classifying a guest as a member is an administrator error, not a supported workaround.
- Linking/replacing/unlinking is audited and revokes all sessions for that HPH user. Unlinking
  removes Microsoft sign-in while retaining any pre-existing HPH password.
- Deactivation blocks both methods on the next API request. Offboarding is **paired manual work**:
  IT disables/unassigns in Entra and an HPH admin deactivates the HPH account. Entra changes alone
  do not immediately terminate an HPH session; no directory synchronization is implemented.
- HPH's configured role timeout remains an absolute timeout. Re-entering Microsoft sign-in may
  use the browser's Microsoft SSO; expiry does not guarantee a new password/MFA prompt.
- Log out revokes the HPH session only and clears per-user frontend data. It does not sign the
  browser out of Microsoft applications. Other tabs receive session-change notifications.
- Password first-login setup applies only to password-authenticated sessions. Microsoft login
  bypasses that gate without clearing the account's outstanding password-setup requirement.
- Keep temporary-password resend/change functionality for password-enabled accounts. Microsoft-only
  accounts cannot receive an HPH password through these existing endpoints.

## Operator bootstrap and recovery

Privileged accounts are hidden from ordinary HPH user lists. An authorized deployment operator can
link an existing Super Admin without needing another authenticated HPH session:

```sh
flask --app run:app link-entra-user --user-id USER_ID --object-id COMPANY_TENANT_OBJECT_ID --account-type member --operator CHANGE_TICKET_OR_OPERATOR
flask --app run:app link-entra-user --user-id USER_ID --unlink --operator CHANGE_TICKET_OR_OPERATOR
flask --app run:app cleanup-auth-transactions
```

The CLI requires host/database operator access, records the supplied operator, and revokes sessions.
It is not a browser recovery endpoint. Restrict deployment access accordingly.

## API and browser contract

- `GET /auth/options`: unauthenticated `{microsoftEnabled: boolean}`; no secret metadata.
- `GET /auth/login?return_to=/reports`: top-level navigation; creates a ten-minute encrypted,
  browser-bound transaction and redirects to the tenant authority.
- `GET /auth/callback`: consumes that transaction atomically; validates Microsoft identity and
  redirects to an allowlisted local destination with a fresh HttpOnly HPH cookie.
- Existing `/api/sessions/login`, `/whoami`, and password-change responses retain their envelope
  and user shape, adding `csrfToken` and `sessionId` to `data`, and `passwordEnabled` and
  `authMethod` to `data.user`. `firstLogin` reflects the current session's password-setup gate.
- Every API mutation sends `X-CSRF-Token` from the latest authenticated response. Login instead
  requires an allowed `Origin`; password request/response payload encryption is preserved.
- `GET/PUT/DELETE /api/users/{id}/entra-identity` requires Admin/Super Admin, user-management write
  permission, and existing target hierarchy restrictions. PUT takes `object_id` and
  `account_type` (`member`/`guest`); tenant ID always comes from backend configuration.
- POST `/api/users` additionally accepts `password_login_enabled` (default true); this option is
  create-only. Admin user responses add `password_enabled`.

## Routing, logs, credentials, and rollback

The frontend's Nginx and Vite proxy must send `/auth/` to Flask unchanged. The callback is outside
`/api/` and must not be encrypted, wrapped as JSON, cached, or served by the SPA fallback.
Public callback/return URLs use explicit configuration and never untrusted forwarded host headers.
Allow outbound HTTPS to `login.microsoftonline.com` for discovery, code exchange, and JWKS retrieval.

Nginx disables access logging for `/auth/`; Gunicorn logs paths without query strings. Ensure the
outer load balancer/reverse proxy also redacts callback query parameters. Callback responses use
`Cache-Control: no-store` and `Referrer-Policy: no-referrer`; they load no analytics.

All workers share PostgreSQL transaction storage and the same transaction key. Run transaction
cleanup periodically using the CLI if sign-in traffic stops; login also removes expired records.
For client credential rotation, deploy a newly registered credential, test, then remove the old one.
Rotating the transaction key invalidates pending sign-ins only; employees can start a new flow.

To roll back Entra availability, set `ENTRA_ENABLED=false`; existing Entra sessions will be denied
and users with passwords can select password login. Do not silently retry authentication using
another method. Keep the additive migration during application rollback: schema downgrade is
refused while passwordless accounts exist. Never manufacture passwords during rollback.

## Verification

`tests/test_entra_auth.py` exercises the actual PostgreSQL models, session cookies, encrypted API,
CSRF protection, JWT verification, guest restrictions and provisioning with mocked Microsoft HTTP.
The full suite must run only against a dedicated disposable `TEST_DATABASE_URL`: its fixture performs
migrations and destructive cleanup. Real tenant assignment, consent, MFA and certificate acceptance
still require staging validation with IT; they cannot be proved with local fixtures.

## Initial production rollout

Deploy this feature with `ENTRA_ENABLED=false` explicitly set in `/opt/hph/settings.json`.
Set `PUBLIC_APP_ORIGIN=https://digital-cockpit.archpocs.in` and retain secure cookies and CSRF checks.
The public login page offers password login and displays Microsoft sign-in as unavailable.
No development tenant credentials are copied to production.

The frontend deploys through its production GitHub Actions workflow. The current backend service
runs on the `hph-backend` VM in project `hph-backend-prod-20261014`, zone `us-central1-a`, with
source in `/opt/hph`. Its launcher merges pinned Secret Manager values with `settings.json`.
Deploy reviewed application files with a source/settings backup, install authentication dependencies,
run the existing migration chain, and restart `hph-backend.service`. Verify `/auth/options` returns
`{"microsoftEnabled":false}` through both the backend and public frontend.

## Production certificate runtime

The versioned VM launcher is `deploy/gce/run-with-secrets.py`. Install it as
`/opt/hph/run-with-secrets.py`, and install `deploy/gce/entra-runtime.conf` as
`/etc/systemd/system/hph-backend.service.d/entra-runtime.conf`. Run `systemctl daemon-reload`
before restarting `hph-backend.service`. The existing service runs as `hph` with restrictive
filesystem permissions; systemd creates `/run/hph-entra` owned by that user with mode `0700`.

Keep the certificate private key in the `hph-entra-private-key` Secret Manager secret and the
Fernet transaction key in `hph-entra-transaction-key`. Pin their exact version resource names
under `ENTRA_PRIVATE_KEY_PEM` and `ENTRA_TRANSACTION_KEY` in `/opt/hph/secret-refs.json`.
The runtime service account needs Secret Accessor on those two resources, not permission to
create new versions. The launcher writes the certificate key into the systemd runtime directory
with mode `0600`, removes the PEM value from the environment passed to Gunicorn, and supplies
only `ENTRA_PRIVATE_KEY_FILE=/run/hph-entra/private-key.pem`. Other secrets retain the existing
environment-based loading behavior. Never commit private keys, transaction keys, settings files,
secret payloads, or per-user identity exports.

Set `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, and `ENTRA_CERT_THUMBPRINT` in backend settings.
Upload only the corresponding public certificate to the production Entra app registration.
The registration and certificate must match before enabling login; certificate rotation requires
registering the replacement public certificate and updating the pinned secret version and thumbprint.
Keep `ENTRA_ENABLED=false` during this setup and preserve password login.

As of 2026-10-28, the production backend credentials are configured and all 42 approved active
HPH group members have verified identity links. The 41 new links were applied through the
audited application linking service; the existing pilot link was preserved. Passwords, roles,
and project assignments were checked unchanged. Public certificate registration was verified
on 2026-10-28; a real Microsoft sign-in test remains pending. Admin consent was reported granted by the tenant admin.
Do not treat configuration checks as evidence of a successful real Microsoft login.

For a first-user production test, set `ENTRA_PILOT_OBJECT_IDS` to that user's company-tenant
object ID before setting `ENTRA_ENABLED=true`. The pilot restriction is checked both before
issuing a Microsoft-authenticated HPH session and on each subsequent API request. Other assigned
and linked users cannot use Microsoft login during the pilot; their password login remains
available. Invalid UUIDs, whitespace-only values, or empty comma-separated entries fail startup
validation. The login button may still be visible to all users; authorization is enforced by
the backend after Microsoft authentication.

The initial production pilot is `vinay.vudatala@humanpoweredhealth.com`, object ID
`3698e584-75e3-4cec-9367-beb70af910b2`, in the configured company tenant. Multiple pilot IDs may
be comma separated. An empty value disables the pilot restriction and enables normal access for
all assigned, linked users, so clear it only when a broader rollout is explicitly approved.
Verify both Microsoft and password sign-in, and roll back availability with
`ENTRA_ENABLED=false` if the pilot fails. Do not remove identity links or manufacture passwords.

To validate just the VM launcher without a database or cloud credentials:

```sh
python -m unittest discover -s tests/unit -p test_gce_secret_launcher.py
```
