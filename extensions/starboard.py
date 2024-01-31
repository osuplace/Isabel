import asyncio
import contextlib
import datetime
import math
import re
from collections import OrderedDict
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple, Set, Union

import aiohttp
import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands, tasks

import helper

if TYPE_CHECKING:
    from main import Isabel

MAX_PROMOTION_SECONDS = 24 * 60 * 60
MAX_AGE_SECONDS = 7 * 24 * 60 * 60
STARBOARD_INTERVAL_SECONDS = 60 * 60
MIN_STARS = 2  # set to 0 for testing  # TODO: raise to 4
REQUIREMENTS_UP_MULTIPLIER = 10 / 9
REQUIREMENTS_DOWN_MULTIPLIER = 19 / 20
STAR_EMOJI = '⭐'
VALID_IMAGE_EXTENSIONS = ('png', 'jpg', 'jpeg', 'gif', 'webp')

# idk where copilot got this regex from but it's a good one
# https://gist.github.com/LittleEndu/6c7e36b834034b98b800e64a05377ff4
# noinspection RegExpRedundantEscape
IMAGE_URL_REGEX = re.compile(r'(?:\|\|)?<?(https?:\/\/(?:[a-z0-9-]+\.)+[a-z]{2,6}(?:\/[^/#?]+)+\.(?:' + '|'.join(
    VALID_IMAGE_EXTENSIONS
) + r')(?:\?[^#]+)?(?:#[^#]+)?)>?(?:\|\|)?', re.IGNORECASE)
TENOR_VIEW_REGEX = re.compile(r'https://tenor.com/view/[^/]+-([0-9]+)')


def fake_max_promotion_snowflake():
    return discord.utils.time_snowflake(
        datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(seconds=MAX_PROMOTION_SECONDS)
    )


def fake_max_age_snowflake():
    return discord.utils.time_snowflake(
        datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(seconds=MAX_AGE_SECONDS)
    )


def star_count_emoji(count: int) -> str:
    index = max(0, min((count - 3) // 6, 3))
    return ['⭐', '🌟', '💫', '✨'][index]


def star_count_color(count: int) -> discord.Color:
    gradient = [(187, 127, 31), (191, 130, 33), (196, 134, 35), (203, 139, 37), (209, 145, 39),
                (216, 150, 41), (223, 155, 42), (229, 159, 43), (234, 163, 43), (238, 166, 43),
                (242, 169, 43), (244, 171, 43), (246, 174, 45), (248, 177, 50), (249, 180, 55),
                (250, 184, 62), (251, 188, 69), (252, 192, 77), (253, 196, 84), (253, 199, 89),
                (254, 202, 94), (254, 204, 97), (255, 206, 99), (255, 207, 100), (255, 208, 100)]
    index = min(len(gradient) - 1, count // 2)
    return discord.Color.from_rgb(*gradient[index])


class StarredMessage:
    def __init__(self, stars=0, message=None):
        self.stars = stars
        self.message: Optional[discord.Message] = message

    def __int__(self):
        return self.stars

    def increment(self):
        self.stars += 1

    def decrement(self):
        self.stars = max(self.stars - 1, 0)

    def __repr__(self):
        return f"<StarredMessage stars={self.stars} message={self.message}>"


class SetupConfirm(discord.ui.View):
    def __init__(self):
        super().__init__()
        self.selected: Optional[discord.TextChannel] = None
        self.next_interaction: discord.Interaction = None

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Change channel"
    )
    async def change(self, interaction: discord.Interaction, channel_select: discord.ui.ChannelSelect):
        self.selected = channel_select.values[0]
        await interaction.response.defer()

    @discord.ui.button(label='Confirm', style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.selected = self.selected or interaction.channel
        self.next_interaction = interaction
        self.stop()

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.grey)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.selected = None
        self.next_interaction = interaction
        self.stop()


class EditConfirm(discord.ui.View):
    def __init__(self):
        super().__init__()
        self.action: str = "Timeout"
        self.next_interaction: discord.Interaction = None

    @discord.ui.button(label='Change Channel', style=discord.ButtonStyle.blurple)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.action = 'change'
        self.next_interaction = interaction
        self.stop()

    @discord.ui.button(label='Stop the starboard', style=discord.ButtonStyle.red)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.action = 'stop'
        self.next_interaction = interaction
        self.stop()

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.grey)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.action = 'cancel'
        self.next_interaction = interaction
        self.stop()


class StarboardCog(commands.Cog):
    def __init__(
            self,
            bot: 'Isabel',
            starboards: Dict[discord.Guild, discord.TextChannel],
            requirements: Dict[discord.Guild, int],
            starred_messages: Dict[Tuple[int, int], StarredMessage]
    ):
        self.bot = bot
        self.starboards: Dict[discord.Guild, discord.TextChannel] = starboards
        self.current_requirements: Dict[discord.Guild, int] = requirements
        self.star_cache: Dict[Tuple[int, int], StarredMessage] = starred_messages
        self.known_dirty_messages: Set[Tuple[int, int]] = set()  # channel_id, message_id
        self.promoted_messages: List[int] = []
        self.session = aiohttp.ClientSession()
        self.session.headers.update({'User-Agent': helper.use().get_user_agent(bot)})
        self.tenor_cache: OrderedDict[str, str] = OrderedDict()
        self.promotion_lock = asyncio.Lock()

        self.hourly.start()

    async def cog_unload(self) -> None:
        self.hourly.cancel()
        await self.session.close()

    @tasks.loop(hours=1, reconnect=True)
    async def hourly(self):
        # noinspection PyProtectedMember
        while self.bot.database is None or self.bot.database._running is False:
            await asyncio.sleep(1)
        self.bot.logger.info("Running hourly starboard maintenance")
        # even though we have a while db is None loop in extension setup
        with contextlib.suppress(asyncio.CancelledError):
            # lower requirements
            for guild in self.current_requirements:
                self.current_requirements[guild] = max(
                    math.floor(self.current_requirements[guild] * REQUIREMENTS_DOWN_MULTIPLIER),
                    MIN_STARS
                )

            # delete old stars
            async with self.bot.database.cursor() as cursor:
                await cursor.execute("DELETE FROM starboard_reference WHERE original_message_id < ?",
                                     (fake_max_age_snowflake(),))
                await cursor.execute("DELETE FROM star_givers WHERE message_id < ?", (fake_max_age_snowflake(),))
            for channel_id, message_id in list(self.star_cache.keys()):
                if message_id < fake_max_age_snowflake():
                    del self.star_cache[(channel_id, message_id)]
            for channel_id, message_id in list(self.known_dirty_messages):
                if message_id < fake_max_age_snowflake():
                    self.known_dirty_messages.remove((channel_id, message_id))
            await self.bot.database.commit()

    async def make_starboard_message_kwargs(self, message: discord.Message, stars: int) -> Dict:
        """
        Makes the kwargs for a starboard message to be used in `discord.TextChannel.send` or `discord.Message.edit`
        """
        # TODO: what if original message has embeds?

        rv_content = f"{star_count_emoji(stars)} **{stars}** | {message.jump_url}"
        rv_color = star_count_color(stars)

        if any([
            not message.channel.permissions_for(message.guild.default_role).view_channel,
            message.channel.is_nsfw() and self.starboards[message.guild].is_nsfw() is False
        ]):
            # keep track of stars but do not expose the message content in any way
            return {'content': rv_content}

        valid_extensions = tuple(f'.{ext}' for ext in VALID_IMAGE_EXTENSIONS)
        embed = discord.Embed(
            description=message.content,
            color=rv_color
        )
        embed.set_author(name=message.author.display_name, icon_url=message.author.avatar.url)
        all_embeds = [embed]

        # add all valid image attachments that are not spoilers
        valid_for_image_attachments: List[str] = [
            attachment.url
            for attachment in message.attachments
            if attachment.filename.lower().endswith(valid_extensions) and not attachment.is_spoiler()
        ]
        # add all valid image URLs that are not spoilers (whole match and group 1 are the same)
        # group 1 is the URL without the <> or || so if group 1 is different from the whole match, the URL is a spoiler
        valid_for_image_attachments.extend(
            match[1]
            for match in IMAGE_URL_REGEX.finditer(message.content)
            if match[0] == match[1]
        )
        # finally add all valid stickers (there should only be one but just in case)
        valid_for_image_attachments.extend(sticker.url for sticker in message.stickers)

        if 'tenor_key' in self.bot.config:
            for tenor in TENOR_VIEW_REGEX.finditer(message.content):
                tenor_id = tenor[1]
                if tenor_id in self.tenor_cache:
                    valid_for_image_attachments.append(self.tenor_cache[tenor_id])
                    continue
                async with self.session.get(
                        "https://tenor.googleapis.com/v2/posts",
                        params={
                            'ids': tenor_id,
                            'key': self.bot.config['tenor_key'],
                            'media_filter': 'gif,tinygif'
                        }
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data['results']:
                            gif_size = data['results'][0]['media_formats']['gif']['size']
                            _format = 'gif' if gif_size < 1000000 else 'tinygif'  # 1MB?
                            valid_for_image_attachments.append(data['results'][0]['media_formats'][_format]['url'])
                            self.tenor_cache[tenor_id] = data['results'][0]['media_formats'][_format]['url']
                            if len(self.tenor_cache) > 100:
                                self.tenor_cache.popitem(last=False)

        if valid_for_image_attachments:
            embed.set_image(url=valid_for_image_attachments[0])
            all_embeds.extend(
                discord.Embed(color=rv_color).set_image(url=attachment)
                for attachment in valid_for_image_attachments[1:]
            )
        embed.add_field(name="Original", value=f"[Jump to message]({message.jump_url})", inline=False)
        if message.reference:
            res = message.reference.resolved
            name = res.author.display_name if isinstance(res, discord.Message) else 'Jump to message'
            embed.add_field(
                name="Replying to",
                value=f"[{name}]({message.reference.jump_url})",
                inline=False
            )

        if message.attachments:
            for attachment in message.attachments:
                if len(embed.fields) < 25 and attachment.url not in valid_for_image_attachments:
                    name = attachment.filename or 'Unknown file'  # should never happen but just in case
                    embed.add_field(
                        name='Open attachment',
                        value=f"[{name}]({attachment.url})",
                        inline=False
                    )

        all_embeds = all_embeds[:10]  # max 10 embeds
        all_embeds[-1].set_footer(text=f"Next at {self.current_requirements[message.guild]}{STAR_EMOJI}")
        all_embeds[-1].timestamp = message.created_at

        return {
            'content': rv_content,
            'embeds': all_embeds,
            'allowed_mentions': discord.AllowedMentions.none()
        }

    def swap_message_in_cache(self, new_message: discord.Message):
        if (new_message.channel.id, new_message.id) in self.known_dirty_messages:
            self.known_dirty_messages.remove((new_message.channel.id, new_message.id))
        if (new_message.channel.id, new_message.id) in self.star_cache:
            current_stars = self.star_cache.get((new_message.channel.id, new_message.id))
            current_stars.message = new_message

    async def check_message_in_starboard(self, channel_id, message_id) -> bool:
        async with self.bot.database.cursor() as cursor:
            query = """
            SELECT 1 
            FROM starboard_reference 
            WHERE original_channel_id = ? AND original_message_id = ?
            """
            await cursor.execute(query, (channel_id, message_id))
            return bool(await cursor.fetchone())

    async def get_message(self, channel_id, message_id, get_clean=False, use_api=True) -> Optional[discord.Message]:
        """
        Gets a message from the cog's cache, the client's cache, or from the API
        :param channel_id: the channel ID where the message was sent
        :param message_id: the message ID
        :param get_clean: whether to only get messages with known up-to-date content
        :param use_api: whether to use the API to get the message if it's not in the cache
        :return: the message, or None if it doesn't exist
        """
        if not get_clean:
            for cached_channel_id, cached_message_id in self.star_cache:
                cached = self.star_cache[(cached_channel_id, cached_message_id)]
                if cached.message and cached.message.id == message_id and cached.message.channel.id == channel_id:
                    return cached.message
        for cached in self.bot.cached_messages:
            if cached.id == message_id and cached.channel.id == channel_id:
                self.swap_message_in_cache(cached)
                return cached
        if not use_api:
            return None
        channel = self.bot.get_channel(channel_id)
        if channel is not None:
            msg = await channel.fetch_message(message_id)
            if msg is not None:
                self.swap_message_in_cache(msg)
            return msg

    async def get_clean_message(self, channel_id, message_id) -> Optional[discord.Message]:
        msg = await self.get_message(channel_id, message_id)
        if (msg.channel.id, msg.id) in self.known_dirty_messages:
            return await self.get_message(channel_id, message_id, get_clean=True)
        return msg

    async def check_promotion(self, message: discord.Message):
        self.bot.logger.debug(f"Checking promotion for message {message.id} in channel {message.channel.id}")
        # we arrive here only if the message is not in starboard yet
        stars = self.star_cache[(message.channel.id, message.id)].stars
        current_requirements = self.current_requirements.setdefault(message.guild, MIN_STARS)
        if stars >= current_requirements:
            self.current_requirements[message.guild] = math.ceil(current_requirements * REQUIREMENTS_UP_MULTIPLIER)
            await self.promote(await self.get_clean_message(message.channel.id, message.id), stars)

    async def promote(self, message: discord.Message, stars: int):
        self.bot.logger.debug(f"Promoting message {message.id} in channel {message.channel.id}")
        if message.id < fake_max_promotion_snowflake():
            return  # ignore old messages
        starred = await self.starboards[message.guild].send(**await self.make_starboard_message_kwargs(message, stars))
        await starred.add_reaction(STAR_EMOJI)
        async with self.bot.database.cursor() as cursor:
            query = """
            INSERT INTO starboard_reference (starboard_message_id, starboard_channel_id, original_message_id, original_channel_id)
            VALUES (?, ?, ?, ?)
            """
            await cursor.execute(query, (starred.id, starred.channel.id, message.id, message.channel.id))
        await self.bot.database.commit()

    async def demote(self, channel_id, message_id):
        async with self.bot.database.cursor() as cursor:
            select_query = """
            SELECT starboard_message_id, starboard_channel_id
            FROM starboard_reference
            WHERE original_message_id = ? AND original_channel_id = ?
            """
            await cursor.execute(select_query, (message_id, channel_id))
            row = await cursor.fetchone()
            if row:
                # get the starboard message ID and channel ID to delete the message from discord
                starboard_message_id, starboard_channel_id = row
                starboard_channel = self.bot.get_channel(starboard_channel_id)
                if starboard_channel is not None:
                    starboard_message = await starboard_channel.fetch_message(starboard_message_id)
                    if starboard_message is not None:
                        await starboard_message.delete()
                delete_query = """
                DELETE FROM starboard_reference
                WHERE original_message_id = ? AND original_channel_id = ?
                """
                await cursor.execute(delete_query, (message_id, channel_id))
            else:
                # maybe the starboard message was deleted manually and we just need to delete the reference from db
                query = """
                DELETE FROM starboard_reference 
                WHERE starboard_message_id = ? AND starboard_channel_id = ?
                """
                await cursor.execute(query, (message_id, channel_id))
                # TODO: maybe alert staff to delete the original message from the original channel
                # or maybe delete it automatically
                # or maybe implement a blacklist

    async def star_amount_changed(self, message: discord.Message, increased: Optional[bool] = None):
        self.bot.logger.debug(
            f"Star amount changed (increased={increased}) for message {message.id} in channel {message.channel.id}"
        )
        star_cache = self.star_cache.setdefault((message.channel.id, message.id), StarredMessage(message=message))
        if increased is not None:
            if increased:
                star_cache.increment()
            else:
                star_cache.decrement()

        async with self.bot.database.cursor() as cursor:
            async with self.promotion_lock:
                query = """
                SELECT starboard_message_id, starboard_channel_id
                FROM starboard_reference
                WHERE original_message_id = ? AND original_channel_id = ?
                """
                await cursor.execute(query, (message.id, message.channel.id))
                starboard_message = await cursor.fetchone()
                if starboard_message is not None:
                    partial_message = discord.PartialMessage(channel=self.bot.get_channel(starboard_message[1]),
                                                             id=starboard_message[0])
                    kwargs = await self.make_starboard_message_kwargs(
                        message,
                        self.star_cache[(message.channel.id, message.id)].stars
                    )
                    await partial_message.edit(**kwargs)
                else:
                    await self.check_promotion(message)

    async def star(self, giver: discord.Member, message: discord.Message):
        self.bot.logger.debug(f"Starred message {message.id} in channel {message.channel.id}")
        if message.author == giver:
            return  # can't star own message
        if giver.bot:
            return  # ignore bots
        if message.id < fake_max_age_snowflake():
            return  # ignore old messages
        if giver.guild not in self.starboards:
            return  # ignore messages from guilds without starboard
        stars_increased = True
        try:
            async with self.bot.database.cursor() as cursor:
                await cursor.execute(
                    "INSERT INTO star_givers (channel_id, message_id, giver_id) VALUES (?, ?, ?)",
                    (message.channel.id, message.id, giver.id)
                )
        except aiosqlite.IntegrityError:
            stars_increased = False
        if stars_increased:
            await self.star_amount_changed(message, True)

    async def unstar(self, giver: discord.Member, message: discord.Message):
        self.bot.logger.debug(f"Unstarred message {message.id} in channel {message.channel.id}")
        if message.id < fake_max_age_snowflake():
            return  # ignore old messages
        async with self.bot.database.cursor() as cursor:
            await cursor.execute(
                "DELETE FROM star_givers WHERE channel_id = ? AND message_id = ? AND giver_id = ?",
                (message.channel.id, message.id, giver.id)
            )
            if cursor.rowcount == 1:
                await self.star_amount_changed(message, False)

    async def choose_channel(
            self,
            interaction: discord.Interaction,
            content: str
    ) -> Optional[Tuple[discord.TextChannel, discord.Interaction]]:
        confirm = SetupConfirm()
        await interaction.response.send_message(
            content=content,
            view=confirm,
            ephemeral=True
        )
        if await confirm.wait():
            await interaction.delete_original_response()
            return
        if confirm.selected:
            await interaction.delete_original_response()
            return self.bot.get_channel(confirm.selected.id), confirm.next_interaction
        else:
            await interaction.delete_original_response()
            await confirm.next_interaction.response.send_message("Cancelled", ephemeral=True, delete_after=15)

    async def edit_starboard(self, interaction: discord.Interaction):
        confirm = EditConfirm()
        await interaction.response.send_message(
            content=f"""
# __Starboard already running in {self.starboards[interaction.guild].mention}.__
You can change starboard channel or stop the starboard entirely.
""",
            view=confirm,
            ephemeral=True
        )
        if await confirm.wait():
            await interaction.delete_original_response()
            return
        if confirm.action == 'cancel':
            await interaction.delete_original_response()
            await confirm.next_interaction.response.send_message("Cancelled", ephemeral=True, delete_after=15)
        elif confirm.action == 'stop':
            await interaction.delete_original_response()
            await self.stop_starboard(confirm.next_interaction)
        elif confirm.action == 'change':
            await interaction.delete_original_response()
            await self.change_channel(confirm.next_interaction)

    async def start_starboard(self, interaction: discord.Interaction):
        choice = await self.choose_channel(interaction, f"Want to setup starboard in {interaction.channel.mention}?")
        if choice is not None:
            channel, next_interaction = choice
            self.starboards[interaction.guild] = channel
            async with self.bot.database.cursor() as cursor:
                await cursor.execute("INSERT OR IGNORE INTO starboard_channels (channel_id) VALUES (?)", (channel.id,))
            await next_interaction.response.send_message("Starboard has started")

    async def stop_starboard(self, interaction: discord.Interaction):
        assert interaction.guild in self.starboards
        channel = self.starboards[interaction.guild]
        del self.starboards[interaction.guild]
        async with self.bot.database.cursor() as cursor:
            await cursor.execute("DELETE FROM starboard_channels WHERE channel_id = ?", (channel.id,))
        await interaction.response.send_message(content="Starboard stopped", ephemeral=True)

    async def change_channel(self, interaction: discord.Interaction):
        choice = await self.choose_channel(
            interaction,
            f"What channel to change to? Choosing nothing will change the channel to {interaction.channel.mention}"
        )
        if choice is not None:
            next_channel, next_interaction = choice
            current_channel = self.starboards[interaction.guild]
            self.starboards[interaction.guild] = next_channel
            async with self.bot.database.cursor() as cursor:
                await cursor.execute(
                    "UPDATE starboard_channels SET channel_id = ? WHERE channel_id = ?",
                    (next_channel.id, current_channel.id)
                )
            await next_interaction.response.send_message(
                content=f"Starboard channel changed to {next_channel.mention}",
                ephemeral=True
            )

    async def on_star_reaction(self, channel_id, message_id, giver: Union[discord.Member, discord.Object],
                               increment: bool):
        if message_id < fake_max_age_snowflake():
            return  # ignore old messages
        for channel in self.starboards.values():
            if channel.id == channel_id:
                # get the original message instead
                async with self.bot.database.cursor() as cursor:
                    query = """
                    SELECT original_channel_id, original_message_id 
                    FROM starboard_reference 
                    WHERE starboard_channel_id = ? AND starboard_message_id = ?
                    """
                    await cursor.execute(query, (channel_id, message_id))
                    with contextlib.suppress(TypeError):
                        original_channel_id, original_message_id = await cursor.fetchone()
                        await self.on_star_reaction(original_channel_id, original_message_id, giver, increment)
                return

        func = self.star if increment else self.unstar
        msg = await self.get_message(channel_id, message_id)
        if msg is not None:
            await func(giver, msg)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        self.bot.logger.debug(f"Reaction added: {payload}")
        if payload.emoji.name != STAR_EMOJI or payload.member is None:
            return
        await self.on_star_reaction(payload.channel_id, payload.message_id, payload.member, True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        self.bot.logger.debug(f"Reaction removed: {payload}")
        # apparently member is always None here:
        # https://discordpy.readthedocs.io/en/stable/api.html#discord.RawReactionActionEvent.member
        # reaction_add eventually does member.bot but reaction_remove doesn't care so we can discord.Object it for db
        if payload.emoji.name != STAR_EMOJI:
            return
        await self.on_star_reaction(payload.channel_id, payload.message_id, discord.Object(id=payload.user_id), False)

    @commands.Cog.listener()
    async def on_raw_reaction_clear(self, payload: discord.RawReactionClearEvent):
        await self.demote(payload.channel_id, payload.message_id)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        await self.demote(payload.channel_id, payload.message_id)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent):
        for message_id in payload.message_ids:
            await self.demote(payload.channel_id, message_id)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        if not payload.cached_message:
            self.known_dirty_messages.add((payload.channel_id, payload.message_id))
            # check to see if the message is in the starboard and the content needs to be updated
            if await self.check_message_in_starboard(payload.channel_id, payload.message_id):
                await self.star_amount_changed(await self.get_clean_message(payload.channel_id, payload.message_id))
        # nothing else is done in this event because the cached message is the before variant
        # see on_message_edit where the after variant is used

    @commands.Cog.listener()
    async def on_message_edit(self, _, after: discord.Message):
        self.swap_message_in_cache(after)
        # same as on_raw_message_edit, but with the after variant
        if await self.check_message_in_starboard(after.channel.id, after.id):
            await self.star_amount_changed(after)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        if channel.guild in self.starboards:
            del self.starboards[channel.guild]
            async with self.bot.database.cursor() as cursor:
                await cursor.execute("DELETE FROM starboard_channels WHERE channel_id = ?", (channel.id,))
                await cursor.execute("DELETE FROM starboard_reference WHERE starboard_channel_id = ?", (channel.id,))
            await self.bot.database.commit()

    @app_commands.command(description="Will setup starboard on this server")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def starboard(self, interaction: discord.Interaction):
        if interaction.guild in self.starboards:
            await self.edit_starboard(interaction)
        else:
            await self.start_starboard(interaction)


async def setup(bot: 'Isabel'):
    while not bot.database:
        await asyncio.sleep(0)
    starboards: Dict[discord.Guild, discord.TextChannel] = {}
    requirements: Dict[discord.Guild, int] = {}
    star_cache: Dict[Tuple[int, int], StarredMessage] = {}
    async with bot.database.cursor() as cursor:
        await cursor.execute("""
        CREATE TABLE IF NOT EXISTS starboard_reference (
            starboard_message_id INTEGER,
            starboard_channel_id INTEGER,
            original_message_id INTEGER,
            original_channel_id INTEGER
        )
        """)
        await cursor.execute("""
        CREATE TABLE IF NOT EXISTS starboard_channels (
            channel_id INTEGER UNIQUE
        )
        """)
        await cursor.execute("""
        CREATE TABLE IF NOT EXISTS star_givers (
            channel_id INTEGER,
            message_id INTEGER,
            giver_id INTEGER,
            UNIQUE (channel_id, message_id, giver_id)
        )
        """)
        # get starboard channels
        await cursor.execute("SELECT * FROM starboard_channels")
        rows = await cursor.fetchall()
        for row in rows:
            channel = bot.get_channel(row[0])
            starboards[channel.guild] = channel
        # calculate requirements for each starboard by simulation
        # requirements should be MIN_STARS * REQUIREMENTS_UP_MULTIPLIER ^ number of starred messages * REQUIREMENTS_DOWN_MULTIPLIER ^ (hours since MAX_AGE_SECONDS)
        fake_message_id = fake_max_age_snowflake()
        hours_in_max_age_seconds = MAX_AGE_SECONDS / 3600
        query = """
        SELECT starboard_channel_id, COUNT(*) 
        FROM starboard_reference 
        WHERE original_message_id > ?
        GROUP BY starboard_channel_id
        """
        await cursor.execute(query, (fake_message_id,))
        rows = await cursor.fetchall()
        for row in rows:
            channel = bot.get_channel(row[0])
            current_requirement = math.ceil(MIN_STARS * REQUIREMENTS_UP_MULTIPLIER ** row[1])
            current_requirement *= math.floor(REQUIREMENTS_DOWN_MULTIPLIER ** hours_in_max_age_seconds)
            current_requirement = max(current_requirement, MIN_STARS)
            requirements[channel.guild] = current_requirement
        # count star givers for each message
        await cursor.execute("SELECT channel_id, message_id, COUNT(*) FROM star_givers GROUP BY channel_id, message_id")
        rows = await cursor.fetchall()
        for row in rows:
            star_cache[(row[0], row[1])] = StarredMessage(row[2])

    await bot.add_cog(StarboardCog(bot, starboards, requirements, star_cache))

# todo:
# [x] keep track of star givers in database (so starboard and original message stay in-sync)
# [x] can't star own messages / can't star messages twice (can't give more than 1 star)
# [x] star & unstar (reaction_add, reaction_remove, message_delete, bulk_message_delete, raw_reaction_clear)
# [x] keep track of nsfw (not necessary for osuplace)
# [x] manage spoiler content `re.compile(r'\|\|(.+?)\|\|')`
# [x] valid image formats `'png', 'jpeg', 'jpg', 'gif', 'webp'`
# [x] display all images via multiple embeds
# [x] display other attachments via fields
# [x] show jump to original message and "replying to"
# [x] keep a message cache
# [x] starboard sanity (see if deleted via `on_channel_removed` or whatever the event is)
# [] add star reaction to messages that have a lot of non-star reactions (just so people know they can star it)
