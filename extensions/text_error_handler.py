import logging
import traceback
from abc import ABCMeta, abstractmethod
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING

from discord.ext import commands

import helper

if TYPE_CHECKING:
    from typing import List


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
    async def handle(self, ctx: commands.Context, err: commands.CommandError):
        pass

    def __str__(self):
        return f"<{self.__class__.__name__}>"


class AnyHandler(HandlerMeta):
    def i_handle(self) -> type:
        return commands.CommandError

    async def handle(self, ctx: commands.Context, err: commands.CommandError):
        ctx.bot.logger.error(f"Unhandled error of type {err.__class__.__name__}")


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
        return commands.CommandInvokeError

    async def handle(self, ctx: commands.Context, err: commands.CommandInvokeError):
        if ctx.command.name == 'debug':
            return
        if not ctx.channel.permissions_for(ctx.me).send_messages:
            return await helper.use().react_or_false(ctx, ("\U0001f507",))
        ctx.bot.logger.error(
            f"CommandInvokeError {err.original.__class__.__module__}.{err.original.__class__.__name__}"
        )

        self.logger.debug("".join(traceback.format_exception(type(err), err, err.__traceback__)))
        await ctx.send("\u274c Error occurred while handling the command.")


class NotFoundHandler(HandlerMeta):
    def i_handle(self) -> type:
        return commands.CommandNotFound

    async def handle(self, ctx: commands.Context, err: commands.CommandNotFound):
        await helper.use().react_or_false(ctx, ("\u2753",))
        try:
            logger = ctx.bot.commands_logger
        except AttributeError:
            logger = logging.getLogger('commands')
            ch = RotatingFileHandler("logs/commands.log", maxBytes=5000000, backupCount=1, encoding='UTF-8')
            ch.setFormatter(logging.Formatter('%(asctime)s %(levelname)-8s [%(name)s] %(message)s'))
            ch.setLevel(1)
            logger.addHandler(ch)
            ctx.bot.commands_logger = logger
        logger.info(f'Unknown command: {ctx.invoked_with}')


class CheckFailureHandler(HandlerMeta):
    def i_handle(self):
        return commands.CheckFailure

    async def handle(self, ctx: commands.Context, err: commands.CheckFailure):
        if any(i.__qualname__.startswith('is_owner') for i in ctx.command.checks):
            return await helper.use().react_or_false(ctx, ("\u2753",))
        await ctx.send(f"❌ Check failure. {str(err)}")


class BadInputHandler(HandlerMeta):
    def i_handle(self) -> type:
        return commands.UserInputError

    async def handle(self, ctx: commands.Context, err: commands.UserInputError):
        await ctx.send(f"❌ Bad argument: {' '.join(err.args)}")


class ConversionHandler(HandlerMeta):
    def i_handle(self) -> type:
        return commands.ConversionError

    async def handle(self, ctx: commands.Context, err: commands.ConversionError):
        await ctx.send("\u274c Bad argument: Failed to use converter. "
                       "You shouldn't see this error, please report it")


class CooldownHandler(HandlerMeta):
    def i_handle(self) -> type:
        return commands.CommandOnCooldown

    async def handle(self, ctx: commands.Context, err: commands.CommandOnCooldown):
        if not await helper.use().react_or_false(ctx, ("\u23f0",)):
            await ctx.send(f"⏰ {str(err)}")


class TextErrorCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.handlers: List[HandlerMeta] = []

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

    @commands.Cog.listener()
    async def on_command_error(self, ctx, err):
        await self.get_best_handler(err).handle(ctx, err)


async def setup(bot):
    ec = TextErrorCog(bot)
    logger = logging.getLogger('TextErrorHandler')
    for c in list(globals().values()):
        if not isinstance(c, type):
            continue
        if issubclass(c, HandlerMeta) and c is not HandlerMeta:
            try:
                ec.add_handler(c())
            except Exception as err:
                logger.error(f"Failed to add handler {c.__name__} due to {err.__class__}: {err}")
    await bot.add_cog(ec)

