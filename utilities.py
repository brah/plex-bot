from io import BytesIO
import aiohttp
import nextcord
from nextcord.ext import menus
from nextcord import File


def days_hours_minutes(seconds):
    """Converts seconds to days, hours, minutes.

    Args:
        seconds (int): Seconds to convert.

    Returns:
        str: Time string in the format of "7 days, 3 hours, 51 minutes".
    """
    if not isinstance(seconds, int):
        raise TypeError("Seconds must be an integer.")

    if seconds < 0:
        raise ValueError("Seconds must be non-negative.")

    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)

    day_text = "day" if days == 1 else "days"
    hour_text = "hour" if hours == 1 else "hours"
    minute_text = "minute" if minutes == 1 else "minutes"

    parts = []
    if days > 0:
        parts.append(f"{days} {day_text}")
    if hours > 0:
        parts.append(f"{hours} {hour_text}")
    if minutes > 0:
        parts.append(f"{minutes} {minute_text}")

    if len(parts) == 0:
        return "0 minutes"
    else:
        return ", ".join(parts)


def format_duration(milliseconds):
    seconds = milliseconds // 1000
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours}h {minutes}m"


def format_size(bytes):
    if bytes < 1024:
        return f"{bytes} B"
    elif bytes < 1024**2:
        return f"{bytes / 1024:.2f} KB"
    elif bytes < 1024**3:
        return f"{bytes / 1024 ** 2:.2f} MB"
    return f"{bytes / 1024 ** 3:.2f} GB"


class NoStopButtonMenuPages(menus.ButtonMenuPages, inherit_buttons=False):
    def __init__(self, source, timeout=60) -> None:
        super().__init__(source, timeout=timeout)

        # Add the buttons we want
        self.add_item(menus.MenuPaginationButton(emoji=self.PREVIOUS_PAGE))
        self.add_item(menus.MenuPaginationButton(emoji=self.NEXT_PAGE))

        # Disable buttons that are unavailable to be pressed at the start
        self._disable_unavailable_buttons()


# taken from nextcord docs - to be revised
class MyEmbedDescriptionPageSource(menus.ListPageSource):
    def __init__(self, data, tautulli_ip):
        super().__init__(data, per_page=2)
        self.tautulli_ip = tautulli_ip

    async def fetch_image(self, url):
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    return BytesIO(await response.read())
                return None

    async def format_page(self, menu, entries):
        embed = nextcord.Embed(title="Recently Added", color=0xE5A00D)
        embed.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}")

        for entry in entries:
            embed.add_field(name="\u200b", value=entry["description"], inline=False)
            thumb_key = entry.get("thumb_key", "")
            if thumb_key:
                thumb_url = f"http://{self.tautulli_ip}/pms_image_proxy?img={thumb_key}&width=200&height=400&fallback=poster"
                image_data = await self.fetch_image(thumb_url)
                if image_data:
                    file = File(fp=image_data, filename="image.jpg")
                    embed.set_image(url="attachment://image.jpg")
                    return {"embed": embed, "file": file}

        return embed


import subprocess


def get_git_revision_short_hash() -> str:
    return (
        subprocess.check_output(["git", "rev-parse", "--short", "HEAD"])
        .decode("ascii")
        .strip()
    )


def get_git_revision_short_hash_latest():
    return (
        subprocess.check_output(["git", "rev-parse", "--short", "HEAD"])
        .decode("ascii")
        .strip()
    )


import json


def reload_config_json():
    with open("config.json", "r") as f:
        return json.load(f)
