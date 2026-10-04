"""
OpenSky Network Authentication & Credential Manager
===================================================
Manages credentials for OpenSky Network REST API requests.

Supports:
1. OAuth2 Client Credentials Flow (OpenSky API Client: client_id & client_secret)
   - Automatically exchanges client_id/client_secret for Bearer tokens
   - Caches and refreshes tokens before 30-minute expiration
2. Direct API Key / Bearer Token (OPENSKY_API_KEY / OPENSKY_TOKEN)
3. Direct JSON credential file (credentials.json / opensky_credentials.json)
4. Legacy Basic Auth fallback (username & password)
5. Anonymous access (400 requests/day rate limit)
"""

import os
import json
import time
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"


class OpenSkyAuth:
    """Manages OpenSky API authentication, OAuth2 token lifecycle, and request headers."""

    def __init__(self, project_root: str = None):
        self.project_root = project_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        self.client_id: str | None = None
        self.client_secret: str | None = None
        self.api_token: str | None = None
        self.username: str | None = None
        self.password: str | None = None

        # OAuth2 token cache
        self.cached_access_token: str | None = None
        self.token_expires_at: float = 0.0

        self._discover_credentials()

    def _discover_credentials(self):
        """Discovers credentials from environment variables or local JSON files."""
        # 1. Check environment variables
        self.client_id = os.environ.get("OPENSKY_CLIENT_ID", "").strip() or None
        self.client_secret = os.environ.get("OPENSKY_CLIENT_SECRET", "").strip() or None
        self.api_token = (
            os.environ.get("OPENSKY_API_KEY", "").strip()
            or os.environ.get("OPENSKY_TOKEN", "").strip()
            or None
        )
        self.username = os.environ.get("OPENSKY_USERNAME", "").strip() or None
        self.password = os.environ.get("OPENSKY_PASSWORD", "").strip() or None

        # 2. Check JSON credential files if client_id/secret not in environment
        if not (self.client_id and self.client_secret) and not self.api_token:
            candidate_files = [
                os.path.join(self.project_root, "credentials.json"),
                os.path.join(self.project_root, "opensky_credentials.json"),
                os.path.join(self.project_root, "rpi", "credentials.json"),
                os.path.join(self.project_root, "rpi", "data", "credentials.json"),
                os.path.join(os.getcwd(), "credentials.json"),
            ]
            for path in candidate_files:
                if os.path.isfile(path):
                    try:
                        with open(path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            # Support various JSON key structures from OpenSky downloads
                            cid = data.get("client_id") or data.get("clientId") or data.get("client")
                            csec = data.get("client_secret") or data.get("clientSecret") or data.get("secret")
                            tok = data.get("access_token") or data.get("token") or data.get("api_key")
                            user = data.get("username") or data.get("user")
                            pw = data.get("password") or data.get("pass")

                            if cid and csec:
                                self.client_id = str(cid).strip()
                                self.client_secret = str(csec).strip()
                                break
                            elif tok:
                                self.api_token = str(tok).strip()
                                break
                            elif user and pw:
                                self.username = str(user).strip()
                                self.password = str(pw).strip()
                                break
                    except Exception:
                        pass

    def get_token(self) -> str | None:
        """
        Retrieves a valid OAuth2 bearer token.
        Fetches a new token if expired or not yet loaded.
        """
        if self.api_token:
            return self.api_token

        if not (self.client_id and self.client_secret):
            return None

        # Return cached token if valid (with 60-second safety margin)
        now = time.time()
        if self.cached_access_token and now < (self.token_expires_at - 60):
            return self.cached_access_token

        # Exchange client_id and client_secret for OAuth2 bearer token
        try:
            payload = {
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret
            }
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            res = requests.post(TOKEN_URL, data=payload, headers=headers, timeout=8, verify=False)
            res.raise_for_status()
            data = res.json()
            access_token = data.get("access_token")
            expires_in = int(data.get("expires_in", 1800))
            if access_token:
                self.cached_access_token = access_token
                self.token_expires_at = now + expires_in
                return access_token
        except Exception as e:
            print(f"[OpenSky Auth Warning] OAuth2 token exchange failed: {e}")

        return self.cached_access_token

    def get_headers(self) -> dict:
        """Returns HTTP headers including Authorization Bearer token if available."""
        token = self.get_token()
        headers = {
            "User-Agent": "MSP-Runway-Tracker/1.0"
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def get_basic_auth(self) -> tuple[str, str] | None:
        """Returns Basic Auth tuple if username/password are configured and no OAuth2 token."""
        if not self.client_id and not self.api_token and self.username and self.password:
            return (self.username, self.password)
        return None

    def is_authenticated(self) -> bool:
        """Returns True if any authentication credential is provided."""
        return bool((self.client_id and self.client_secret) or self.api_token or (self.username and self.password))

    def get_auth_status_str(self) -> str:
        """Returns human-readable description of current authentication mode and quota."""
        if self.client_id and self.client_secret:
            cid_masked = self.client_id[:4] + "..." + self.client_id[-2:] if len(self.client_id) > 6 else "active"
            return f"Authenticated via OAuth2 (Client: {cid_masked}) | Quota: ~4,000 req/day"
        elif self.api_token:
            return "Authenticated via API Token | Quota: ~4,000 req/day"
        elif self.username and self.password:
            user_masked = self.username[:3] + "***" if len(self.username) > 3 else "active"
            return f"Basic Auth (User: {user_masked}) | Quota: ~4,000 req/day"
        else:
            return "Anonymous (No credentials) | Quota: 400 req/day"
