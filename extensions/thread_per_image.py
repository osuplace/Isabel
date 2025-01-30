from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from extensions.starboard import IMAGE_URL_REGEX

CHANNEL_IDS = (1221887700890816583, 1286279551898615971)  # TODO: consider making this a config option

if TYPE_CHECKING:
    from main import Isabel


class ThreadPerImageCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.channel.id not in CHANNEL_IDS:
            return

        has_image_attachment = any(attachment.content_type.startswith("image") for attachment in message.attachments)
        has_image_link = IMAGE_URL_REGEX.search(message.content)
        if has_image_attachment or has_image_link:
            await message.create_thread(name=message.content or "Artwork", auto_archive_duration=10080)


async def setup(bot: 'Isabel'):
    await bot.add_cog(ThreadPerImageCog(bot))
