# Autoalex Specifications

## Feature: Shared "Recent Raves" Playlist

### Goal
Create a shared playlist "Recent Raves" on each contributor's account containing the 50 most recently rated 5-star tracks from the contributor group.

### Contributors
The following Home Users' ratings drive the content of the playlist:
- `WHS.IV`
- `jac7k`
- `rakbarut`
- `Casey Stewart`

### Recipients
The playlist is created/updated on **each contributor's account** (the 4 users above).

### Logic
1. **Collect** all 5-star tracks (rating = 10.0) from each contributor.
2. **Sort** combined tracks by `lastRatedAt` (newest first).
3. **Deduplicate**: Keep the most recently rated instance if a track appears multiple times.
4. **Truncate** to top **50** tracks.
5. **For each contributor**:
   - If playlist exists: **Add** new tracks (don't replace all).
   - If playlist is over 50 tracks after adding: **Trim** oldest.
   - If no playlist: **Create** with the 50 tracks.

### Command
`!alex recent-raves update`
- Triggers the update process manually.
- Reports new tracks added and total playlist size.