from typing import TYPE_CHECKING

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from main import Isabel

OSU_LOGO_BUILDERS = 297657542572507137


class LogoBuildersCog(commands.Cog):
    def __init__(self, bot: 'Isabel'):
        self.bot = bot
        self.guild = bot.get_guild(OSU_LOGO_BUILDERS)
        self.bans_channel = bot.get_channel(1139236953968087211)
        self.lite_moderation_channel = bot.get_channel(1139240735791665152)
        self.everything_channel = bot.get_channel(1139241038456815686)
        self.last_message_delete_entry: discord.AuditLogAction.message_delete = None
        self.last_message_delete_message: discord.Message = None

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry):
        if entry.guild != self.guild:
            return
        # on_member_ban
        if entry.action == discord.AuditLogAction.ban:
            embed = discord.Embed(
                description=f"💥 {entry.user.mention} banned <@{entry.target.id}>",
                color=discord.Color.red()
            )
            embed.set_author(name=entry.user, icon_url=entry.user.avatar.url)
            embed.add_field(name="Reason" if entry.reason else "No reason provided", value=entry.reason or "\u200b")
            content = None if entry.reason else f"{entry.user.mention} please provide a reason in this channel"
            await self.bans_channel.send(content=content, embed=embed)
        # on_member_unban # TODO: this doesn't get triggered??
        elif entry.action == discord.AuditLogAction.unban:
            embed = discord.Embed(
                description=f"🚪 {entry.user.mention} unbanned <@{entry.target.id}>",
                color=discord.Color.green()
            )
            embed.set_author(name=entry.user, icon_url=entry.user.avatar.url)
        # on_member_kick
        elif entry.action == discord.AuditLogAction.kick:
            embed = discord.Embed(
                description=f"👢 {entry.user.mention} kicked <@{entry.target.id}>",
                color=discord.Color.dark_orange()
            )
            embed.set_author(name=entry.user, icon_url=entry.user.avatar.url)
            embed.add_field(name="Reason" if entry.reason else "No reason provided", value=entry.reason or "\u200b")
            content = None if entry.reason else f"@silent {entry.user.mention} please provide a reason in this channel"
            await self.bans_channel.send(content=content, embed=embed)
        # on_member_update (timed_out_until)
        elif entry.action == discord.AuditLogAction.member_update and hasattr(entry.after, 'timed_out_until'):
            content = None
            if entry.after.timed_out_until:
                embed = discord.Embed(
                    description=f"⏱️ {entry.user.mention} timed out {entry.target.mention}",
                    color=discord.Color.dark_gray()
                )
                embed.add_field(name="Expires", value=discord.utils.format_dt(entry.after.timed_out_until, style='R'))
                embed.add_field(name="Reason" if entry.reason else "No reason provided", value=entry.reason or "\u200b")
                content = None if entry.reason else f"@silent {entry.user.mention} please provide a reason in this channel"
            else:
                # TODO: is this triggered automatically when the timeout expires? if so, what is entry.user?
                embed = discord.Embed(
                    description=f"🏃 {entry.user.mention} removed {entry.target.mention}'s timeout",
                    color=discord.Color.light_gray()
                )
            embed.set_author(name=entry.user, icon_url=entry.user.avatar.url)
            await self.lite_moderation_channel.send(content=content, embed=embed)
        # on_member_update (mute)
        elif entry.action == discord.AuditLogAction.member_update and hasattr(entry.after, 'mute'):
            if entry.after.mute:
                embed = discord.Embed(
                    description=f"🙊 {entry.user.mention} server muted {entry.target.mention}",
                    color=discord.Color.dark_gray()
                )
            else:
                embed = discord.Embed(
                    description=f"🗣️ {entry.user.mention} server unmuted {entry.target.mention}",
                    color=discord.Color.light_gray()
                )
            embed.set_author(name=entry.user, icon_url=entry.user.avatar.url)
            await self.lite_moderation_channel.send(embed=embed)
        # on_member_update (deaf)
        elif entry.action == discord.AuditLogAction.member_update and hasattr(entry.after, 'deaf'):
            if entry.after.deaf:
                embed = discord.Embed(
                    description=f"🔇 {entry.user.mention} server deafened {entry.target.mention}",
                    color=discord.Color.dark_gray()
                )
            else:
                embed = discord.Embed(
                    description=f"🔈 {entry.user.mention} server undeafened {entry.target.mention}",
                    color=discord.Color.light_gray()
                )
            embed.set_author(name=entry.user, icon_url=entry.user.avatar.url)
            await self.lite_moderation_channel.send(embed=embed)
        # on_member_update (nick)
        elif entry.action == discord.AuditLogAction.member_update and hasattr(entry.after, 'nick'):
            their = 'their' if entry.target == entry.user else f"{entry.target.mention}'s"
            actioned = "changed" if entry.before.nick else "set"
            actioned = actioned if entry.after.nick else "removed"
            embed = discord.Embed(
                description=f"📝 {entry.user.mention} {actioned} {their} nickname",
                color=discord.Color.lighter_grey()
            )
            if getattr(entry.before, 'nick', None):
                embed.add_field(name="Before", value=entry.before.nick)
            if getattr(entry.after, 'nick', None):
                embed.add_field(name="After", value=entry.after.nick)
            embed.set_author(name=entry.user, icon_url=entry.user.avatar.url)
            await self.everything_channel.send(embed=embed)
            if entry.target != entry.user:
                if entry.user.bot:
                    return  # don't log automated nickname changes in the lite moderation channel
                await self.lite_moderation_channel.send(embed=embed)
        # on_member_update (roles)
        elif entry.action == discord.AuditLogAction.member_role_update:
            # TODO: check if this event is only fired once on entry creation
            their = 'their' if entry.target == entry.user else f"{entry.target.mention}'s"
            embed = discord.Embed(
                description=f"👥 {entry.user.mention} changed {their} roles",
                color=discord.Color.lighter_grey()
            )
            if len(entry.before.roles) > len(entry.after.roles):
                embed.add_field(
                    name="🚮 Removed",
                    value=', '.join(r.mention for r in entry.before.roles if r not in entry.after.roles)
                )
            if len(entry.before.roles) < len(entry.after.roles):
                embed.add_field(
                    name="⚙️ Added",
                    value=', '.join(r.mention for r in entry.after.roles if r not in entry.before.roles)
                )
            embed.set_author(name=entry.user, icon_url=entry.user.avatar.url)
            await self.everything_channel.send(embed=embed)
            if entry.target != entry.user:
                if entry.user.bot:
                    return  # don't log automated role changes in the lite moderation channel
                await self.lite_moderation_channel.send(embed=embed)
        # on_message_delete
        elif entry.action == discord.AuditLogAction.message_delete:
            # TODO: check if this event is only fired once on entry creation
            channel = self.bot.get_channel(entry.extra.channel.id)
            embed = discord.Embed(
                description=f"❌ {entry.user.mention} deleted <@{entry.target.id}>'s messages in {channel.mention}",
                color=discord.Color.dark_red()
            )
            embed.set_author(name=entry.user, icon_url=entry.user.avatar.url)
            await self.lite_moderation_channel.send(embed=embed)
        # on_bulk_message_delete
        elif entry.action == discord.AuditLogAction.message_bulk_delete:
            channel = self.bot.get_channel(entry.extra.channel.id)
            embed = discord.Embed(
                description=f"❌ {entry.user.mention} deleted {entry.extra.count} messages in {channel.mention}",
                color=discord.Color.dark_red()
            )
            # pretty sure this is bot only endpoint, so they should always add a reason
            embed.add_field(name="Reason" if entry.reason else "No reason provided", value=entry.reason or "\u200b")
            embed.set_author(name=entry.user, icon_url=entry.user.avatar.url)
            await self.lite_moderation_channel.send(embed=embed)


async def setup(bot: 'Isabel'):
    guild = bot.get_guild(OSU_LOGO_BUILDERS)
    if not guild.chunked:
        await guild.chunk(cache=True)
    await bot.add_cog(LogoBuildersCog(bot))
