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
        thumb_path = "/tmp/autoplex_thumb.jpg"
        if plex_service.download_thumb(data['artist_thumb_path'], thumb_path):
            thumb_file = discord.File(thumb_path, filename="thumb.jpg")
            embed.set_thumbnail(url="attachment://thumb.jpg")
    
    # Global Stats
    embed.add_field(name="Total Plays", value=str(total_plays), inline=True)
    embed.add_field(name="Unique Tracks", value=f"{unique_played} / {total_tracks} ({global_percent:.1f}%)", inline=True)
    
    # Global Progress Bar
    bar_length = 20
    filled_length = int(bar_length * global_percent // 100)
    bar = '█' * filled_length + '-' * (bar_length - filled_length)
    embed.add_field(name="Collection Progress", value=f"`[{bar}]`", inline=False)
    
    # Album Breakdown
    # Separation: 100% completed vs In Progress
    completed_albums = []
    in_progress_albums = []

    for album in albums:
        if album['percent'] >= 100:
            completed_albums.append(album)  # Keep full album dict for thumbs
        else:
            in_progress_albums.append(album)
            
    # Discord limit is 25 fields total
    # Used so far: 3 (Stats) + 1 (Visual Bar) + 1 (Header) = 5
    # Safe limit for albums = 18
    limit = 18
    
    embed.add_field(name="In Progress", value="\u200b", inline=False)
    
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
        
    # Show Completed Albums as text list + thumbnail strip
    files_to_send = [thumb_file] if thumb_file else []

    if completed_albums:
        # List album names
        album_names = ", ".join(a['title'] for a in completed_albums)
        if len(album_names) > 1000:
            album_names = album_names[:1000] + "..."
        embed.add_field(name=f"Completed ({len(completed_albums)})", value=album_names, inline=False)

        # Add thumbnail strip
        strip_path = "/tmp/autoplex_album_strip.jpg"
        if plex_service.create_album_strip(completed_albums, strip_path):
            strip_file = discord.File(strip_path, filename="albums.jpg")
            files_to_send.append(strip_file)
            embed.set_image(url="attachment://albums.jpg")

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
        thumb_path = "/tmp/autoplex_thumb.jpg"
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

def parse_remix_args(args: str) -> tuple[str, float, str]:
    """
    Parse remix command arguments.

    Formats:
        stem "Song Title"           -> (stem, DEFAULT_GAIN_DB, song)
        stem 8 "Song Title"         -> (stem, 8, song)
        stem Song Title             -> (stem, DEFAULT_GAIN_DB, song)
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
        gain_db = DEFAULT_GAIN_DB
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
    Usage: !alex reduce drums "In the Air Tonight"
           !alex reduce vocals 10 "Song Title"
    """
    try:
        stem, gain_db, song_title = parse_remix_args(args)
        # Ensure negative gain for reduce
        gain_db = -abs(gain_db)
        await _process_remix(ctx, stem, gain_db, song_title, "Reduce")
    except ValueError as e:
        await ctx.send(str(e))


if __name__ == "__main__":
    try:
        bot.run(Config.DISCORD_TOKEN)
    except Exception as e:
        logger.error(f"Error running bot: {e}")
