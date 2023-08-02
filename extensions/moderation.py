import asyncio
import contextlib
import datetime
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from main import Isabel


class ModerationCog(commands.Cog):
    def __init__(self, bot: 'Isabel'):
        self.bot = bot

    @app_commands.command(description="Bulk deletes messages")
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.checks.bot_has_permissions(manage_messages=True)
    async def purge(self, interaction: discord.Interaction, minutes: int = 5):
        if not interaction.channel.permissions_for(interaction.guild.me).manage_messages:
            raise app_commands.BotMissingPermissions(["manage_messages"])

        minutes = max(min(minutes, 60), 1)
        count = 0
        messages_to_delete = []

        await interaction.response.send_message(
            content=f"Deleting messages sent in the last {minutes} minutes",
            delete_after=15
        )

        async with interaction.channel.typing():
            after = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes)
            async for message in interaction.channel.history(limit=None, after=after):
                if message.interaction and message.interaction.name == "purge":
                    continue
                messages_to_delete.append(message)
                count += 1
                if len(messages_to_delete) == 100:
                    await interaction.channel.delete_messages(messages_to_delete)
                    messages_to_delete = []
            await interaction.channel.delete_messages(messages_to_delete)

        with contextlib.suppress(Exception):
            msg = await interaction.channel.send(f"Deleted {count} messages")
            await asyncio.sleep(15)
            await msg.delete()


async def setup(bot):
    await bot.add_cog(ModerationCog(bot))
