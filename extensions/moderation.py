import asyncio
import contextlib
import datetime
from typing import TYPE_CHECKING, Dict, Union

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from main import Isabel


def add_permissions_fields_to(embed: discord.Embed, permissions: discord.Permissions):
    all_permissions: Dict[str, bool] = {
        k.replace('_', ' ').capitalize(): v for k, v in permissions
    }
    all_permissions['Bake a cake'] = False
    if len(all_permissions) % 2 != 0:
        all_permissions['Sing a lullaby'] = False

    field0 = ""
    field1 = ""
    for i, (k, v) in enumerate(all_permissions.items()):
        if i % 2 == 0:
            field0 += f"{'🟢' if v else '🔴'} {k}\n"
        else:
            field1 += f"{'🟢' if v else '🔴'} {k}\n"
    embed.add_field(name="\u200b", value=field0)
    embed.add_field(name="\u200b", value=field1)
    return embed


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
                    await interaction.channel.delete_messages(
                        messages_to_delete,
                        reason=f"Purge initiated by {interaction.user.global_name} ID: {interaction.user.id}"
                    )
                    messages_to_delete = []
            await interaction.channel.delete_messages(
                messages_to_delete,
                reason=f"Purge initiated by {interaction.user.global_name} ID: {interaction.user.id}"
            )

        with contextlib.suppress(Exception):
            msg = await interaction.channel.send(f"Deleted {count} messages")
            await asyncio.sleep(15)
            await msg.delete()

    @app_commands.command(description="Gets the avatar URL of a user")
    async def avatar(self, interaction: discord.Interaction, user: discord.User = None):
        user = user or interaction.user
        embed = discord.Embed(description=f"# {user.mention}'s Avatar")
        embed.set_thumbnail(url=user.avatar.url)
        embed.add_field(name="Avatar URL", value=user.avatar.url)

        embeds = [embed]

        if interaction.guild:
            if member := interaction.guild.get_member(user.id):
                if member.guild_avatar:
                    embed = discord.Embed(description=f"# {member.mention}'s Server Avatar")
                    embed.set_thumbnail(url=member.guild_avatar.url)
                    embed.add_field(name="Avatar URL", value=member.guild_avatar.url)
                    embeds.append(embed)

        await interaction.response.send_message(embeds=embeds)

    @app_commands.command(description="Gets the permissions someone has in a channel")
    async def permsin(
            self,
            interaction: discord.Interaction,
            channel: Union[
                discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel, discord.StageChannel, discord.ForumChannel] = None,
            user: discord.User = None
    ):
        if not interaction.guild:
            raise app_commands.NoPrivateMessage()
        channel = channel or interaction.channel
        user = user or interaction.user
        user = interaction.guild.get_member(user.id) or user
        permissions = channel.permissions_for(user)
        embed = discord.Embed(description=f"# {user.mention}'s permissions in {channel.mention}")
        embed = add_permissions_fields_to(embed, permissions)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(description="Gets the permissions someone globally in the server")
    async def permsfor(self, interaction: discord.Interaction, user: discord.User = None):
        if not interaction.guild:
            raise app_commands.NoPrivateMessage()
        user = user or interaction.user
        user = interaction.guild.get_member(user.id)
        if not user and not interaction.guild.chunked:
            await interaction.guild.chunk(cache=True)
            user = interaction.guild.get_member(user.id)
        embed = discord.Embed(description=f"# {user.mention}'s permissions in {interaction.guild.name}")
        embed = add_permissions_fields_to(embed, user.guild_permissions)
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(ModerationCog(bot))
