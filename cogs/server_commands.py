# cogs/server_commands.py

import asyncio
import io
import logging
import re
from pathlib import Path

import aiohttp
import nextcord
from nextcord.ext import commands, tasks

from config import config
from utilities import (
    get_git_revision_short_hash,
    get_git_revision_short_hash_latest,
)
from tautulli_wrapper import Tautulli

# Configure logging for this module
logger = logging.getLogger("plexbot.server_commands")
logger.setLevel(logging.INFO)

# --- `plex logs` helpers ---------------------------------------------------

# A record starts with the "YYYY-MM-DD HH:MM:SS LEVEL " prefix from plexbot.py's log
# format; lines without it (traceback lines) belong to the record above.
_RECORD_START = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} (DEBUG|INFO|WARNING|ERROR|CRITICAL) ")
_LEVEL_WORDS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}
# Secrets embedded in logged URLs — aiohttp errors include the full request URL,
# and Tautulli/TMDB keys travel as query parameters.
_SECRET_PARAM = re.compile(r"((?:apikey|api_key|token|password)=)[^&\s'\"]+", re.IGNORECASE)
_REDACTED = "[REDACTED]"
# Leaves room in Discord's 2000-char message limit for the header and code fences.
_PREVIEW_BUDGET = 1800
_HEADER_SEARCH_MAX = 60


def _strip_quotes(text: str) -> str:
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _parse_logs_query(query: str | None) -> tuple[int | None, str | None]:
    """Split `plex logs` input into (minimum level, search text).

    A leading level word (`error`, `warn`, ...) is a minimum-level filter; the rest
    is a case-insensitive search. Quoted input is always search text.
    """
    query = (query or "").strip()
    if not query:
        return None, None
    min_level = None
    if query[0] not in "\"'":
        first, *rest = query.split(maxsplit=1)
        if first.lower() in _LEVEL_WORDS:
            min_level = _LEVEL_WORDS[first.lower()]
            query = rest[0] if rest else ""
    return min_level, _strip_quotes(query.strip()) or None


def _split_records(text: str) -> list[str]:
    """Group log lines into records, keeping traceback lines with their record."""
    records: list[str] = []
    for line in text.splitlines():
        if _RECORD_START.match(line) or not records:
            records.append(line)
        else:
            records[-1] += "\n" + line
    return records


def _record_level(record: str) -> int | None:
    match = _RECORD_START.match(record)
    return _LEVEL_WORDS[match.group(1).lower()] if match else None


def _filter_records(records: list[str], min_level: int | None, search: str | None) -> list[str]:
    needle = search.lower() if search else None
    matches = []
    for record in records:
        if min_level is not None:
            level = _record_level(record)
            if level is None or level < min_level:
                continue
        if needle and needle not in record.lower():
            continue
        matches.append(record)
    return matches


def _redact(text: str, secrets) -> str:
    """Replace configured secret values and secret-looking URL parameters."""
    values = {str(s) for s in secrets if s not in (None, "")}
    # Longest first, so a secret containing another is replaced whole.
    for value in sorted(values, key=len, reverse=True):
        text = text.replace(value, _REDACTED)
    return _SECRET_PARAM.sub(rf"\g<1>{_REDACTED}", text)


def _fit_tail(records: list[str], budget: int) -> list[str]:
    """Return the most recent records whose newline-joined length fits in budget.

    If even the newest record is too long, it is cut short rather than dropped.
    """
    shown: list[str] = []
    used = 0
    for record in reversed(records):
        cost = len(record) + (1 if shown else 0)
        if used + cost > budget:
            if not shown:
                shown.append(record[: budget - 1] + "…")
            break
        shown.append(record)
        used += cost
    return shown[::-1]


def _find_log_path(log: logging.Logger | None = None) -> Path | None:
    """Locate the file the bot logs to, from the first FileHandler up the logger tree."""
    current = log or logging.getLogger()
    while current:
        for handler in current.handlers:
            if isinstance(handler, logging.FileHandler):
                return Path(handler.baseFilename)
        if not current.propagate:
            break
        current = current.parent
    return None


def _format_file_size(size: int) -> str:
    if size >= 1_000_000:
        return f"{size / 1_000_000:.1f} MB"
    return f"{max(size / 1000, 0.1):.1f} KB"


class ServerCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tautulli: Tautulli = bot.shared_resources.get("tautulli")
        self.plex_embed_color = config.get("ui", "plex_embed_color", 0xE5A00D)
        self.plex_image = config.get("ui", "plex_image")
        self._display_streams = True  # presence alternates between stream stats and a help hint
        self.status_task.start()  # type: ignore[attr-defined]

    async def _log_startup_info(self):
        """Log Tautulli connectivity and the bot's git version, once, at startup."""
        try:
            r = await self.tautulli.get_home_stats()

            # These shell out to git (and the "latest" check hits the network),
            # so run them in threads — concurrently — to avoid blocking the event loop.
            local_commit, latest_commit = await asyncio.gather(
                asyncio.to_thread(get_git_revision_short_hash),
                asyncio.to_thread(get_git_revision_short_hash_latest),
            )
            # The git helpers return the sentinel "unknown" on failure (e.g. offline),
            # which is truthy — exclude it so a failed fetch isn't read as "outdated".
            up_to_date = ""
            known = local_commit not in ("", "unknown") and latest_commit not in ("", "unknown")
            if known and local_commit != latest_commit:
                up_to_date = "Version outdated. Consider running git pull"

            if Tautulli.check_response(r):
                logger.info(f"Logged in as {self.bot.user}")
                logger.info("Connection to Tautulli successful")
                logger.info(
                    f"Current PlexBot version ID: {local_commit or 'unknown'}; "
                    f"latest: {latest_commit or 'unknown'}; {up_to_date}"
                )
            else:
                logger.critical("Connection to Tautulli failed")
        except Exception as e:
            logger.error(f"Error during startup info logging: {e}")

    def cog_unload(self):
        # Stop the background presence loop. The shared Tautulli client is owned by
        # the bot (PlexBot.close()) and must not be closed here.
        self.status_task.cancel()  # type: ignore[attr-defined]

    @tasks.loop(seconds=15)
    async def status_task(self):
        """Update the bot's presence, alternating between stream stats and a help hint."""
        try:
            response = await self.tautulli.get_activity()
            if not Tautulli.check_response(response):
                logger.error("Failed to retrieve activity from Tautulli.")
                return
            data = Tautulli.get_response_data(response, {})
            stream_count = data.get("stream_count", 0)
            wan_bandwidth_mbps = round(data.get("wan_bandwidth", 0) / 1000, 1)

            if self._display_streams:
                activity_text = f"{stream_count} streams at {wan_bandwidth_mbps} mbps"
                activity_type = nextcord.ActivityType.playing
            else:
                activity_text = ": plex help"
                activity_type = nextcord.ActivityType.listening

            await self.bot.change_presence(
                activity=nextcord.Activity(type=activity_type, name=activity_text)
            )
            self._display_streams = not self._display_streams  # Toggle the display mode
        except Exception as e:
            logger.error(f"Error in status_task(): {e}")

    @status_task.before_loop  # type: ignore[attr-defined]
    async def _before_status_task(self):
        """Wait for the gateway, initialize Tautulli, and log startup info before the first tick."""
        await self.bot.wait_until_ready()
        self.tautulli.initialize()
        await self._log_startup_info()

    @commands.command()
    async def status(self, ctx):
        """Displays the status of the Plex server and other related information."""
        try:
            # Getting Tautulli server info
            server_info_response = await self.tautulli.get_server_info()
            if not Tautulli.check_response(server_info_response):
                await ctx.send("Failed to retrieve server info from Tautulli.")
                logger.error("Failed to retrieve server info from Tautulli.")
                return
            server_data = Tautulli.get_response_data(server_info_response, {})

            # Fetching Plex status from Plex API asynchronously
            async with aiohttp.ClientSession() as session:
                async with session.get("https://status.plex.tv/api/v2/status.json") as response:
                    if response.status == 200:
                        json_response = await response.json()
                        plex_status = json_response.get("status", {}).get("description", "Unknown")
                    else:
                        plex_status = "Plex status unavailable"

            # Setting up the embed message with server information and Plex status
            embed = nextcord.Embed(title="Plex Server Details", colour=self.plex_embed_color)
            embed.set_thumbnail(url=self.plex_image)
            embed.add_field(name="Server Name", value=server_data.get("pms_name", "Unknown"), inline=True)
            embed.add_field(
                name="Server Version",
                value=server_data.get("pms_version", "Unknown"),
                inline=True,
            )
            embed.add_field(
                name="Server IP",
                value=f"{server_data.get('pms_ip', '?')}:{server_data.get('pms_port', '?')}",
            )
            embed.add_field(name="Platform", value=server_data.get("pms_platform", "Unknown"))
            embed.add_field(name="Plex Pass", value=server_data.get("pms_plexpass", "Unknown"))
            embed.add_field(name="Plex API Status", value=plex_status)

            await ctx.send(embed=embed)

        except Exception as e:
            logger.error(f"Failed to retrieve server status: {e}")
            await ctx.send("Failed to retrieve server status.")

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def killstream(self, ctx, session_key: str = None, *, message: str = None):
        """Terminates a Plex stream based on the session key."""
        session_keys = []
        if session_key is None:
            activity = await self.tautulli.get_activity()
            if not Tautulli.check_response(activity):
                await ctx.send("Failed to retrieve current activity from Tautulli.")
                return
            sessions = Tautulli.get_response_data(activity, {}).get("sessions", [])
            for users in sessions:
                session_keys.append(f"\n**Session key:** {users['session_key']} is: **{users['user']}**,")
            await ctx.send(
                f"You provided no session keys, current users are: {''.join(session_keys)}\nYou can use `plex killstream [session_key] '[message]'` to kill a stream above;"
                "\nMessage will be passed to the user in a pop-up window on their Plex client.\n ⚠️ It is recommended to use 'apostrophes' around the message to avoid errors."
            )
            return
        try:
            r = await self.tautulli.terminate_session(session_key, message=message)
            if r == 400:
                await ctx.send(f"Could not find a stream with **{session_key}** or another error occurred")
                logger.warning(f"Failed to terminate session with key {session_key}.")
            elif r == 200:
                await ctx.send(
                    f"Killed stream with session_key: **{session_key}** and message if provided: **{message}**"
                )
                logger.info(f"Terminated session {session_key} with message: {message}")
            else:
                await ctx.send("An unexpected error occurred - check logs for more information.")
                logger.error(f"Unexpected response code {r} when terminating session {session_key}.")
        except Exception as e:
            logger.error(f"Exception occurred while terminating session {session_key}: {e}")
            await ctx.send("Failed to terminate the session due to an error.")

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def logs(self, ctx, *, query: str = None):
        """DMs you the most recent bot log records plus the full log file.

        Optionally filter: `plex logs [level] [search text]`, e.g. `plex logs error`,
        `plex logs tautulli`, `plex logs warn timed out`. Quote text to search for a
        level word itself: `plex logs "error"`.
        """
        min_level, search = _parse_logs_query(query)

        log_path = _find_log_path()
        if log_path is None or not log_path.exists():
            await ctx.send("No log file found.")
            return
        raw = await asyncio.to_thread(log_path.read_text, encoding="utf-8", errors="replace")
        text = _redact(raw, self._log_secrets())
        records = _split_records(text)
        if not records:
            await ctx.send("The log file is empty.")
            return

        filtered = min_level is not None or search is not None
        matches = _filter_records(records, min_level, search)
        # Escape code fences so a logged ``` can't break out of the preview block.
        shown = _fit_tail([r.replace("```", "``​`") for r in matches], _PREVIEW_BUDGET)

        full_log = text.encode("utf-8")
        files = [nextcord.File(io.BytesIO(full_log), filename="plexbot.log")]
        attached = f"full log attached ({_format_file_size(len(full_log))})"
        if filtered:
            parts = []
            if min_level is not None:
                parts.append(f"{logging.getLevelName(min_level)}+")
            if search:
                shown_search = search
                if len(search) > _HEADER_SEARCH_MAX:
                    shown_search = search[: _HEADER_SEARCH_MAX - 1] + "…"
                parts.append(f'matching "{shown_search}"')
            description = " ".join(parts)
            if not matches:
                header = f"No log records **{description}** · {attached}"
            else:
                header = (
                    f"**{description}** · {len(matches)} matches, showing last {len(shown)} · {attached}"
                )
            if len(matches) > len(shown):
                matches_log = ("\n".join(matches) + "\n").encode("utf-8")
                files.append(nextcord.File(io.BytesIO(matches_log), filename="plexbot-matches.log"))
        else:
            header = f"Last {len(shown)} of {len(records)} log records · {attached}"

        content = header + ("\n```\n" + "\n".join(shown) + "\n```" if shown else "")
        try:
            await ctx.author.send(content, files=files)
        except nextcord.Forbidden:
            await ctx.send("I couldn't DM you the logs — allow DMs from server members and try again.")
            return
        except nextcord.HTTPException as e:
            logger.error(f"Failed to DM logs to {ctx.author}: {e}")
            await ctx.send("Failed to send the logs — check the bot's console output.")
            return

        try:
            await ctx.message.add_reaction("✅")
        except nextcord.HTTPException:
            await ctx.send("Sent the logs to your DMs.")

    @staticmethod
    def _log_secrets():
        return [
            config.get("core", "token"),
            config.get("tautulli", "apikey"),
            config.get("tmdb", "apikey"),
            config.get("qbittorrent", "password"),
        ]


def setup(bot):
    bot.add_cog(ServerCommands(bot))
