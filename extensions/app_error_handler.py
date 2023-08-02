import logging
import traceback
from abc import ABCMeta, abstractmethod
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING

from discord import app_commands, Interaction
from discord.ext import commands

if TYPE_CHECKING:
    from typing import List
    from main import Isabel


class UnhandledError(Exception):
    pass


class HandlerMeta(metaclass=ABCMeta):
    @abstractmethod
    def i_handle(self) -> type:
        pass

    def can_handle(self, err):
        if isinstance(err, Exception):
            return isinstance(err, self.i_handle())
        return issubclass(err, self.i_handle())

    @abstractmethod
    async def handle(self, bot: 'Isabel', interaction: Interaction, err: app_commands.AppCommandError):
        pass

    def __str__(self):
        return f"<{self.__class__.__name__}>"


class AnyHandler(HandlerMeta):
    def i_handle(self) -> type:
        return app_commands.AppCommandError

    async def handle(self, bot: 'Isabel', interaction: Interaction, err: app_commands.AppCommandError):
        bot.logger.error(f"Unhandled error of type {err.__class__.__name__}")
        await interaction.response.send_message(
            "\u274c You should not see this error. Please report this.", ephemeral=True
        )


class CheckFailureHandler(HandlerMeta):
    def i_handle(self):
        return app_commands.CheckFailure

    async def handle(self, bot, interaction: Interaction, err: app_commands.CheckFailure):
        await interaction.response.send_message(f"❌ Check failure. {str(err)}", ephemeral=True)


class InvokeHandler(HandlerMeta):
    def __init__(self):
        formatter = logging.Formatter('%(asctime)s %(levelname)-8s [%(name)s] %(message)s')
        self.logger = logging.getLogger("invoke")
        ih = RotatingFileHandler("logs/invoke.log", maxBytes=1000000, backupCount=1, encoding='UTF-8')
        ih.setLevel(1)
        ih.setFormatter(formatter)
        self.logger.handlers = []
        self.logger.addHandler(ih)

    def i_handle(self) -> type:
        return app_commands.CommandInvokeError

    async def handle(self, bot, interaction: Interaction, err: app_commands.CommandInvokeError):
        bot.logger.error(f"CommandInvokeError {err.original.__class__.__module__}.{err.original.__class__.__name__}")
        self.logger.debug("".join(traceback.format_exception(type(err), err, err.__traceback__)))
        await interaction.response.send_message("\u274c Error occurred while handling the command.", ephemeral=True)


class AppErrorCog(commands.Cog):
    def __init__(self, bot: 'Isabel'):
        self.bot = bot
        self.handlers: List[HandlerMeta] = []

        # jank
        global isabel
        isabel = bot
        bot.tree.on_error = self.handle

    def add_handler(self, handler: HandlerMeta):
        if not isinstance(handler, HandlerMeta):
            raise TypeError("Can only add handlers that inherit from HandlerMeta")

        # check if handler handles something unique
        ss = sorted([i for i in self.handlers if i.can_handle(handler.i_handle())],
                    key=lambda a: len(a.i_handle().mro()))
        if ss and len(ss[-1].i_handle().mro()) >= len(handler.i_handle().mro()):
            raise ValueError("This handler isn't unique enough")

        self.handlers.append(handler)

    def get_best_handler(self, err) -> HandlerMeta:
        return sorted([i for i in self.handlers if i.can_handle(err)], key=lambda a: len(a.i_handle().mro()))[-1]

    async def handle(self, interaction, err):
        await self.get_best_handler(err).handle(self.bot, interaction, err)


async def setup(bot):
    ec = AppErrorCog(bot)
    logger = logging.getLogger('AppErrorHandler')
    for c in list(globals().values()):
        if not isinstance(c, type):
            continue
        if issubclass(c, HandlerMeta) and c is not HandlerMeta:
            try:
                ec.add_handler(c())
            except Exception as err:
                logger.error(f"Failed to add handler {c.__name__} due to {err.__class__}: {err}")
    await bot.add_cog(ec)
