# Autoalex Commands

Prefix: `!alex`

## Stats
- `usage` - active Plex streams
- `completion "Artist" [user]` - discography completion %
- `compare "Artist" user1 user2` - listening battle

## Library
- `sync_top` - sync "Top Rated" playlist [not built]
- `enrich "Album"` - add MusicBrainz credits [not built]

## AI Remix
Stems: `bass` `drums` `vocals` `other`

- `boost [stem] [dB?] "Song"` - amplify stem (default +4dB)
- `reduce [stem] [dB?] "Song"` - attenuate stem (default -4dB)

```
!alex boost bass "Billie Jean"
!alex boost vocals 8 "Halo"
!alex reduce bass 60 "Song"      # backing track (removes bass)
```

