import contextlib
import logging
from typing import TYPE_CHECKING, Union

import discord
from discord.ext import commands
from extensions.anti_links import ROLE_ID as PERMITS_LINKS_ROLE_ID

if TYPE_CHECKING:
    from main import Isabel

OSU_LOGO_BUILDERS = 297657542572507137
ISABEL_ID = 1134144074987864186
IGNORE_MESSAGE_DELETIONS = (1185520392027263027, 1165042770633826384)


def get_message_delete_embed(entry: discord.AuditLogEntry):
    channel = f"<#{entry.extra.channel.id}>"
    messages = f"{entry.extra.count} messages" if entry.extra.count > 1 else "a message"
    embed = discord.Embed(
        description=f"❌ {entry.user.mention} deleted {messages} by <@{entry.target.id}> in {channel}",
        color=discord.Color.dark_red()
    )
    embed.set_author(name=entry.user, icon_url=entry.user.avatar.url)
    return embed


class RoleUpdateHandler:
    def __init__(self, entry: discord.AuditLogEntry):
        if entry.action != discord.AuditLogAction.member_role_update:
            raise ValueError(
                f"RoleUpdateHandler expected {discord.AuditLogAction.member_role_update}, got {entry.action}"
            )
        self.user: discord.Member = entry.user
        self.target: Union[discord.Member, discord.Object] = entry.target
        self.added: set[int] = {i.id for i in entry.after.roles}
        self.removed: set[int] = {i.id for i in entry.before.roles}
        self.messages: list[discord.Message] = []

        self.embed = self.create_embed()

    def create_embed(self):
        their = 'their' if self.target == self.user else f"{self.target.mention}'s"
        embed = discord.Embed(
            description=f"👥 {self.user.mention} changed {their} roles",
            color=discord.Color.lighter_grey()
        )
        if self.added:
            embed.add_field(
                name="🆕 Added",
                value=', '.join(f"<@&{r}>" for r in self.added)
            )
        if self.removed:
            embed.add_field(
                name="🚮 Removed",
                value=', '.join(f"<@&{r}>" for r in self.removed)
            )
        embed.set_author(name=self.user, icon_url=self.user.avatar.url)
        return embed

    async def update(self, entry):
        if entry.action != discord.AuditLogAction.member_role_update:
            raise ValueError(
                f"RoleUpdateHandler expected {discord.AuditLogAction.member_role_update}, got {entry.action}"
            )
        # add the new roles to the list
        self.added = self.added.union([i.id for i in entry.after.roles])
        self.removed = self.removed.union([i.id for i in entry.before.roles])
        # remove the old roles from the list (added then removed or removed then added)
        in_both = self.added.intersection(self.removed)
        self.added = self.added.difference(in_both)
        self.removed = self.removed.difference(in_both)
        # update the embed
        embed = self.create_embed()
        if embed.to_dict() == self.embed.to_dict():
            logging.info("embeds are the same")
            return
        self.embed = embed
        # edit messages
        for message in self.messages:
            await message.edit(embed=self.embed)


class LogoBuildersCog(commands.Cog):
    def __init__(self, bot: 'Isabel'):
        self.bot = bot
        self.guild = bot.get_guild(OSU_LOGO_BUILDERS)
        self.test_channel = bot.get_channel(1139543003946549338)
        is_isabel = bot.user.id == ISABEL_ID
        self.voice_logs_channel = bot.get_channel(1254072010787651584) if is_isabel else self.test_channel
        self.bans_channel = bot.get_channel(1139236953968087211) if is_isabel else self.test_channel
        self.lite_moderation_channel = bot.get_channel(1139240735791665152) if is_isabel else self.test_channel
        self.everything_channel = bot.get_channel(1139241038456815686) if is_isabel else self.test_channel
        self.delete_messages_entries: dict[int, tuple[int, int]] = {}  # {entry_id: (message_id, count)}
        self.role_update_handlers: list[RoleUpdateHandler] = []

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
            content = None if entry.reason else f"{entry.user.mention} please provide a reason in this channel"
            await self.bans_channel.send(content=content, embed=embed, silent=True)
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
                content = None if entry.reason else f"{entry.user.mention} please provide a reason in this channel"
            else:
                # TODO: is this triggered automatically when the timeout expires? if so, what is entry.user?
                # TODO: does this combine if done fast enough and no new entry is created?
                embed = discord.Embed(
                    description=f"🏃 {entry.user.mention} removed {entry.target.mention}'s timeout",
                    color=discord.Color.light_gray()
                )
            embed.set_author(name=entry.user, icon_url=entry.user.avatar.url)
            await self.lite_moderation_channel.send(content=content, embed=embed, silent=True)
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
            embed.set_footer(text=f"Their global username: {entry.target.global_name}")
            await self.everything_channel.send(embed=embed)
            if entry.target != entry.user:
                if entry.user.bot:
                    return  # don't log automated nickname changes in the lite moderation channel
                await self.lite_moderation_channel.send(embed=embed)
        # on_member_update (roles)
        elif entry.action == discord.AuditLogAction.member_role_update:
            # special case to not log the links permitting role changes
            if entry.user.bot:
                adds_links = (
                        len(entry.before.roles) == 0
                        and len(entry.after.roles) == 1
                        and entry.after.roles[0].id == PERMITS_LINKS_ROLE_ID
                )
                removes_links = (
                        len(entry.before.roles) == 1
                        and len(entry.after.roles) == 0
                        and entry.before.roles[0].id == PERMITS_LINKS_ROLE_ID
                )
                if adds_links or removes_links:
                    return

            # even though this appears as one entry in the audit log, API treats it as multiple entries
            for ruh in self.role_update_handlers:
                if ruh.user == entry.user and ruh.target == entry.target:
                    await ruh.update(entry)
                    return  # RoleUpdateHandler handles editing the messages

            self.role_update_handlers = self.role_update_handlers[-5:]  # only keep the last 5

            ruh = RoleUpdateHandler(entry)
            self.role_update_handlers.append(ruh)
            messages = [await self.everything_channel.send(embed=ruh.embed)]

            if entry.target != entry.user:
                if entry.user.bot:
                    return  # don't log automated role changes in the lite moderation channel
                messages.append(await self.lite_moderation_channel.send(embed=ruh.embed))
            ruh.messages = messages

        # on_message_delete
        elif entry.action == discord.AuditLogAction.message_delete:
            embed = get_message_delete_embed(entry)
            is_temp = entry.extra.channel.id in IGNORE_MESSAGE_DELETIONS
            channel = self.everything_channel if is_temp else self.lite_moderation_channel
            message = await channel.send(embed=embed)
            self.delete_messages_entries[entry.id] = (message.id, entry.extra.count)
        # on_bulk_message_delete
        elif entry.action == discord.AuditLogAction.message_bulk_delete:
            embed = discord.Embed(
                description=f"❌ {entry.user.mention} deleted {entry.extra.count} messages in <#{entry.target.id}>",
                color=discord.Color.dark_red()
            )
            # pretty sure this is bot only endpoint, so they should always add a reason
            embed.add_field(name="Reason" if entry.reason else "No reason provided", value=entry.reason or "\u200b")
            embed.set_author(name=entry.user, icon_url=entry.user.avatar.url)
            await self.lite_moderation_channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, _):
        not_found_ids = list(self.delete_messages_entries.keys())
        async for entry in self.guild.audit_logs(
                limit=20,
                action=discord.AuditLogAction.message_delete,
        ):
            if entry.id not in not_found_ids:
                continue

            not_found_ids.remove(entry.id)

            message_id, count = self.delete_messages_entries[entry.id]
            if count == entry.extra.count:
                continue

            embed = get_message_delete_embed(entry)
            is_temp = entry.extra.channel.id in IGNORE_MESSAGE_DELETIONS
            channel = self.everything_channel if is_temp else self.lite_moderation_channel
            message = discord.PartialMessage(channel=channel, id=message_id)
            with contextlib.suppress(discord.HTTPException):  # for messages that can't be edited for whatever reason
                await message.edit(embed=embed)
                self.delete_messages_entries[entry.id] = (message_id, entry.extra.count)
        for i in not_found_ids:
            del self.delete_messages_entries[i]

    @commands.Cog.listener()
    async def on_voice_state_update(self, member:discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        async def create_embed(description: str):
            embed = discord.Embed(
                description=description, color=discord.Color.light_gray()
            )
            embed.set_author(name=member, icon_url=member.avatar.url)
            await self.voice_logs_channel.send(embed=embed)

        if isinstance(after.channel, discord.StageChannel) or isinstance(
            before.channel, discord.StageChannel
        ):
            description = ""
            if before.suppress == True and after.suppress == False:
                description = (
                    f"🔈 {member.mention} started speaking in {after.channel.mention}"
                )
                await create_embed(description)
            elif before.suppress == False and after.suppress == True:
                description = (
                    f"🔇 {member.mention} stopped speaking in {before.channel.mention}"
                )
                await create_embed(description)

            else:
                return

        if before.channel == after.channel:
            return

        description = ""
        if before.channel and not after.channel:
            description = f"🔇 {member.mention} left {before.channel.mention}"
            await create_embed(description)
        elif not before.channel and after.channel:
            description = f"🔈 {member.mention} joined {after.channel.mention}"
            await create_embed(description)
        else:
            description = f"🔄 {member.mention} moved from {before.channel.mention} to {after.channel.mention}"
            await create_embed(description)


async def setup(bot: 'Isabel'):
    guild = bot.get_guild(OSU_LOGO_BUILDERS)
    if not guild.chunked:
        await guild.chunk(cache=True)
    await bot.add_cog(LogoBuildersCog(bot))
