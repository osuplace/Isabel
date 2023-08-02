import asyncio
from typing import TYPE_CHECKING, Dict, List, Literal, Optional, Tuple

import discord
from discord import Guild, TextChannel, app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from main import Isabel


class SetupConfirm(discord.ui.View):
    def __init__(self):
        super().__init__()
        self.selected: Optional[discord.TextChannel] = None
        self.next_interaction: discord.Interaction = None

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Change channel"
    )
    async def change(self, interaction: discord.Interaction, channel_select: discord.ui.ChannelSelect):
        self.selected = channel_select.values[0]
        await interaction.response.defer()

    @discord.ui.button(label='Confirm', style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.selected = self.selected or interaction.channel
        self.next_interaction = interaction
        self.stop()

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.grey)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.selected = None
        self.next_interaction = interaction
        self.stop()


class EditConfirm(discord.ui.View):
    def __init__(self):
        super().__init__()
        self.action: str = "Timeout"
        self.next_interaction: discord.Interaction = None

    @discord.ui.button(label='Change Channel', style=discord.ButtonStyle.blurple)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.action = 'change'
        self.next_interaction = interaction
        self.stop()

    @discord.ui.button(label='Stop the starboard', style=discord.ButtonStyle.red)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.action = 'stop'
        self.next_interaction = interaction
        self.stop()

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.grey)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.action = 'cancel'
        self.next_interaction = interaction
        self.stop()


class StarboardCog(commands.Cog):
    def __init__(self, bot: 'Isabel', guilds: Dict[Guild, TextChannel]):
        self.bot = bot
        self.guilds = guilds

    async def choose_channel(
            self,
            interaction: discord.Interaction,
            content: str
    ) -> Optional[Tuple[discord.TextChannel, discord.Interaction]]:
        confirm = SetupConfirm()
        await interaction.response.send_message(
            content=content,
            view=confirm,
            ephemeral=True
        )
        if await confirm.wait():
            await interaction.delete_original_response()
            return
        if confirm.selected:
            await interaction.delete_original_response()
            return self.bot.get_channel(confirm.selected.id), confirm.next_interaction
        else:
            await interaction.delete_original_response()
            await confirm.next_interaction.response.send_message("Cancelled", ephemeral=True, delete_after=15)

    async def edit_starboard(self, interaction: discord.Interaction):
        confirm = EditConfirm()
        await interaction.response.send_message(
            content="# __Starboard already running.__\nYou can change starboard channel or stop the starboard entirely.",
            view=confirm,
            ephemeral=True
        )
        if await confirm.wait():
            await interaction.delete_original_response()
            return
        if confirm.action == 'cancel':
            await interaction.delete_original_response()
            await confirm.next_interaction.response.send_message("Cancelled", ephemeral=True, delete_after=15)
        elif confirm.action == 'stop':
            await interaction.delete_original_response()
            await self.stop_starboard(confirm.next_interaction)
        elif confirm.action == 'change':
            await interaction.delete_original_response()
            await self.change_channel(confirm.next_interaction)

    async def start_starboard(self, interaction: discord.Interaction):
        choice = await self.choose_channel(interaction, f"Want to setup starboard in {interaction.channel.mention}?")
        if choice is not None:
            channel, next_interaction = choice
            self.guilds[interaction.guild] = channel
            async with self.bot.database.cursor() as cursor:
                await cursor.execute("INSERT OR IGNORE INTO starboard_channels (channel_id) VALUES (?)", (channel.id,))
            await next_interaction.response.send_message("Starboard has started")

    async def stop_starboard(self, interaction: discord.Interaction):
        assert interaction.guild in self.guilds
        channel = self.guilds[interaction.guild]
        del self.guilds[interaction.guild]
        async with self.bot.database.cursor() as cursor:
            await cursor.execute("DELETE FROM starboard_channels WHERE channel_id = ?", (channel.id,))
        await interaction.response.send_message(content="Starboard stopped", ephemeral=True)


    async def change_channel(self, interaction: discord.Interaction):
        choice = await self.choose_channel(
            interaction,
            f"What channel to change to? Choosing nothing will change the channel to {interaction.channel.mention}"
        )
        if choice is not None:
            next_channel, next_interaction = choice
            current_channel = self.guilds[interaction.guild]
            self.guilds[interaction.guild] = next_channel
            async with self.bot.database.cursor() as cursor:
                await cursor.execute(
                    "UPDATE starboard_channels SET channel_id = ? WHERE channel_id = ?",
                    (next_channel.id, current_channel.id)
                )
            await next_interaction.response.send_message(
                content=f"Starboard channel changed to {next_channel.mention}",
                ephemeral=True
            )

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        pass

    @app_commands.command(description="Will setup starboard on this server")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def starboard(self, interaction: discord.Interaction):
        if interaction.guild in self.guilds:
            await self.edit_starboard(interaction)
        else:
            await self.start_starboard(interaction)


async def setup(bot: 'Isabel'):
    while not bot.database:
        await asyncio.sleep(0)
    guilds = {}
    async with bot.database.cursor() as cursor:
        await cursor.execute("""
        CREATE TABLE IF NOT EXISTS starboard_reference (
            starboard_id INTEGER,
            original_id INTEGER
        )
        """)
        await cursor.execute("""
        CREATE TABLE IF NOT EXISTS starboard_channels (
            channel_id INTEGER UNIQUE
        )
        """)
        await cursor.execute("SELECT * FROM starboard_channels")
        rows = await cursor.fetchall()
        for row in rows:
            channel = bot.get_channel(row[0])
            guilds[channel.guild] = channel
    await bot.add_cog(StarboardCog(bot, guilds))
