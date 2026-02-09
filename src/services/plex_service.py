import logging
import os
import requests
import musicbrainzngs
from datetime import datetime, timedelta
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

    def get_recently_added(self, days: int = 7) -> dict:
        """
        Get recently added movies, TV shows, and music from Plex.

        Returns dict with:
        - movies: list of movie items with size and metadata
        - shows: list of show items with size and metadata
        - music: list of music items (albums/tracks) with size and metadata
        """
        cutoff = datetime.now() - timedelta(days=days)
        results = {"movies": [], "shows": [], "music": []}

        # Get movie, TV, and music libraries
        movie_libs = [lib for lib in self.plex.library.sections() if lib.type == 'movie']
        tv_libs = [lib for lib in self.plex.library.sections() if lib.type == 'show']
        music_libs = [lib for lib in self.plex.library.sections() if lib.type == 'artist']

        # Process movies - use high limit to get all recent items
        for lib in movie_libs:
            try:
                recent = lib.recentlyAdded(maxresults=2000)
                for item in recent:
                    if item.addedAt and item.addedAt >= cutoff:
                        size_bytes = self._get_item_size(item)
                        tmdb_id = self._get_tmdb_id(item)
                        results["movies"].append({
                            "title": item.title,
                            "year": item.year,
                            "added_at": item.addedAt,
                            "size_bytes": size_bytes,
                            "tmdb_id": tmdb_id,
                            "rating_key": item.ratingKey
                        })
            except Exception as e:
                logger.warning(f"Error getting recently added from {lib.title}: {e}")

        # Process TV shows - use searchEpisodes as recentlyAdded returns shows not episodes
        # Cache TMDB IDs by show rating key to avoid repeated API calls
        show_tmdb_cache = {}
        for lib in tv_libs:
            try:
                episodes = lib.searchEpisodes(sort='addedAt:desc', limit=5000)
                for item in episodes:
                    if item.addedAt and item.addedAt >= cutoff:
                        show_key = item.grandparentRatingKey if hasattr(item, 'grandparentRatingKey') else item.ratingKey
                        show_title = item.grandparentTitle if hasattr(item, 'grandparentTitle') else item.title

                        size_bytes = self._get_item_size(item)

                        # Use cached TMDB ID if available
                        if show_key not in show_tmdb_cache:
                            try:
                                show_tmdb_cache[show_key] = self._get_show_tmdb_id(item)
                            except Exception:
                                show_tmdb_cache[show_key] = None
                        tmdb_id = show_tmdb_cache[show_key]

                        results["shows"].append({
                            "title": show_title,
                            "episode_title": item.title if hasattr(item, 'grandparentTitle') else None,
                            "added_at": item.addedAt,
                            "size_bytes": size_bytes,
                            "tmdb_id": tmdb_id,
                            "rating_key": show_key
                        })
            except Exception as e:
                logger.warning(f"Error getting recently added from {lib.title}: {e}")

        # Process music - get albums for count, tracks for total size
        for lib in music_libs:
            try:
                # Get albums for counting
                albums = lib.searchAlbums(sort='addedAt:desc', limit=5000)
                for album in albums:
                    if album.addedAt and album.addedAt >= cutoff:
                        artist_name = album.parentTitle if hasattr(album, 'parentTitle') else "Unknown Artist"
                        results["music"].append({
                            "title": album.title,
                            "artist": artist_name,
                            "year": album.year,
                            "added_at": album.addedAt,
                            "size_bytes": 0,  # Size calculated separately via tracks
                            "rating_key": album.ratingKey
                        })
            except Exception as e:
                logger.warning(f"Error getting recently added albums from {lib.title}: {e}")

            # Get total music size by querying tracks directly (much faster)
            try:
                tracks = lib.searchTracks(sort='addedAt:desc', limit=10000)
                music_size = 0
                for track in tracks:
                    if track.addedAt and track.addedAt >= cutoff:
                        music_size += self._get_item_size(track)
                results["music_total_size"] = results.get("music_total_size", 0) + music_size
            except Exception as e:
                logger.warning(f"Error calculating music size from {lib.title}: {e}")

        return results

    def _get_item_size(self, item) -> int:
        """Get total file size for a Plex item in bytes."""
        try:
            total_size = 0
            for media in item.media:
                for part in media.parts:
                    if part.size:
                        total_size += part.size
            return total_size
        except Exception:
            return 0

    def _get_tmdb_id(self, item) -> int | None:
        """Extract TMDB ID from a Plex item's guids."""
        try:
            for guid in item.guids:
                if guid.id.startswith("tmdb://"):
                    return int(guid.id.replace("tmdb://", ""))
        except Exception:
            pass
        return None

    def _get_show_tmdb_id(self, item) -> int | None:
        """Extract TMDB ID for a TV show from an episode."""
        try:
            # Try to get the show object if we have an episode
            if hasattr(item, 'grandparentRatingKey'):
                # This is an episode, try to fetch show's guids
                show = self.plex.fetchItem(item.grandparentRatingKey)
                for guid in show.guids:
                    if guid.id.startswith("tmdb://"):
                        return int(guid.id.replace("tmdb://", ""))
            else:
                # This is the show itself
                for guid in item.guids:
                    if guid.id.startswith("tmdb://"):
                        return int(guid.id.replace("tmdb://", ""))
        except Exception:
            pass
        return None

    def _get_album_size(self, album) -> int:
        """Get total file size for all tracks in an album."""
        try:
            total_size = 0
            for track in album.tracks():
                total_size += self._get_item_size(track)
            return total_size
        except Exception:
            return 0

    def update_recent_raves(self, contributors: list[str], max_songs: int = 50, playlist_name: str = "Recent Raves") -> dict:
        """
        Aggregates recent 5-star tracks from contributors and updates the playlist for each contributor.
        Adds new tracks to existing playlist (doesn't replace all), caps at max_songs.

        Returns dict with 'added' count, 'total' count, and 'tracks' list.
        """
        logger.info(f"Updating '{playlist_name}' from contributors: {contributors}")

        all_rated_tracks = []
        track_users = {}  # Map ratingKey to username who rated it

        # 1. Collect 5-star tracks from each contributor
        for username in contributors:
            try:
                user_plex = self.plex.switchUser(username)
                music_libs = [lib for lib in user_plex.library.sections() if lib.type == 'artist']

                user_tracks = []
                for lib in music_libs:
                    # Plex API: 5 stars = 10.0, use 9.9 to catch floating point
                    results = lib.search(libtype='track', filters={'userRating>>': 9.9})
                    for track in results:
                        if hasattr(track, 'lastRatedAt') and track.lastRatedAt:
                            user_tracks.append(track)
                            track_users[track.ratingKey] = username

                logger.info(f"Fetched {len(user_tracks)} 5-star tracks for {username}")
                all_rated_tracks.extend(user_tracks)

            except Exception as e:
                logger.warning(f"Could not fetch ratings for user '{username}': {e}")

        if not all_rated_tracks:
            logger.warning("No 5-star tracks found across all contributors.")
            return {'added': 0, 'total': 0, 'tracks': []}

        # 2. Sort by lastRatedAt (descending) and deduplicate
        all_rated_tracks.sort(key=lambda t: t.lastRatedAt, reverse=True)

        unique_tracks = []
        seen_keys = set()

        for track in all_rated_tracks:
            if track.ratingKey not in seen_keys:
                unique_tracks.append(track)
                seen_keys.add(track.ratingKey)
                if len(unique_tracks) >= max_songs:
                    break

        logger.info(f"Compiled top {len(unique_tracks)} unique 5-star tracks.")

        if not unique_tracks:
            return {'added': 0, 'total': 0, 'tracks': []}

        # Build track info list for response
        track_list = []
        for track in unique_tracks:
            track_list.append({
                'title': track.title,
                'artist': track.grandparentTitle or track.originalTitle or 'Unknown',
                'user': track_users.get(track.ratingKey, 'Unknown'),
                'rated_at': track.lastRatedAt.strftime('%m/%d') if track.lastRatedAt else ''
            })

        # 3. Update playlist for each contributor (not all users)
        total_added = 0

        for username in contributors:
            try:
                user_plex = self.plex.switchUser(username)

                # Find existing playlist
                playlist = None
                for pl in user_plex.playlists():
                    if pl.title == playlist_name:
                        playlist = pl
                        break

                if playlist:
                    # Get existing track keys
                    existing_keys = {item.ratingKey for item in playlist.items()}

                    # Find new tracks to add
                    new_tracks = [t for t in unique_tracks if t.ratingKey not in existing_keys]

                    if new_tracks:
                        logger.info(f"Adding {len(new_tracks)} new tracks to '{playlist_name}' for {username}")
                        playlist.addItems(new_tracks)
                        total_added += len(new_tracks)

                        # Trim to max_songs if needed (remove from end = oldest)
                        current_items = playlist.items()
                        if len(current_items) > max_songs:
                            items_to_remove = current_items[max_songs:]
                            playlist.removeItems(items_to_remove)
                            logger.info(f"Trimmed playlist to {max_songs} tracks for {username}")
                    else:
                        logger.info(f"No new tracks to add for {username}")
                else:
                    # Create new playlist with all tracks
                    logger.info(f"Creating '{playlist_name}' for {username} with {len(unique_tracks)} tracks")
                    user_plex.createPlaylist(playlist_name, items=unique_tracks)
                    total_added += len(unique_tracks)

            except Exception as e:
                logger.error(f"Failed to update playlist for {username}: {e}")

        return {'added': total_added, 'total': len(unique_tracks), 'tracks': track_list}

    def sync_jam_jar(self, members: list[str], playlist_name: str = "Jam Jar") -> dict:
        """
        Syncs the Jam Jar playlist across all members.
        Merges all tracks (union), deduplicates, sorts by addedAt, pushes to all members.

        Returns dict with 'total' count and 'tracks' list.
        """
        logger.info(f"Syncing '{playlist_name}' across members: {members}")

        all_tracks = []
        track_info = {}  # ratingKey -> {track, addedAt, addedBy}

        # 1. Collect tracks from all members' Jam Jar playlists
        for username in members:
            try:
                user_plex = self.plex.switchUser(username)

                # Find Jam Jar playlist
                playlist = None
                for pl in user_plex.playlists():
                    if pl.title == playlist_name:
                        playlist = pl
                        break

                if playlist:
                    items = playlist.items()
                    logger.info(f"Found {len(items)} tracks in '{playlist_name}' for {username}")

                    for item in items:
                        key = item.ratingKey
                        # Track the earliest addedAt (who added it first)
                        if key not in track_info:
                            track_info[key] = {
                                'track': item,
                                'added_at': item.addedAt if hasattr(item, 'addedAt') else None,
                                'added_by': username,
                                'title': item.title,
                                'artist': item.grandparentTitle or item.originalTitle or 'Unknown'
                            }
                        else:
                            # Keep the earlier addedAt
                            existing = track_info[key]
                            if item.addedAt and (not existing['added_at'] or item.addedAt < existing['added_at']):
                                track_info[key]['added_at'] = item.addedAt
                                track_info[key]['added_by'] = username
                else:
                    logger.info(f"No '{playlist_name}' playlist found for {username}, will create")

            except Exception as e:
                logger.warning(f"Could not access playlist for user '{username}': {e}")

        if not track_info:
            logger.info("No tracks found in any Jam Jar playlist.")
            # Still create empty playlists for everyone
            for username in members:
                try:
                    user_plex = self.plex.switchUser(username)
                    existing = None
                    for pl in user_plex.playlists():
                        if pl.title == playlist_name:
                            existing = pl
                            break
                    if not existing:
                        # Can't create empty playlist, need at least one item
                        logger.info(f"No tracks to create '{playlist_name}' for {username}")
                except Exception as e:
                    logger.warning(f"Could not create playlist for {username}: {e}")

            return {'total': 0, 'tracks': []}

        # 2. Sort by addedAt (newest first)
        sorted_tracks = sorted(
            track_info.values(),
            key=lambda x: x['added_at'] if x['added_at'] else datetime.min,
            reverse=True
        )

        # Build track list for response
        track_list = []
        tracks_to_sync = []
        for t in sorted_tracks:
            tracks_to_sync.append(t['track'])
            track_list.append({
                'title': t['title'],
                'artist': t['artist'],
                'user': t['added_by'],
                'added_at': t['added_at'].strftime('%m/%d') if t['added_at'] else ''
            })

        logger.info(f"Merged Jam Jar has {len(tracks_to_sync)} unique tracks")

        # 3. Push merged list to all members
        for username in members:
            try:
                user_plex = self.plex.switchUser(username)

                # Find or create playlist
                playlist = None
                for pl in user_plex.playlists():
                    if pl.title == playlist_name:
                        playlist = pl
                        break

                if playlist:
                    # Clear and repopulate
                    current_items = playlist.items()
                    if current_items:
                        playlist.removeItems(current_items)
                    playlist.addItems(tracks_to_sync)
                    logger.info(f"Updated '{playlist_name}' for {username} with {len(tracks_to_sync)} tracks")
                else:
                    # Create new playlist
                    user_plex.createPlaylist(playlist_name, items=tracks_to_sync)
                    logger.info(f"Created '{playlist_name}' for {username} with {len(tracks_to_sync)} tracks")

            except Exception as e:
                logger.error(f"Failed to sync playlist for {username}: {e}")

        return {'total': len(tracks_to_sync), 'tracks': track_list}

    def sync_staff_picks(self, curators: list[str], playlist_name: str = "Staff Picks") -> dict:
        """
        Syncs the Staff Picks playlist from curators to ALL users.
        Merges curator playlists, deduplicates, and pushes to everyone.

        Returns dict with 'total' count, 'tracks' list, and 'users_updated' count.
        """
        logger.info(f"Syncing '{playlist_name}' from curators: {curators}")

        track_info = {}  # ratingKey -> {track, addedAt, addedBy}

        # 1. Collect tracks from curators' Staff Picks playlists
        for username in curators:
            try:
                user_plex = self.plex.switchUser(username)

                playlist = None
                for pl in user_plex.playlists():
                    if pl.title == playlist_name:
                        playlist = pl
                        break

                if playlist:
                    items = playlist.items()
                    logger.info(f"Found {len(items)} tracks in '{playlist_name}' for curator {username}")

                    for item in items:
                        key = item.ratingKey
                        if key not in track_info:
                            track_info[key] = {
                                'track': item,
                                'added_at': item.addedAt if hasattr(item, 'addedAt') else None,
                                'added_by': username,
                                'title': item.title,
                                'artist': item.grandparentTitle or item.originalTitle or 'Unknown'
                            }
                        else:
                            existing = track_info[key]
                            if item.addedAt and (not existing['added_at'] or item.addedAt < existing['added_at']):
                                track_info[key]['added_at'] = item.addedAt
                                track_info[key]['added_by'] = username
                else:
                    logger.info(f"No '{playlist_name}' playlist found for curator {username}")

            except Exception as e:
                logger.warning(f"Could not access playlist for curator '{username}': {e}")

        if not track_info:
            logger.info("No tracks found in any curator's Staff Picks.")
            return {'total': 0, 'tracks': [], 'users_updated': 0}

        # 2. Sort by addedAt (newest first)
        sorted_tracks = sorted(
            track_info.values(),
            key=lambda x: x['added_at'] if x['added_at'] else datetime.min,
            reverse=True
        )

        track_list = []
        tracks_to_sync = []
        for t in sorted_tracks:
            tracks_to_sync.append(t['track'])
            track_list.append({
                'title': t['title'],
                'artist': t['artist'],
                'user': t['added_by'],
                'added_at': t['added_at'].strftime('%m/%d') if t['added_at'] else ''
            })

        logger.info(f"Merged Staff Picks has {len(tracks_to_sync)} unique tracks")

        # 3. Get all users (Home users + admin)
        account = self.plex.myPlexAccount()
        all_users = account.users()
        home_users = [u for u in all_users if getattr(u, 'home', False)]

        users_updated = 0

        # Update admin's playlist first
        try:
            playlist = None
            for pl in self.plex.playlists():
                if pl.title == playlist_name:
                    playlist = pl
                    break

            if playlist:
                current_items = playlist.items()
                if current_items:
                    playlist.removeItems(current_items)
                playlist.addItems(tracks_to_sync)
            else:
                self.plex.createPlaylist(playlist_name, items=tracks_to_sync)

            users_updated += 1
            logger.info(f"Updated '{playlist_name}' for admin")
        except Exception as e:
            logger.error(f"Failed to update playlist for admin: {e}")

        # Update all home users
        for user in home_users:
            try:
                user_plex = self.plex.switchUser(user.title)

                playlist = None
                for pl in user_plex.playlists():
                    if pl.title == playlist_name:
                        playlist = pl
                        break

                if playlist:
                    current_items = playlist.items()
                    if current_items:
                        playlist.removeItems(current_items)
                    playlist.addItems(tracks_to_sync)
                else:
                    user_plex.createPlaylist(playlist_name, items=tracks_to_sync)

                users_updated += 1
                logger.info(f"Updated '{playlist_name}' for {user.title}")

            except Exception as e:
                logger.error(f"Failed to update playlist for {user.title}: {e}")

        return {'total': len(tracks_to_sync), 'tracks': track_list, 'users_updated': users_updated}
