import asyncio
from typing import TYPE_CHECKING, Dict, List, Literal

import discord
from discord import Guild, TextChannel, app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from main import Isabel


class MentionMonitorCog(commands.Cog):
    def __init__(self, bot: 'Isabel', guilds: Dict[Guild, List[TextChannel]]):
        self.bot = bot
        self.guilds = guilds

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild in self.guilds and message.mentions:
            mentions = ', '.join(map(lambda a: a.mention, message.mentions))
            embed = discord.Embed(
                description=f"{message.author.mention} sent a message that mentioned {mentions}"
            )
            button = discord.ui.Button(label="Jump to Message", url=message.jump_url)
            view = discord.ui.View()
            view.add_item(button)
            for channel in self.guilds[message.guild]:
                await channel.send(embed=embed, view=view)

    async def add_channel(self, channel: discord.TextChannel):
        guild = channel.guild
        if channel not in self.guilds.setdefault(guild, []):
            self.guilds.setdefault(guild, []).append(channel)
            async with self.bot.database.cursor() as cursor:
                await cursor.execute("INSERT OR IGNORE INTO monitor_channels (channel_id) VALUES (?)", (channel.id,))
            return True
        return False

    async def remove_channel(self, channel: discord.TextChannel):
        guild = channel.guild
        if channel in self.guilds.setdefault(guild, []):
            self.guilds.setdefault(guild, []).remove(channel)
            async with self.bot.database.cursor() as cursor:
                await cursor.execute("DELETE FROM monitor_channels WHERE channel_id = ?", (channel.id,))
            return True
        return False

    @app_commands.command(name='mentions')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def monitor_mentions(self, interaction: discord.Interaction, action: Literal["stop", "start"]):
        # sourcery skip: merge-else-if-into-elif
        start = action == "start"
        if start:
            if await self.add_channel(interaction.channel):
                await interaction.response.send_message("Will use this channel to monitor mentions")
            else:
                await interaction.response.send_message(
                    "Failed to start. Mention monitor is probably already running on this channel", ephemeral=True
                )
        else:
            if await self.remove_channel(interaction.channel):
                await interaction.response.send_message("Removed mention monitor from this channel")
            else:
                await interaction.response.send_message(
                    "Failed to stop. There's probably no mention monitor running on this channel", ephemeral=True
                )


async def setup(bot: 'Isabel'):
    while not bot.database:
        await asyncio.sleep(0)
    channel_dict = {}
    async with bot.database.cursor() as cursor:
        await cursor.execute("""
        CREATE TABLE IF NOT EXISTS monitor_channels (
            channel_id INTEGER UNIQUE
        )
        """)
        await cursor.execute("SELECT * FROM monitor_channels")
        rows = await cursor.fetchall()
        for row in rows:
            channel = bot.get_channel(row[0])
            channel_dict.setdefault(channel.guild, []).append(channel)
    mmc = MentionMonitorCog(bot, channel_dict)
    await bot.add_cog(mmc)
