from typing import TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    from main import Isabel


async def setup(bot: 'Isabel'):
    bot.database = await aiosqlite.connect('isabel.db')


async def teardown(bot: 'Isabel'):
    db = bot.database
    bot.database = None
    await db.commit()
    await db.close()

