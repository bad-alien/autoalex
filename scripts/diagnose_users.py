#!/usr/bin/env python3
"""Diagnostic script to list Plex users and preview Recent Raves tracks."""

import sys
sys.path.insert(0, '/home/badalien/dev/autoplex/src')

from plexapi.server import PlexServer
from config import Config
from dotenv import load_dotenv

load_dotenv('/home/badalien/dev/autoplex/.env')

def get_user_tracks(plex, username, limit=20):
    """Get top rated tracks for a user."""
    try:
        user_plex = plex.switchUser(username)
        music_libs = [lib for lib in user_plex.library.sections() if lib.type == 'artist']

        tracks = []
        for lib in music_libs:
            results = lib.search(libtype='track', filters={'userRating>>': 7.9})
            for track in results:
                if hasattr(track, 'lastRatedAt') and track.lastRatedAt:
                    tracks.append({
                        'title': track.title,
                        'artist': track.grandparentTitle or track.originalTitle or 'Unknown',
                        'album': track.parentTitle or 'Unknown',
                        'rated_at': track.lastRatedAt,
                        'rating': track.userRating,
                        'key': track.ratingKey
                    })

        # Sort by rated date descending
        tracks.sort(key=lambda t: t['rated_at'], reverse=True)
        return tracks[:limit], len(tracks)
    except Exception as e:
        return [], f"Error: {e}"

def main():
    print("Connecting to Plex...")
    plex = PlexServer(Config.PLEX_URL, Config.PLEX_TOKEN)
    print(f"Connected to: {plex.friendlyName}\n")

    # The 3 users to analyze
    users = ["WHS.IV", "jac7k", "rakbarut"]

    for username in users:
        print("=" * 70)
        print(f"USER: {username}")
        print("=" * 70)

        tracks, total = get_user_tracks(plex, username, limit=20)

        if isinstance(total, str):  # Error case
            print(f"  {total}\n")
            continue

        print(f"Total 4/5 star tracks: {total}")
        print("-" * 70)
        print(f"{'#':>2} | {'Track':<35} | {'Artist':<20} | {'Rating':<5} | {'Rated'}")
        print("-" * 70)

        for i, t in enumerate(tracks, 1):
            stars = "★" * int(t['rating'] / 2)
            title = t['title'][:33] + ".." if len(t['title']) > 35 else t['title']
            artist = t['artist'][:18] + ".." if len(t['artist']) > 20 else t['artist']
            print(f"{i:2} | {title:<35} | {artist:<20} | {stars:<5} | {t['rated_at'].strftime('%Y-%m-%d')}")

        print()

    # Show balancing options
    print("=" * 70)
    print("BALANCING OPTIONS")
    print("=" * 70)
    print("""
1. ROUND-ROBIN: Take tracks alternating between users
   - e.g., 50 tracks = ~12-13 per user, interleaved by date

2. PER-USER CAP: Max N tracks per contributor
   - e.g., max 15 per user = 60 max, then fill rest by recency

3. EQUAL SPLIT: Force equal representation
   - e.g., 50 tracks = 12 per user (48 total) + 2 most recent overall

4. TIME WINDOW: Only tracks rated in last N days
   - e.g., last 30 days, then balanced within that window
""")

if __name__ == "__main__":
    main()
