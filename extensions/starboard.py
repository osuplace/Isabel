import asyncio
import contextlib
import datetime
import math
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple, Set

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands, tasks

if TYPE_CHECKING:
    from main import Isabel

MAX_PROMOTION_SECONDS = 24 * 60 * 60
MAX_AGE_SECONDS = 7 * 24 * 60 * 60
STARBOARD_INTERVAL_SECONDS = 60 * 60
MIN_STARS = 1  # TODO: raise to 4
STAR_EMOJI = '⭐'


def fake_max_promotion_snowflake():
    return discord.utils.time_snowflake(
        datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(seconds=MAX_PROMOTION_SECONDS)
    )


def fake_max_age_snowflake():
    return discord.utils.time_snowflake(
        datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(seconds=MAX_AGE_SECONDS)
    )


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
            starred_messages: Dict[int, StarredMessage]
    ):
        self.bot = bot
        self.starboards: Dict[discord.Guild, discord.TextChannel] = starboards
        self.current_requirements: Dict[discord.Guild, int] = requirements
        self.star_cache: Dict[int, StarredMessage] = starred_messages
        self.known_dirty_messages: Set[Tuple[int, int]] = set()  # channel_id, message_id
        self.promoted_messages: List[int] = []

        self.hourly.start()

    def cog_unload(self) -> None:
        self.hourly.cancel()

    @tasks.loop(hours=1)
    async def hourly(self):
        self.bot.logger.info("Running hourly starboard maintenance")
        with contextlib.suppress(asyncio.CancelledError):
            # lower requirements
            for guild in self.current_requirements:
                self.current_requirements[guild] = max(math.floor(self.current_requirements[guild] * 19 / 20),
                                                       MIN_STARS)

            # delete old stars
            async with self.bot.database.cursor() as cursor:
                await cursor.execute("DELETE FROM starboard_reference WHERE original_message_id < ?",
                                     (fake_max_age_snowflake(),))
                await cursor.execute("DELETE FROM star_givers WHERE message_id < ?", (fake_max_age_snowflake(),))
            for message_id in self.star_cache:
                if message_id < fake_max_age_snowflake():
                    del self.star_cache[message_id]
            for channel_id, message_id in self.known_dirty_messages:
                if message_id < fake_max_age_snowflake():
                    self.known_dirty_messages.remove((channel_id, message_id))
            await self.bot.database.commit()

    def swap_message_in_cache(self, new_message: discord.Message):
        if (new_message.channel.id, new_message.id) in self.known_dirty_messages:
            self.known_dirty_messages.remove((new_message.channel.id, new_message.id))
        if new_message.id in self.star_cache:
            current_stars = self.star_cache.get(new_message.id)
            del self.star_cache[new_message.id]
            self.star_cache[new_message.id] = current_stars

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
            for cached_id in self.star_cache:
                cached = self.star_cache[cached_id]
                if cached.message and cached_id == message_id and cached.message.channel.id == channel_id:
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

    async def make_starboard_message_kwargs(self, message: discord.Message, stars: int) -> Dict:
        """
        Makes the kwargs for a starboard message to be used in `discord.TextChannel.send` or `discord.Message.edit`
        """
        # TODO: add embed to starboard message
        return {'content': f"{stars} - {message.jump_url}"}

    async def get_clean_message(self, channel_id, message_id) -> Optional[discord.Message]:
        msg = await self.get_message(channel_id, message_id)
        if (msg.channel.id, msg.id) in self.known_dirty_messages:
            return await self.get_message(channel_id, message_id, get_clean=True)
        return msg

    async def check_promotion(self, message: discord.Message):
        # we arrive here only if the message is not in starboard yet
        stars = self.star_cache[message.id].stars
        current_requirements = self.current_requirements.setdefault(message.guild, MIN_STARS)
        if stars >= current_requirements:
            await self.promote(await self.get_clean_message(message.channel.id, message.id), stars)
            self.current_requirements[message.guild] = math.ceil(current_requirements * 10 / 9)

    async def promote(self, message: discord.Message, stars: int):
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
            query = """
            DELETE FROM starboard_reference 
            WHERE original_message_id = ? AND original_channel_id = ?
            RETURNING starboard_message_id, starboard_channel_id
            """
            await cursor.execute(query, (message_id, channel_id))
            if cursor.rowcount != 0:
                # get the starboard message ID and channel ID to delete the message from discord
                starboard_message_id, starboard_channel_id = await cursor.fetchone()
                starboard_channel = self.bot.get_channel(starboard_channel_id)
                if starboard_channel is not None:
                    starboard_message = await starboard_channel.fetch_message(starboard_message_id)
                    if starboard_message is not None:
                        await starboard_message.delete()
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

    async def star_amount_changed(self, message: discord.Message, increased: bool):
        star_cache = self.star_cache.setdefault(message.id, StarredMessage(message=message))
        if increased:
            star_cache.increment()
        else:
            star_cache.decrement()

        async with self.bot.database.cursor() as cursor:
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
                kwargs = await self.make_starboard_message_kwargs(message, self.star_cache[message.id].stars)
                await partial_message.edit(**kwargs)
            else:
                await self.check_promotion(message)

    async def star(self, giver: discord.Member, message: discord.Message):
        if message.author == giver:
            return  # can't star own message
        if giver.bot:
            return  # ignore bots
        if message.id < fake_max_age_snowflake():
            return  # ignore old messages
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
            content="# __Starboard already running.__\nYou can change starboard channel or stop the starboard entirely.",
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

    async def on_star_reaction(self, channel_id, message_id, giver: discord.Member, increment: bool):
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
        if payload.emoji.name != STAR_EMOJI or payload.member is None:
            return
        await self.on_star_reaction(payload.channel_id, payload.message_id, payload.member, True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.emoji.name != STAR_EMOJI or payload.member is None:
            return
        await self.on_star_reaction(payload.channel_id, payload.message_id, payload.member, False)

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
            # TODO: add a check to see if the message is in the starboard and the content needs to be updated
        # nothing else is done in this event because the cached message is the before variant
        # see on_message_edit where the after variant is used

    @commands.Cog.listener()
    async def on_message_edit(self, _, after: discord.Message):
        self.swap_message_in_cache(after)
        # TODO: same as above, check if the message is in the starboard and the content needs to be updated

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
    starboards = {}
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
        await cursor.execute("SELECT * FROM starboard_channels")
        rows = await cursor.fetchall()
        for row in rows:
            channel = bot.get_channel(row[0])
            starboards[channel.guild] = channel
        # TODO: calculate requirements for each starboard by simulation
        # TODO: count star givers for each message
    await bot.add_cog(StarboardCog(bot, starboards, {}, {}))

# todo:
# [x] keep track of star givers in database (so starboard and original message stay in-sync)
# [x] can't star own messages / can't star messages twice (can't give more than 1 star)
# [x] star & unstar (reaction_add, reaction_remove, message_delete, bulk_message_delete, raw_reaction_clear)
# [] keep track of nsfw (not necessary for osuplace)
# [] manage spoiler content `re.compile(r'\|\|(.+?)\|\|')`
# [] valid image formats `'png', 'jpeg', 'jpg', 'gif', 'webp'`
# [] display all images via multiple embeds
# [] display other attachments via fields
# [] show jump to original message and "replying to"
# [x] keep a message cache
# [] starboard sanity (see if deleted via `on_channel_removed` or whatever the event is)
