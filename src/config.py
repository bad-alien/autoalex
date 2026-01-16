import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
    
    PLEX_URL = os.getenv("PLEX_URL")
    PLEX_TOKEN = os.getenv("PLEX_TOKEN")
    
    TAUTULLI_URL = os.getenv("TAUTULLI_URL")
    TAUTULLI_API_KEY = os.getenv("TAUTULLI_API_KEY")
    
    MUSICBRAINZ_APP_NAME = os.getenv("MUSICBRAINZ_APP_NAME", "Autoalex")
    MUSICBRAINZ_APP_VERSION = os.getenv("MUSICBRAINZ_APP_VERSION", "0.1.0")
    MUSICBRAINZ_CONTACT = os.getenv("MUSICBRAINZ_CONTACT", "")

    @staticmethod
    def validate():
        missing = []
        if not Config.DISCORD_TOKEN: missing.append("DISCORD_TOKEN")
        if not Config.PLEX_URL: missing.append("PLEX_URL")
        if not Config.PLEX_TOKEN: missing.append("PLEX_TOKEN")
        if not Config.TAUTULLI_URL: missing.append("TAUTULLI_URL")
        if not Config.TAUTULLI_API_KEY: missing.append("TAUTULLI_API_KEY")
        
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
