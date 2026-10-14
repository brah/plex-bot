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
from errors import LoggedModal, LoggedView
from utilities import (
    UserMappings,
    get_git_revision_short_hash,
    get_git_revision_short_hash_latest,
)
from slash_commands import plex, plexadmin
from tautulli_wrapper import Tautulli

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


def _truncate(text: str, length: int) -> str:
    return text if len(text) <= length else text[: length - 1] + "…"


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
                shown.append(_truncate(record, budget))
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
            parts.append(f'matching "{_truncate(search, _HEADER_SEARCH_MAX)}"')
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

_NO_STREAM_PERMISSION = "You don't have permission to stop streams."
# Discord caps autocomplete results and select options at 25, and their labels at 100 chars.
_MAX_CHOICES = 25
_MAX_LABEL = 100


def _format_ms(ms: int) -> str:
    """Format milliseconds into H:MM:SS or M:SS."""
    total_seconds = ms // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _session_user(session: dict) -> str:
    return session.get("friendly_name") or session.get("username") or "Unknown"


def _session_title(session: dict) -> str:
    return session.get("full_title") or session.get("title") or "Unknown"


def _session_label(session: dict, suffix: str = "") -> str:
    """One-line "user — title" label for a stream, within Discord's label limit."""
    label = f"{_session_user(session)} — {_session_title(session)}"
    return _truncate(label, _MAX_LABEL - len(suffix)) + suffix


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


def _format_session_field(session: dict) -> tuple:
    """Format a single Tautulli session into an embed field (name, value)."""
    user = _session_user(session)
    title = _session_title(session)

    # State indicator
    state = session.get("state", "unknown").lower()
    state_icons = {"playing": "▶", "paused": "⏸", "buffering": "⏳"}
    state_icon = state_icons.get(state, "•")

    # Progress bar
    progress_bar = ""
    try:
        view_offset = int(session.get("view_offset", 0))
        duration = int(session.get("duration", 0))
        if duration > 0:
            pct = min(100, (view_offset / duration) * 100)
            filled = round(pct / 10)
            bar = "█" * filled + "░" * (10 - filled)
            elapsed = _format_ms(view_offset)
            total = _format_ms(duration)
            progress_bar = f"`{bar}` {elapsed} / {total}"
        else:
            elapsed = _format_ms(view_offset)
            progress_bar = f"{elapsed}"
    except (ValueError, TypeError):
        pass

    # Transcode vs direct
    decision = session.get("transcode_decision", "").lower()
    if decision == "transcode":
        stream_type = "Transcode"
    elif decision == "copy":
        stream_type = "Direct Stream"
    elif decision == "direct play":
        stream_type = "Direct Play"
    else:
        stream_type = session.get("quality_profile", "")

    # Player info
    player = session.get("player", "")
    platform = session.get("platform_name") or session.get("platform", "")
    device_parts = [p for p in [player, platform] if p]
    device = " • ".join(device_parts) if device_parts else None

    # Build value lines
    lines = [f"{state_icon}  **{title}**"]
    if progress_bar:
        lines.append(progress_bar)

    detail_parts = []
    if stream_type:
        detail_parts.append(stream_type)
    if device:
        detail_parts.append(device)
    if detail_parts:
        lines.append("  •  ".join(detail_parts))

    return user, "\n".join(lines)


class StopStreamModal(LoggedModal):
    """Asks for an optional message to the viewer, then stops the chosen stream."""

    def __init__(self, cog: "ServerCommands", session_id: str, label: str, watchers_view: "WatchersView"):
        super().__init__(title=_truncate(f"Stop: {label}", 45), timeout=300)
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
        await interaction.response.defer(ephemeral=True)
        reply = await self.cog.stop_stream(interaction.user, self.session_id, self.message_input.value)
        await interaction.followup.send(reply, ephemeral=True)
        # Plex takes a moment to drop a stopped session from its activity list.
        await asyncio.sleep(2)
        await self.watchers_view.refresh()


class StreamSelect(nextcord.ui.StringSelect):
    def __init__(self, choices: dict[str, str]):
        self.labels = {session_id: label for label, session_id in choices.items()}
        options = [nextcord.SelectOption(label=label, value=sid) for label, sid in choices.items()]
        super().__init__(placeholder="Choose a stream to stop…", options=options)

    async def callback(self, interaction: nextcord.Interaction):
        panel: StopStreamPanel = self.view
        session_id = self.values[0]
        await interaction.response.send_modal(
            StopStreamModal(panel.cog, session_id, self.labels[session_id], panel.watchers_view)
        )


class StopStreamPanel(LoggedView):
    """Private (ephemeral) panel listing streams a manager can stop."""

    def __init__(self, cog: "ServerCommands", choices: dict[str, str], watchers_view: "WatchersView"):
        super().__init__(timeout=300)
        self.cog = cog
        self.watchers_view = watchers_view
        self.add_item(StreamSelect(choices))


class WatchersView(LoggedView):
    """Buttons under the public /plex watchers message: Refresh for anyone, Manage for stream managers."""

    def __init__(self, cog: "ServerCommands", timeout: float = 600):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.message = None

    async def refresh(self):
        """Re-render the public message, e.g. after a stream was stopped from the panel."""
        if self.message is None:
            return
        embed = self.cog.build_watchers_embed(await self.cog.fetch_activity())
        try:
            await self.message.edit(embed=embed, view=self)
        except nextcord.HTTPException as e:
            logger.warning(f"Could not refresh /plex watchers message: {e}")

    @nextcord.ui.button(label="Refresh", emoji="\U0001F504", style=nextcord.ButtonStyle.secondary)
    async def refresh_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        await interaction.response.defer()
        embed = self.cog.build_watchers_embed(await self.cog.fetch_activity())
        await interaction.edit_original_message(embed=embed, view=self)

    @nextcord.ui.button(label="Manage streams", emoji="\U0001F6D1", style=nextcord.ButtonStyle.secondary)
    async def manage_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        if not self.cog.can_manage(interaction.user):
            await interaction.response.send_message(_NO_STREAM_PERMISSION, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, with_message=True)
        activity = await self.cog.fetch_activity()
        if activity is None:
            await interaction.followup.send(
                "Couldn't reach Tautulli — try again in a moment.", ephemeral=True
            )
            return
        # Managers see every stream, including users hidden from the public list.
        choices = _session_choices(activity.get("sessions", []), "")
        if not choices:
            await interaction.followup.send("No one is streaming right now.", ephemeral=True)
            return
        await interaction.followup.send(
            "Pick a stream to stop:", view=StopStreamPanel(self.cog, choices, self), ephemeral=True
        )

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except nextcord.HTTPException:
                pass


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

    # --- /plex status ------------------------------------------------------

    @plex.subcommand(description="Show Plex server details and Plex's service status")
    async def status(self, interaction: nextcord.Interaction):
        await interaction.response.defer()

        server_info_response = await self.tautulli.get_server_info()
        if not Tautulli.check_response(server_info_response):
            logger.error("Failed to retrieve server info from Tautulli.")
            await interaction.followup.send(
                "Couldn't reach Tautulli for server details — try again shortly."
            )
            return
        server_data = Tautulli.get_response_data(server_info_response, {})

        plex_status = "Plex status unavailable"
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get("https://status.plex.tv/api/v2/status.json") as response:
                    if response.status == 200:
                        json_response = await response.json()
                        plex_status = json_response.get("status", {}).get("description", "Unknown")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"Could not fetch Plex service status: {e}")

        embed = nextcord.Embed(title="Plex Server Details", colour=self.plex_embed_color)
        embed.set_thumbnail(url=self.plex_image)
        embed.add_field(name="Server Name", value=server_data.get("pms_name", "Unknown"), inline=True)
        embed.add_field(name="Server Version", value=server_data.get("pms_version", "Unknown"), inline=True)
        embed.add_field(
            name="Server IP",
            value=f"{server_data.get('pms_ip', '?')}:{server_data.get('pms_port', '?')}",
        )
        embed.add_field(name="Platform", value=server_data.get("pms_platform", "Unknown"))
        embed.add_field(name="Plex Pass", value=server_data.get("pms_plexpass", "Unknown"))
        embed.add_field(name="Plex API Status", value=plex_status)
        await interaction.followup.send(embed=embed)

    # --- /plex watchers ----------------------------------------------------

    def can_manage(self, user) -> bool:
        return _can_manage_streams(user, config.get("discord", "stream_manager_ids", []))

    async def fetch_activity(self) -> dict | None:
        response = await self.tautulli.get_activity()
        if not Tautulli.check_response(response):
            logger.error("Failed to retrieve activity from Tautulli.")
            return None
        return Tautulli.get_response_data(response, {})

    def build_watchers_embed(self, activity: dict | None) -> nextcord.Embed:
        embed = nextcord.Embed(title="Plex Activity", color=self.plex_embed_color)
        embed.set_thumbnail(url=self.plex_image)
        embed.timestamp = nextcord.utils.utcnow()
        if activity is None:
            embed.description = "Couldn't reach Tautulli for current activity. Try **Refresh** in a moment."
            return embed

        mappings = UserMappings.load_user_mappings()
        ignored_users = {u["plex_username"] for u in mappings if u.get("ignore", False)}
        sessions = activity.get("sessions", [])
        visible_sessions = [s for s in sessions if s.get("username") not in ignored_users]
        if not visible_sessions:
            embed.description = "No one is currently watching."
            return embed

        stream_count = activity.get("stream_count", len(visible_sessions))
        total_bw = activity.get("total_bandwidth", 0)
        header_parts = [f"{stream_count} active stream{'s' if int(stream_count) != 1 else ''}"]
        if total_bw:
            header_parts.append(f"{round(int(total_bw) / 1000, 1)} Mbps")
        embed.description = "  •  ".join(header_parts)
        for session in visible_sessions:
            name, value = _format_session_field(session)
            embed.add_field(name=name, value=value, inline=False)
        return embed

    async def stop_stream(self, actor, session_id: str, message: str | None) -> str:
        """Stop a stream and return the reply for the manager who asked."""
        activity = await self.fetch_activity()
        if activity is None:
            return "Couldn't reach Tautulli to stop that stream — try again in a moment."
        sessions = activity.get("sessions", [])
        session = next((s for s in sessions if str(s.get("session_id")) == session_id), None)
        if session is None:
            return "That stream isn't running anymore — run `/plex watchers` to see current streams."

        label = _session_label(session)
        status = await self.tautulli.terminate_session(session_id=session_id, message=message or None)
        if status == 200:
            note = f" with message: {message}" if message else ""
            logger.info(f"{actor} stopped stream {session_id} ({label}){note}")
            return f"Stopped **{_session_user(session)}**'s stream of **{_session_title(session)}**."
        logger.warning(f"{actor} failed to stop stream {session_id} ({label}): Tautulli returned an error")
        return "Tautulli couldn't stop that stream. An admin can check `/plexadmin logs` for details."

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
                await interaction.response.send_message(_NO_STREAM_PERMISSION, ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            reply = await self.stop_stream(interaction.user, stop, message)
            await interaction.followup.send(reply, ephemeral=True)
            return

        await interaction.response.defer()
        view = WatchersView(self)
        embed = self.build_watchers_embed(await self.fetch_activity())
        view.message = await interaction.followup.send(embed=embed, view=view, wait=True)

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
