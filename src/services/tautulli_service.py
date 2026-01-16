import aiohttp
import logging
from config import Config

logger = logging.getLogger("Autoalex.TautulliService")

class TautulliService:
    def __init__(self):
        self.base_url = Config.TAUTULLI_URL.rstrip('/')
        self.api_key = Config.TAUTULLI_API_KEY

    async def _request(self, cmd, params=None):
        if params is None:
            params = {}
        
        params['apikey'] = self.api_key
        params['cmd'] = cmd
        
        url = f"{self.base_url}/api/v2"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as response:
                    if response.status != 200:
                        logger.error(f"Tautulli API Error: HTTP {response.status}")
                        return None
                    
                    data = await response.json()
                    if data['response']['result'] != 'success':
                        logger.error(f"Tautulli API Error: {data['response'].get('message', 'Unknown error')}")
                        return None
                    
                    return data['response']['data']
        except Exception as e:
            logger.error(f"Failed to contact Tautulli: {e}")
            return None

    async def get_activity(self):
        """
        Fetches current activity from Tautulli.
        Cmd: get_activity
        """
        return await self._request('get_activity')

    async def get_history(self, user=None, rating_key=None, artist_name=None):
        """
        Fetches history. Can filter by user, specific item, or artist.
        Cmd: get_history
        """
        params = {'length': 5000} # Fetch up to 5000 items from history
        if user:
            params['user'] = user
        if rating_key:
            params['rating_key'] = rating_key
        if artist_name:
            # Tautulli allows searching/filtering by query or parent_rating_key
            # But 'search' param is easiest for artist name in history
            params['search'] = artist_name
            
        return await self._request('get_history', params)
