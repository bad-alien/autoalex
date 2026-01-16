from plexapi.server import PlexServer
import musicbrainzngs
from config import Config
import logging

logger = logging.getLogger("Autoalex.Clients")

class Clients:
    def __init__(self):
        self.plex = None
        self.tautulli_config = {
            "url": Config.TAUTULLI_URL,
            "apikey": Config.TAUTULLI_API_KEY
        }

    def initialize_plex(self):
        try:
            logger.info(f"Connecting to Plex at {Config.PLEX_URL}...")
            self.plex = PlexServer(Config.PLEX_URL, Config.PLEX_TOKEN)
            logger.info(f"Connected to Plex: {self.plex.friendlyName} (version {self.plex.version})")
        except Exception as e:
            logger.error(f"Failed to connect to Plex: {e}")
            raise

    def initialize_musicbrainz(self):
        logger.info("Initializing MusicBrainz...")
        musicbrainzngs.set_useragent(
            Config.MUSICBRAINZ_APP_NAME,
            Config.MUSICBRAINZ_APP_VERSION,
            Config.MUSICBRAINZ_CONTACT
        )

clients = Clients()
