# Autoplex Commands

All commands use the prefix `!plex`

---

### `!plex usage`
- Shows current Plex streaming activity from Tautulli
- Displays user, title, device, and quality for each active stream
- No arguments required

---

### `!plex completion "Artist Name" [username] [full]`
- Shows what percentage of an artist's discography has been played
- Optionally specify a username for user-specific stats
- Add `full` to show all albums instead of top 6

---

### `!plex compare "Artist Name" user1 user2`
- Head-to-head comparison of two users' listening progress
- Shows completion percentage, unique tracks, and total plays
- Visual progress bars for quick comparison

---

### `!plex sync_top`
- Syncs the "Top Rated" playlist with all 4+ star tracks
- Scans all music libraries for highly-rated songs
- Creates the playlist if it doesn't exist

---

### `!plex enrich "Album Name"`
- Enriches album metadata using MusicBrainz
- Adds artist credits and release info to the Plex summary
- Useful for jazz albums with multiple instrumentalists

---

### `!plex bassboost "Song Title"`
- AI-powered bass boost using Demucs stem separation
- Separates track into bass/drums/vocals/other, boosts bass +6dB
- Auto-compresses to fit Discord's 8MB upload limit
