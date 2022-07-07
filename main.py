#!/usr/bin/env python3
import argparse
import logging
from configparser import ConfigParser
from pathlib import Path

from databases import Database
from pyrogram import Client, filters, idle
from pyrogram.types import Message, InlineQuery, InlineQueryResultCachedSticker, \
    InlineKeyboardMarkup, InlineKeyboardButton, \
    CallbackQuery, InputTextMessageContent
from sqlalchemy import select, func, null
from sqlalchemy.ext.asyncio import create_async_engine

from models import metadata, stickers

DEFAULT_CACHE_TIME = 300
DEFAULT_BOT_NAME = 'StickerBookmarkBot'


def create_bot(bot: Client, db: Database, admin_list: list = None, bot_name: str = DEFAULT_BOT_NAME):
    admin_filter = ~filters.user() if admin_list is None or not any(admin_list) else filters.user(admin_list)

    @bot.on_message(filters.private & filters.command('start'))
    async def handle_start(client: Client, message: Message) -> None:
        await message.reply(
            f'欢迎使用 {bot_name} \n'
            '\n'
            '发送 **sticker** 开始使用，发送 /help 获取帮助\n'
        )

    @bot.on_message(filters.private & filters.command('help'))
    async def handle_start(client: Client, message: Message) -> None:
        await message.reply(
            f'{bot_name} 能够保存、标记、查询你的 sticker\n'
            '\n'
            '**Usage[PM Mode]**\n'
            '/add - 添加 sticker\n'
            '/rm - 移除 sticker\n'
            '/tag - 更新 tag\n'
            '/stat - 查看状态\n'
            '/force_sync - 强制同步数据库\n'
            '/help_inline - 查看 inline 模式'
        )

    @bot.on_message(filters.private & filters.command('help_inline'))
    async def handle_start(client: Client, message: Message) -> None:
        await message.reply(
            '**Usage[Inline Mode]**\n'
            '`[FILE_ID]` - 发送对应 Sticker\n'
            '`ls(\\d{0,2})` - 查看最后添加的 50 个 sticker\n'
            '`page(\\d{1,2})` - 获取对应分页的 sticker\n'
            '`uniq(\\d{1,2})` - 获取对应分页的 [FILE_ID]\n'
            '`#(.{1,16})#` - 获取 [TAG] 对应的 sticker\n'
            '\n'
            '**P.S.:** 每个分页有 50 个 sticker, ' 
            'inline 模式结果默认缓存5分钟\n'
        )

    @bot.on_message(admin_filter & filters.private & filters.sticker)
    async def handle_sticker(client: Client, message: Message) -> None:
        await message.reply(
            '**[FILE_ID]**\n'
            f'`{message.sticker.file_id}`\n'
            '**[FILE_UNIQUE_ID]**\n'
            f'`{message.sticker.file_unique_id}`',
            reply_to_message_id=message.id,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton('Add', callback_data=f'add#{str(message.id)}#'),
                    InlineKeyboardButton('Remove', callback_data=f'remove#{str(message.id)}#')
                ]
            ])
        )

    @bot.on_callback_query(admin_filter & filters.regex(r"(?P<METHOD>(\w|\d)+)#(?P<MSG_ID>\d+)#"))
    async def handle_callback(client: Client, callback: CallbackQuery) -> None:
        match = callback.matches[0]
        method, _ = match.group('METHOD'), match.group('MSG_ID')
        message = callback.message.reply_to_message
        await callback.edit_message_reply_markup(None)
        if method == 'add':
            await save_sticker(callback.from_user.id, message)
        elif method == 'remove':
            await remove_sticker(callback.from_user.id, message, message.sticker.file_unique_id)

    async def save_sticker(user_id: int, message: Message):
        try:
            await db.execute(
                query=stickers.insert(),
                values={'user_id': str(user_id), 'file_unique_id': message.sticker.file_unique_id,
                        'message_id': message.id}
            )
        except Exception:
            # ToDo: 等待 databases 更新
            # https://github.com/encode/databases/discussions/317
            await message.reply('已存在 Bot 中', reply_to_message_id=message.id)
        else:
            await message.reply('保存成功', reply_to_message_id=message.id)

    @bot.on_message(admin_filter & filters.private & filters.command('add'))
    async def handle_save(client: Client, message: Message) -> None:
        if message.reply_to_message and message.reply_to_message.sticker:
            await save_sticker(message.from_user.id, message.reply_to_message)
        else:
            await message.reply('请回复需要保存的 Sticker')

    async def remove_sticker(user_id: int, message: Message, file_unique_id: str = None) -> None:
        user_id = str(user_id)
        file_unique_id = file_unique_id or message.sticker.file_unique_id
        result = await db.execute(
            query=stickers.delete()
                .where(stickers.c.user_id == user_id, stickers.c.file_unique_id == file_unique_id)
        )
        await message.reply(f'删除了 **{result}** 个 Sticker')

    @bot.on_message(admin_filter & filters.private & filters.command('rm'))
    async def handle_remove(client: Client, message: Message) -> None:
        if len(message.command) == 1 and message.reply_to_message and message.reply_to_message.sticker:
            file_unique_id = message.reply_to_message.sticker.file_unique_id
            await remove_sticker(message.from_user.id, message, file_unique_id)
        elif len(message.command) == 2:
            file_unique_id = message.command[1]
            await remove_sticker(message.from_user.id, message, file_unique_id)
        else:
            await message.reply('请**回复**需要删除的 Sticker 或 `/rm [FILE_UNIQUE_ID]`')

    @bot.on_message(admin_filter & filters.private & filters.command('force_sync'))
    async def handle_update(client: Client, message: Message) -> None:
        await message.reply('正在强制同步数据库...')
        data = await db.fetch_all(
            query=select(stickers.c.message_id).where(stickers.c.user_id == str(message.from_user.id))
        )
        msgs = await client.get_messages(message.from_user.id, (d[0] for d in data))
        data = await db.execute(
            query=stickers.delete().where(stickers.c.message_id.in_([msg.id for msg in msgs if msg.empty])),
        )
        await message.reply(
            f'更新数据库完成，移除 {data} 个无效 Sticker',
            reply_to_message_id=message.id
        )

    @bot.on_message(admin_filter & filters.private & filters.command('tag'))
    async def handle_tag(client: Client, message: Message) -> None:
        if message.reply_to_message and message.reply_to_message.sticker:
            if len(message.command) == 1:
                tag = null()
            elif len(message.command) == 2 and len(message.command[1]) <= 16:
                tag = message.command[1]
            else:
                tag = None
            if tag is not None:
                result = await db.execute(
                    query=stickers.update().where(
                        stickers.c.user_id == message.from_user.id,
                        stickers.c.file_unique_id == message.reply_to_message.sticker.file_unique_id
                    ),
                    values={'tag': tag}
                )
                if result == 0:
                    await message.reply(
                        '标签**没有改变**或该 Sticker **不存在** Bot 中',
                        reply_to_message_id=message.reply_to_message_id
                    )
                else:
                    await message.reply(
                        '移除标签' if tag is null() else f'已添加标签 `{tag}`' ,
                        reply_to_message_id=message.reply_to_message_id
                    )
            else:
                await message.reply('命令参数不正确 或 标签长度大于 16')
        else:
            await message.reply('请以 `/tag [TAG]` 或 `/tag` 形式回复需要标记/移除标记的 Sticker')

    @bot.on_message(admin_filter & filters.private & filters.command('stat'))
    async def handle_info(client: Client, message: Message) -> None:
        data = await db.fetch_all(
            query=select(stickers.c.tag, func.count()).where(stickers.c.user_id == str(message.from_user.id))
                .group_by(stickers.c.tag)
        )
        reply_msg = '\n'.join(f'{n} - `{tag}`' for tag, n in data if tag)
        await message.reply(
            f"Total Sticker: {sum(map(lambda x: x[1], data))}\n"
            "**[Tag List]**\n"
            f"{reply_msg if any(reply_msg) else '没有tag'}",
            reply_to_message_id=message.id,
        )

    @bot.on_inline_query(filters.regex(r'(?P<FILE_ID>[A-Za-z0-9+-_/=]{65,80})'))
    async def handle_send_cache_sticker(client: Client, inline_query: InlineQuery) -> None:
        file_id = inline_query.matches[0].group('FILE_ID')
        await inline_query.answer([InlineQueryResultCachedSticker(file_id)], is_gallery=True)

    @bot.on_inline_query(admin_filter & filters.regex(r'ls(\d{0,2})'))
    async def handle_last_inline_query(client: Client, inline_query: InlineQuery) -> None:
        data = await db.fetch_all(
            query=select(stickers.c.message_id)
                .where(stickers.c.user_id == str(inline_query.from_user.id))
                .order_by(stickers.c.id.desc())
                .limit(50)
        )
        msgs = await client.get_messages(inline_query.from_user.id, (d[0] for d in data))
        await inline_query.answer(
            [InlineQueryResultCachedSticker(msg.sticker.file_id) for msg in msgs if not msg.empty],
            is_gallery=True,
            is_personal=True,
        )

    @bot.on_inline_query(admin_filter & filters.regex(r'(?P<CMD>(P|p)(age)?)(?P<PAGE>\d{1,2})$'))
    async def handle_page_inline_query(client: Client, inline_query: InlineQuery) -> None:
        cmd = inline_query.matches[0].group('CMD')
        page = int(inline_query.matches[0].group('PAGE'))
        data = await db.fetch_all(
            query=select(stickers.c.message_id).where(stickers.c.user_id == str(inline_query.from_user.id))
                .order_by(stickers.c.id.desc())
                .limit(50)
                .offset((page - 1) * 50)
        )
        msgs = await client.get_messages(inline_query.from_user.id, (d[0] for d in data))
        await inline_query.answer(
            [InlineQueryResultCachedSticker(msg.sticker.file_id) for msg in msgs if not msg.empty],
            is_personal=True,
            is_gallery=True,
            cache_time=30 if cmd in {'P', 'p'} else DEFAULT_CACHE_TIME
        )

    @bot.on_inline_query(admin_filter & filters.regex(r'(?P<CMD>(U|u)(niq)?)(?P<PAGE>\d{1,2})$'))
    async def handle_msg_inline_query(client: Client, inline_query: InlineQuery) -> None:
        cmd = inline_query.matches[0].group('CMD')
        page = int(inline_query.matches[0].group('PAGE'))
        data = await db.fetch_all(
            query=select(stickers.c.message_id)
                .where(stickers.c.user_id == str(inline_query.from_user.id))
                .order_by(stickers.c.id.desc())
                .limit(50)
                .offset((page - 1) * 50)
        )
        msgs = await client.get_messages(inline_query.from_user.id, (d[0] for d in data))
        await inline_query.answer(
            [InlineQueryResultCachedSticker(
                sticker_file_id=msg.sticker.file_id,
                input_message_content=InputTextMessageContent(f'**[FILE_UNIQUE_ID]** `{msg.sticker.file_unique_id}`')
            ) for msg in msgs if not msg.empty],
            is_personal=True,
            is_gallery=True,
            cache_time=30 if cmd in {'U', 'u'} else DEFAULT_CACHE_TIME
        )

    @bot.on_inline_query(admin_filter & filters.regex(r'#(?P<TAG>.{1,16})#$'))
    async def handle_tag_inline_query(client: Client, inline_query: InlineQuery) -> None:
        tag = inline_query.matches[0].group('TAG')
        data = await db.fetch_all(
            query=select(stickers.c.message_id)
                .where(stickers.c.user_id == str(inline_query.from_user.id), stickers.c.tag == tag)
                .order_by(stickers.c.id.desc())
                .limit(50)
        )
        msgs = await client.get_messages(inline_query.from_user.id, (d[0] for d in data))
        await inline_query.answer(
            [InlineQueryResultCachedSticker(sticker_file_id=msg.sticker.file_id) for msg in msgs if not msg.empty],
            is_personal=True,
            is_gallery=True,
            cache_time=60,
        )

    return bot


def main():
    parser = argparse.ArgumentParser(description='CaaBouBot')
    parser.add_argument('--config', dest='config', metavar='filename', default='config.ini',
                        action='store', help='Configure File, default = \'config.ini\'', required=False)
    args = parser.parse_args()
    config_file = Path(args.config)
    if not config_file.exists():
        logging.error(f'配置文件 {str(config_file)} 不存在')
        return
    config = ConfigParser()
    config.read(config_file, encoding='utf8')
    bot_name = config.get('bot', 'name', raw=True)
    api_id = config.get('bot', 'api_id')
    api_hash = config.get('bot', 'api_hash')
    bot_token = config.get('bot', 'bot_token')
    db_uri = config.get('db', 'uri')
    admin_list = [int(u) for u in config.get('user', 'admin', fallback='').split(',') if any(u)]

    db = Database(url=db_uri, force_rollback=False)
    bot = create_bot(
        Client(bot_name, api_id, api_hash, bot_token=bot_token),
        db,
        admin_list
    )

    async def async_main():
        engine = create_async_engine(db_uri)
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
        async with db, bot:
            await idle()

    bot.run(async_main())


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s [%(name)s] %(message)s')
    logging.getLogger('databases').setLevel(logging.DEBUG)
    main()
