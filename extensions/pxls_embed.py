import asyncio
import re
from typing import TYPE_CHECKING

import discord
import urllib.parse
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from main import Isabel

PXLS_REGEX = re.compile(r"(?:https?://)?((?:www\.)?pxls\.space|(?:[a-z0-9\-]+\.)?pxls\.world)/#\S+")


class PxlsEmbedCog(commands.Cog):
    def __init__(self, bot, channels):
        self.bot: 'Isabel' = bot
        self.channels = channels

    @staticmethod
    def embed(url):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).fragment)
        title = params.get('title', ['Template'])[0]
        if template := params.get('template', [''])[0]:
            # TODO: template image might be stylized, so we need to fetch the image and remove the style
            escaped = urllib.parse.unquote(template)
            embed = discord.Embed(title=title, url=url)
            embed.set_image(url=escaped)
            return embed
        return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return  # ignore bots
        if message.channel not in self.channels:
            return  # ignore channels that are not set up for pxls embeds
        embeds = []
        for match in PXLS_REGEX.findall(message.content):
            e = self.embed(match)
            if e and len(embeds) < 10:
                embeds.append(e)
        if embeds:
            await message.reply(embeds=embeds, mention_author=False)

    @app_commands.command(description="Will start embedding pxls links in this channel", name="pxembed")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def pxls_embed(self, interaction: discord.Interaction):
        if interaction.channel in self.channels:
            await interaction.response.send_message("This channel is already set up for pxls embeds")
            return
        async with self.bot.database.cursor() as cursor:
            await cursor.execute("INSERT INTO pxls_embed_channels VALUES (?)", (interaction.channel.id,))
        self.channels.append(interaction.channel)
        await interaction.response.send_message("This channel is now set up for pxls embeds")

    @app_commands.command(description="Will stop embedding pxls links in this channel", name="pxunembed")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def pxls_unembed(self, interaction: discord.Interaction):
        if interaction.channel not in self.channels:
            await interaction.response.send_message("This channel is not set up for pxls embeds")
            return
        async with self.bot.database.cursor() as cursor:
            await cursor.execute("DELETE FROM pxls_embed_channels WHERE channel_id = ?", (interaction.channel.id,))
        self.channels.remove(interaction.channel)
        await interaction.response.send_message("This channel is no longer set up for pxls embeds")


async def setup(bot: 'Isabel'):
    while not bot.database:
        await asyncio.sleep(0)

    channels = []

    # create channels table
    async with bot.database.cursor() as cursor:
        await cursor.execute("""
                CREATE TABLE IF NOT EXISTS pxls_embed_channels (
                    channel_id INTEGER UNIQUE
                )
                """)
        await cursor.execute("SELECT channel_id FROM pxls_embed_channels")
        channels.extend([bot.get_channel(i[0]) for i in await cursor.fetchall()])

    await bot.add_cog(PxlsEmbedCog(bot, channels))
