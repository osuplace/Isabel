import asyncio
import concurrent.futures
import contextlib
import inspect
import io
import json
import logging
import signal
import textwrap
import traceback
from contextlib import redirect_stderr, redirect_stdout
from typing import TYPE_CHECKING

import discord
from contextlib import suppress
from discord.ext import commands

import core_cog
import helper
import phelp

if TYPE_CHECKING:
    from main import Isabel


async def never_unload(me):
    try:
        await me.bot.add_cog(core_cog.use().Core(me.bot))
    except Exception as err:
        logging.error("".join(traceback.format_exception(type(err), err, err.__traceback__)))
        await me.bot.add_cog(me.__class__(me.bot))


ping_cooldown = commands.CooldownMapping.from_cooldown(1, 30, commands.BucketType.user)


def owner_or_cooldown():
    def predicate(ctx):
        if ctx.author.id == ctx.bot.owner_id:
            return True
        bucket = ping_cooldown.get_bucket(ctx.message)
        if retry_after := bucket.update_rate_limit():
            raise commands.CommandOnCooldown(bucket, retry_after, commands.BucketType.user)
        return True

    return commands.check(predicate)


def source(obj):
    return "".join(inspect.getsourcelines(obj)[0])


class Core(commands.Cog):
    def __init__(self, bot):
        self._last_result = None
        self.bot: 'Isabel' = bot
        self._futures = []

    def cog_unload(self):
        logging.info("reloading core cog")
        asyncio.get_event_loop().create_task(never_unload(self))

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if self.bot.owner_id != payload.user_id:
            return
        if str(payload.emoji) == '\U0001f502':
            channel = self.bot.get_channel(payload.channel_id)
            if channel is None:
                channel = await self.bot.fetch_channel(payload.channel_id)
            message = await channel.fetch_message(payload.message_id)
            if payload.guild_id:
                coro = self.bot.get_guild(payload.guild_id).fetch_member(payload.user_id)
                message.author = await coro
            else:
                message.author = self.bot.get_user(payload.user_id)
            ctx = await self.bot.get_context(message)
            if ctx.command and ctx.command.name == "eval":
                with suppress(commands.BotMissingPermissions):
                    await phelp.use().p_remove_reaction(message, payload.emoji, message.author)
                msg = await ctx.send(message.content)
                # figure out why past me put this in (2020 me figured this out)
                # I think it's to add reactions to the re-ran message (the one bot just sent in last line)
                # so that they could be run again
                ctx.re_runner = msg
                await ctx.reinvoke()

    @commands.command(name='latency', aliases=['ping', 'marco', 'hello', 'hi', 'hey', 'yo'])
    @owner_or_cooldown()
    async def _latency(self, ctx):
        """Reports bot latency"""
        if ctx.invoked_with.lower() == 'ping':
            msg = await ctx.send("Pong")
        elif ctx.invoked_with.lower() == 'marco':
            msg = await ctx.send("Polo")
        elif ctx.invoked_with.lower() in ['hello', 'hi', 'hey']:
            msg = await ctx.send("Hey")
        else:
            msg = await ctx.send("\U0001f4e1")
        latency = msg.created_at.timestamp() - ctx.message.created_at.timestamp()
        await phelp.use().p_send(
            ctx,
            f"That took {int(latency * 1000)}ms. Discord reports latency of {int(self.bot.latency * 1000)}ms"
        )

    @commands.command(aliases=['reloadall', 'loadall'], hidden=True)
    @commands.is_owner()
    async def reload(self, ctx):
        """Reload all extensions"""
        logging.info("Reloading all extensions")
        for ext in set([i.replace("extensions.", "") for i in self.bot.extensions.keys()] + self.bot.auto_load()):
            try:
                await self.bot.reload_extension(f"extensions.{ext}")
            except commands.ExtensionNotLoaded:
                await self.bot.load_extension(f"extensions.{ext}")
        await phelp.use().p_send(
            ctx,
            "Reloaded already loaded extensions and extensions under auto_load"
        )

    @commands.command()
    @commands.is_owner()
    async def sync(self, ctx):
        await self.bot.tree.sync()
        await helper.use().report_success(ctx, "Commands synced")

    @commands.command(hidden=True)
    @commands.is_owner()
    async def reloadcore(self, ctx):
        """Reloads this cog"""
        await self.bot.remove_cog('Core')
        await phelp.use().p_send(ctx, "Reloading the core cog")

    @commands.command(hidden=True, aliases=['reloadconfig', 'reloadjson', 'loadjson'])
    @commands.is_owner()
    async def loadconfig(self, ctx):
        """Reload the config"""
        try:
            with open(self.bot.config_name) as file_in:
                config = json.load(file_in)
            self.bot.config = config
            if not await helper.use().react_or_false(ctx):
                await phelp.use().p_send(ctx, "Successfully loaded config")
        except Exception as err:
            await phelp.use().p_send(ctx, f"Could not reload config: `{err}`")

    @commands.command(hidden=True)
    @commands.is_owner()
    async def load(self, ctx, *, extension: str):
        """
        Load an extension.
        """
        try:
            await self.bot.load_extension(f"extensions.{extension}")
            await helper.use().report_success(ctx, f"Loaded extension `{extension}`")
        except Exception as err:
            await phelp.use().p_send(ctx, f"Could not load extension: `{err}`")
            self.bot.logger.error(
                "".join(
                    traceback.format_exception(
                        type(err), err.__cause__, err.__traceback__
                    )
                )
            )

    @commands.command(hidden=True)
    @commands.is_owner()
    async def unload(self, ctx, *, extension: str):
        """Unloads an extension."""
        self.bot.logger.info(f"Unloading {extension}")
        try:
            await self.bot.unload_extension(f"extensions.{extension}")
            await helper.use().report_success(ctx, f"Unloaded `{extension}`.")
        except Exception as err:
            await phelp.use().p_send(ctx, f"Could not unload `{extension}` -> `{err}`")
            self.bot.logger.error(
                "".join(
                    traceback.format_exception(
                        type(err), err.__cause__, err.__traceback__
                    )
                )
            )


    @commands.command(hidden=True, name='eval', aliases=['debug', 'exec'])
    @commands.is_owner()
    async def _eval(self, ctx, *, body: str):
        """
        Evaluates a piece of code
        """

        class SignalAlarmError(Exception):
            pass

        def alarm_handler(*_):
            raise SignalAlarmError()

        try:
            signal.signal(signal.SIGALRM, alarm_handler)
        except AttributeError:
            await phelp.use().p_send(ctx, "Can't run eval, SIGALRM is not supported on this platform")
            return

        def remove_sensitive_data(string):
            for sens in [self.bot.config.get(i) for i in self.bot.config.get('unsafe_to_expose')]:
                string = string.replace(sens, '\u2588' * 10)
            return string

        self.bot.logger.debug(f"Running eval command: {ctx.message.content}")
        env = {
            '_': self._last_result,
            'self': self,
            'bot': self.bot,
            'ctx': ctx,
            'channel': ctx.channel,
            'author': ctx.author,
            'guild': ctx.guild,
            'message': ctx.message,
        }
        env.update(globals())

        if body.startswith('```') and body.endswith('```'):
            body = '\n'.join(body.split('\n')[1:-1])
        body = body.strip()
        stdout = io.StringIO()

        to_compile = f'async def func():\n{textwrap.indent(body, "  ")}'

        try:
            exec(to_compile, env)
        except Exception as e:
            return await helper.use().send_or_post_gist(
                ctx,
                f'Can not compile:\n```py\n{e.__class__.__name__}: {e}\n```'
            )

        func = env['func']
        ret = None
        raised_exception = False
        try:
            with redirect_stderr(stdout):
                with redirect_stdout(stdout):
                    try:
                        signal.alarm(1)
                        fut = asyncio.run_coroutine_threadsafe(func(), self.bot.loop)
                        self._futures.append(fut)
                        while not fut.done():
                            await asyncio.sleep(0)
                            signal.alarm(1)
                        ret = fut.result()
                    except (SignalAlarmError, concurrent.futures.CancelledError) as err:
                        ret = err
                    finally:
                        self._futures.remove(fut)
                        signal.alarm(0)

        except Exception as e:
            new_line = "\n"
            to_send = f'```py\n{new_line.join(traceback.format_exception(type(e), e, e.__traceback__))}\n```'
            to_send = remove_sensitive_data(to_send)

            await helper.use().send_or_post_gist(
                ctx, to_send
            )
            raised_exception = True
        finally:
            value = stdout.getvalue()
            if not await helper.use().react_or_false(ctx):
                await ctx.send('\u2705')
            if hasattr(ctx, 're_runner'):
                with suppress(commands.BotMissingPermissions):
                    await phelp.use().p_add_reaction(ctx.re_runner, '\u2705')
            to_send = ""

            if value:
                value = remove_sensitive_data(value)
                value = helper.use().safety_escape_regular(value)
                to_send += f'```py\n{value}\n```'
            if ret:
                if isinstance(ret, Exception):
                    to_send += f'*Return value: {ret.__class__.__name__}*'
                else:
                    self._last_result = ret
                    ret = remove_sensitive_data(str(ret))
                    ret = helper.use().safety_escape_regular(ret)
                    to_send += f'```py\n{ret}\n```'

            if not to_send:
                if not raised_exception:
                    await phelp.use().p_send(ctx, '*No output.*')
            else:
                await helper.use().send_or_post_gist(ctx, to_send)

        await helper.use().react_or_false(ctx, ['\U0001f502'])
        if hasattr(ctx, 're_runner'):
            with suppress(commands.BotMissingPermissions):
                await phelp.use().p_add_reaction(ctx.re_runner, '\U0001f502')

    @commands.command()
    @commands.is_owner()
    async def cancel(self, ctx):
        """Will cancel all running eval tasks"""
        for task in self._futures:
            task.cancel()
        if not await helper.use().react_or_false(ctx):
            await ctx.send("All tasks cancelled")
