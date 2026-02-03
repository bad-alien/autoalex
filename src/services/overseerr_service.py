import aiohttp
import logging
from datetime import datetime, timedelta
from config import Config

logger = logging.getLogger("Autoalex.OverseerrService")


class OverseerrService:
    """Service for interacting with Overseerr API to get request information."""

    def __init__(self):
        self.base_url = Config.OVERSEERR_URL.rstrip('/') if Config.OVERSEERR_URL else None
        self.api_key = Config.OVERSEERR_API_KEY

    def is_configured(self) -> bool:
        """Check if Overseerr is configured."""
        return bool(self.base_url and self.api_key)

    async def _request(self, endpoint: str, params: dict = None) -> dict | None:
        """Make request to Overseerr API."""
        if not self.is_configured():
            return None

        url = f"{self.base_url}/api/v1{endpoint}"
        headers = {"X-Api-Key": self.api_key}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, params=params) as response:
                    if response.status != 200:
                        logger.error(f"Overseerr API Error: HTTP {response.status}")
                        return None
                    return await response.json()
        except Exception as e:
            logger.error(f"Failed to contact Overseerr: {e}")
            return None

    async def get_requests(self, days: int = 7) -> list[dict]:
        """
        Fetch all requests from Overseerr (we match by TMDB ID, not by date).

        The date filtering happens on the Plex side based on addedAt.
        We fetch all available requests to build a complete TMDB -> requester lookup.

        Returns list of dicts with:
        - tmdb_id: TMDB ID for matching with Plex
        - media_type: 'movie' or 'tv'
        - requested_by: Username who requested
        - requested_at: When it was requested
        """
        results = []

        # Fetch requests with pagination - get all available requests
        page = 1
        page_size = 100

        while True:
            data = await self._request("/request", {
                "take": page_size,
                "skip": (page - 1) * page_size,
                "sort": "added"
            })

            if not data or "results" not in data:
                break

            for req in data["results"]:
                media = req.get("media", {})
                requested_by = req.get("requestedBy", {})

                # Parse request date
                created_at = req.get("createdAt", "")
                try:
                    req_date = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    req_date = req_date.replace(tzinfo=None)
                except (ValueError, AttributeError):
                    req_date = None

                tmdb_id = media.get("tmdbId")
                if tmdb_id:  # Only include if we have a TMDB ID
                    results.append({
                        "tmdb_id": tmdb_id,
                        "media_type": media.get("mediaType"),  # 'movie' or 'tv'
                        "requested_by": requested_by.get("displayName") or requested_by.get("plexUsername") or "Unknown",
                        "requested_at": req_date
                    })

            # Check if we need more pages
            total = data.get("pageInfo", {}).get("results", 0)
            if page * page_size >= total:
                break
            page += 1

        return results

    async def get_user_requests(self, username: str, days: int = 7) -> list[dict]:
        """Get requests for a specific user within the date range."""
        all_requests = await self.get_requests(days)
        return [
            r for r in all_requests
            if r["requested_by"].lower() == username.lower()
        ]

    def build_tmdb_lookup(self, requests: list[dict]) -> dict:
        """
        Build a lookup dict from TMDB ID to requester info.

        Returns dict like:
        {
            ('movie', 12345): 'username',
            ('tv', 67890): 'username',
        }
        """
        lookup = {}
        for req in requests:
            key = (req["media_type"], req["tmdb_id"])
            lookup[key] = req["requested_by"]
        return lookup
