# Autoalex Commands

Prefix: `!alex`

## Stats
- `usage` - active Plex streams
- `status` - Plex health check (shows logs if down)
- `completion "Artist" [user]` - discography completion %
- `compare "Artist" user1 user2` - listening battle
- `added [days] [user]` - recently added media with size breakdown (default: 7 days)

## Library
- `sync_top` - sync "Top Rated" playlist
- `enrich "Album"` - add MusicBrainz credits

## Playlists
- `recent-raves update` - sync shared "Recent Raves" playlist with latest 5-star tracks from contributors (WHS.IV, jac7k, rakbarut, Casey Stewart). Adds new tracks, caps at 50.
- `jam-jar sync` - sync collaborative "Jam Jar" playlist across members (WHS.IV, jac7k, rakbarut, Casey Stewart). Merges all tracks, deduplicates, sorted by date added. Removals sync when track is removed from all members' playlists.
- `staff-picks sync` - sync "Staff Picks" playlist to ALL users. Curators (WHS.IV, jac7k, rakbarut, Casey Stewart) add tracks which push to everyone.

## AI Remix
Stems: `bass`, `drums`, `vocals`, `other`

- `boost [stem] [dB?] "Song"` - amplify stem (default +4dB)
- `reduce [stem] [dB?] "Song"` - remove/attenuate stem (default -60dB removes it)

### Examples
```
!alex boost bass "Billie Jean"
!alex boost vocals 8 "Halo"
!alex reduce vocals "Song"       # karaoke (removes vocals)
!alex reduce drums 7 "Song"      # partially reduce drums
```
