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
        self.counters: dict[int, MessageCounter] = {}
        self.save_to_json_task.start()
        if Path("anti_links.json").exists():
            with open("anti_links.json", "r") as f:
                data = json.load(f)
                now = discord.utils.time_snowflake(discord.utils.utcnow())
                for user_id in data:
                    self.counters[int(user_id)] = MessageCounter()
                    self.counters[int(user_id)].messages = [now] * MESSAGE_LIMIT

    def cog_unload(self):
        self.save_to_json_task.cancel()
        self.save_to_json()

    @tasks.loop(hours=1)
    async def save_to_json_task(self):
        self.save_to_json()

    def save_to_json(self):
        with open("anti_links.json", "w") as f:
            json.dump([str(k) for k, v in self.counters.items() if len(v.messages) == MESSAGE_LIMIT], f)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if not message.guild:
            return
        if message.guild.id != LOGO_BUILDERS_ID:
            return
        if message.author.guild_permissions.manage_messages:
            return

        self.counters.setdefault(message.author.id, MessageCounter()).clear_old_messages()
        not_active = len(self.counters.get(message.author.id, [])) < MESSAGE_LIMIT
        missing = MESSAGE_LIMIT - len(self.counters.get(message.author.id, []))
        messages = "messages" if missing != 1 else "message"

        if any(link in message.content for link in LINKS) and not_active:
            await message.delete()
            await message.channel.send(
                f"{message.author.mention}, you must send {missing} more {messages} before you can send links."
            )
        elif (message.attachments or message.embeds) and not_active:
            await message.delete()
            await message.channel.send(
                f"{message.author.mention}, you must send {missing} more {messages} before you can send files."
            )
        else:
            self.counters.setdefault(message.author.id, MessageCounter()).add_message(message)


async def setup(bot: 'Isabel'):
    alc = AntiLinksCog(bot)
    await bot.add_cog(alc)
