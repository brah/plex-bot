import datetime


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
