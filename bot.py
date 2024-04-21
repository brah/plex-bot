# Core bot functionalities module for plex-bot
class PlexBot:
    def __init__(self, token):
        self.token = token
        self.plex_interface = PlexInterface('http://localhost:32400', token)
        self.command_handler = CommandHandler(self.plex_interface)

    def start(self):
        # Connect to Discord and listen for commands
        pass