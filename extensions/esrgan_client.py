import asyncio
import io
import time
from typing import TYPE_CHECKING

import discord
from discord.ext import commands
from discord import app_commands, Interaction

if TYPE_CHECKING:
    from main import Isabel

DISCLAIMER = "-# run the model yourself on a GPU, download [chaiNNer](<https://github.com/chaiNNer-org/chaiNNer>)"


def progress_to_message(progress: int, total: int) -> str:
    percentage = int(progress / total * 100)
    return f"Processing image... {percentage}%\n{DISCLAIMER}"


class EsrganCog(commands.Cog):
    def __init__(self, bot: 'Isabel'):
        self.bot = bot
        self.running_users = set()

    @app_commands.command(description="Downscale an image to 25% using custom ESRGAN model")
    async def downscale(self, interaction: Interaction, image: discord.Attachment):
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', 7272)
        except ConnectionRefusedError:
            await interaction.response.send_message("ESRGAN server is not running", ephemeral=True)
            return

        if interaction.user.id in self.running_users:
            await interaction.response.send_message("You are already processing an image", ephemeral=True)
            return

        self.running_users.add(interaction.user.id)

        try:
            if image.content_type == 'image/png':
                'PNG'
            elif image.content_type == 'image/jpeg':
                'JPEG'
            else:
                await interaction.response.send_message("Unsupported format", ephemeral=True)
                return

            if image.height > 1000 or image.width > 1000:
                await interaction.response.send_message("Image is too large (max 1000x1000)", ephemeral=True)
                return


            writer.write(await image.read())
            writer.write_eof()
            await writer.drain()

            tile_count_bytes = await reader.readexactly(4)
            tile_count = int.from_bytes(tile_count_bytes, 'big')
            processed_tiles = 0

            await interaction.response.send_message(progress_to_message(processed_tiles, tile_count))
            last_message_sent = time.time()

            while True:
                await reader.readline()
                processed_tiles += 1
                if processed_tiles == tile_count:
                    break
                # only send progress message every 2 seconds
                if time.time() - last_message_sent > 2:
                    last_message_sent = time.time()
                    await interaction.edit_original_response(content=progress_to_message(processed_tiles, tile_count))

            wrapper = io.BytesIO(await reader.read())
            wrapper.seek(0)

            response_file = discord.File(wrapper, filename='output.png')
            await interaction.edit_original_response(content=DISCLAIMER, attachments=[response_file])
            writer.close()
            if interaction.guild is None:
                msg = await interaction.original_response()
                await msg.reply(f"{interaction.user.mention} Finished downscaling image")
            else:
                await interaction.followup.send(f"{interaction.user.mention} Finished downscaling image", ephemeral=True)
        finally:
            self.running_users.discard(interaction.user.id)


async def setup(bot):
    await bot.add_cog(EsrganCog(bot))
