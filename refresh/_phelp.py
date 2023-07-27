from typing import Optional, Union

import discord
from discord.ext import commands


def _p_ch(channel: Union[discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel]):
    return channel.permissions_for(channel.guild.me)


async def p_move_to(member: discord.Member, channel: Optional[discord.VoiceChannel]):
    if not member.guild.me.guild_permissions.move_members:
        raise commands.BotMissingPermissions(['move_members'])
    if not member.voice:
        return False
    if member.voice.channel == channel:
        return True
    await member.move_to(channel)
    return True


async def p_channel_delete(channel: Union[
    discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel, None
]):
    if channel is None:
        return True
    if not _p_ch(channel).manage_channels:
        raise commands.BotMissingPermissions(['manage_channels'])
    try:
        await channel.delete()
        return True
    except discord.NotFound:
        return True


async def p_send(channel: discord.abc.Messageable, content, **kwargs):
    if isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel)):
        if not _p_ch(channel).send_messages:
            return False
        if content and len(content) > 2000:
            return False
        if 'embed' in kwargs and not _p_ch(channel).embed_links:
            raise commands.BotMissingPermissions(['embed_links'])
    elif content and len(content) > 2000:
        return False
    # noinspection PyUnresolvedReferences
    await channel.send(content, **kwargs)
    return True


async def p_message_delete(message: discord.Message):
    if isinstance(message.channel, discord.DMChannel):
        if message.author != message.channel.me:
            return False
        await message.delete()
        return True
    if message.author == message.channel.guild.me:
        await message.delete()
        return True
    if not _p_ch(message.channel).manage_messages:
        raise commands.BotMissingPermissions(['manage_messages'])
    await message.delete()
    return True


async def p_add_reaction(message: discord.Message, emoji):
    if isinstance(message.channel, discord.DMChannel):
        await message.add_reaction(emoji)
        return True
    if not _p_ch(message.channel).add_reactions:
        raise commands.BotMissingPermissions(['add_reactions'])
    await message.add_reaction(emoji)
    return True


async def p_remove_reaction(message: discord.Message, emoji, user: discord.abc.User):
    if isinstance(message.channel, discord.DMChannel):
        if user == message.channel.me:
            await message.remove_reaction(emoji, user)
            return True
        return False
    if not _p_ch(message.channel).manage_messages and user != message.guild.me:
        raise commands.BotMissingPermissions(['manage_messages'])
    await message.remove_reaction(emoji, user)
    return True
