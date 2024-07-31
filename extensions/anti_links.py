import asyncio
import contextlib
import datetime
import json
from pathlib import Path
from typing import TYPE_CHECKING

import discord
from discord.ext import commands, tasks

if TYPE_CHECKING:
    from main import Isabel

LINKS = ["discord.gg/", "discord.com/invite/", "http://", "https://"]
MESSAGE_LIMIT = 3
LOGO_BUILDERS_ID = 297657542572507137
ROLE_ID = 1230732817550278686


class MessageCollection:
    def __init__(self, init_as_full=False):
        now = discord.utils.time_snowflake(discord.utils.utcnow())
        self.messages = [now] * MESSAGE_LIMIT if init_as_full else []

    def add_message(self, message: discord.Message):
        self.messages.append(message.id)
        if len(self.messages) > MESSAGE_LIMIT:
            self.messages.pop(0)

    def clear_old_messages(self):
        now = discord.utils.utcnow()
        self.messages = [m for m in self.messages if now - discord.utils.snowflake_time(m) < datetime.timedelta(days=7)]

    def is_full(self):
        return len(self.messages) == MESSAGE_LIMIT


class AntiLinksCog(commands.Cog):
    def __init__(self, bot: 'Isabel'):
        self.bot = bot
        self.collectors: dict[int, MessageCollection] = {}

        if Path("anti_links.json").exists():
            with open("anti_links.json", "r") as f:
                data = json.load(f)
                for user_id in data:
                    self.collectors[int(user_id)] = MessageCollection(True)

        self.hourly.start()

    def cog_unload(self):
        self.hourly.cancel()
        self.save_to_json()

    @tasks.loop(hours=1)
    async def hourly(self):
        guild = self.bot.get_guild(LOGO_BUILDERS_ID)
        role = guild.get_role(ROLE_ID)
        for key in list(self.collectors):
            counter = self.collectors[key]
            counter.clear_old_messages()
            if not counter.messages:
                if guild and role:
                    member = guild.get_member(key)
                    if member and role in member.roles:
                        with contextlib.suppress(discord.HTTPException):
                            await member.remove_roles(role, reason="Not active in chat anymore.")
                del self.collectors[key]
            await asyncio.sleep(0)
        self.save_to_json()

    def save_to_json(self):
        with open("anti_links.json", "w") as f:
            json.dump([str(k) for k, v in self.collectors.items() if v.is_full()], f)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if not message.guild:
            return
        if message.guild.id != LOGO_BUILDERS_ID:
            return
        if message.type != discord.MessageType.default:
            return

        self.collectors.setdefault(message.author.id, MessageCollection()).clear_old_messages()
        not_active = not self.collectors[message.author.id].is_full()

        # sourcery skip: remove-pass-elif
        if any(link in message.content for link in LINKS) and not_active:
            pass
        elif (message.attachments or message.embeds) and not_active:
            pass
        else:
            self.collectors.setdefault(message.author.id, MessageCollection()).add_message(message)
            if self.collectors[message.author.id].is_full():
                if guild := self.bot.get_guild(LOGO_BUILDERS_ID):
                    member = guild.get_member(message.author.id)
                    role = guild.get_role(ROLE_ID)
                    if member and role and role not in member.roles:
                        await member.add_roles(role, reason="Active in chat.")


async def setup(bot: 'Isabel'):
    alc = AntiLinksCog(bot)
    await bot.add_cog(alc)
