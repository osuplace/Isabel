import asyncio
from typing import TYPE_CHECKING, Literal, List

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from main import Isabel


class AntiHoistCog(commands.Cog):
    def __init__(self, bot: 'Isabel', guilds: List[discord.Guild]):
        self.bot = bot
        self.guilds = guilds

    async def remove_hoisted_name(self, member: discord.Member):
        if member.guild not in self.guilds:
            return
        if member.guild_permissions.manage_messages:
            return
        global_tried = False
        candidate_name = member.display_name.replace('/u/', '')
        while candidate_name < 'A':
            while candidate_name and candidate_name < 'A':
                first_char = candidate_name[1]
                candidate_name = candidate_name[1:]
                if candidate_name.endswith(first_char):
                    candidate_name = candidate_name[:-1]
            if not candidate_name:
                candidate_name = "no hoisting" if global_tried else (member.global_name or "no hoisting")
                global_tried = True
        if candidate_name != member.display_name:
            await member.edit(nick=candidate_name, reason="Removing hoisted display name")

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        await self.remove_guild(guild)

    @commands.Cog.listener()
    async def on_member_update(self, _, member: discord.Member):
        await self.remove_hoisted_name(member)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self.remove_hoisted_name(member)

    async def add_guild(self, guild: discord.Guild):
        if guild not in self.guilds:
            self.guilds.append(guild)
            async with self.bot.database.cursor() as cursor:
                await cursor.execute("INSERT OR IGNORE INTO hoist_guilds (guild_id) VALUES (?)", (guild.id,))
            return True
        return False

    async def remove_guild(self, guild: discord.Guild):
        if guild in self.guilds:
            self.guilds.remove(guild)
            async with self.bot.database.cursor() as cursor:
                await cursor.execute("DELETE FROM hoist_guilds WHERE guild_id = ?", (guild.id,))
            return True
        return False

    @app_commands.command(description="Starts/Stops anti-hoisting")
    @app_commands.checks.has_permissions(manage_guild=True, manage_nicknames=True)
    @app_commands.checks.bot_has_permissions(manage_nicknames=True)
    async def hoisting(self, interaction: discord.Interaction, action: Literal["stop", "start"]):
        # sourcery skip: merge-else-if-into-elif
        start = action == "start"
        if start:
            if await self.add_guild(interaction.guild):
                await interaction.response.send_message("Will remove hoisted usernames in this server")
            else:
                await interaction.response.send_message(
                    "Failed to start. Anti-hoisting is probably already running in this server", ephemeral=True
                )
        else:
            if await self.remove_guild(interaction.guild):
                await interaction.response.send_message("Stopped anti-hoisting in this server")
            else:
                await interaction.response.send_message(
                    "Failed to stop. Anti-hoisting probably isn't running in this server", ephemeral=True
                )


async def setup(bot: 'Isabel'):
    while not bot.database:
        await asyncio.sleep(0)
    guilds = []
    async with bot.database.cursor() as cursor:
        await cursor.execute("""
        CREATE TABLE IF NOT EXISTS hoist_guilds (
            guild_id INTEGER UNIQUE
        )
        """)
        await cursor.execute("SELECT * FROM hoist_guilds")
        rows = await cursor.fetchall()
        guilds.extend(bot.get_guild(row[0]) for row in rows)
    await bot.add_cog(AntiHoistCog(bot, guilds))
