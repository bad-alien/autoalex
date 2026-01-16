import logging
import os
import requests
import musicbrainzngs
from PIL import Image
from io import BytesIO
from clients import clients
from config import Config

logger = logging.getLogger("Autoalex.PlexService")

class PlexService:
    def __init__(self):
        pass

    @property
    def plex(self):
        if not clients.plex:
            raise RuntimeError("Plex client not initialized")
        return clients.plex

    def get_server_info(self):
        return {
            "name": self.plex.friendlyName,
            "version": self.plex.version,
            "platform": self.plex.platform,
            "users": len(self.plex.systemUsers())
        }

    async def get_artist_completion(self, artist_name, user=None, tautulli_service=None):
        """
        Calculates the completion percentage for an artist with album breakdown.
        """
        music_libs = [lib for lib in self.plex.library.sections() if lib.type == 'artist']
        if not music_libs:
            return None

        # Search for artist
        artist = None
        for lib in music_libs:
            results = lib.search(artist_name, libtype='artist')
            if results:
                artist = results[0]
                break
        
        if not artist:
            return None

        all_tracks = artist.tracks()
        total_tracks = len(all_tracks)
        
        if total_tracks == 0:
            return None

        played_titles = set()
        total_play_count_user = 0
        
        if user and tautulli_service:
            # Fetch Tautulli History
            history = await tautulli_service.get_history(user=user, artist_name=artist_name)
            
            if history and 'data' in history:
                data = history['data']
                total_play_count_user = len(data)
                
                for play in data:
                    if play.get('rating_key'):
                        played_titles.add(str(play['rating_key']))
        else:
            # Fallback to Owner ViewCount
            for track in all_tracks:
                if track.viewCount > 0:
                    played_titles.add(str(track.ratingKey))
                    total_play_count_user += track.viewCount

        # Calculate Album Breakdown
        album_stats = []
        for album in artist.albums():
            album_tracks = album.tracks()
            if not album_tracks:
                continue
                
            album_total = len(album_tracks)
            album_played = 0
            
            for track in album_tracks:
                if str(track.ratingKey) in played_titles:
                    album_played += 1
            
            if album_total > 0:
                percentage = (album_played / album_total) * 100
                album_stats.append({
                    'title': album.title,
                    'played': album_played,
                    'total': album_total,
                    'percent': percentage,
                    'year': album.year or 0,
                    'thumb_path': album.thumb
                })

        # Sort albums by Completion Percentage (descending)
        album_stats.sort(key=lambda x: x['percent'], reverse=True)

        # Global Stats
        artist_track_keys = {str(t.ratingKey) for t in all_tracks}
        unique_played = len(played_titles.intersection(artist_track_keys))
        global_percentage = (unique_played / total_tracks) * 100

        return {
            'artist': artist.title,
            'artist_thumb_path': artist.thumb,  # Just the path, we'll download separately
            'user': user,
            'global_percent': global_percentage,
            'unique_played': unique_played,
            'total_tracks': total_tracks,
            'total_plays': total_play_count_user,
            'albums': album_stats
        }

    def create_album_strip(self, albums: list, save_path: str, max_albums: int = 8, thumb_size: int = 100) -> bool:
        """
        Creates a horizontal strip of album thumbnails.

        Args:
            albums: List of album dicts with 'thumb_path' key
            save_path: Where to save the composite image
            max_albums: Maximum number of albums to include
            thumb_size: Size of each thumbnail (square)

        Returns:
            True if successful, False otherwise
        """
        albums_with_thumbs = [a for a in albums if a.get('thumb_path')][:max_albums]

        if not albums_with_thumbs:
            return False

        try:
            images = []
            for album in albums_with_thumbs:
                url = f"{Config.PLEX_URL}{album['thumb_path']}?X-Plex-Token={Config.PLEX_TOKEN}"
                response = requests.get(url, timeout=10)
                response.raise_for_status()

                img = Image.open(BytesIO(response.content))
                img = img.convert('RGB')
                img = img.resize((thumb_size, thumb_size), Image.Resampling.LANCZOS)
                images.append(img)

            if not images:
                return False

            # Create horizontal strip
            strip_width = len(images) * thumb_size
            strip = Image.new('RGB', (strip_width, thumb_size))

            for i, img in enumerate(images):
                strip.paste(img, (i * thumb_size, 0))

            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            strip.save(save_path, 'JPEG', quality=90)
            logger.info(f"Created album strip with {len(images)} albums")
            return True

        except Exception as e:
            logger.warning(f"Failed to create album strip: {e}")
            return False

    def download_thumb(self, thumb_path: str, save_path: str) -> bool:
        """
        Downloads a thumbnail from Plex to a local file.

        Args:
            thumb_path: The Plex thumb path (e.g., /library/metadata/123/thumb/456)
            save_path: Where to save the downloaded image

        Returns:
            True if successful, False otherwise
        """
        if not thumb_path:
            return False

        try:
            url = f"{Config.PLEX_URL}{thumb_path}?X-Plex-Token={Config.PLEX_TOKEN}"
            response = requests.get(url, timeout=10)
            response.raise_for_status()

            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            with open(save_path, 'wb') as f:
                f.write(response.content)

            logger.info(f"Downloaded thumbnail to {save_path}")
            return True
        except Exception as e:
            logger.warning(f"Failed to download thumbnail: {e}")
            return False

    def create_playlist_from_rating(self, min_rating=8.0, playlist_name="Top Rated"):
        """
        Creates or updates a playlist with tracks matching the rating.
        Plex uses a 10-point scale for API (user sees 5 stars). 4 stars = 8.0.
        """
        logger.info(f"Syncing playlist '{playlist_name}' with tracks rating >= {min_rating}")
        
        # 1. Search all tracks with userRating >= min_rating
        # We need to check all music libraries
        music_libs = [lib for lib in self.plex.library.sections() if lib.type == 'artist']
        all_top_tracks = []
        
        for lib in music_libs:
            # Plex API allows filtering by userRating
            # Note: 'userRating' filter might need to be 'userRating>>' for greater than
            tracks = lib.search(
                libtype='track',
                filters={'userRating>>': min_rating - 0.1} # -0.1 to include exact match if float issues
            )
            all_top_tracks.extend(tracks)
            
        logger.info(f"Found {len(all_top_tracks)} tracks with rating >= {min_rating}")
        
        if not all_top_tracks:
            return 0

        # 2. Check if playlist exists
        playlist = None
        for pl in self.plex.playlists():
            if pl.title == playlist_name:
                playlist = pl
                break
        
        # 3. Create or Update
        if playlist:
            logger.info(f"Updating existing playlist: {playlist_name}")
            # It's often safer/easier to remove all and re-add for static playlists to ensure sync
            # Alternatively, we can diff. For MVP, we'll replace the items.
            playlist.removeItems(playlist.items())
            playlist.addItems(all_top_tracks)
        else:
            logger.info(f"Creating new playlist: {playlist_name}")
            self.plex.createPlaylist(playlist_name, items=all_top_tracks)
            
        return len(all_top_tracks)

    def search_track(self, query):
        """
        Searches for a track by name. Returns the best match or None.
        """
        # Strip surrounding quotes that Discord may include
        query = query.strip().strip('"\'')

        music_libs = [lib for lib in self.plex.library.sections() if lib.type == 'artist']

        for lib in music_libs:
            results = lib.search(query, libtype='track', limit=1)
            if results:
                return results[0]
        return None

    def download_track(self, track, save_dir):
        """
        Downloads the track file to the specified directory.
        Returns the full path to the downloaded file.
        """
        logger.info(f"Downloading track: {track.title}")
        
        # Ensure save_dir exists
        import os
        os.makedirs(save_dir, exist_ok=True)
        
        # download() saves the file to the current working directory or specified path
        # It usually returns the list of file paths.
        downloaded_files = track.download(savepath=save_dir)
        
        if downloaded_files:
            return downloaded_files[0]
        return None

    def enrich_jazz_album(self, query):
        """
        Enriches album metadata with instrumentalists from MusicBrainz.
        Returns the updated album object or None.
        """
        logger.info(f"Enriching metadata for query: {query}")
        
        # 1. Search in MusicBrainz
        try:
            mb_search = musicbrainzngs.search_releases(query, limit=1)
            if not mb_search.get('release-list'):
                logger.warning(f"No results found in MusicBrainz for: {query}")
                return None
            
            release = mb_search['release-list'][0]
            release_id = release['id']
            logger.info(f"Found MusicBrainz Release: {release['title']} ({release_id})")
            
            # 2. Get Release Details (credits)
            # We need 'artist-credits' and 'recording-level-rels' + 'work-level-rels' 
            # might be too deep. Let's try fetching release with 'artist-credits' and 'recordings'
            
            # A better approach for "Lineup":
            # Often the release group or release has artist credits. 
            # But specific instrumentalists are often on tracks (recordings).
            # Let's fetch the release with 'artist-credits' and 'recording-rels'
            
            details = musicbrainzngs.get_release_by_id(release_id, includes=['artist-credits', 'recordings'])
            
            # Extract primary artists first
            credits = []
            if 'artist-credit' in details['release']:
                for ac in details['release']['artist-credit']:
                    if isinstance(ac, dict) and 'artist' in ac:
                        credits.append(ac['artist']['name'])
                        
            # This is a simplification. Extracting a full jazz lineup often requires 
            # parsing the 'relations' on recordings which is heavy.
            # For this MVP, we will append the "Artist Credit" string and 
            # the disambiguation if available.
            
            enrichment_text = f"\n\n[Autoalex] MusicBrainz Identified: {details['release']['title']}"
            if 'date' in details['release']:
                enrichment_text += f" ({details['release']['date']})"
            
            # 3. Find Album in Plex
            # We assume the query passed in is close enough to finding it in Plex too, 
            # or we pass the Plex Rating Key. 
            # Ideally, the User provides a search string that finds ONE album in Plex.
            
            music_libs = [lib for lib in self.plex.library.sections() if lib.type == 'artist']
            target_album = None
            
            for lib in music_libs:
                results = lib.search(query, libtype='album')
                if results:
                    target_album = results[0]
                    break
            
            if not target_album:
                logger.warning("Album not found in Plex to update.")
                return None
            
            # 4. Update Plex Summary
            current_summary = target_album.summary or ""
            if "[Autoalex]" not in current_summary:
                new_summary = current_summary + enrichment_text
                target_album.edit(**{'summary': new_summary})
                target_album.reload()
                logger.info(f"Updated summary for {target_album.title}")
                return target_album
            else:
                logger.info("Album already enriched.")
                return target_album

        except Exception as e:
            logger.error(f"Error enriching album: {e}")
            return None
