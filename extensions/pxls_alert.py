import asyncio
import itertools
import json
import logging
import time
from io import BytesIO
from typing import TYPE_CHECKING

import aiohttp
import contextlib
import discord
from PIL import Image
from discord import app_commands
from discord.ext import commands

import helper

if TYPE_CHECKING:
    from main import Isabel
    from typing import Awaitable


def hex_to_rgb(hexcode):
    # Convert hex to RGB
    hexcode = hexcode.lstrip('#')
    return tuple(int(hexcode[i:i + 2], 16) for i in (0, 2, 4, 6))


class PxlsCog(commands.Cog):
    def __init__(self, bot: 'Isabel'):
        self.bot = bot
        self.logger = logging.getLogger("pxls")
        self.session = aiohttp.ClientSession()
        self.session.headers.update({'User-Agent': helper.use().get_user_agent(bot)})
        self.palette = []
        self.width = 0
        self.height = 0
        self.board = []
        self.task = asyncio.create_task(self.listen_to_pixels())
        self.on_board_change: list['Awaitable'] = []

    def cog_unload(self):
        self.task.cancel()

    async def listen_to_pixels(self):
        self.logger.info("Starting websocket task")
        # get palette
        async with self.session.get("https://pxls.space/info") as resp:
            data = await resp.json()
            self.palette = [hex_to_rgb(i['value'] + 'FF') for i in data['palette']]
            self.width = data['width']
            self.height = data['height']
        # get board
        async with self.session.get("https://pxls.space/boarddata") as resp:
            self.board = bytearray(await resp.read())
        # connect to websocket
        async with self.session.ws_connect("wss://pxls.space/ws") as ws:
            while True:
                try:
                    msg = await ws.receive()
                    try:
                        parsed = msg.json()
                        if parsed['type'] == 'pixel':
                            for pixel in parsed['pixels']:
                                x, y, color = pixel['x'], pixel['y'], pixel['color']
                                self.board[y * self.width + x] = color
                                for awaitable in self.on_board_change:
                                    await awaitable
                    except (json.JSONDecodeError, TypeError):
                        if msg:
                            self.logger.warning(f"Received invalid JSON from websocket: {msg}")
                        else:
                            self.logger.warning("Received empty message from websocket")
                        await asyncio.sleep(5)
                except asyncio.CancelledError:
                    await ws.close()
                    return
                except Exception as e:
                    self.logger.exception(e)
                    await asyncio.sleep(5)

    @app_commands.command(description="Shows the current pxls.space canvas")
    async def canvas(self, interaction: discord.Interaction):
        while not self.board:
            await asyncio.sleep(1)
        img = Image.new('P', (self.width, self.height))
        img.putpalette(itertools.chain.from_iterable(self.palette + [(0, 0, 0, 0)] * (256 - len(self.palette))), 'RGBA')
        img.putdata(self.board)
        buffer = BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        embed = discord.Embed(title="Current pxls.space canvas")
        attachment_name = f"canvas-{int(time.time())}.png"
        embed.set_image(url=f"attachment://{attachment_name}")
        await interaction.response.send_message(embed=embed, file=discord.File(buffer, filename=attachment_name))


async def setup(bot):
    pc = PxlsCog(bot)
    await bot.add_cog(pc)

# todo list
# [x] add a way for discord user to see current canvas
# [ ] helper function to parse pxls template url
# [ ] add a way for template managers to add templates
# [ ] add a way for template managers to remove templates
# [ ] add a way for template managers to update templates
# [ ] allow moderators to assign what permissions/roles are template managers
# [ ] allow moderators to designate a channel as a template channel
# [ ] alert general pxls channel when a template is under attack
# [ ] allow moderators to designate a channel as a template alert channel
# [ ] allow moderators to start/stop template alerts (per template?)
