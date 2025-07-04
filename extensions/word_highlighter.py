from typing import TYPE_CHECKING

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from main import Isabel


class WordHighlighterCog(commands.Cog):
    def __init__(self, bot: 'Isabel'):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild:
            return
        config = self.bot.config.get('word_highlighter', {})
        if str(message.guild.id) in config:
            for word, dest in config[str(message.guild.id)]:
                if message.channel.id == dest:
                    # the first message we send includes the word, so we don't want to loop in on ourselves
                    continue
                if message.content and word.lower() in message.content.lower():
                    if channel := self.bot.get_channel(dest):
                        await channel.send(f"**{message.author.display_name}** ({message.author.id}) mentioned '{word}':")
                        await message.forward(channel)


async def setup(bot: 'Isabel'):
    await bot.add_cog(WordHighlighterCog(bot))
