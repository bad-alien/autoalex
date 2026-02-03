import os
import discord
from discord.ext import commands
import asyncio
import logging
from config import Config
from clients import clients
from services.tautulli_service import TautulliService
from services.plex_service import PlexService
from services.remix_service import RemixService, VALID_STEMS, DEFAULT_GAIN_DB, MAX_GAIN_DB
from services.plex_monitor import PlexMonitor
from services.overseerr_service import OverseerrService

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("Autoalex")

# Validate Config
try:
    Config.validate()
except ValueError as e:
    logger.critical(str(e))
    exit(1)

# Initialize Services
tautulli_service = TautulliService()
plex_service = PlexService()
remix_service = RemixService()
overseerr_service = OverseerrService()
plex_monitor = PlexMonitor(
    plex_url=Config.PLEX_URL,
    container_name=Config.PLEX_CONTAINER_NAME,
    poll_interval=Config.PLEX_POLL_INTERVAL,
    alert_cooldown=Config.PLEX_ALERT_COOLDOWN,
    alert_channel_id=int(Config.DISCORD_ALERT_CHANNEL_ID) if Config.DISCORD_ALERT_CHANNEL_ID else None,
)

# Initialize Bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!alex ", intents=intents)

@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user} (ID: {bot.user.id})')

    # Initialize Plex Connection
    try:
        clients.initialize_plex()
    except Exception as e:
        logger.error("Could not connect to Plex on startup. Commands requiring Plex will fail.")

    # Initialize MusicBrainz
    clients.initialize_musicbrainz()

    # Start Plex Monitor
    async def send_alert(message: str):
        """Send alert to configured Discord channel."""
        if plex_monitor.alert_channel_id:
            channel = bot.get_channel(plex_monitor.alert_channel_id)
            if channel:
                await channel.send(message)
            else:
                logger.error(f"Alert channel {plex_monitor.alert_channel_id} not found")
        else:
            logger.warning("No alert channel configured - alerts will only be logged")

    plex_monitor.set_alert_callback(send_alert)
    await plex_monitor.start()

    logger.info('Autoalex is ready to serve.')

@bot.event
async def on_message(message):
    # DEBUG: Log every message seen
    logger.info(f"DEBUG: Message received from {message.author}: {message.content}")
    
    # This is crucial: without this line, commands won't work if on_message is defined
    await bot.process_commands(message)

@bot.command()
async def usage(ctx):
    """
    Shows current stream activity from Tautulli.
    """
    await ctx.typing()
    activity = await tautulli_service.get_activity()
    
    if not activity:
        await ctx.send("Unable to fetch activity from Tautulli.")
        return

    stream_count = int(activity.get('stream_count', 0))
    
    if stream_count == 0:
        embed = discord.Embed(title="Plex Usage", description="No active streams.", color=discord.Color.green())
        await ctx.send(embed=embed)
        return

    embed = discord.Embed(title=f"Plex Usage ({stream_count} Active)", color=discord.Color.blue())
    
    for session in activity.get('sessions', []):
        title = session.get('full_title') or session.get('title')
        user = session.get('user')
        device = session.get('player')
        quality = session.get('quality_profile')
        state = session.get('state') # playing, paused, buffering
        
        status_icon = "▶️" if state == 'playing' else "II" if state == 'paused' else "buffer"
        
        embed.add_field(
            name=f"{status_icon} {user}",
            value=f"**{title}**\nDevice: {device}\nQuality: {quality}",
            inline=False
        )
    
    await ctx.send(embed=embed)


@bot.command()
async def status(ctx):
    """
    Shows Plex server health status and recent logs if down.
    """
    await ctx.typing()

    status_data = await plex_monitor.check_status()

    if status_data["healthy"]:
        embed = discord.Embed(
            title="Plex Status",
            description="Plex is online and responding.",
            color=discord.Color.green()
        )
    else:
        embed = discord.Embed(
            title="Plex Status",
            description=f"Plex is **DOWN**: {status_data['error']}",
            color=discord.Color.red()
        )

    # Add monitoring info
    embed.add_field(
        name="Monitoring",
        value="Active" if status_data["monitoring"] else "Stopped",
        inline=True
    )
    embed.add_field(
        name="Mode",
        value="Mock (Dev)" if status_data["mock_mode"] else "Production",
        inline=True
    )
    embed.add_field(
        name="Last Alert",
        value=status_data["last_alert"],
        inline=True
    )

    # Show logs if Plex is down
    if not status_data["healthy"] and "logs" in status_data:
        logs = status_data["logs"]
        if len(logs) > 1000:
            logs = logs[-1000:] + "\n... (truncated)"
        embed.add_field(
            name="Recent Logs",
            value=f"```\n{logs}\n```",
            inline=False
        )

    await ctx.send(embed=embed)


@bot.command()
async def completion(ctx, artist_name: str, user: str = None):
    """
    Calculates percentage of artist's discography played.
    Usage: !alex completion "Aphex Twin" [username]
    """
    await ctx.typing()

    try:
        # We pass tautulli_service to enable user-specific lookups
        data = await plex_service.get_artist_completion(artist_name, user, tautulli_service)
    except RuntimeError:
        await ctx.send("Plex is not connected.")
        return

    if not data:
        await ctx.send(f"Artist '{artist_name}' not found in music libraries.")
        return

    # Unpack Data
    artist = data['artist']
    global_percent = data['global_percent']
    unique_played = data['unique_played']
    total_tracks = data['total_tracks']
    total_plays = data['total_plays']
    albums = data['albums']

    title = f"Artist Completion: {artist}"
    if user:
        title += f" ({user})"

    embed = discord.Embed(title=title, color=discord.Color.gold())

    # Download and attach artist thumbnail if available
    thumb_file = None
    if data.get('artist_thumb_path'):
        thumb_path = "/tmp/autoalex_thumb.jpg"
        if plex_service.download_thumb(data['artist_thumb_path'], thumb_path):
            thumb_file = discord.File(thumb_path, filename="thumb.jpg")
            embed.set_thumbnail(url="attachment://thumb.jpg")
    
    # Global Stats
    embed.add_field(name="Total Plays", value=str(total_plays), inline=True)
    embed.add_field(name="Unique Tracks", value=f"{unique_played} / {total_tracks} ({global_percent:.1f}%)", inline=True)
    
    # Global Progress Bar (no title)
    bar_length = 20
    filled_length = int(bar_length * global_percent // 100)
    bar = '█' * filled_length + '-' * (bar_length - filled_length)
    embed.add_field(name="\u200b", value=f"`[{bar}]`", inline=False)

    # Album Breakdown
    # Separation: 100% completed vs In Progress
    completed_albums = []
    in_progress_albums = []

    for album in albums:
        if album['percent'] >= 100:
            completed_albums.append(album)  # Keep full album dict for thumbs
        else:
            in_progress_albums.append(album)

    # Show Completed Albums first (right below progress bar)
    files_to_send = [thumb_file] if thumb_file else []
    strip_file = None

    if completed_albums:
        # Create smaller thumbnail strip (50px thumbnails)
        # Attached without set_image() so it appears above the embed
        strip_path = "/tmp/autoalex_album_strip.jpg"
        if plex_service.create_album_strip(completed_albums, strip_path, thumb_size=50):
            strip_file = discord.File(strip_path, filename="albums.jpg")
            files_to_send.append(strip_file)

        # List album names
        album_names = ", ".join(a['title'] for a in completed_albums)
        if len(album_names) > 1000:
            album_names = album_names[:1000] + "..."
        embed.add_field(name=f"Completed ({len(completed_albums)})", value=album_names, inline=False)

    # In Progress albums (no header)
    # Discord limit is 25 fields total
    # Used so far: 2 (Stats) + 1 (Bar) + 1 (Completed) = 4
    # Safe limit for albums = 18
    limit = 18
    count = 0
    for album in in_progress_albums:
        if count >= limit:
            remaining = len(in_progress_albums) - limit
            embed.add_field(name="...", value=f"And {remaining} more in-progress...", inline=False)
            break

        p = album['percent']
        mini_bar_len = 10
        mini_fill = int(mini_bar_len * p // 100)
        mini_bar = '▓' * mini_fill + '░' * (mini_bar_len - mini_fill)

        value_str = f"`{mini_bar}` **{p:.0f}%** ({album['played']}/{album['total']})"
        embed.add_field(name=f"{album['title']} ({album['year']})", value=value_str, inline=False)
        count += 1

    if files_to_send:
        await ctx.send(embed=embed, files=files_to_send)
    else:
        await ctx.send(embed=embed)

@bot.command()
async def sync_top(ctx):
    """
    Syncs 'Top Rated' playlist with 4+ star tracks.
    """
    await ctx.typing()
    try:
        count = plex_service.create_playlist_from_rating(min_rating=8.0)
        await ctx.send(f"✅ Synced 'Top Rated' playlist with {count} tracks.")
    except Exception as e:
        logger.error(f"Sync failed: {e}")
        await ctx.send("Failed to sync playlist. Check logs.")

@bot.command()
async def enrich(ctx, *, query: str):
    """
    Enriches an album's metadata from MusicBrainz.
    Usage: !alex enrich Kind of Blue
    """
    await ctx.typing()
    try:
        album = plex_service.enrich_jazz_album(query)
        if album:
            await ctx.send(f"✅ Enriched metadata for **{album.title}**.")
        else:
            await ctx.send(f"❌ Could not find album or enrich metadata for '{query}'.")
    except Exception as e:
        logger.error(f"Enrich failed: {e}")
        await ctx.send("An error occurred during enrichment.")

@bot.command()
async def compare(ctx, artist_name: str, user1: str, user2: str):
    """
    Compares two users' progress for an artist.
    Usage: !alex compare "Aphex Twin" user1 user2
    """
    await ctx.typing()
    
    try:
        # Fetch data for both users concurrently? 
        # For simplicity/safety with the shared service, we do sequential await.
        data1 = await plex_service.get_artist_completion(artist_name, user1, tautulli_service)
        data2 = await plex_service.get_artist_completion(artist_name, user2, tautulli_service)
    except RuntimeError:
        await ctx.send("Plex is not connected.")
        return

    if not data1 or not data2:
        await ctx.send(f"Could not fetch data for artist '{artist_name}'. Check names.")
        return

    # Unpack
    p1 = data1['global_percent']
    p2 = data2['global_percent']
    plays1 = data1['total_plays']
    plays2 = data2['total_plays']
    
    # Determine Winner
    if p1 > p2:
        winner = f"🏆 **{user1}** leads by {p1-p2:.1f}%"
        color = discord.Color.blue()
    elif p2 > p1:
        winner = f"🏆 **{user2}** leads by {p2-p1:.1f}%"
        color = discord.Color.red()
    else:
        winner = "🤝 It's a Tie!"
        color = discord.Color.gold()

    embed = discord.Embed(title=f"Battle: {data1['artist']}", description=winner, color=color)

    # Download and attach artist thumbnail if available
    thumb_file = None
    if data1.get('artist_thumb_path'):
        thumb_path = "/tmp/autoalex_thumb.jpg"
        if plex_service.download_thumb(data1['artist_thumb_path'], thumb_path):
            thumb_file = discord.File(thumb_path, filename="thumb.jpg")
            embed.set_thumbnail(url="attachment://thumb.jpg")
    
    # Side by Side Stats
    embed.add_field(name=f"👤 {user1}", value=f"**{p1:.1f}%**\n{data1['unique_played']} tracks\n{plays1} plays", inline=True)
    embed.add_field(name="VS", value="|", inline=True)
    embed.add_field(name=f"👤 {user2}", value=f"**{p2:.1f}%**\n{data2['unique_played']} tracks\n{plays2} plays", inline=True)
    
    # Visual Bar Comparison
    # Normalize to max 20 chars
    # If p1=50, p2=20 -> [█████-----] vs [██--------]
    
    def make_bar(percent):
        fill = int(20 * percent // 100)
        return '█' * fill + '░' * (20 - fill)

    embed.add_field(name="Visual Comparison", value=f"**{user1}**\n`[{make_bar(p1)}]`\n\n**{user2}**\n`[{make_bar(p2)}]`", inline=False)

    if thumb_file:
        await ctx.send(embed=embed, file=thumb_file)
    else:
        await ctx.send(embed=embed)

DEFAULT_REDUCE_DB = 60  # Effectively removes the stem by default


def parse_remix_args(args: str, default_gain: float = DEFAULT_GAIN_DB) -> tuple[str, float, str]:
    """
    Parse remix command arguments.

    Formats:
        stem "Song Title"           -> (stem, default_gain, song)
        stem 8 "Song Title"         -> (stem, 8, song)
        stem Song Title             -> (stem, default_gain, song)
        stem 8 Song Title           -> (stem, 8, song)

    Returns:
        (stem, gain_db, song_title)
    """
    parts = args.split(maxsplit=2)

    if len(parts) < 2:
        raise ValueError("Usage: `!alex boost/reduce [stem] [dB?] \"Song Title\"`")

    stem = parts[0].lower()

    if stem not in VALID_STEMS:
        raise ValueError(f"Invalid stem `{stem}`. Must be one of: {', '.join(VALID_STEMS)}")

    # Check if second part is a number (dB amount)
    try:
        gain_db = float(parts[1])
        # If it parsed as a number, song title is the rest
        if len(parts) < 3:
            raise ValueError("Missing song title")
        song_title = parts[2]
    except ValueError:
        # Second part is not a number, so it's part of the song title
        gain_db = default_gain
        song_title = " ".join(parts[1:])

    # Clean up quotes from song title
    song_title = song_title.strip('"\'')

    if abs(gain_db) > MAX_GAIN_DB:
        raise ValueError(f"Gain must be between -{MAX_GAIN_DB} and +{MAX_GAIN_DB} dB")

    return stem, gain_db, song_title


async def _process_remix(ctx, stem: str, gain_db: float, song_title: str, action: str):
    """
    Shared logic for boost and reduce commands.
    """
    await ctx.typing()

    # 1. Search
    msg = await ctx.send(f"Searching for **{song_title}**...")
    track = plex_service.search_track(song_title)

    if not track:
        await msg.edit(content=f"Track '{song_title}' not found in Plex.")
        return

    artist = track.originalTitle or track.grandparentTitle
    await msg.edit(content=f"Found **{track.title}** by {artist}. Downloading...")

    try:
        # 2. Download
        download_path = await asyncio.to_thread(
            plex_service.download_track,
            track,
            remix_service.temp_dir
        )

        if not download_path:
            await msg.edit(content="Failed to download file from Plex.")
            return

        # 3. Process with AI
        db_display = f"+{gain_db}" if gain_db > 0 else str(gain_db)
        await msg.edit(
            content=f"Processing audio (AI Separation)... {stem} {db_display}dB"
        )

        output_path = await asyncio.to_thread(
            remix_service.process_track,
            download_path,
            stem,
            gain_db
        )

        # 4. Upload
        await msg.edit(content="Uploading...")

        try:
            await ctx.send(
                content=f"**{track.title}** ({stem.capitalize()} {action})",
                file=discord.File(output_path)
            )
            await msg.delete()
        except discord.HTTPException as e:
            if e.code == 40005:
                await msg.edit(content="The processed file is too large for Discord.")
            else:
                await msg.edit(content=f"Upload failed: {e}")

    except ValueError as e:
        await msg.edit(content=str(e))
    except Exception as e:
        logger.error(f"Remix error: {e}")
        await msg.edit(content=f"An error occurred: {e}")
    finally:
        remix_service.cleanup()


@bot.command()
async def boost(ctx, *, args: str):
    """
    Boosts a stem (bass, drums, vocals, other) in a track using AI.
    Usage: !alex boost bass "Billie Jean"
           !alex boost vocals 8 "Halo"
    """
    try:
        stem, gain_db, song_title = parse_remix_args(args)
        # Ensure positive gain for boost
        gain_db = abs(gain_db)
        await _process_remix(ctx, stem, gain_db, song_title, "Boost")
    except ValueError as e:
        await ctx.send(str(e))


@bot.command()
async def reduce(ctx, *, args: str):
    """
    Reduces a stem (bass, drums, vocals, other) in a track using AI.
    Default: -60dB (effectively removes stem). Use lower values for partial reduction.
    Usage: !alex reduce vocals "Song"        # removes vocals
           !alex reduce drums 7 "Song"       # partially reduces drums
    """
    try:
        stem, gain_db, song_title = parse_remix_args(args, default_gain=DEFAULT_REDUCE_DB)
        # Ensure negative gain for reduce
        gain_db = -abs(gain_db)
        await _process_remix(ctx, stem, gain_db, song_title, "Reduce")
    except ValueError as e:
        await ctx.send(str(e))


def format_size(size_bytes: int) -> str:
    """Format bytes as human-readable size."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / (1024 ** 2):.1f} MB"
    else:
        return f"{size_bytes / (1024 ** 3):.1f} GB"


@bot.command()
async def added(ctx, days: int = 7, user: str = None):
    """
    Shows media added to Plex in the last N days, with size breakdown.
    Optionally filter by Overseerr requester.

    Usage: !alex added 7              # All additions in last 7 days
           !alex added 30 john        # John's requests in last 30 days
    """
    await ctx.typing()

    try:
        # Get recently added from Plex
        recent = plex_service.get_recently_added(days)
    except RuntimeError:
        await ctx.send("Plex is not connected.")
        return

    movies = recent["movies"]
    shows = recent["shows"]
    music = recent["music"]

    if not movies and not shows and not music:
        await ctx.send(f"No media added in the last {days} days.")
        return

    # Get Overseerr requests for requester tracking
    overseerr_requests = []
    tmdb_lookup = {}
    if overseerr_service.is_configured():
        overseerr_requests = await overseerr_service.get_requests(days)
        tmdb_lookup = overseerr_service.build_tmdb_lookup(overseerr_requests)

    # Aggregate shows by unique show (not per-episode)
    show_aggregates = {}
    for show in shows:
        key = show["rating_key"]
        if key not in show_aggregates:
            show_aggregates[key] = {
                "title": show["title"],
                "size_bytes": 0,
                "tmdb_id": show["tmdb_id"],
                "episode_count": 0
            }
        show_aggregates[key]["size_bytes"] += show["size_bytes"]
        show_aggregates[key]["episode_count"] += 1

    aggregated_shows = list(show_aggregates.values())

    # Match items with requesters
    def get_requester(tmdb_id: int, media_type: str) -> str:
        if not tmdb_id:
            return "Manual/Admin"
        key = (media_type, tmdb_id)
        return tmdb_lookup.get(key, "Manual/Admin")

    # Attach requesters
    for movie in movies:
        movie["requester"] = get_requester(movie["tmdb_id"], "movie")

    for show in aggregated_shows:
        show["requester"] = get_requester(show["tmdb_id"], "tv")

    # Filter by user if specified
    if user:
        movies = [m for m in movies if m["requester"].lower() == user.lower()]
        aggregated_shows = [s for s in aggregated_shows if s["requester"].lower() == user.lower()]

        if not movies and not aggregated_shows:
            await ctx.send(f"No media found for user '{user}' in the last {days} days.")
            return

    # Calculate totals
    movie_count = len(movies)
    movie_size = sum(m["size_bytes"] for m in movies)
    # For TV, count episodes from aggregated shows (respects user filter)
    episode_count = sum(s["episode_count"] for s in aggregated_shows)
    show_size = sum(s["size_bytes"] for s in aggregated_shows)
    music_count = len(music)
    music_size = recent.get("music_total_size", 0)
    total_count = movie_count + episode_count + music_count
    total_size = movie_size + show_size + music_size

    # Aggregate by requester
    requester_stats = {}
    for movie in movies:
        req = movie["requester"]
        if req not in requester_stats:
            requester_stats[req] = {"count": 0, "size": 0}
        requester_stats[req]["count"] += 1
        requester_stats[req]["size"] += movie["size_bytes"]

    for show in aggregated_shows:
        req = show["requester"]
        if req not in requester_stats:
            requester_stats[req] = {"count": 0, "size": 0}
        requester_stats[req]["count"] += show["episode_count"]  # Count episodes, not shows
        requester_stats[req]["size"] += show["size_bytes"]

    # Sort requesters by size descending
    sorted_requesters = sorted(
        requester_stats.items(),
        key=lambda x: x[1]["size"],
        reverse=True
    )

    # Build embed
    title = f"Library Additions (Last {days} Days)"
    if user:
        title += f" - {user}"

    embed = discord.Embed(title=title, color=discord.Color.blue())

    # Summary stats
    summary = f"**Movies:** {movie_count} ({format_size(movie_size)})\n"
    summary += f"**TV Episodes:** {episode_count} ({format_size(show_size)})\n"
    summary += f"**Music:** {music_count} albums ({format_size(music_size)})\n"
    summary += f"**Total:** {total_count} items ({format_size(total_size)})"
    embed.add_field(name="Summary", value=summary, inline=False)

    # Top requesters (only if not filtered by user and we have multiple)
    if not user and len(sorted_requesters) > 1:
        requester_lines = []
        for req_name, stats in sorted_requesters[:5]:  # Top 5
            requester_lines.append(
                f"• {req_name}: {stats['count']} items ({format_size(stats['size'])})"
            )
        if requester_lines:
            embed.add_field(
                name="Top Requesters",
                value="\n".join(requester_lines),
                inline=False
            )

    # Note if Overseerr not configured
    if not overseerr_service.is_configured():
        embed.set_footer(text="Overseerr not configured - requester data unavailable")

    await ctx.send(embed=embed)


@bot.group(name="recent-raves", invoke_without_command=True)
async def recent_raves(ctx):
    """
    Manages the shared 'Recent Raves' playlist.
    Subcommands: update
    """
    await ctx.send("Usage: `!alex recent-raves update`")

@recent_raves.command(name="update")
async def recent_raves_update(ctx):
    """
    Updates the 'Recent Raves' playlist with latest 5-star tracks.
    Contributors: WHS.IV, jac7k, rakbarut, Casey Stewart
    Adds new tracks to each contributor's playlist (max 50).
    """
    await ctx.typing()

    contributors = ["WHS.IV", "jac7k", "rakbarut", "Casey Stewart"]

    try:
        result = await asyncio.to_thread(
            plex_service.update_recent_raves,
            contributors=contributors
        )

        if result['total'] == 0:
            await ctx.send("⚠️ No 5-star tracks found from contributors.")
            return

        # Build embed with track list
        if result['added'] > 0:
            title = f"Recent Raves Updated (+{result['added']} new)"
            color = discord.Color.green()
        else:
            title = "Recent Raves (up to date)"
            color = discord.Color.blue()

        embed = discord.Embed(title=title, color=color)

        # Format track list (Discord has 4096 char limit for description)
        track_lines = []
        for i, t in enumerate(result['tracks'], 1):
            # Truncate long titles/artists
            title_str = t['title'][:30] + '..' if len(t['title']) > 32 else t['title']
            artist_str = t['artist'][:18] + '..' if len(t['artist']) > 20 else t['artist']
            line = f"`{i:2}.` **{title_str}** - {artist_str} ({t['user'][:8]})"
            track_lines.append(line)

        # Split into chunks if needed (embed field limit is 1024 chars)
        chunk_size = 10
        for chunk_idx in range(0, len(track_lines), chunk_size):
            chunk = track_lines[chunk_idx:chunk_idx + chunk_size]
            field_name = f"Tracks {chunk_idx + 1}-{min(chunk_idx + chunk_size, len(track_lines))}"
            embed.add_field(name=field_name, value="\n".join(chunk), inline=False)

        embed.set_footer(text=f"{result['total']} tracks total • 5-star ratings only")

        await ctx.send(embed=embed)

    except Exception as e:
        logger.error(f"Recent Raves update failed: {e}")
        await ctx.send(f"❌ Update failed: {e}")


@bot.group(name="jam-jar", invoke_without_command=True)
async def jam_jar(ctx):
    """
    Manages the shared 'Jam Jar' collaborative playlist.
    Subcommands: sync
    """
    await ctx.send("Usage: `!alex jam-jar sync`")

@jam_jar.command(name="sync")
async def jam_jar_sync(ctx):
    """
    Syncs the 'Jam Jar' playlist across all members.
    Merges tracks from all members, deduplicates, and pushes to everyone.
    Members: WHS.IV, jac7k, rakbarut, Casey Stewart
    """
    await ctx.typing()

    members = ["WHS.IV", "jac7k", "rakbarut", "Casey Stewart"]

    try:
        result = await asyncio.to_thread(
            plex_service.sync_jam_jar,
            members=members
        )

        if result['total'] == 0:
            await ctx.send("Jam Jar is empty. Add some tracks and sync again.")
            return

        # Build embed with track list
        embed = discord.Embed(
            title="Jam Jar Synced",
            description=f"Merged playlist across {len(members)} members",
            color=discord.Color.purple()
        )

        # Format track list
        track_lines = []
        for i, t in enumerate(result['tracks'], 1):
            title_str = t['title'][:30] + '..' if len(t['title']) > 32 else t['title']
            artist_str = t['artist'][:18] + '..' if len(t['artist']) > 20 else t['artist']
            line = f"`{i:2}.` **{title_str}** - {artist_str} ({t['user'][:8]})"
            track_lines.append(line)

        # Split into chunks (embed field limit is 1024 chars)
        chunk_size = 10
        for chunk_idx in range(0, min(len(track_lines), 50), chunk_size):  # Show max 50
            chunk = track_lines[chunk_idx:chunk_idx + chunk_size]
            field_name = f"Tracks {chunk_idx + 1}-{chunk_idx + len(chunk)}"
            embed.add_field(name=field_name, value="\n".join(chunk), inline=False)

        if len(track_lines) > 50:
            embed.add_field(name="...", value=f"And {len(track_lines) - 50} more tracks", inline=False)

        embed.set_footer(text=f"{result['total']} tracks total")

        await ctx.send(embed=embed)

    except Exception as e:
        logger.error(f"Jam Jar sync failed: {e}")
        await ctx.send(f"Jam Jar sync failed: {e}")


if __name__ == "__main__":
    try:
        bot.run(Config.DISCORD_TOKEN)
    except Exception as e:
        logger.error(f"Error running bot: {e}")
