"""Microsoft Entra ID through MSAL: OpenID Connect authorization code flow with PKCE."""


class EntraSignInError(Exception):
    """Microsoft refused the sign-in or answered something that does not validate.

    Parameters
    ----------
    reason : str
        Short code shown to the frontend: ``microsoft_error``, ``invalid_response`` or
        ``microsoft_unavailable``.
    detail : str, optional, default = None
        Diagnostic detail for the logs; never shown to the user.
    """

    def __init__(self, reason, detail=None):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


class EntraClient:
    """Starts and finishes sign-ins against one HPH tenant.

    Only the ID token is used. HPH keeps no Microsoft access or refresh tokens: it does not call
    Microsoft APIs for the user, and its own session takes over after sign-in.

    Parameters
    ----------
    tenant_id : str
        HPH's directory (tenant) id; sign-in is limited to this tenant.
    client_id : str
        Application (client) id of HPH's app registration.
    client_credential : str or dict
        Client secret, or ``{"private_key": <PEM>, "thumbprint": <hex>}`` for a certificate.
    redirect_uri : str
        The callback URL registered for this environment.
    http_client : object, optional, default = None
        HTTP client for MSAL; tests pass a fake Microsoft.
    """

    def __init__(self, tenant_id, client_id, client_credential, redirect_uri, http_client=None):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_credential = client_credential
        self.redirect_uri = redirect_uri
        self.http_client = http_client

    @property
    def authority(self):
        return f"https://login.microsoftonline.com/{self.tenant_id}"

    def _application(self):
        import msal

        # A fresh application per call with an empty cache, so no Microsoft token outlives the
        # request; offline_access is excluded so Microsoft issues no refresh token.
        options = {"token_cache": msal.TokenCache(), "exclude_scopes": ["offline_access"]}
        if self.http_client is not None:
            options["http_client"] = self.http_client

        return msal.ConfidentialClientApplication(
            self.client_id,
            client_credential=self.client_credential,
            authority=self.authority,
            **options,
        )

    def begin(self):
        """Return MSAL's flow for a new sign-in; ``flow["auth_uri"]`` is where to send the user.

        Raises
        ------
        EntraSignInError
            If Microsoft cannot be reached.
        """
        import requests

        try:
            return self._application().initiate_auth_code_flow(
                scopes=[],
                redirect_uri=self.redirect_uri,
                response_mode="query",
                prompt="select_account",
            )
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            raise EntraSignInError("microsoft_unavailable", str(exc)) from exc

    def finish(self, flow, auth_response):
        """Redeem the code Microsoft sent back and return the validated ID token claims.

        MSAL checks the state, sends the PKCE verifier, and validates the ID token's issuer,
        audience and nonce; the token comes straight from Microsoft over TLS. The caller checks
        the tenant and expiry.

        Raises
        ------
        EntraSignInError
            If the response does not match the flow or Microsoft returns an error.
        """
        import requests

        try:
            result = self._application().acquire_token_by_auth_code_flow(flow, auth_response)
        except requests.RequestException as exc:
            raise EntraSignInError("microsoft_unavailable", str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            # MSAL raises ValueError for a state mismatch and RuntimeError (or its IdTokenError
            # subclass) for an ID token with the wrong issuer, audience or nonce.
            raise EntraSignInError("invalid_response", str(exc)) from exc

        if "error" in result:
            raise EntraSignInError(
                "microsoft_error", result.get("error_description") or result["error"]
            )

        claims = result.get("id_token_claims")
        if not claims:
            raise EntraSignInError("invalid_response", "Microsoft returned no ID token")

        return claims
