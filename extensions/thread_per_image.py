from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from extensions.starboard import IMAGE_URL_REGEX

CHANNEL_IDS = (1221887700890816583, 1286279551898615971, 1346680639168057344)

if TYPE_CHECKING:
    from main import Isabel


class ThreadPerImageCog(commands.Cog):
    def __init__(self, bot: 'Isabel'):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.channel.id not in self.bot.config.get('thread_channel_ids', CHANNEL_IDS):
            return

        has_image_attachment = any(attachment.content_type.startswith("image") for attachment in message.attachments)
        image_link_found = IMAGE_URL_REGEX.search(message.content)
        if has_image_attachment or image_link_found:
            name = message.content or "Artwork"
            if image_link_found:
                # remove image link from message content
                link = image_link_found.group(1)
                name = name.replace(link, "").strip()
            await message.create_thread(name=name[:100], auto_archive_duration=10080)


async def setup(bot: 'Isabel'):
    await bot.add_cog(ThreadPerImageCog(bot))
