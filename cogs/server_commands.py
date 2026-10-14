# cogs/server_commands.py

import asyncio
import io
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiohttp
import nextcord
from nextcord.ext import commands, tasks

from config import config
from errors import LoggedModal, LoggedView
from utilities import (
    UserMappings,
    get_git_revision_short_hash,
    get_git_revision_short_hash_latest,
)
from slash_commands import plex, plexadmin
from tautulli_wrapper import Tautulli
from ui.cards import plex_web_link, session_title, session_user, stream_detail, stream_overview
from ui.embeds import ErrorKind, Reply, card, empty_state, error_state, service_down, set_thumbnail, success
from ui.format import is_truthy, join, plural, to_int, truncate
from ui.images import current_frame, mascot, plex_image, poster_key, poster_strip
from ui.theme import Colour

# Configure logging for this module
logger = logging.getLogger("plexbot.server_commands")
logger.setLevel(logging.INFO)

# --- /plexadmin logs helpers ---------------------------------------------------------

# A record starts with the "YYYY-MM-DD HH:MM:SS LEVEL " prefix from plexbot.py's log
# format; lines without it (traceback lines) belong to the record above.
_RECORD_START = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} (DEBUG|INFO|WARNING|ERROR|CRITICAL) ")
_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
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
    return _LEVELS[match.group(1).lower()] if match else None


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
                shown.append(truncate(record, budget))
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


def _build_logs_reply(text: str, min_level: int | None, search: str | None):
    """Build the /plexadmin logs message and attachments as (content, [(filename, bytes), ...])."""
    records = _split_records(text)
    filtered = min_level is not None or search is not None
    matches = _filter_records(records, min_level, search)
    # Escape code fences so a logged ``` can't break out of the preview block.
    shown = _fit_tail([r.replace("```", "``​`") for r in matches], _PREVIEW_BUDGET)

    full_log = text.encode("utf-8")
    files = [("plexbot.log", full_log)]
    attached = f"full log attached ({_format_file_size(len(full_log))})"
    if filtered:
        parts = []
        if min_level is not None:
            parts.append(f"{logging.getLevelName(min_level)}+")
        if search:
            parts.append(f'matching "{truncate(search, _HEADER_SEARCH_MAX)}"')
        description = " ".join(parts)
        if not matches:
            header = f"No log records **{description}** · {attached}"
        else:
            count = f"{len(matches)} match{'es' if len(matches) != 1 else ''}"
            header = f"**{description}** · {count}, showing last {len(shown)} · {attached}"
        if len(matches) > len(shown):
            files.append(("plexbot-matches.log", ("\n".join(matches) + "\n").encode("utf-8")))
    else:
        header = f"Last {len(shown)} of {len(records)} log records · {attached}"

    content = header + ("\n```\n" + "\n".join(shown) + "\n```" if shown else "")
    return content, files


# --- /plex watchers helpers -----------------------------------------------------

_NO_STREAM_PERMISSION = "Only stream managers can stop streams."
# Discord caps autocomplete results and select options at 25, and their labels at 100 chars.
_MAX_CHOICES = 25
_MAX_LABEL = 100


def _session_label(session: dict, suffix: str = "") -> str:
    """One-line "user — title" label for a stream, within Discord's label limit."""
    label = f"{session_user(session)} — {session_title(session)}"
    return truncate(label, _MAX_LABEL - len(suffix)) + suffix


def _session_choices(sessions: list[dict], query: str) -> dict[str, str]:
    """Map stream labels to session IDs for autocomplete / select menus, filtered by query."""
    needle = (query or "").lower()
    choices: dict[str, str] = {}
    for session in sessions:
        session_id = session.get("session_id")
        if not session_id:
            continue
        label = _session_label(session)
        if needle and needle not in label.lower():
            continue
        n = 2
        while label in choices:  # same user watching the same title on two devices
            label = _session_label(session, f" ({n})")
            n += 1
        choices[label] = str(session_id)
        if len(choices) == _MAX_CHOICES:
            break
    return choices


def _can_manage_streams(user, manager_ids) -> bool:
    """Admins, plus any user or role listed in discord.stream_manager_ids, may stop streams."""
    permissions = getattr(user, "guild_permissions", None)
    if permissions is not None and permissions.administrator:
        return True
    ids = set()
    for raw in manager_ids or []:
        try:
            ids.add(int(raw))
        except (TypeError, ValueError):
            continue
    if user.id in ids:
        return True
    return any(role.id in ids for role in getattr(user, "roles", []))


def _find_session(activity: dict, session_id: str) -> dict | None:
    return next((s for s in activity.get("sessions", []) if str(s.get("session_id")) == session_id), None)


class StopStreamModal(LoggedModal):
    """Asks for an optional message to the viewer, then stops the stream."""

    def __init__(self, cog: "ServerCommands", session_id: str, label: str, watchers_view: "WatchersView"):
        super().__init__(title=truncate(f"Stop: {label}", 45), timeout=300)
        self.cog = cog
        self.session_id = session_id
        self.watchers_view = watchers_view
        self.message_input = nextcord.ui.TextInput(
            label="Message to the viewer (optional)",
            style=nextcord.TextInputStyle.paragraph,
            required=False,
            max_length=200,
            placeholder="Shown on their Plex client when the stream stops",
        )
        self.add_item(self.message_input)

    async def callback(self, interaction: nextcord.Interaction):
        # Opened from the private detail card: acknowledge without a new message, then
        # edit that card into the result.
        await interaction.response.defer()
        ok, text = await self.cog.stop_stream(interaction.user, self.session_id, self.message_input.value)
        reply = success(text) if ok else error_state(ErrorKind.INPUT, text)
        await reply.edit(interaction)
        # Plex takes a moment to drop a stopped session from its activity list.
        await asyncio.sleep(2)
        await self.watchers_view.rerender()


class StreamDetailView(LoggedView):
    """Controls on a private stream detail card: Stop, for stream managers only."""

    def __init__(self, cog: "ServerCommands", session_id: str, label: str, watchers_view: "WatchersView",
                 can_stop: bool):
        super().__init__(timeout=600)
        self.cog = cog
        self.session_id = session_id
        self.label = label
        self.watchers_view = watchers_view
        if not can_stop:
            self.remove_item(self.stop_button)

    @nextcord.ui.button(label="Stop this stream", emoji="\U0001F6D1", style=nextcord.ButtonStyle.danger)
    async def stop_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        if not self.cog.can_manage(interaction.user):
            await error_state(ErrorKind.PERMISSION, _NO_STREAM_PERMISSION).send(interaction, ephemeral=True)
            return
        await interaction.response.send_modal(
            StopStreamModal(self.cog, self.session_id, self.label, self.watchers_view)
        )


class StreamDetailsSelect(nextcord.ui.StringSelect):
    def __init__(self, choices: dict[str, str]):
        options = [nextcord.SelectOption(label=label, value=sid) for label, sid in choices.items()]
        super().__init__(placeholder="Stream details…", options=options, row=0)

    async def callback(self, interaction: nextcord.Interaction):
        watchers_view: WatchersView = self.view
        await watchers_view.cog.show_stream_detail(interaction, self.values[0], watchers_view)


class WatchersView(LoggedView):
    """Controls under the public /plex watchers overview: stream details (anyone) and Refresh."""

    def __init__(self, cog: "ServerCommands", sessions: list, timeout: float = 600):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.message = None
        self.set_sessions(sessions)

    def set_sessions(self, sessions: list) -> None:
        for item in [child for child in self.children if isinstance(child, StreamDetailsSelect)]:
            self.remove_item(item)
        choices = _session_choices(sessions, "")
        if choices:
            self.add_item(StreamDetailsSelect(choices))

    # Not named refresh(): nextcord calls View.refresh(components) itself when the
    # message is edited, and overriding it crashed the gateway connection.
    async def rerender(self):
        """Re-render the public overview, e.g. after a stream was stopped or ended."""
        if self.message is None:
            return
        activity = await self.cog.fetch_activity()
        if activity is None:
            return
        reply = await self.cog.render_watchers(activity, self)
        try:
            await reply.edit_message(self.message)
        except nextcord.HTTPException as e:
            logger.warning(f"Could not refresh /plex watchers message: {e}")

    @nextcord.ui.button(label="Refresh", emoji="\U0001F504", style=nextcord.ButtonStyle.secondary, row=1)
    async def refresh_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        await interaction.response.defer()
        activity = await self.cog.fetch_activity()
        if activity is None:
            await service_down("Tautulli").send(interaction, ephemeral=True)
            return
        reply = await self.cog.render_watchers(activity, self)
        await reply.edit(interaction)

    async def on_timeout(self):
        # Expired controls are removed; the embed's "updated" time shows its age.
        if self.message:
            try:
                await self.message.edit(view=None)
            except nextcord.HTTPException:
                pass


# --- /plex status helpers ----------------------------------------------------


def library_totals(rows) -> dict:
    """Sum Tautulli's get_libraries rows into movie, show, episode, artist and track counts."""
    totals = {"movies": 0, "shows": 0, "episodes": 0, "artists": 0, "tracks": 0}
    for row in rows or []:
        kind = row.get("section_type")
        if kind == "movie":
            totals["movies"] += to_int(row.get("count"))
        elif kind == "show":
            totals["shows"] += to_int(row.get("count"))
            totals["episodes"] += to_int(row.get("child_count"))
        elif kind == "artist":
            totals["artists"] += to_int(row.get("count"))
            totals["tracks"] += to_int(row.get("child_count"))
    return totals


def build_status_reply(server: dict, libraries, activity: Optional[dict], update: Optional[dict],
                       plex_service: Optional[dict], *, now: datetime) -> Reply:
    incident = plex_service is not None and (plex_service.get("indicator") or "none") != "none"
    plex_pass = " · Plex Pass" if to_int(server.get("pms_plexpass")) else ""
    embed = card(
        title=server.get("pms_name") or "Plex Media Server",
        description=(
            f"Plex Media Server {server.get('pms_version') or 'unknown version'} "
            f"on {server.get('pms_platform') or 'unknown platform'}{plex_pass}"
        ),
        colour=Colour.WARNING if incident else Colour.PLAYING,
    )

    totals = library_totals(libraries)
    library = [
        f"\U0001F3AC {plural(totals['movies'], 'movie')}" if totals["movies"] else None,
        f"\U0001F4FA {plural(totals['shows'], 'show')} · {plural(totals['episodes'], 'episode')}"
        if totals["shows"] else None,
        f"\U0001F3B5 {plural(totals['artists'], 'artist')} · {plural(totals['tracks'], 'track')}"
        if totals["artists"] else None,
    ]
    library = [line for line in library if line]
    if library:
        embed.add_field(name="Library", value="\n".join(library), inline=True)

    if activity is None:
        watching = "Unavailable"
    else:
        streams = to_int(activity.get("stream_count"))
        bandwidth = to_int(activity.get("total_bandwidth"))
        watching = (
            join([plural(streams, "stream"), f"{bandwidth / 1000:.1f} Mbps" if bandwidth else None])
            if streams else "Nobody's watching"
        )
    embed.add_field(name="Activity", value=watching, inline=True)

    service = plex_service.get("description") if plex_service else "Status unavailable"
    embed.add_field(name="Plex services", value=("⚠️ " if incident else "") + service, inline=False)

    if update and is_truthy(update.get("update_available")):
        embed.add_field(
            name="Update available", value=f"Plex Media Server {update.get('version')}", inline=False
        )

    embed.timestamp = now
    art = mascot("mascot-server")
    set_thumbnail(embed, art)
    return Reply([embed], [art] if art else [])


class ServerCommands(commands.Cog):
    help_category = "watching"

    def __init__(self, bot):
        self.bot = bot
        self.tautulli: Tautulli = bot.shared_resources.get("tautulli")
        self.plex_embed_color = config.get("ui", "plex_embed_color", 0xE5A00D)
        self._pms_identifier: str | None = None  # cached by _server_id()
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
                activity_text = "/plex help"
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

    # --- /plex status ------------------------------------------------------

    async def _plex_service_status(self) -> Optional[dict]:
        """plex.tv's own service status (Statuspage format), or None if it can't be fetched."""
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get("https://status.plex.tv/api/v2/status.json") as response:
                    if response.status != 200:
                        return None
                    status = (await response.json()).get("status", {})
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            logger.warning(f"Could not fetch Plex service status: {e}")
            return None
        return {"indicator": status.get("indicator"), "description": status.get("description") or "Unknown"}

    @plex.subcommand(description="Show how the Plex server is doing: library, activity, updates")
    async def status(self, interaction: nextcord.Interaction):
        await interaction.response.defer()
        server_r, libraries_r, activity_r, update_r, plex_service = await asyncio.gather(
            self.tautulli.get_server_info(),
            self.tautulli.get_libraries(),
            self.tautulli.get_activity(),
            self.tautulli.get_pms_update(),
            self._plex_service_status(),
        )
        if not Tautulli.check_response(server_r):
            logger.error("Failed to retrieve server info from Tautulli.")
            await service_down("Tautulli").send(interaction, ephemeral=True, replace_placeholder=True)
            return
        server = Tautulli.get_response_data(server_r, {})
        self._pms_identifier = server.get("pms_identifier") or self._pms_identifier

        def data(response, default):
            if Tautulli.check_response(response):
                return Tautulli.get_response_data(response, default)
            return default

        reply = build_status_reply(
            server, data(libraries_r, []), data(activity_r, None), data(update_r, None), plex_service,
            now=nextcord.utils.utcnow(),
        )
        await reply.send(interaction)

    # --- /plex watchers ----------------------------------------------------

    def can_manage(self, user) -> bool:
        return _can_manage_streams(user, config.get("discord", "stream_manager_ids", []))

    async def fetch_activity(self) -> dict | None:
        response = await self.tautulli.get_activity()
        if not Tautulli.check_response(response):
            logger.error("Failed to retrieve activity from Tautulli.")
            return None
        return Tautulli.get_response_data(response, {})

    def _visible_sessions(self, activity: dict) -> list:
        mappings = UserMappings.load_user_mappings()
        ignored = {u["plex_username"] for u in mappings if u.get("ignore", False)}
        return [s for s in activity.get("sessions", []) if s.get("username") not in ignored]

    async def render_watchers(self, activity: dict, view: "WatchersView") -> Reply:
        """The public overview for the current activity, with its controls rebuilt to match."""
        sessions = self._visible_sessions(activity)
        view.set_sessions(sessions)
        if not sessions:
            reply = empty_state("Nobody's watching right now.", hint="Hit Refresh to check again.")
            reply.view = view
            return reply
        posters = await asyncio.gather(*(
            plex_image(self.tautulli, poster_key(s), filename=f"poster-{i}.jpg", width=200, height=300)
            for i, s in enumerate(sessions[:6])
        ))
        strip = await asyncio.to_thread(
            poster_strip, [p.data for p in posters if p], extra=max(0, len(sessions) - 6)
        )
        embed = stream_overview(
            sessions, bandwidth_kbps=to_int(activity.get("total_bandwidth")), strip=strip,
            updated=nextcord.utils.utcnow(),
        )
        return Reply([embed], [strip] if strip else [], view=view)

    async def _server_id(self) -> str | None:
        """The Plex server's machine identifier, for app.plex.tv links (cached)."""
        if self._pms_identifier is None:
            response = await self.tautulli.get_server_info()
            if Tautulli.check_response(response):
                self._pms_identifier = Tautulli.get_response_data(response, {}).get("pms_identifier") or ""
        return self._pms_identifier or None

    def _avatar_url(self, session: dict) -> str | None:
        """The viewer's Discord avatar when mapped (recognisable in the server), else their Plex one."""
        mapping = UserMappings.get_mapping_by_plex_username(session.get("username"))
        if mapping and mapping.get("discord_id"):
            user = self.bot.get_user(to_int(mapping["discord_id"]))
            if user:
                return user.display_avatar.url
        thumb = session.get("user_thumb") or ""
        return thumb if thumb.startswith("http") else None

    async def show_stream_detail(self, interaction: nextcord.Interaction, session_id: str,
                                 watchers_view: "WatchersView") -> None:
        await interaction.response.defer(ephemeral=True, with_message=True)
        activity = await self.fetch_activity()
        if activity is None:
            await service_down("Tautulli").send(interaction, ephemeral=True)
            return
        session = _find_session(activity, session_id)
        if session is None:
            await empty_state("That stream has ended.", hint="The list has been refreshed.").send(
                interaction, ephemeral=True
            )
            await watchers_view.rerender()
            return

        frame, poster = await asyncio.gather(
            current_frame(self.tautulli, session, filename="frame.jpg"),
            plex_image(self.tautulli, poster_key(session), filename="poster.jpg"),
        )
        if frame is None:
            frame = await plex_image(
                self.tautulli, session.get("art"), filename="art.jpg", width=640, height=360
            )
        embed = stream_detail(
            session,
            avatar_url=self._avatar_url(session),
            link=plex_web_link(await self._server_id(), session.get("rating_key")),
            frame=frame,
            poster=poster,
            now=nextcord.utils.utcnow(),
        )
        view = StreamDetailView(self, session_id, _session_label(session), watchers_view,
                                can_stop=self.can_manage(interaction.user))
        await Reply([embed], [a for a in (frame, poster) if a], view=view).send(interaction, ephemeral=True)

    async def stop_stream(self, actor, session_id: str, message: str | None) -> tuple[bool, str]:
        """Stop a stream; returns (stopped, text for the manager who asked)."""
        activity = await self.fetch_activity()
        if activity is None:
            return False, "Couldn't reach Tautulli to stop that stream. Try again in a moment."
        session = _find_session(activity, session_id)
        if session is None:
            return False, "That stream isn't running anymore. Run `/plex watchers` to see current streams."

        label = _session_label(session)
        status = await self.tautulli.terminate_session(session_id=session_id, message=message or None)
        if status == 200:
            note = f" with message: {message}" if message else ""
            logger.info(f"{actor} stopped stream {session_id} ({label}){note}")
            return True, f"Stopped **{session_user(session)}**'s stream of **{session_title(session)}**."
        logger.warning(f"{actor} failed to stop stream {session_id} ({label}): Tautulli returned an error")
        return False, (
            "Tautulli couldn't stop that stream. An admin can check `/plexadmin logs` for details."
        )

    @plex.subcommand(description="See who's watching on Plex. Stream managers can also stop streams.")
    async def watchers(
        self,
        interaction: nextcord.Interaction,
        stop: str = nextcord.SlashOption(
            description="A stream to stop (stream managers only)", required=False, autocomplete=True
        ),
        message: str = nextcord.SlashOption(
            description="Message shown to the viewer when their stream is stopped",
            required=False,
            max_length=200,
        ),
    ):
        if stop:
            if not self.can_manage(interaction.user):
                await error_state(ErrorKind.PERMISSION, _NO_STREAM_PERMISSION).send(
                    interaction, ephemeral=True
                )
                return
            await interaction.response.defer(ephemeral=True)
            ok, text = await self.stop_stream(interaction.user, stop, message)
            reply = success(text) if ok else error_state(ErrorKind.INPUT, text)
            await reply.send(interaction, ephemeral=True)
            return

        await interaction.response.defer()
        activity = await self.fetch_activity()
        if activity is None:
            await service_down("Tautulli").send(interaction, ephemeral=True, replace_placeholder=True)
            return
        view = WatchersView(self, [])
        reply = await self.render_watchers(activity, view)
        view.message = await reply.send(interaction)

    @watchers.on_autocomplete("stop")
    async def _watchers_stop_autocomplete(self, interaction: nextcord.Interaction, stop: str):
        if not self.can_manage(interaction.user):
            await interaction.response.send_autocomplete({})
            return
        activity = await self.fetch_activity()
        sessions = activity.get("sessions", []) if activity else []
        await interaction.response.send_autocomplete(_session_choices(sessions, stop))

    # --- /plexadmin logs ---------------------------------------------------

    # Admin-only and guild-only via the /plexadmin group.
    @plexadmin.subcommand(description="Show recent bot logs and attach the full log file")
    async def logs(
        self,
        interaction: nextcord.Interaction,
        level: str = nextcord.SlashOption(
            description="Only show records at this level or above",
            required=False,
            choices={name.title(): name for name in _LEVELS},
        ),
        search: str = nextcord.SlashOption(
            description="Only show records containing this text", required=False, max_length=100
        ),
    ):
        await interaction.response.defer(ephemeral=True)

        log_path = _find_log_path()
        if log_path is None or not log_path.exists():
            await interaction.followup.send("No log file found.", ephemeral=True)
            return
        raw = await asyncio.to_thread(log_path.read_text, encoding="utf-8", errors="replace")
        text = _redact(raw, self._log_secrets())
        if not text.strip():
            await interaction.followup.send("The log file is empty.", ephemeral=True)
            return

        content, files = _build_logs_reply(text, _LEVELS.get(level) if level else None, search or None)
        await interaction.followup.send(
            content,
            files=[nextcord.File(io.BytesIO(data), filename=name) for name, data in files],
            ephemeral=True,
        )

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
