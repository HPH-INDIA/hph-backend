# Microsoft sign-in (Microsoft Entra ID)

HPH employees sign in with their company Microsoft 365 account (FR-19 to FR-22). The code is in
`appraisal_system/access_bff/entra/` and is off until `ENTRA_SIGN_IN_ENABLED=true`; switching it
on needs only configuration once IT has created the app registration ([request below](#request-for-it)).

## How it works

1. The frontend's "Sign in with Microsoft" button opens `/auth/microsoft/login?next=/some/page`.
2. The Flask app stores a short-lived sign-in record (state, nonce, PKCE verifier) on the server,
   gives the browser a random cookie pointing to it, and redirects to Microsoft's sign-in page for
   HPH's tenant only.
3. Microsoft checks the password and MFA under HPH's own policies; HPH never sees the password.
4. Microsoft redirects to `/auth/microsoft/callback` with a one-time code. The Flask app redeems it
   server to server (with the PKCE verifier and the app's credential) for an ID token, which the
   MSAL library validates (issuer, audience, nonce); HPH then checks the tenant and expiry.
5. The token's tenant id and object id (`tid`, `oid`, which never change) are looked up in
   `external_identities`. A deactivated, unlinked or guest account is refused.
6. The user gets the app's normal session cookie and lands on `FRONTEND_APP_URL` + `next`.
   Everything after that, including the gateway to the appraisal services, is unchanged.

### What is stored

| What | Where | How long |
| --- | --- | --- |
| Link from Microsoft account (`tid`, `oid`) to HPH user | `external_identities` | Once per person, until unlinked |
| Sign-in in progress (state, nonce, PKCE verifier) | `entra_login_transactions` | Until the callback, 10 minutes at most |
| HPH session | `sessions` (as today) | The role's session timeout |
| Microsoft ID, access or refresh tokens | Not stored | Checked once and discarded |

Because no Microsoft token is kept, disabling someone's Microsoft account does not end an HPH
session that is already open; it lasts until it expires. Offboarding should also deactivate the
user in HPH: from that moment the gateway and Microsoft sign-in both refuse them.

## Linking accounts

- **First sign-in:** when a Microsoft account is not linked yet, it is linked to the active HPH
  user whose email equals the account's company email (`preferred_username`). This applies to
  Employees, Leads and Managers.
- **Admins:** Admin and Super Admin accounts are never linked automatically. Link them with
  `flask --app run:app entra-link --email <hph email> --object-id <oid>`.
- **A second Microsoft account for the same person** (e.g. recreated by IT) is refused until an
  administrator relinks it: `entra-link ... --replace`.
- **Unlinking:** `flask --app run:app entra-unlink --email <hph email>`.
- Set `ENTRA_LINK_BY_EMAIL=false` to require every link to be made by an administrator.

## Settings

| Variable | Meaning |
| --- | --- |
| `ENTRA_SIGN_IN_ENABLED` | `true` turns the `/auth/microsoft/*` routes on; default off |
| `ENTRA_TENANT_ID` | HPH's Directory (tenant) ID, a GUID; never `common` |
| `ENTRA_CLIENT_ID` | The app registration's Application (client) ID |
| `ENTRA_CLIENT_SECRET` | The client secret, or use a certificate: |
| `ENTRA_CLIENT_CERTIFICATE_PRIVATE_KEY`, `ENTRA_CLIENT_CERTIFICATE_THUMBPRINT` | Certificate credential (preferred in production) |
| `ENTRA_REDIRECT_URI` | `https://<host>/auth/microsoft/callback`, exactly as registered |
| `ENTRA_LINK_BY_EMAIL` | Link at first sign-in by email; default `true` |
| `PASSWORD_LOGIN_ENABLED` | `false` refuses password sign-in for everyone; default `true` |
| `FRONTEND_APP_URL` | Where users land after sign-in; defaults to the origin of `FRONTEND_LOGIN_URL` |
| `FRONTEND_LOGIN_URL` | Existing setting; refused sign-ins return here with `?sso_error=<code>` |

With `ENTRA_SIGN_IN_ENABLED=true`, the app refuses to start if any of these are missing or invalid.

**Host:** the session cookie is host-only, so `ENTRA_REDIRECT_URI` must be on the host the browser
uses for `/api` calls. If the frontend proxies `/api` to the Flask app, proxy `/auth/` the same way
and register the frontend host's callback URL.

**Logs:** the callback URL carries a one-time code. It cannot be reused, but keep query strings
for `/auth/microsoft/callback` out of access logs where possible.

## Frontend changes

- A "Sign in with Microsoft" button linking to `/auth/microsoft/login?next=<path to return to>`.
- On the login page, show a message for `sso_error`:

| Code | Message to show |
| --- | --- |
| `cancelled` | Sign-in was cancelled. |
| `expired` | The sign-in took too long or was already used. Please try again. |
| `not_linked` | Your Microsoft account is not set up for HPH. Contact your administrator. |
| `admin_link_required` | Admin accounts must be linked by an administrator. |
| `linked_to_another_account` | This HPH account is linked to a different Microsoft account. Contact your administrator. |
| `inactive` | Your HPH account is not active. |
| `guest_account`, `wrong_tenant` | Use your HPH company Microsoft account. |
| `microsoft_error`, `microsoft_unavailable`, `invalid_response` | Microsoft sign-in did not work. Please try again. |

- When password sign-in is off, `POST /api/sessions/login` answers 403 with
  `code: password_login_disabled`; hide the password form then.

## Rollout

1. IT creates the app registrations (development and production) and sends the IDs and credential.
2. Development: set the variables, `ENTRA_SIGN_IN_ENABLED=true`, run the migrations, test with a few
   accounts.
3. Production: enable it with password sign-in still on; link Admin accounts with `entra-link`.
4. Once everyone has signed in with Microsoft at least once, set `PASSWORD_LOGIN_ENABLED=false`.

## Request for IT

> **Subject: App registration for "Sign in with Microsoft" on the HPH portal**
>
> Hi, we are adding Microsoft 365 sign-in to the HPH portal. Could you set up the following in
> HPH's Microsoft Entra tenant, once for development and once for production?
>
> 1. **App registration**: name "HPH Portal (Development)" / "HPH Portal"; supported account types
>    **Accounts in this organizational directory only (single tenant)**.
> 2. **Authentication → Platform: Web**, redirect URIs:
>    - Development: `http://localhost:5100/auth/microsoft/callback` and
>      `https://<development backend host>/auth/microsoft/callback`
>    - Production: `https://<production backend host>/auth/microsoft/callback`
>    - No front-channel logout URL and no implicit grant (ID or access tokens) needed.
> 3. **Credential**: a certificate (preferred) or a client secret for production, and a client
>    secret for development. Please share them through the secret manager, not by email or chat,
>    with the expiry date so we can rotate in time.
> 4. **API permissions**: only Microsoft Graph delegated `openid` and `profile` (the default
>    `User.Read` may stay; we call no Microsoft APIs). Please grant admin consent for the
>    organization so employees are not prompted.
> 5. **Enterprise application → Properties → Assignment required = Yes**, and assign the group of
>    HPH employees who should have access. Guests and external accounts should not be assigned.
> 6. *Optional:* **Token configuration → add the optional claim `acct` to the ID token**, so the
>    portal can reject guest accounts explicitly.
> 7. Existing MFA and Conditional Access policies apply as usual; nothing special is needed.
>
> Please send back, for each environment: the **Directory (tenant) ID**, the **Application
> (client) ID**, where the credential is stored and when it expires, and the name of the assigned
> group. Thank you!
