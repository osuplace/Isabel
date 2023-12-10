import collections
import json
from typing import Any, Iterable

import aiohttp
import discord
from discord.ext import commands

import phelp

intervals = (
    ('weeks', 60 * 60 * 24 * 7),
    ('days', 60 * 60 * 24),
    ('hours', 60 * 60),
    ('minutes', 60),
    ('seconds', 1),
)


def display_time(seconds, granularity=2):
    seconds = int(seconds)
    result = []

    for name, count in intervals:
        if value := seconds // count:
            seconds -= value * count
            if value == 1:
                name = name.rstrip('s')
            result.append(f"{value} {name}")
    return ', '.join(result[:granularity])


def chunks(list_in, n):
    """Yield successive n-sized chunks from list_in."""
    for i in range(0, len(list_in), n):
        yield list_in[i:i + n]


async def react_or_false(ctx: commands.Context, reactions: Iterable = ("\u2705",)):
    if not isinstance(ctx, commands.Context):
        raise TypeError("ctx must be of type commands.Context")
    if ctx.channel.permissions_for(ctx.me).add_reactions:
        aa = True
        for r in reactions:
            try:  # This should be fine, we have permissions to react to messages
                await ctx.message.add_reaction(r)
            except (discord.HTTPException, discord.NotFound):
                aa = False
                continue
        return aa
    return False


async def report_success(ctx: commands.Context, message: str = "Success!"):
    if not await react_or_false(ctx):
        await phelp.use().p_send(ctx, message)


async def send_or_post_gist(ctx: commands.Context, content: str):
    success = False
    try:
        success = await phelp.use().p_send(ctx.channel, content)
    except commands.CheckFailure:
        pass
    if not success:
        payload = {"description": "debug ret",
                   "public": False,
                   "files": {"ret.md": {"content": content}}
                   }
        async with aiohttp.ClientSession(headers={'Authorization': f'token {ctx.bot.config["gist_token"]}'}) as session:
            async with session.post(
                    'https://api.github.com/gists',
                    params={'scope': 'gist'},
                    data=json.dumps(payload)
            ) as response:
                if 200 <= response.status < 300:
                    jj = json.loads(await response.text())
                    await ctx.send(f"<{jj['html_url']}>")
                else:
                    await ctx.send(f"Result too big and GitHub responded with {response.status}")


def safety_escape_in_monospace(string: Any):
    safe = str(string).replace('`', '\u02cb')
    return f"`{safe}`"


def safety_escape_regular(string: Any):
    return str(string).replace(
        '`', '\u02cb'
    ).replace(
        '*', '\u2217'
    ).replace(
        '@', '@\u200b'
    ).replace(
        '\u0023', '\u0023\u200b'  # replacing '#' with '#zws'
    )


def number_to_reaction(number: int):
    if not isinstance(number, int):
        return "\u26a0"
    if number == 10:
        return '\U0001f51f'
    if number > 9 or number < 0:
        return "\u26a0"
    return f"{number}\u20E3"


def number_to_partial_emoji(number: int):
    return discord.PartialEmoji(name=number_to_reaction(number))


def reaction_to_number(reaction: str):
    try:
        return int(reaction[0])
    except ValueError:
        return -1


def get_user_agent(bot):
    return f"Isabel (https://github.com/osuplace/Isabel) {bot.http.user_agent}"


def find_my_emoji(bot, name: str) -> discord.Emoji:
    return discord.utils.get(discord.utils.get(bot.guilds, owner=bot.user).emojis, name=name)


def safe_attr_get(obj, attr, default):
    try:
        return getattr(obj, attr)
    except AttributeError:
        setattr(obj, attr, default)
        return getattr(obj, attr)
