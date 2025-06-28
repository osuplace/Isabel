from typing import TYPE_CHECKING

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from main import Isabel


class ChannelArchiverCog(commands.Cog):
    def __init__(self, bot: 'Isabel'):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild:
            return
        config = self.bot.config.get('channel_archiver', {})
        if str(message.guild.id) in config:
            for src, dest in config[str(message.guild.id)]:
                if message.channel.id == src:
                    if channel := self.bot.get_channel(dest):
                        await channel.send(f"**{message.author.display_name}** ({message.author.id}) had this to say:")
                        await message.forward(channel)


async def setup(bot: 'Isabel'):
    await bot.add_cog(ChannelArchiverCog(bot))
