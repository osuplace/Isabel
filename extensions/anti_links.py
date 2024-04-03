import datetime
from typing import TYPE_CHECKING

import discord
from discord.ext import commands, tasks

if TYPE_CHECKING:
    from main import Isabel

LINKS = ["discord.gg/", "discord.com/invite/", "http://", "https://"]
MESSAGE_LIMIT = 3
LOGO_BUILDERS_ID = 297657542572507137


class MessageCounter:
    def __init__(self):
        self.messages = []

    def add_message(self, message: discord.Message):
        self.messages.append(message.id)
        if len(self.messages) > MESSAGE_LIMIT:
            self.messages.pop(0)

    def clear_old_messages(self):
        now = discord.utils.utcnow()
        self.messages = [m for m in self.messages if now - discord.utils.snowflake_time(m) < datetime.timedelta(days=7)]

    def __len__(self):
        return len(self.messages)


class AntiLinksCog(commands.Cog):
    def __init__(self, bot: 'Isabel'):
        self.bot = bot
        self.counters = {}
        self.load_channels.start()

    @tasks.loop(count=1)
    async def load_channels(self):
        await self.bot.wait_until_ready()
        guild = self.bot.get_guild(LOGO_BUILDERS_ID)
        if guild is None:
            return
        for channel in guild.text_channels:
            async for message in channel.history(
                    limit=100,
                    after=discord.utils.utcnow() - datetime.timedelta(days=7),
                    oldest_first=False):
                self.counters.setdefault(message.author.id, MessageCounter()).add_message(message)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if message.guild.id != LOGO_BUILDERS_ID:
            return
        if message.author.guild_permissions.manage_messages:
            return

        self.counters.setdefault(message.author.id, MessageCounter()).clear_old_messages()
        not_active = len(self.counters.get(message.author.id, [])) < MESSAGE_LIMIT

        if any(link in message.content for link in LINKS) and not_active:
            await message.delete()
            await message.channel.send(f"{message.author.mention}, you are not high enough level to send links here.")
            # that's a joke, we don't actually have levels
        elif (message.attachments or message.embeds) and not_active:
            await message.delete()
            await message.channel.send(f"{message.author.mention}, you are not high enough level to send files here.")
        else:
            self.counters.setdefault(message.author.id, MessageCounter()).add_message(message)


async def setup(bot: 'Isabel'):
    alc = AntiLinksCog(bot)
    await bot.add_cog(alc)
