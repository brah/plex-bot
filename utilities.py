import datetime
import nextcord
from nextcord.ext import menus


def days_hours_minutes(seconds) -> str:
    """_summary_

    Args:
        seconds (any): Seconds to convert to days, hours, minutes (string/int should be passable)

    Returns:
        str: Time string in the fashion of `7 hours, 51 minutes`
    """
    td = datetime.timedelta(seconds=seconds)
    days, hours, minutes = td.days, td.seconds // 3600, td.seconds // 60 % 60
    day_text = "day" if days == 1 else "days"
    if hours < 1 and days < 1:
        return f"{minutes} minutes"
    if days > 1:
        return f"{days} {day_text}, {hours} hours, {minutes} minutes"
    else:
        return f"{hours} hours, {minutes} minutes"


class NoStopButtonMenuPages(menus.ButtonMenuPages, inherit_buttons=False):
    def __init__(self, source, timeout=60) -> None:
        super().__init__(source, timeout=timeout)

        # Add the buttons we want
        self.add_item(menus.MenuPaginationButton(emoji=self.PREVIOUS_PAGE))
        self.add_item(menus.MenuPaginationButton(emoji=self.NEXT_PAGE))

        # Disable buttons that are unavailable to be pressed at the start
        self._disable_unavailable_buttons()


# taken from nextcord docs - to be revised
class MyEmbedFieldPageSource(menus.ListPageSource):
    def __init__(self, data) -> None:
        super().__init__(data, per_page=2)

    async def format_page(self, menu, entries) -> nextcord.Embed:
        embed = nextcord.Embed(title="Recently added:")
        print(f"entry: {entries}")
        embed.add_field(name=entries["title"], value=entries["summary"], inline=False)
        embed.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}")
        embed.set_thumbnail(url=entries["url"])
        return embed
