from typing import TYPE_CHECKING

from discord.ext import commands
from discord import app_commands, Interaction

if TYPE_CHECKING:
    from main import Isabel


class TestCog(commands.Cog):
    def __init__(self, bot: 'Isabel'):
        self.bot = bot

    @app_commands.command()
    async def test(self, interaction: Interaction):
        await interaction.response.send_message('response')
        await interaction.followup.send('webhook', ephemeral=True)

    @app_commands.command()
    async def error(self, interaction: Interaction):
        await interaction.response.send_message('This will error')
        raise Exception


async def setup(bot):
    await bot.add_cog(TestCog(bot))
