import asyncio
import datetime
import math
from typing import TYPE_CHECKING, Dict, List, Literal, Optional, Tuple

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


def fake_max_promotion_snowflake():
    return discord.utils.time_snowflake(
        datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(seconds=MAX_PROMOTION_SECONDS)
    )


def fake_max_age_snowflake():
    return discord.utils.time_snowflake(
        datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(seconds=MAX_AGE_SECONDS)
    )


class StarAmount:
    def __init__(self):
        self.value = 0

    def __int__(self):
        return self.value

    def increment(self):
        self.value += 1

    def decrement(self):
        self.value -= 1


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
    def __init__(self, bot: 'Isabel', starboards: Dict[discord.Guild, discord.TextChannel]):
        self.bot = bot
        self.starboards: Dict[discord.Guild, discord.TextChannel] = starboards
        self.current_requirements: Dict[discord.Guild, int] = {}
        self.hourly.start()
        self.star_cache: Dict[discord.Message, StarAmount] = {}  # message: stars
        self.known_dirty_messages: List[Tuple[int, int]] = []  # channel_id, message_id
        self.promoted_messages: List[int] = []

    @tasks.loop(hours=1)
    async def hourly(self):
        # lower requirements
        for guild in self.current_requirements:
            self.current_requirements[guild] = max(math.floor(self.current_requirements[guild] * 19 / 20), MIN_STARS)

        # delete old stars
        async with self.bot.database.cursor() as cursor:
            await cursor.execute("DELETE FROM starboard_reference WHERE original_message_id < ?",
                                 (fake_max_age_snowflake(),))
            await cursor.execute("DELETE FROM star_givers WHERE message_id < ?", (fake_max_age_snowflake(),))
        for message in self.star_cache:
            if message.id < fake_max_age_snowflake():
                del self.star_cache[message]
        await self.bot.database.commit()

    def swap_message_in_cache(self, new_message: discord.Message):
        self.known_dirty_messages.remove((new_message.channel.id, new_message.id))
        current_stars = self.star_cache.get(new_message)
        del self.star_cache[new_message]
        self.star_cache[new_message] = current_stars

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
            for cached in self.star_cache:
                if cached.id == message_id and cached.channel.id == channel_id:
                    return cached
        for cached in self.bot.cached_messages:
            if cached.id == message_id and cached.channel.id == channel_id:
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
        stars = self.star_cache[message].value
        current_requirements = self.current_requirements[message.guild]
        if stars >= current_requirements:
            await self.promote(await self.get_clean_message(message.channel.id, message.id), stars)
            self.current_requirements[message.guild] = math.ceil(current_requirements * 10 / 9)

    async def promote(self, message: discord.Message, stars: int):
        if message.id < fake_max_promotion_snowflake():
            return  # ignore old messages
        # TODO: add embed to starboard
        # TODO: add starboard reference to database

    async def demote(self, channel_id, message_id):
        # TODO: check if message is in starboard
        # TODO: delete embed from starboard
        # TODO: remove starboard reference from database
        pass

    async def star(self, giver: discord.Member, message: Optional[discord.Message]):
        if message is None:
            return  # is the case for too old messages, see on_raw_reaction_add
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
            self.star_cache.setdefault(message, StarAmount()).increment()
            await self.check_promotion(message)

    async def unstar(self, giver: discord.Member, message: Optional[discord.Message]):
        if message is None:
            return  # is the case for too old messages, see on_raw_reaction_add
        if message.id < fake_max_age_snowflake():
            return  # ignore old messages
        async with self.bot.database.cursor() as cursor:
            await cursor.execute(
                "DELETE FROM star_givers WHERE channel_id = ? AND message_id = ? AND giver_id = ?",
                (message.channel.id, message.id, giver.id)
            )
            if cursor.rowcount == 1:
                self.star_cache.setdefault(message, StarAmount()).decrement()

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

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.emoji.name != '⭐':
            return
        if payload.member is None:
            return
        # we want to use the API to get new messages, but we want to use the cache for old messages
        # the idea is that old promoted messages are already in the cache,
        # but messages too old to be promoted aren't and shouldn't be
        use_api = payload.message_id > fake_max_promotion_snowflake()
        await self.star(
            payload.member,
            await self.get_message(
                payload.channel_id,
                payload.message_id,
                use_api=use_api
            )
        )

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.emoji.name != '⭐':
            return
        if payload.member is None:
            return
        use_api = payload.message_id > fake_max_promotion_snowflake()  # see above
        await self.unstar(
            payload.member,
            await self.get_message(
                payload.channel_id,
                payload.message_id,
                use_api=use_api
            )
        )

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
            self.known_dirty_messages.append((payload.channel_id, payload.message_id))
            # TODO: add a check to see if the message is in the starboard and the content needs to be updated
        # nothing else is done in this event because the cached message is the before variant
        # see on_message_edit where the after variant is used

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before in self.star_cache:
            self.swap_message_in_cache(after)

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
            UNIQUE (original_id, channel_id, giver_id)
        )
        """)
        await cursor.execute("SELECT * FROM starboard_channels")
        rows = await cursor.fetchall()
        for row in rows:
            channel = bot.get_channel(row[0])
            starboards[channel.guild] = channel
    await bot.add_cog(StarboardCog(bot, starboards))

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
