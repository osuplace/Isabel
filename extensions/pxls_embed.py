import asyncio
import io
import logging
import re
import urllib.parse
from typing import TYPE_CHECKING, Iterable, Optional

import aiohttp
import discord
import numpy as np
from PIL import Image
from discord import app_commands
from discord.ext import commands
from numba import jit

if TYPE_CHECKING:
    from main import Isabel

PXLS_REGEX = re.compile(r"(?:https?://)?((?:www\.)?pxls\.space|(?:[a-z0-9\-]+\.)?pxls\.world)/#\S+")
IMAGE_TIMEOUT = aiohttp.ClientTimeout(total=60)
LEGAL_CHARACTERS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"


def remove_illegal_characters(text: str) -> str:
    return ''.join(c if c in LEGAL_CHARACTERS else '_' for c in text)


@jit(nopython=True, cache=True)
def fast_remove_style(array, target_height, target_width, tile_width):
    #  pxlsspace/Clueless/blob/354b8eb92ad87517d9f488e1d655535de468c8bf/src/utils/pxls/template_manager.py#L812
    #  MIT License https://github.com/pxlsspace/Clueless/blob/354b8eb92ad87517d9f488e1d655535de468c8bf/LICENSE
    result = np.zeros((target_height, target_width, 4), dtype=np.uint8)

    for y in range(target_height):
        for x in range(target_width):
            for j in range(tile_width):
                for i in range(tile_width):
                    py = y * tile_width + j
                    px = x * tile_width + i
                    alpha = array[py, px, 3]
                    if alpha > 128:
                        result[y, x] = array[py, px]
                        result[y, x, 3] = 255
                        break
                else:
                    continue  # inner loop was *not* broken, continue the outer loop
                break  # inner loop was broken, break the outer loop too

    return result


class EmbedController:
    def __init__(self, pxls_urls: Iterable[re.Match[str]], session: aiohttp.ClientSession):
        self.urls = [match[0] for match in pxls_urls]
        self.session = session
        self.message: Optional[discord.Message] = None
        self.images: dict[str, bytes] = {}
        self.files: dict[str, discord.File] = {}
        self.embeds: dict[str, discord.Embed] = {}  # essentially a

    def get_embeds(self):
        return sorted(self.embeds.values(), key=lambda e: self.urls.index(e.url))

    # noinspection PyTypeChecker
    def embed_single(self, url):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).fragment)
        title = params.get('title', ['Template'])[0]
        if template := params.get('template', [''])[0]:
            escaped = urllib.parse.unquote(template)
            self.embeds[url] = discord.Embed(title=title, url=url).set_image(url=escaped)

    # noinspection PyTypeChecker
    def embed_all(self):
        # same as above but tries to use images with no style
        for n, url in enumerate(self.urls):
            params = urllib.parse.parse_qs(urllib.parse.urlparse(url).fragment)
            title = params.get('title', [f'Template{n}'])[0]
            safe_title = remove_illegal_characters(title)
            if template := params.get('template', [''])[0]:
                if url in self.files:
                    self.embeds[url] = discord.Embed(title=title, url=url).set_image(
                        url=f"attachment://{safe_title}.png")
                else:
                    escaped = urllib.parse.unquote(template)
                    self.embeds[url] = discord.Embed(title=title, url=url).set_image(url=escaped)

    # noinspection PyTypeChecker
    async def download_single(self, url):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).fragment)
        if template := params.get('template', [''])[0]:
            escaped = urllib.parse.unquote(template)
            async with self.session.get(escaped, timeout=IMAGE_TIMEOUT) as resp:
                if resp.status == 200:
                    self.images[url] = await resp.read()
                else:
                    resp.raise_for_status()

    # noinspection PyTypeChecker
    def remove_style_all(self):
        for n, url in enumerate(self.urls):
            if url in self.images:
                params = urllib.parse.parse_qs(urllib.parse.urlparse(url).fragment)
                title = params.get('title', [f'Template{n}'])[0]
                safe_title = remove_illegal_characters(title)
                tw = params.get('tw', [-1])[0]
                if tw == -1:
                    continue
                target_width = int(tw)
                img = Image.open(io.BytesIO(self.images[url])).convert('RGBA')
                width, _ = img.size
                if target_width == width:
                    continue
                tile_width = int(width / target_width)
                target_height = int(img.height / tile_width)
                img_arr = np.array(img)
                no_style_arr = fast_remove_style(img_arr, target_height, target_width, tile_width)
                img_no_style = Image.fromarray(no_style_arr)
                # upscale to 400px (seems to be discord css limit)
                scale = 1
                while target_width * scale < 400:
                    scale += 1
                if scale > 1:
                    img_no_style = img_no_style.resize((target_width * scale, target_height * scale), Image.NEAREST)
                img_no_style_bytes = io.BytesIO()
                img_no_style.save(img_no_style_bytes, format='PNG')
                img_no_style_bytes.seek(0)
                self.files[url] = discord.File(img_no_style_bytes, filename=f"{safe_title}.png")

    async def send_reply_to(self, message: discord.Message):
        # send initial message
        for url in self.urls:
            self.embed_single(url)
        if not self.embeds:
            return  # message content had no pxls urls
        try:
            self.message = await message.reply(embeds=self.get_embeds(), mention_author=False)
        except Exception as err:
            logging.exception(err)
            return
        # download images
        tasks = [asyncio.create_task(self.download_single(url)) for url in self.urls]
        await asyncio.gather(*tasks)
        while not all(task.done() for task in tasks):
            await asyncio.sleep(1)
        for task in tasks:
            if exc := task.exception():
                logging.exception(exc)
        # remove style
        await asyncio.get_event_loop().run_in_executor(None, self.remove_style_all)
        # send new message
        self.embed_all()
        await self.message.edit(embeds=self.get_embeds(), attachments=list(self.files.values()))
        await asyncio.sleep(5)


class PxlsEmbedCog(commands.Cog):
    def __init__(self, bot, channels):
        self.bot: 'Isabel' = bot
        self.channels = channels
        self.session = aiohttp.ClientSession()
        # TODO: replace with auth (would require me to get unblocked from pxls.space)
        self.session.headers['User-Agent'] = 'Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)'

    async def cog_unload(self):
        await self.session.close()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return  # ignore bots
        if message.channel not in self.channels:
            return  # ignore channels that are not set up for pxls embeds
        await EmbedController(PXLS_REGEX.finditer(message.content), self.session).send_reply_to(message)

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
