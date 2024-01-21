import asyncio
import itertools
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Optional

import aiosqlite
import discord
from discord.ext import commands

from refresh import generate_refresh

generate_refresh.generate_refresh_files()


class Isabel(commands.Bot):
    def __init__(self, config_name="config.json"):
        _intents = discord.Intents.all()

        super().__init__(command_prefix=commands.when_mentioned,
                         case_insensitive=True,
                         chunk_guilds_at_startup=False,
                         help_command=None,
                         intents=_intents)

        with open(config_name) as file_in:
            config = json.load(file_in)
        self.config_name = config_name
        self.config = config

        self.database: Optional[aiosqlite.Connection] = None

        # Setup logging
        if not os.path.isdir("logs"):
            os.makedirs("logs")
        root_logger = logging.getLogger()
        self.logger = logging.getLogger('isabel')
        formatter = logging.Formatter('%(asctime)s %(levelname)-8s [%(name)s] %(message)s')

        ih = RotatingFileHandler("logs/isabel.log", maxBytes=2000000, backupCount=1, encoding='UTF-8')
        ih.setLevel(1)
        ih.setFormatter(formatter)
        fh = RotatingFileHandler("logs/info.log", maxBytes=1000000, backupCount=1, encoding='UTF-8')
        fh.setLevel(logging.INFO)
        fh.setFormatter(formatter)
        dh = RotatingFileHandler("logs/debug.log", maxBytes=5000000, backupCount=1, encoding='UTF-8')
        dh.setLevel(1)
        dh.setFormatter(formatter)
        sh = logging.StreamHandler()
        sh.setLevel(logging.INFO)
        sh.setFormatter(formatter)

        self.logger.addHandler(ih)
        root_logger.handlers = []
        root_logger.addHandler(fh)
        root_logger.addHandler(dh)
        root_logger.addHandler(sh)
        root_logger.setLevel(1)

    def auto_load(self):
        return ['text_error_handler', 'app_error_handler', 'database'] + self.config.get('auto_load', [])

    async def on_ready(self):
        app = await self.application_info()
        self.owner_id = app.owner.id

        self.logger.info(f"Logged in as {self.user.name}")

    # make DMs work without prefix
    async def get_prefix(self, message: discord.Message):
        if isinstance(message.channel, discord.DMChannel):
            # mention needs to be first to get triggered
            rv = commands.bot.when_mentioned(self, message)
            return rv + ["".join(itertools.takewhile(lambda k: not k.isalnum(), message.content))]
        return await super().get_prefix(message)

    async def close(self):
        self.get_cog('Core').cog_unload = None
        await super().close()


async def main():
    import core_cog

    isabel = Isabel()
    async with isabel:
        await isabel.add_cog(core_cog.use().Core(isabel))
        for ext in isabel.auto_load():
            await isabel.load_extension(f'extensions.{ext}')
        await isabel.start(isabel.config.get('token'))


if __name__ == '__main__':
    asyncio.run(main())
