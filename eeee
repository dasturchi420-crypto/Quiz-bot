import os
import random
import logging
import asyncio
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    CallbackQuery,
    BotCommand
)
from aiogram.filters import CommandStart, Command

# Bot tokeningiz
TOKEN = "8682338480:AAGvBLFyNA6mvprm97DUqO9L_OeEWbnDwuU"

# Reaksiyalar bosilganda xabar boradigan Admin Telegram ID si
ADMIN_ID = 7044084668

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Bot har xil qilib tanlashi uchun emojilar to'plami
EMOJI_POOL = ["👍", "❤️", "🔥", "🤩", "👏", "💯", "🎉", "⚡️", "😍", "🤝", "🚀", "💡"]

# Reaksiya tugmalarini yaratuvchi yordamchi funksiya
def generate_reaction_keyboard():
    chosen_emojis = random.sample(EMOJI_POOL, k=random.randint(4, 5))
    row_buttons = [
        InlineKeyboardButton(text=f"{emoji} 0", callback_data=f"like_{emoji}")
        for emoji in chosen_emojis
    ]
    return InlineKeyboardMarkup(inline_keyboard=[row_buttons])

# Bosh menyu inline tugmalari
def get_start_keyboard(bot_username: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Kanal yoki Guruhga qo'shish", 
                    url=f"https://t.me/{bot_username}?startchannel=true&admin=post_messages+edit_messages"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📖 Qo'llanma va Yo'riqnoma", 
                    callback_data="help_instruction"
                )
            ]
        ]
    )

# /start buyrug'i uchun handler
@dp.message(CommandStart())
async def start_cmd(message: Message):
    bot_info = await bot.get_me()
    text = (
        "<b>Assalomu alaykum! Men kanal va guruhlarga avtomatik layk hamda reaksiyalar qo'shuvchi botman.</b>\n\n"
        "✨ <b>Imkoniyatlarim:</b>\n"
        "1️⃣ Kanalingizga yangi post joylanganda avtomatik reaksiya tugmalarini qo'shaman.\n"
        "2️⃣ Menga ixtiyoriy <b>matn, rasm yoki video</b> yuborsangiz, ularga ham reaksiya tugmalarini qo'shib beraman. Keyin uni kanalingizga uzatishingiz (forward) mumkin!\n"
        "3️⃣ Kanalga bot orqali post yubormoqchi bo'lsangiz, admin @TATU_DI_06 ga murojaat qiling.\n\n"
        "👇 Botni kanalingizga ulashtirish uchun quyidagi tugmani bosing:"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=get_start_keyboard(bot_info.username))

# Qo'llanma tugmasi bosilganda
@dp.callback_query(F.data == "help_instruction")
async def show_help(callback: CallbackQuery):
    instruction_text = (
        "📋 <b>Botni ishlatish bo'yicha yo'riqnoma:</b>\n\n"
        "1️⃣ Botni kanal yoki guruhingizga <b>Admin</b> qilib qo'shing va post joylash huquqini bering.\n"
        "2️⃣ Kanalingizga to'g'ridan-to'g'ri post joylasangiz, bot avtomatik reaksiya biriktiradi.\n"
        "3️⃣ Yoki botning o'ziga rasm, video, fayl yoki matn yuboring — bot uning ostiga reaksiyalar qo'shib beradi. Siz uni guruh yoki kanalingizga forward qilishingiz mumkin!\n"
        "4️⃣ Kanalga bot orqali post joylash uchun admin bilan bog'laning: @TATU_DI_06"
    )
    await callback.message.answer(instruction_text, parse_mode="HTML")
    await callback.answer()

# Bot orqali faqat ADMIN uchun kanallarga post yuborish handler'i (/post buyrug'i)
@dp.message(Command("post"))
async def post_to_channel(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Bot orqali kanalga post joylash uchun admin bilan bog'laning: @TATU_DI_06")
        return

    # Buyruq matnini ajratib olamiz
    caption_or_text = message.caption if message.caption else message.text
    parts = caption_or_text.split(maxsplit=2)

    if len(parts) < 2:
        await message.answer(
            "⚠️ <b>Admin uchun format:</b>\n\n"
            "<code>/post @kanal_username Post matni</code>\n"
            "<b>Rasm/Video uchun:</b> Fayl izohiga <code>/post @kanal_username Matn</code> deb yozing.",
            parse_mode="HTML"
        )
        return

    target_channel = parts[1]
    post_text = parts[2] if len(parts) > 2 else ""
    keyboard = generate_reaction_keyboard()

    try:
        if message.photo:
            await bot.send_photo(
                chat_id=target_channel,
                photo=message.photo[-1].file_id,
                caption=post_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        elif message.video:
            await bot.send_video(
                chat_id=target_channel,
                video=message.video.file_id,
                caption=post_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        elif message.document:
            await bot.send_document(
                chat_id=target_channel,
                document=message.document.file_id,
                caption=post_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        elif post_text:
            await bot.send_message(
                chat_id=target_channel,
                text=post_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        else:
            await message.answer("❌ Yuborish uchun matn yoki media topilmadi.")
            return

        await message.answer(f"✅ Post muvaffaqiyatli <b>{target_channel}</b> kanaliga yuborildi!", parse_mode="HTML")

    except Exception as e:
        logging.error(f"Kanalga post yuborishda xatolik: {e}")
        await message.answer(
            f"❌ <b>Xatolik yuz berdi:</b> {e}\n\n"
            f"Bot <b>{target_channel}</b> kanalida administrator ekanligini va xabar joylash (Post Messages) huquqi borligini tekshiring.",
            parse_mode="HTML"
        )

# Shaxsiy chatda botga Rasm, Video yoki Matn yuborilganda tayyor post qilib berish
@dp.message(F.chat.type == "private")
async def process_user_post(message: Message):
    if message.text and message.text.startswith("/"):
        return  # Komandalarga tegmaymiz

    keyboard = generate_reaction_keyboard()
    
    # Yuborilgan xabar turiga qarab qayta jo'natamiz
    if message.photo:
        await message.answer_photo(photo=message.photo[-1].file_id, caption=message.caption or "", reply_markup=keyboard)
    elif message.video:
        await message.answer_video(video=message.video.file_id, caption=message.caption or "", reply_markup=keyboard)
    elif message.text:
        await message.answer(text=message.text, reply_markup=keyboard)
    else:
        await message.copy_to(chat_id=message.chat.id, reply_markup=keyboard)

# Kanalga post joylanganda ishlovchi handler
@dp.channel_post()
async def auto_like_channel_post(message: Message):
    keyboard = generate_reaction_keyboard()
    try:
        await bot.edit_message_reply_markup(
            chat_id=message.chat.id,
            message_id=message.message_id,
            reply_markup=keyboard
        )
    except Exception as e:
        logging.error(f"Postga layk qo'shishda xatolik: {e}")

# Reaksiya tugmalari bosilganda sanoqni oshirish va Adminga bildirishnoma yuborish
@dp.callback_query(F.data.startswith("like_"))
async def handle_like(callback: CallbackQuery):
    emoji = callback.data.split("_")[1]
    
    if not callback.message or not callback.message.reply_markup:
        await callback.answer("Xatolik yuz berdi.", show_alert=True)
        return

    reply_markup = callback.message.reply_markup
    new_inline_keyboard = []

    for row in reply_markup.inline_keyboard:
        new_row = []
        for btn in row:
            if btn.callback_data == callback.data:
                text_parts = btn.text.split(" ")
                current_count = int(text_parts[1]) if len(text_parts) > 1 and text_parts[1].isdigit() else 0
                new_text = f"{emoji} {current_count + 1}"
                new_row.append(InlineKeyboardButton(text=new_text, callback_data=btn.callback_data))
            else:
                new_row.append(btn)
        new_inline_keyboard.append(new_row)

    try:
        # 1. Post ostidagi tugmani yangilaymiz
        await callback.message.edit_reply_markup(
            reply_markup=InlineKeyboardMarkup(inline_keyboard=new_inline_keyboard)
        )
        await callback.answer(f"Siz {emoji} reaksiyasini qoldirdingiz!")

        # 2. Adminga xabar yuborish
        user = callback.from_user
        username_text = f"@{user.username}" if user.username else "Mavjud emas"
        
        notification_text = (
            "🔔 <b>Yangi reaksiya bosildi!</b>\n\n"
            f"👤 <b>Foydalanuvchi:</b> {user.full_name}\n"
            f"🆔 <b>ID:</b> <code>{user.id}</code>\n"
            f"🔗 <b>Username:</b> {username_text}\n"
            f"🎭 <b>Bosilgan reaksiya:</b> {emoji}"
        )
        
        if ADMIN_ID:
            await bot.send_message(chat_id=ADMIN_ID, text=notification_text, parse_mode="HTML")

    except Exception as e:
        logging.error(f"Tugmani yangilashda xatolik: {e}")
        await callback.answer()

# Chat pastida ko'k menudagi komandalarni sozlash (Faqat /start qoldirildi)
async def set_main_menu(bot: Bot):
    main_commands = [
        BotCommand(command="start", description="Botni ishga tushirish va yo'riqnoma")
    ]
    await bot.set_my_commands(main_commands)

# Render.com portini tinglab turuvchi veb-server
async def handle_ping(request):
    return web.Response(text="Bot is running live 24/7!")

async def main():
    await set_main_menu(bot)
    
    # Render uchun aiohttp serverini ishga tushiramiz
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    logging.info("Bot hamda Web Server muvaffaqiyatli ishga tushdi...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
