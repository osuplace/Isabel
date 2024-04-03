import datetime
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from main import Isabel

LINKS = ["discord.gg/", "discord.com/invite/", "http://", "https://"]
LOGO_BUILDERS_ID = 297657542572507137


class MessageCounter:
    def __init__(self):
        self.messages = []

    def add_message(self, message: discord.Message):
        self.messages.append(message)
        if len(self.messages) > 5:
            self.messages.pop(0)

    def clear_old_messages(self):
        self.messages = [m for m in self.messages if m.created_at > discord.utils.utcnow() - datetime.timedelta(days=7)]

    def __len__(self):
        return len(self.messages)


class AntiLinksCog(commands.Cog):
    def __init__(self, bot: 'Isabel'):
        self.bot = bot
        self.activity = {}

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if message.guild.id != LOGO_BUILDERS_ID:
            return
        if message.author.guild_permissions.manage_messages:
            return

        self.activity.setdefault(message.author.id, MessageCounter()).clear_old_messages()

        if any(link in message.content for link in LINKS) and len(self.activity.get(message.author.id, [])) < 5:
            await message.delete()
            await message.channel.send(f"{message.author.mention}, you are not high enough level to send links here.")
            # that's a joke, we don't actually have levels
        elif (message.attachments or message.embeds) and len(self.activity.get(message.author.id, [])) < 5:
            await message.delete()
            await message.channel.send(f"{message.author.mention}, you are not high enough level to send files here.")
        else:
            self.activity.setdefault(message.author.id, MessageCounter()).add_message(message)


async def setup(bot: 'Isabel'):
    await bot.add_cog(AntiLinksCog(bot))
