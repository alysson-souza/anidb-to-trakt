"""Trakt API client with OAuth2 Device Code authentication."""

import contextlib
import json
import logging
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

from .paths import get_token_path

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Trakt API endpoints
TRAKT_API_URL = "https://api.trakt.tv"
TRAKT_AUTH_URL = "https://trakt.tv"

# Default token storage location
DEFAULT_TOKEN_PATH = get_token_path()

# Rate limiting
POST_RATE_LIMIT = 1.0  # 1 call per second for POST/PUT/DELETE
GET_RATE_LIMIT = 0.3  # ~1000 calls per 5 minutes = 0.3 seconds between calls


class TraktAuthError(Exception):
    """Authentication error with Trakt."""

    pass


class TraktAPIError(Exception):
    """API error from Trakt."""

    def __init__(self, message: str, status_code: int = 0, retry_after: int = 0):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class TraktClient:
    """Trakt API client with OAuth2 Device Code authentication."""

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        token_path: Path | None = None,
    ):
        """Initialize Trakt client.

        Args:
            client_id: Trakt API client ID. Uses env var if None.
            client_secret: Trakt API client secret. Uses env var if None.
            token_path: Path to store tokens. Uses default if None.
        """
        self.client_id = client_id or os.getenv("TRAKT_CLIENT_ID")
        self.client_secret = client_secret or os.getenv("TRAKT_CLIENT_SECRET")
        self.token_path = token_path or DEFAULT_TOKEN_PATH

        if not self.client_id or not self.client_secret:
            raise TraktAuthError(
                "TRAKT_CLIENT_ID and TRAKT_CLIENT_SECRET must be set. "
                "Register an app at https://trakt.tv/oauth/applications"
            )

        self._tokens: dict = {}
        self._last_request_time: float = 0
        self._http_client: httpx.Client | None = None

        # Try to load existing tokens
        self._load_tokens()

    def _load_tokens(self) -> bool:
        """Load tokens from file.

        Returns:
            True if tokens were loaded successfully.
        """
        if not self.token_path.exists():
            return False

        try:
            with open(self.token_path) as f:
                self._tokens = json.load(f)
            logger.info("Loaded existing Trakt tokens")
            return True
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load tokens: {e}")
            return False

    def _save_tokens(self) -> None:
        """Save tokens to file."""
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.token_path, "w") as f:
            json.dump(self._tokens, f, indent=2)
        logger.info("Saved Trakt tokens")

    @property
    def is_authenticated(self) -> bool:
        """Check if we have valid tokens."""
        return bool(self._tokens.get("access_token"))

    @property
    def _access_token(self) -> str:
        """Get the access token, refreshing if needed."""
        if not self._tokens.get("access_token"):
            raise TraktAuthError("Not authenticated. Run 'auth' command first.")

        # Check if token is expired
        expires_at = self._tokens.get("expires_at", 0)
        if time.time() >= expires_at - 300:  # Refresh 5 min before expiry
            self._refresh_token()

        return self._tokens["access_token"]

    def _get_client(self) -> httpx.Client:
        """Get or create HTTP client."""
        if self._http_client is None:
            self._http_client = httpx.Client(timeout=30.0)
        return self._http_client

    def _get_headers(self, auth: bool = True) -> dict:
        """Get headers for API requests.

        Args:
            auth: Include authorization header if True.

        Returns:
            Headers dictionary.
        """
        headers = {
            "Content-Type": "application/json",
            "trakt-api-version": "2",
            "trakt-api-key": self.client_id,
        }
        if auth and self.is_authenticated:
            headers["Authorization"] = f"Bearer {self._access_token}"
        return headers

    def _rate_limit(self, is_post: bool = False) -> None:
        """Apply rate limiting.

        Args:
            is_post: True for POST/PUT/DELETE requests.
        """
        limit = POST_RATE_LIMIT if is_post else GET_RATE_LIMIT
        elapsed = time.time() - self._last_request_time
        if elapsed < limit:
            time.sleep(limit - elapsed)
        self._last_request_time = time.time()

    def _request(
        self,
        method: str,
        endpoint: str,
        data: dict | None = None,
        auth: bool = True,
        retries: int = 3,
    ) -> dict | list:
        """Make an API request with rate limiting and retry logic.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (without base URL)
            data: Request body for POST/PUT
            auth: Include authorization header
            retries: Number of retries on failure

        Returns:
            Response JSON data.

        Raises:
            TraktAPIError: On API errors.
        """
        url = f"{TRAKT_API_URL}{endpoint}"
        is_post = method.upper() in ("POST", "PUT", "DELETE")

        for attempt in range(retries):
            self._rate_limit(is_post)

            try:
                client = self._get_client()
                response = client.request(
                    method=method,
                    url=url,
                    headers=self._get_headers(auth),
                    json=data if data else None,
                )

                # Handle rate limiting
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 60))
                    logger.warning(f"Rate limited, waiting {retry_after}s")
                    time.sleep(retry_after)
                    continue

                # Handle errors
                if response.status_code >= 400:
                    raise TraktAPIError(
                        f"API error: {response.status_code} - {response.text}",
                        status_code=response.status_code,
                    )

                # Return empty dict for 204 No Content
                if response.status_code == 204:
                    return {}

                return response.json()

            except httpx.HTTPError as e:
                if attempt < retries - 1:
                    wait = 2**attempt  # Exponential backoff
                    logger.warning(f"Request failed, retrying in {wait}s: {e}")
                    time.sleep(wait)
                else:
                    raise TraktAPIError(f"Request failed: {e}") from e

        raise TraktAPIError("Max retries exceeded")

    # Authentication methods

    def start_device_auth(self) -> dict:
        """Start OAuth2 Device Code flow.

        Returns:
            Dictionary with device_code, user_code, verification_url.
        """
        response = self._get_client().post(
            f"{TRAKT_API_URL}/oauth/device/code",
            json={"client_id": self.client_id},
            headers={"Content-Type": "application/json"},
        )

        if response.status_code != 200:
            raise TraktAuthError(f"Failed to get device code: {response.text}")

        return response.json()

    def poll_for_token(self, device_code: str, interval: int = 5, expires_in: int = 600) -> bool:
        """Poll for access token after user authorization.

        Args:
            device_code: Device code from start_device_auth.
            interval: Polling interval in seconds.
            expires_in: Timeout in seconds.

        Returns:
            True if authentication was successful.
        """
        start_time = time.time()

        while time.time() - start_time < expires_in:
            time.sleep(interval)

            response = self._get_client().post(
                f"{TRAKT_API_URL}/oauth/device/token",
                json={
                    "code": device_code,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                headers={"Content-Type": "application/json"},
            )

            if response.status_code == 200:
                data = response.json()
                self._tokens = {
                    "access_token": data["access_token"],
                    "refresh_token": data["refresh_token"],
                    "expires_at": time.time() + data["expires_in"],
                    "created_at": time.time(),
                }
                self._save_tokens()
                return True

            elif response.status_code == 400:
                # Still waiting for user authorization
                continue

            elif response.status_code == 404:
                raise TraktAuthError("Invalid device code")

            elif response.status_code == 409:
                raise TraktAuthError("Code already used")

            elif response.status_code == 410:
                raise TraktAuthError("Code expired")

            elif response.status_code == 418:
                raise TraktAuthError("User denied access")

            elif response.status_code == 429:
                # Slow down
                interval = min(interval * 2, 30)

        raise TraktAuthError("Authentication timed out")

    def _refresh_token(self) -> None:
        """Refresh the access token."""
        if not self._tokens.get("refresh_token"):
            raise TraktAuthError("No refresh token available")

        response = self._get_client().post(
            f"{TRAKT_API_URL}/oauth/token",
            json={
                "refresh_token": self._tokens["refresh_token"],
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
                "grant_type": "refresh_token",
            },
            headers={"Content-Type": "application/json"},
        )

        if response.status_code != 200:
            raise TraktAuthError(f"Failed to refresh token: {response.text}")

        data = response.json()
        self._tokens = {
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "expires_at": time.time() + data["expires_in"],
            "created_at": time.time(),
        }
        self._save_tokens()
        logger.info("Refreshed Trakt access token")

    def revoke_token(self) -> None:
        """Revoke the current access token."""
        if not self._tokens.get("access_token"):
            return

        with contextlib.suppress(httpx.HTTPError):
            self._get_client().post(
                f"{TRAKT_API_URL}/oauth/revoke",
                json={
                    "token": self._tokens["access_token"],
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                headers={"Content-Type": "application/json"},
            )

        self._tokens = {}
        if self.token_path.exists():
            self.token_path.unlink()
        logger.info("Revoked Trakt access token")

    # API methods

    def get_user_profile(self) -> dict:
        """Get current user's profile.

        Returns:
            User profile data.
        """
        return self._request("GET", "/users/me")

    def get_user_ratings(self, media_type: str = "shows") -> list:
        """Get user's ratings.

        Args:
            media_type: 'shows' or 'movies'

        Returns:
            List of rated items.
        """
        return self._request("GET", f"/users/me/ratings/{media_type}")

    def get_user_watched(self, media_type: str = "shows") -> list:
        """Get user's watched items.

        Args:
            media_type: 'shows' or 'movies'

        Returns:
            List of watched items.
        """
        return self._request("GET", f"/users/me/watched/{media_type}")

    def get_show_progress(self, show_id: int) -> dict:
        """Get watched progress for a show.

        Args:
            show_id: Trakt show ID.

        Returns:
            Show progress data.
        """
        return self._request("GET", f"/shows/{show_id}/progress/watched")

    def sync_ratings(self, data: dict) -> dict:
        """Sync ratings to Trakt.

        Args:
            data: Ratings data in Trakt API format.

        Returns:
            Sync response with added/existing counts.
        """
        return self._request("POST", "/sync/ratings", data)

    def sync_history(self, data: dict) -> dict:
        """Sync watch history to Trakt.

        Args:
            data: History data in Trakt API format.

        Returns:
            Sync response with added/existing counts.
        """
        return self._request("POST", "/sync/history", data)

    def remove_ratings(self, data: dict) -> dict:
        """Remove ratings from Trakt.

        Args:
            data: Ratings data to remove.

        Returns:
            Sync response with deleted counts.
        """
        return self._request("POST", "/sync/ratings/remove", data)

    def search(self, query: str, search_type: str = "show") -> list:
        """Search for items on Trakt.

        Args:
            query: Search query.
            search_type: Type to search ('show', 'movie', etc.)

        Returns:
            List of search results.
        """
        return self._request(
            "GET",
            f"/search/{search_type}",
            auth=False,
        )

    def lookup_by_id(self, id_type: str, id_value: str | int) -> list:
        """Look up item by external ID.

        Args:
            id_type: ID type ('tvdb', 'imdb', 'tmdb')
            id_value: ID value

        Returns:
            List of matching items.
        """
        return self._request(
            "GET",
            f"/search/{id_type}/{id_value}",
            auth=False,
        )

    def close(self) -> None:
        """Close the HTTP client."""
        if self._http_client:
            self._http_client.close()
            self._http_client = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def interactive_auth(client: TraktClient) -> bool:
    """Run interactive device code authentication.

    Args:
        client: TraktClient instance.

    Returns:
        True if authentication was successful.
    """
    print("\n=== Trakt Authentication ===\n")

    try:
        auth_data = client.start_device_auth()
    except TraktAuthError as e:
        print(f"Error: {e}")
        return False

    user_code = auth_data["user_code"]
    verification_url = auth_data["verification_url"]
    device_code = auth_data["device_code"]
    interval = auth_data.get("interval", 5)
    expires_in = auth_data.get("expires_in", 600)

    print(f"1. Go to: {verification_url}")
    print(f"2. Enter code: {user_code}")
    print(f"\nWaiting for authorization (expires in {expires_in // 60} minutes)...")

    try:
        if client.poll_for_token(device_code, interval, expires_in):
            print("\n✓ Authentication successful!")
            profile = client.get_user_profile()
            print(f"  Logged in as: {profile.get('username', 'Unknown')}")
            return True
    except TraktAuthError as e:
        print(f"\nAuthentication failed: {e}")
        return False

    return False
