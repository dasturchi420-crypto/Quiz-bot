import asyncio
import html
import io
import json
import logging
import os
import random
import re
import secrets
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramRetryAfter
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (BotCommand, BufferedInputFile, CallbackQuery, InputMediaPhoto, KeyboardButton,
                           KeyboardButtonPollType, Message, PollAnswer, ReplyKeyboardMarkup,
                           ReplyKeyboardRemove)
from aiogram.utils.keyboard import InlineKeyboardBuilder

try:
    from aiogram.types import ReactionTypeEmoji
except ImportError:  # eski aiogram
    ReactionTypeEmoji = None

# ============================ SOZLAMALAR ============================
BASE_DIR = Path(__file__).parent


def _load_token():
    """Token kodda saqlanmaydi (GitHub'ga tushib qolmasligi uchun):
    1) BOT_TOKEN muhit o'zgaruvchisi (hosting), 2) app.py yonidagi token.txt (kompyuteringiz)."""
    t = os.getenv("BOT_TOKEN")
    if t:
        return t.strip()
    f = BASE_DIR / "token.txt"
    return f.read_text(encoding="utf-8").strip() if f.exists() else ""


TOKEN = _load_token()
ADMIN_ID = int(os.getenv("ADMIN_ID") or 7044084668)
DB_FILE = Path(os.getenv("DATA_DIR") or BASE_DIR) / "data.json"  # hostingda DATA_DIR=/data (doimiy disk)
DB_FILE.parent.mkdir(parents=True, exist_ok=True)
BOT_USERNAME = ""

# Telegram xabar effektlari (faqat shaxsiy chatda ishlaydi)
EFFECTS = {
    "confetti": "5046509860389126442",
    "fire": "5104841245755180586",
    "like": "5107584321108051014",
    "heart": "5044134455711629726",
}

LEVELS = [
    (0, "🌱 Boshlovchi"),
    (20, "📘 O'quvchi"),
    (60, "⭐ Bilimdon"),
    (150, "🔥 Mutaxassis"),
    (300, "👑 Daho"),
]

logging.basicConfig(level=logging.INFO)
router = Router()

pending = {}        # poll_id -> {"ev", "choice"} yoki {"group": True, "votes": {}}
running = {}        # user_id -> {"stop": bool, "ev": Event | None, "poll": int | None}
text_waiters = {}   # user_id -> {"ev": Event, "text": str | None}
games = {}          # chat_id -> {"stop": bool}
tasks = set()


# ============================ MA'LUMOTLAR BAZASI ============================
def demo_db():
    return {
        "tests": {
            "demo0001": {
                "id": "demo0001", "owner": 0, "title": "Namuna test",
                "time": 15, "shuffle": True, "results": {},
                "questions": [
                    {"type": "quiz", "q": "Qaysi avtomobil brendi o'z nomini yaponcha familiyadan olgan?",
                     "options": ["Honda", "Ferrari", "Renault"], "correct": 0,
                     "exp": "Honda nomi asoschisi Soichiro Honda familiyasidan olingan."},
                    {"type": "text", "q": "Python dasturlash tilini kim yaratgan? (familiyasini yozing)",
                     "answers": ["Guido van Rossum", "Guido", "Van Rossum", "Rossum"],
                     "exp": "Python'ni 1991-yilda Guido van Rossum yaratgan."},
                ],
            }
        },
    }


def load_db():
    db = None
    if DB_FILE.exists():
        try:
            db = json.loads(DB_FILE.read_text(encoding="utf-8"))
        except Exception:
            DB_FILE.replace(DB_FILE.with_suffix(".broken"))
    if not isinstance(db, dict):
        db = demo_db()
    db.setdefault("tests", {})
    db.setdefault("users", {})
    db.setdefault("stats", {})
    db.setdefault("channels", {})
    db.setdefault("contests", {})
    db.setdefault("langs", {})
    return db


def save_db():
    tmp = DB_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(DB, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DB_FILE)


DB = load_db()
save_db()


# ============================ YORDAMCHI FUNKSIYALAR ============================
def esc(t):
    return html.escape(str(t))


def is_admin(uid):
    return uid == ADMIN_ID


def norm(t):
    """Javoblarni solishtirish uchun: kichik harf, belgi va bo'shliqsiz."""
    return re.sub(r"[\W_]+", "", str(t).casefold())


def level_of(correct):
    cur, nxt = LEVELS[0], None
    for i, lv in enumerate(LEVELS):
        if correct >= lv[0]:
            cur = lv
            nxt = LEVELS[i + 1] if i + 1 < len(LEVELS) else None
    return cur, nxt


def qtype(q):
    return q.get("type", "quiz")


async def is_chat_admin(bot: Bot, chat_id, uid):
    if is_admin(uid):
        return True
    try:
        m = await bot.get_chat_member(chat_id, uid)
        return m.status in ("administrator", "creator")
    except Exception:
        return False


def build_rand():
    pool = [q for t in DB["tests"].values() for q in t["questions"]]
    if not pool:
        return None
    qs = random.sample(pool, min(10, len(pool)))
    return {"id": "rand", "owner": 0, "title": "⚡ Tezkor quiz", "time": 15,
            "shuffle": True, "questions": qs, "results": {}}


def get_test(tid):
    return build_rand() if tid == "rand" else DB["tests"].get(tid)


# ============================ TILLAR (I18N) ============================
LANGS = {"uz": "🇺🇿 O'zbekcha", "tr": "🇹🇷 Türkçe", "en": "🇬🇧 English", "ru": "🇷🇺 Русский"}
LANG_IDX = {"uz": 0, "tr": 1, "en": 2, "ru": 3}
LANG_PROMPT = "🌐 Tilni tanlang · Dil seçin · Choose language · Выберите язык"

T = {
    # ---- umumiy
    "b_menu": ("🏠 Menyu", "🏠 Menü", "🏠 Menu", "🏠 Меню"),
    "menu_title": ("🏠 Asosiy menyu:", "🏠 Ana menü:", "🏠 Main menu:", "🏠 Главное меню:"),
    "menu_short": ("🏠 Menyu:", "🏠 Menü:", "🏠 Menu:", "🏠 Меню:"),
    "cancelled": ("❌ Bekor qilindi.", "❌ İptal edildi.", "❌ Cancelled.", "❌ Отменено."),
    "b_new": ("➕ Yangi test tuzish", "➕ Yeni test oluştur", "➕ Create a new test", "➕ Создать тест"),
    "b_my": ("📚 Testlarim", "📚 Testlerim", "📚 My tests", "📚 Мои тесты"),
    "b_public": ("🌐 Ommaviy testlar", "🌐 Herkese açık testler", "🌐 Public tests", "🌐 Открытые тесты"),
    "b_quick": ("⚡ Tezkor quiz", "⚡ Hızlı quiz", "⚡ Quick quiz", "⚡ Быстрый квиз"),
    "b_profile": ("🏅 Profilim", "🏅 Profilim", "🏅 My profile", "🏅 Мой профиль"),
    "b_top": ("🏆 Reyting", "🏆 Sıralama", "🏆 Leaderboard", "🏆 Рейтинг"),
    "b_help": ("ℹ️ Yordam", "ℹ️ Yardım", "ℹ️ Help", "ℹ️ Помощь"),
    "b_addch": ("📣 Kanalga qo'shish", "📣 Kanala ekle", "📣 Add to channel", "📣 Добавить в канал"),
    "b_addgr": ("👥 Guruhga qo'shish", "👥 Gruba ekle", "👥 Add to group", "👥 Добавить в группу"),
    "b_lang": ("🌐 Til", "🌐 Dil", "🌐 Language", "🌐 Язык"),
    "welcome": (
        "Salom, <b>{name}</b>! 👋\n\nMen test (quiz) tuzish va yechish botiman:\n"
        "🖼 rasmli savollar · ✍️ yozma javobli savollar\n"
        "📥 ko'p savolni matn yoki .txt fayldan yuklash\n"
        "📣 kanalga quiz yuborish · 👥 guruhda musobaqa\n"
        "🏆 reyting va 🎉 tabriklar\n\nPastdagi tugmalardan birini tanlang:",
        "Merhaba, <b>{name}</b>! 👋\n\nBen test (quiz) hazırlama ve çözme botuyum:\n"
        "🖼 görselli sorular · ✍️ yazılı cevaplı sorular\n"
        "📥 çok sayıda soruyu metin veya .txt dosyasından yükleme\n"
        "📣 kanala quiz gönderme · 👥 grupta yarışma\n"
        "🏆 sıralama ve 🎉 tebrikler\n\nAşağıdaki düğmelerden birini seçin:",
        "Hello, <b>{name}</b>! 👋\n\nI'm a bot for creating and taking tests (quizzes):\n"
        "🖼 questions with images · ✍️ written-answer questions\n"
        "📥 bulk import of many questions from text or a .txt file\n"
        "📣 post quizzes to a channel · 👥 group contests\n"
        "🏆 leaderboards and 🎉 celebrations\n\nChoose one of the buttons below:",
        "Привет, <b>{name}</b>! 👋\n\nЯ бот для создания и прохождения тестов (квизов):\n"
        "🖼 вопросы с картинками · ✍️ вопросы с письменным ответом\n"
        "📥 загрузка множества вопросов из текста или .txt файла\n"
        "📣 отправка квизов в канал · 👥 соревнования в группе\n"
        "🏆 рейтинг и 🎉 поздравления\n\nВыберите одну из кнопок ниже:",
    ),
    "help_head": (
        "<b>Qanday ishlaydi?</b>\n\n1️⃣ «Yangi test tuzish» ni bosing\n2️⃣ Nom, vaqt va aralashtirishni tanlang\n3️⃣ Savollarni yuboring:\n\n",
        "<b>Nasıl çalışır?</b>\n\n1️⃣ «Yeni test oluştur» düğmesine basın\n2️⃣ Ad, süre ve karıştırma seçeneğini seçin\n3️⃣ Soruları gönderin:\n\n",
        "<b>How does it work?</b>\n\n1️⃣ Press «Create a new test»\n2️⃣ Choose a name, time and shuffling\n3️⃣ Send the questions:\n\n",
        "<b>Как это работает?</b>\n\n1️⃣ Нажмите «Создать тест»\n2️⃣ Выберите название, время и перемешивание\n3️⃣ Отправьте вопросы:\n\n",
    ),
    "help_tail": (
        "\n\n/stop — testni to'xtatish\n/cancel — bekor qilish\n/lang — tilni o'zgartirish\nGuruhda: /quiz — test tanlash",
        "\n\n/stop — testi durdur\n/cancel — iptal\n/lang — dili değiştir\nGrupta: /quiz — test seç",
        "\n\n/stop — stop the test\n/cancel — cancel\n/lang — change language\nIn a group: /quiz — choose a test",
        "\n\n/stop — остановить тест\n/cancel — отмена\n/lang — сменить язык\nВ группе: /quiz — выбрать тест",
    ),
    "format_help": (
        "<b>1) Variantli savol</b> (to'g'ri javob oldiga +):\n"
        "<code>Savol matni\n+To'g'ri javob\nNoto'g'ri javob\nNoto'g'ri javob\n?Izoh (ixtiyoriy)</code>\n\n"
        "<b>2) Yozma javobli savol</b> (javob oldiga =):\n"
        "<code>Savol matni\n=To'g'ri javob\n=Boshqa to'g'ri yozilishi (ixtiyoriy)\n?Izoh</code>\n\n"
        "<b>3) Ko'p savol</b> (A, B, C... variantlar, to'g'ri javobga #):\n"
        "<code>1. Python qaysi yili yaratilgan?\nA) 1989\n#B) 1991\nC) 2000\n\n2. print() nima qiladi?\nA) O'chiradi\nB) Chop etadi #\nC) Yozadi</code>\n\n"
        "🖼 <b>Rasm</b>: rasm yuboring, savolni <b>izoh (caption)</b> qismiga shu formatda yozing.\n"
        "📥 <b>Ko'p savol</b>: matn yoki <b>.txt fayl</b> qilib yuboring (20, 50, 100+ savol). Savollar orasida bitta <b>bo'sh qator</b> qoldiring.\n"
        "🎛 Yoki Telegram'ning tayyor <b>Quiz</b> so'rovini yuboring.",
        "<b>1) Seçenekli soru</b> (doğru cevabın başına +):\n"
        "<code>Soru metni\n+Doğru cevap\nYanlış cevap\nYanlış cevap\n?Açıklama (isteğe bağlı)</code>\n\n"
        "<b>2) Yazılı cevaplı soru</b> (cevabın başına =):\n"
        "<code>Soru metni\n=Doğru cevap\n=Başka doğru yazılış (isteğe bağlı)\n?Açıklama</code>\n\n"
        "<b>3) Çok sayıda soru</b> (A, B, C... seçenekleri, doğru cevaba #):\n"
        "<code>1. Python hangi yıl oluşturuldu?\nA) 1989\n#B) 1991\nC) 2000\n\n2. print() ne yapar?\nA) Siler\nB) Ekrana yazdırır #\nC) Kaydeder</code>\n\n"
        "🖼 <b>Görsel</b>: görsel gönderin, soruyu <b>açıklama (caption)</b> kısmına aynı biçimde yazın.\n"
        "📥 <b>Toplu soru</b>: metin veya <b>.txt dosyası</b> olarak gönderin (20, 50, 100+ soru). Sorular arasında bir <b>boş satır</b> bırakın.\n"
        "🎛 Ya da Telegram'ın hazır <b>Quiz</b> anketini gönderin.",
        "<b>1) Multiple-choice question</b> (put + before the correct answer):\n"
        "<code>Question text\n+Correct answer\nWrong answer\nWrong answer\n?Explanation (optional)</code>\n\n"
        "<b>2) Written-answer question</b> (put = before the answer):\n"
        "<code>Question text\n=Correct answer\n=Another accepted spelling (optional)\n?Explanation</code>\n\n"
        "<b>3) Many questions</b> (options A, B, C..., mark the correct one with #):\n"
        "<code>1. In which year was Python created?\nA) 1989\n#B) 1991\nC) 2000\n\n2. What does print() do?\nA) Deletes\nB) Prints #\nC) Saves</code>\n\n"
        "🖼 <b>Image</b>: send an image and write the question in the <b>caption</b> in the same format.\n"
        "📥 <b>Bulk import</b>: send as text or as a <b>.txt file</b> (20, 50, 100+ questions). Leave one <b>blank line</b> between questions.\n"
        "🎛 Or send Telegram's built-in <b>Quiz</b> poll.",
        "<b>1) Вопрос с вариантами</b> (перед правильным ответом поставьте +):\n"
        "<code>Текст вопроса\n+Правильный ответ\nНеправильный ответ\nНеправильный ответ\n?Пояснение (необязательно)</code>\n\n"
        "<b>2) Вопрос с письменным ответом</b> (перед ответом поставьте =):\n"
        "<code>Текст вопроса\n=Правильный ответ\n=Другой допустимый вариант (необязательно)\n?Пояснение</code>\n\n"
        "<b>3) Много вопросов</b> (варианты A, B, C..., правильный отмечайте #):\n"
        "<code>1. В каком году создан Python?\nA) 1989\n#B) 1991\nC) 2000\n\n2. Что делает print()?\nA) Удаляет\nB) Выводит #\nC) Сохраняет</code>\n\n"
        "🖼 <b>Картинка</b>: отправьте картинку, а вопрос напишите в <b>подписи</b> в том же формате.\n"
        "📥 <b>Массовая загрузка</b>: отправьте текстом или <b>.txt файлом</b> (20, 50, 100+ вопросов). Между вопросами оставляйте одну <b>пустую строку</b>.\n"
        "🎛 Или отправьте готовый опрос-<b>Quiz</b> Telegram.",
    ),
    # ---- yangi test
    "ask_title": (
        "📝 Test nomini yozing (masalan: <i>Python asoslari</i>):",
        "📝 Testin adını yazın (örneğin: <i>Python temelleri</i>):",
        "📝 Enter the test name (e.g. <i>Python basics</i>):",
        "📝 Введите название теста (например: <i>Основы Python</i>):",
    ),
    "ask_time": (
        "⏱ Har bir savolga necha soniya vaqt beriladi?",
        "⏱ Her soruya kaç saniye süre verilsin?",
        "⏱ How many seconds for each question?",
        "⏱ Сколько секунд даётся на каждый вопрос?",
    ),
    "t_sec": ("{s} soniya", "{s} saniye", "{s} sec", "{s} сек"),
    "ask_shuffle": (
        "Savollar va variantlar aralashtirilsinmi?",
        "Sorular ve seçenekler karıştırılsın mı?",
        "Shuffle questions and options?",
        "Перемешивать вопросы и варианты?",
    ),
    "b_shuf": ("🔀 Aralashtirish", "🔀 Karıştır", "🔀 Shuffle", "🔀 Перемешать"),
    "b_noshuf": ("➡️ Tartib bilan", "➡️ Sırayla", "➡️ In order", "➡️ По порядку"),
    "poll_btn": ("📝 Savol tuzish", "📝 Soru oluştur", "📝 Create question", "📝 Создать вопрос"),
    "poll_ph": (
        "«📝 Savol tuzish» tugmasini bosing",
        "«📝 Soru oluştur» düğmesine basın",
        "Press «📝 Create question»",
        "Нажмите «📝 Создать вопрос»",
    ),
    "ask_questions": (
        "✅ Endi savollarni qo'shamiz!\n\n"
        "👇 Pastdagi <b>«📝 Savol tuzish»</b> tugmasini bosing. Telegram'ning savol yaratish oynasi ochiladi: "
        "savol, variantlar, to'g'ri javob va izohni o'zingiz belgilaysiz, so'ng <b>Create</b> ni bosasiz.\n"
        "🖼 Rasm qo'shish uchun oynadagi <b>Add Description</b> qatoridagi 📎 belgisini bosing: "
        "rasm savol bilan bir xabarda chiqadi.\n\n"
        "Qo'shimcha usullar (xohlasangiz):\n"
        "• savolni matn ko'rinishida yozish\n"
        "• rasm + izoh bilan rasmli savol\n"
        "• yozma javobli savol\n"
        "• 📥 ko'p savolni matn yoki .txt fayldan yuklash\n"
        "Formatlar: /help",
        "✅ Şimdi soruları ekleyelim!\n\n"
        "👇 Aşağıdaki <b>«📝 Soru oluştur»</b> düğmesine basın. Telegram'ın soru oluşturma penceresi açılır: "
        "soru, seçenekler, doğru cevap ve açıklamayı siz belirlersiniz, sonra <b>Create</b> düğmesine basarsınız.\n"
        "🖼 Görsel eklemek için penceredeki <b>Add Description</b> satırındaki 📎 simgesine basın: "
        "görsel soruyla aynı mesajda görünür.\n\n"
        "Ek yöntemler (isterseniz):\n"
        "• soruyu metin olarak yazmak\n"
        "• görsel + açıklama ile görselli soru\n"
        "• yazılı cevaplı soru\n"
        "• 📥 çok sayıda soruyu metin veya .txt dosyasından yüklemek\n"
        "Biçimler: /help",
        "✅ Now let's add the questions!\n\n"
        "👇 Press the <b>«📝 Create question»</b> button below. Telegram's poll creation window opens: "
        "you set the question, options, correct answer and explanation, then press <b>Create</b>.\n"
        "🖼 To add an image, tap the 📎 icon on the <b>Add Description</b> line: "
        "the image appears in the same message as the question.\n\n"
        "Other ways (optional):\n"
        "• type the question as text\n"
        "• image + caption question\n"
        "• written-answer question\n"
        "• 📥 bulk import of many questions from text or a .txt file\n"
        "Formats: /help",
        "✅ Теперь добавим вопросы!\n\n"
        "👇 Нажмите кнопку <b>«📝 Создать вопрос»</b> внизу. Откроется окно создания опроса Telegram: "
        "вы задаёте вопрос, варианты, правильный ответ и пояснение, затем нажимаете <b>Create</b>.\n"
        "🖼 Чтобы добавить картинку, нажмите 📎 в строке <b>Add Description</b>: "
        "картинка появится в одном сообщении с вопросом.\n\n"
        "Другие способы (по желанию):\n"
        "• написать вопрос текстом\n"
        "• вопрос с картинкой и подписью\n"
        "• вопрос с письменным ответом\n"
        "• 📥 загрузка множества вопросов из текста или .txt файла\n"
        "Форматы: /help",
    ),
    "bulk_prompt": (
        "📥 Ko'p savolingiz bormi? Quyidagi tugmalardan foydalaning:",
        "📥 Çok sayıda sorunuz mu var? Aşağıdaki düğmeleri kullanın:",
        "📥 Have many questions? Use the buttons below:",
        "📥 Много вопросов? Используйте кнопки ниже:",
    ),
    "b_bulk": ("📥 Ko'p savol yuklash", "📥 Toplu soru yükle", "📥 Bulk import", "📥 Загрузить много вопросов"),
    "b_sample": ("📄 Namuna fayl", "📄 Örnek dosya", "📄 Sample file", "📄 Образец файла"),
    "bulk_info": (
        "📥 <b>Ko'p savol yuklash</b>\n\nSavollarni matn qilib yuboring yoki <b>.txt fayl</b> tashlang (20, 50, 100+ savol bo'lishi mumkin).\n\n"
        "<code>1. Python qaysi yili yaratilgan?\nA) 1989\n#B) 1991\nC) 2000\nD) 2010\n\n2. print() nima qiladi?\nA) O'chiradi\nB) Chop etadi #\nC) Yozadi\nD) Hisoblaydi</code>\n\n"
        "• To'g'ri javob: variant oldiga <b>#</b> (<code>#B) ...</code>) yoki oxiriga <b>#</b> (<code>B) ... #</code>)\n"
        "• Savollar orasida bo'sh qator qoldiring (raqamlash ham bo'ladi)\n"
        "• Izoh uchun savol tagiga <code>?Izoh</code> qatorini qo'shing\n"
        "• Xabar 4096 belgidan oshmasligi kerak: ko'p savolni bir necha xabarga bo'ling yoki <b>.txt fayl</b> yuboring\n\n"
        "📄 Tayyor shablon uchun «Namuna fayl» tugmasini bosing.",
        "📥 <b>Toplu soru yükleme</b>\n\nSorularınızı metin olarak gönderin veya bir <b>.txt dosyası</b> yükleyin (20, 50, 100+ soru olabilir).\n\n"
        "<code>1. Python hangi yıl oluşturuldu?\nA) 1989\n#B) 1991\nC) 2000\nD) 2010\n\n2. print() ne yapar?\nA) Siler\nB) Ekrana yazdırır #\nC) Kaydeder\nD) Hesaplar</code>\n\n"
        "• Doğru cevap: seçeneğin başına <b>#</b> (<code>#B) ...</code>) veya sonuna <b>#</b> (<code>B) ... #</code>)\n"
        "• Sorular arasında boş satır bırakın (numaralandırma da olur)\n"
        "• Açıklama için sorunun altına <code>?Açıklama</code> satırı ekleyin\n"
        "• Mesaj 4096 karakteri geçmemeli: çok soruyu birkaç mesaja bölün veya <b>.txt dosyası</b> gönderin\n\n"
        "📄 Hazır şablon için «Örnek dosya» düğmesine basın.",
        "📥 <b>Bulk import</b>\n\nSend your questions as text or upload a <b>.txt file</b> (20, 50, 100+ questions are fine).\n\n"
        "<code>1. In which year was Python created?\nA) 1989\n#B) 1991\nC) 2000\nD) 2010\n\n2. What does print() do?\nA) Deletes\nB) Prints #\nC) Saves\nD) Calculates</code>\n\n"
        "• Correct answer: put <b>#</b> before the option (<code>#B) ...</code>) or at the end (<code>B) ... #</code>)\n"
        "• Leave a blank line between questions (numbering works too)\n"
        "• For an explanation add a <code>?Explanation</code> line under the question\n"
        "• A message can't exceed 4096 characters: split many questions into several messages or send a <b>.txt file</b>\n\n"
        "📄 Press «Sample file» for a ready-made template.",
        "📥 <b>Массовая загрузка вопросов</b>\n\nОтправьте вопросы текстом или загрузите <b>.txt файл</b> (можно 20, 50, 100+ вопросов).\n\n"
        "<code>1. В каком году создан Python?\nA) 1989\n#B) 1991\nC) 2000\nD) 2010\n\n2. Что делает print()?\nA) Удаляет\nB) Выводит #\nC) Сохраняет\nD) Вычисляет</code>\n\n"
        "• Правильный ответ: <b>#</b> перед вариантом (<code>#B) ...</code>) или в конце (<code>B) ... #</code>)\n"
        "• Между вопросами оставляйте пустую строку (нумерация тоже подойдёт)\n"
        "• Для пояснения добавьте под вопросом строку <code>?Пояснение</code>\n"
        "• Сообщение не должно превышать 4096 символов: разбейте вопросы на несколько сообщений или отправьте <b>.txt файл</b>\n\n"
        "📄 Готовый шаблон — кнопка «Образец файла».",
    ),
    "sample_txt": (
        "1. Python qaysi yili yaratilgan?\nA) 1989\n#B) 1991\nC) 2000\nD) 2010\n\n"
        "2. print() nima qiladi?\nA) O'chiradi\nB) Chop etadi #\nC) Yozadi\nD) Hisoblaydi\n\n"
        "3. Python'da ro'yxat qaysi qavs bilan yoziladi?\nA) ( )\nB) { }\n#C) [ ]\n?Ro'yxat kvadrat qavs bilan yoziladi: [1, 2, 3]\n",
        "1. Python hangi yıl oluşturuldu?\nA) 1989\n#B) 1991\nC) 2000\nD) 2010\n\n"
        "2. print() ne yapar?\nA) Siler\nB) Ekrana yazdırır #\nC) Kaydeder\nD) Hesaplar\n\n"
        "3. Python'da liste hangi parantezle yazılır?\nA) ( )\nB) { }\n#C) [ ]\n?Liste köşeli parantezle yazılır: [1, 2, 3]\n",
        "1. In which year was Python created?\nA) 1989\n#B) 1991\nC) 2000\nD) 2010\n\n"
        "2. What does print() do?\nA) Deletes\nB) Prints #\nC) Saves\nD) Calculates\n\n"
        "3. Which brackets are used for a list in Python?\nA) ( )\nB) { }\n#C) [ ]\n?A list is written in square brackets: [1, 2, 3]\n",
        "1. В каком году создан Python?\nA) 1989\n#B) 1991\nC) 2000\nD) 2010\n\n"
        "2. Что делает print()?\nA) Удаляет\nB) Выводит #\nC) Сохраняет\nD) Вычисляет\n\n"
        "3. В каких скобках пишется список в Python?\nA) ( )\nB) { }\n#C) [ ]\n?Список пишется в квадратных скобках: [1, 2, 3]\n",
    ),
    "added": (
        "➕ {n} ta savol qo'shildi. Jami: <b>{total}</b>\n",
        "➕ {n} soru eklendi. Toplam: <b>{total}</b>\n",
        "➕ {n} question(s) added. Total: <b>{total}</b>\n",
        "➕ Добавлено вопросов: {n}. Всего: <b>{total}</b>\n",
    ),
    "with_photo": ("🖼 Rasm bilan\n", "🖼 Görselli\n", "🖼 With image\n", "🖼 С картинкой\n"),
    "errors_head": ("\n⚠️ <b>Xatolar:</b>\n", "\n⚠️ <b>Hatalar:</b>\n", "\n⚠️ <b>Errors:</b>\n", "\n⚠️ <b>Ошибки:</b>\n"),
    "next_q": (
        "\nKeyingi savolni yuboring yoki «Tayyor» ni bosing.",
        "\nSıradaki soruyu gönderin veya «Hazır» düğmesine basın.",
        "\nSend the next question or press «Done».",
        "\nОтправьте следующий вопрос или нажмите «Готово».",
    ),
    "b_done": ("✅ Tayyor ({n} savol)", "✅ Hazır ({n} soru)", "✅ Done ({n} questions)", "✅ Готово (вопросов: {n})"),
    "b_undo": ("↩️ Oxirgisini o'chirish", "↩️ Sonuncuyu sil", "↩️ Delete the last one", "↩️ Удалить последний"),
    "b_cancel": ("❌ Bekor qilish", "❌ İptal", "❌ Cancel", "❌ Отмена"),
    "note_multi": (
        "Bir nechta to'g'ri javob belgilangan, faqat birinchisi olindi.",
        "Birden fazla doğru cevap işaretlenmiş, yalnızca ilki alındı.",
        "Several correct answers were marked; only the first one was used.",
        "Отмечено несколько правильных ответов, использован только первый.",
    ),
    "poll_no_correct": (
        "⚠️ Bu so'rovda to'g'ri javob belgilanmagan.\n«📝 Savol tuzish» tugmasi orqali yarating va to'g'ri javobni belgilang.",
        "⚠️ Bu ankette doğru cevap işaretlenmemiş.\n«📝 Soru oluştur» düğmesiyle oluşturun ve doğru cevabı işaretleyin.",
        "⚠️ No correct answer is marked in this poll.\nCreate it with the «📝 Create question» button and mark the correct answer.",
        "⚠️ В этом опросе не отмечен правильный ответ.\nСоздайте его кнопкой «📝 Создать вопрос» и отметьте правильный ответ.",
    ),
    "photo_need_caption": (
        "⚠️ Rasm izohiga (caption) savolni yozing:\n\n",
        "⚠️ Görselin açıklamasına (caption) soruyu yazın:\n\n",
        "⚠️ Write the question in the image caption:\n\n",
        "⚠️ Напишите вопрос в подписи к картинке:\n\n",
    ),
    "undo_none": ("O'chiradigan savol yo'q.", "Silinecek soru yok.", "No question to delete.", "Нечего удалять."),
    "undo_ok": ("O'chirildi. Qoldi: {n}", "Silindi. Kalan: {n}", "Deleted. Remaining: {n}", "Удалено. Осталось: {n}"),
    "done_none": (
        "Avval kamida 1 ta savol qo'shing!",
        "Önce en az 1 soru ekleyin!",
        "Add at least 1 question first!",
        "Сначала добавьте хотя бы 1 вопрос!",
    ),
    "test_ready": ("🎉 Test tayyor: <b>{title}</b>", "🎉 Test hazır: <b>{title}</b>", "🎉 Test ready: <b>{title}</b>", "🎉 Тест готов: <b>{title}</b>"),
    "stray_poll": (
        "Savol qo'shish uchun avval «➕ Yangi test tuzish» ni bosing.",
        "Soru eklemek için önce «➕ Yeni test oluştur» düğmesine basın.",
        "To add questions, first press «➕ Create a new test».",
        "Чтобы добавить вопросы, сначала нажмите «➕ Создать тест».",
    ),
    "fallback": ("Menyudan tanlang 👇", "Menüden seçin 👇", "Choose from the menu 👇", "Выберите в меню 👇"),
    "doc_big": ("⚠️ Fayl juda katta (2 MB gacha).", "⚠️ Dosya çok büyük (en fazla 2 MB).", "⚠️ The file is too large (up to 2 MB).", "⚠️ Файл слишком большой (до 2 МБ)."),
    "doc_type": ("⚠️ Faqat .txt fayl yuboring.", "⚠️ Yalnızca .txt dosyası gönderin.", "⚠️ Please send a .txt file only.", "⚠️ Отправьте только .txt файл."),
    "doc_empty": (
        "⚠️ Fayldan savol topilmadi. Namuna uchun «📄 Namuna fayl» tugmasini bosing.",
        "⚠️ Dosyada soru bulunamadı. Örnek için «📄 Örnek dosya» düğmesine basın.",
        "⚠️ No questions found in the file. Press «📄 Sample file» for an example.",
        "⚠️ В файле не найдено вопросов. Пример — кнопка «📄 Образец файла».",
    ),
    "doc_err": ("⚠️ Faylni o'qib bo'lmadi.", "⚠️ Dosya okunamadı.", "⚠️ Could not read the file.", "⚠️ Не удалось прочитать файл."),
    # ---- o'qish xatolari
    "e_few": ("Savol va kamida bitta javob kerak.", "Soru ve en az bir cevap gerekli.", "A question and at least one answer are required.", "Нужны вопрос и хотя бы один ответ."),
    "e_len900": ("Savol 900 belgidan oshmasin.", "Soru 900 karakteri geçmemeli.", "The question must not exceed 900 characters.", "Вопрос не должен превышать 900 символов."),
    "e_text_eq": (
        "Yozma savolda barcha javob qatorlari = bilan boshlansin.",
        "Yazılı soruda tüm cevap satırları = ile başlamalı.",
        "In a written-answer question all answer lines must start with =.",
        "В вопросе с письменным ответом все строки ответов должны начинаться с =.",
    ),
    "e_no_ans": (
        "Kamida bitta to'g'ri javob yozing (=javob).",
        "En az bir doğru cevap yazın (=cevap).",
        "Write at least one correct answer (=answer).",
        "Укажите хотя бы один правильный ответ (=ответ).",
    ),
    "e_two": ("Kamida 2 ta variant kerak.", "En az 2 seçenek gerekli.", "At least 2 options are required.", "Нужно минимум 2 варианта."),
    "e_ten": ("Variantlar 10 tadan oshmasin.", "Seçenekler 10'u geçmemeli.", "No more than 10 options.", "Не более 10 вариантов."),
    "e_q280": ("Savol 280 belgidan oshmasin.", "Soru 280 karakteri geçmemeli.", "The question must not exceed 280 characters.", "Вопрос не должен превышать 280 символов."),
    "e_one_correct": (
        "To'g'ri javob faqat bitta bo'lishi kerak.",
        "Doğru cevap yalnızca bir tane olmalı.",
        "There must be only one correct answer.",
        "Правильный ответ должен быть только один.",
    ),
    "e_empty_opt": ("Bo'sh variant bo'lmasin.", "Boş seçenek olmasın.", "Empty options are not allowed.", "Пустых вариантов быть не должно."),
    "e_opt100": ("Variant 100 belgidan oshmasin.", "Seçenek 100 karakteri geçmemeli.", "An option must not exceed 100 characters.", "Вариант не должен превышать 100 символов."),
    "e_mark": ("To'g'ri javob oldiga + qo'ying.", "Doğru cevabın başına + koyun.", "Put + before the correct answer.", "Поставьте + перед правильным ответом."),
    "e_noq": ("Savol matni topilmadi.", "Soru metni bulunamadı.", "Question text not found.", "Текст вопроса не найден."),
    "e_hash0": (
        "To'g'ri javob belgilanmagan (variant oldiga yoki oxiriga # qo'ying).",
        "Doğru cevap işaretlenmemiş (seçeneğin başına veya sonuna # koyun).",
        "No correct answer marked (put # before or after an option).",
        "Правильный ответ не отмечен (поставьте # перед вариантом или в конце).",
    ),
    "e_hash2": (
        "Bir nechta to'g'ri javob (#) belgilangan.",
        "Birden fazla doğru cevap (#) işaretlenmiş.",
        "More than one correct answer (#) is marked.",
        "Отмечено несколько правильных ответов (#).",
    ),
    "err_block": ("{i}-savol: {err}", "{i}. soru: {err}", "Question {i}: {err}", "Вопрос {i}: {err}"),
    # ---- test kartasi va ro'yxatlar
    "test_missing": ("Bunday test topilmadi.", "Böyle bir test bulunamadı.", "Test not found.", "Такой тест не найден."),
    "card": (
        "📋 <b>{title}</b>\n\n🖊 {n} ta savol (✍️ yozma: {nt} · 🖼 rasmli: {np})\n⏱ Har biriga {t} soniya\n{mode}\n👥 {k} kishi yechgan",
        "📋 <b>{title}</b>\n\n🖊 {n} soru (✍️ yazılı: {nt} · 🖼 görselli: {np})\n⏱ Her biri için {t} saniye\n{mode}\n👥 {k} kişi çözdü",
        "📋 <b>{title}</b>\n\n🖊 {n} questions (✍️ written: {nt} · 🖼 with images: {np})\n⏱ {t} seconds each\n{mode}\n👥 Taken by {k}",
        "📋 <b>{title}</b>\n\n🖊 Вопросов: {n} (✍️ письменных: {nt} · 🖼 с картинками: {np})\n⏱ По {t} сек. на каждый\n{mode}\n👥 Прошли: {k}",
    ),
    "m_shuf": ("🔀 Aralashtiriladi", "🔀 Karıştırılır", "🔀 Shuffled", "🔀 Перемешивается"),
    "b_start": ("▶️ Boshlash", "▶️ Başla", "▶️ Start", "▶️ Начать"),
    "b_share": ("🔗 Ulashish", "🔗 Paylaş", "🔗 Share", "🔗 Поделиться"),
    "b_group_play": ("👥 Guruhda o'ynash", "👥 Grupta oyna", "👥 Play in a group", "👥 Играть в группе"),
    "b_post": ("📣 Kanalga yuborish", "📣 Kanala gönder", "📣 Post to channel", "📣 Отправить в канал"),
    "b_contest": ("🏆 Kanalda musobaqa", "🏆 Kanalda yarışma", "🏆 Channel contest", "🏆 Конкурс в канале"),
    "b_del": ("🗑 O'chirish", "🗑 Sil", "🗑 Delete", "🗑 Удалить"),
    "n_q": ("{title} · {n} savol", "{title} · {n} soru", "{title} · {n} questions", "{title} · вопросов: {n}"),
    "my_head": ("📚 <b>Testlaringiz:</b>", "📚 <b>Testleriniz:</b>", "📚 <b>Your tests:</b>", "📚 <b>Ваши тесты:</b>"),
    "my_none": (
        "Sizda hali test yo'q. «Yangi test tuzish» ni bosing.",
        "Henüz testiniz yok. «Yeni test oluştur» düğmesine basın.",
        "You don't have any tests yet. Press «Create a new test».",
        "У вас пока нет тестов. Нажмите «Создать тест».",
    ),
    "pub_head": ("🌐 <b>Ommaviy testlar:</b>", "🌐 <b>Herkese açık testler:</b>", "🌐 <b>Public tests:</b>", "🌐 <b>Открытые тесты:</b>"),
    "share_msg": ("🔗 Ulashish havolasi:\n{link}", "🔗 Paylaşım bağlantısı:\n{link}", "🔗 Share link:\n{link}", "🔗 Ссылка для публикации:\n{link}"),
    "deleted": ("🗑 Test o'chirildi.", "🗑 Test silindi.", "🗑 Test deleted.", "🗑 Тест удалён."),
    # ---- reyting va profil
    "rate_head": ("🏆 <b>{title}</b> — reyting:\n\n", "🏆 <b>{title}</b> — sıralama:\n\n", "🏆 <b>{title}</b> — leaderboard:\n\n", "🏆 <b>{title}</b> — рейтинг:\n\n"),
    "rate_none": ("Hali hech kim yechmagan.", "Henüz kimse çözmedi.", "Nobody has taken it yet.", "Пока никто не проходил."),
    "top_head": (
        "🏆 <b>Umumiy reyting (to'g'ri javoblar):</b>\n\n",
        "🏆 <b>Genel sıralama (doğru cevaplar):</b>\n\n",
        "🏆 <b>Overall leaderboard (correct answers):</b>\n\n",
        "🏆 <b>Общий рейтинг (правильные ответы):</b>\n\n",
    ),
    "top_none": ("Hali natijalar yo'q.", "Henüz sonuç yok.", "No results yet.", "Результатов пока нет."),
    "anon": ("Foydalanuvchi", "Kullanıcı", "User", "Пользователь"),
    "lv0": ("🌱 Boshlovchi", "🌱 Başlangıç", "🌱 Beginner", "🌱 Новичок"),
    "lv1": ("📘 O'quvchi", "📘 Öğrenci", "📘 Learner", "📘 Ученик"),
    "lv2": ("⭐ Bilimdon", "⭐ Bilgili", "⭐ Knowledgeable", "⭐ Знаток"),
    "lv3": ("🔥 Mutaxassis", "🔥 Uzman", "🔥 Expert", "🔥 Эксперт"),
    "lv4": ("👑 Daho", "👑 Dahi", "👑 Genius", "👑 Гений"),
    "prof_none": (
        "Siz hali test yechmagansiz. «⚡ Tezkor quiz» ni sinab ko'ring!",
        "Henüz test çözmediniz. «⚡ Hızlı quiz» deneyin!",
        "You haven't taken any tests yet. Try «⚡ Quick quiz»!",
        "Вы ещё не проходили тесты. Попробуйте «⚡ Быстрый квиз»!",
    ),
    "prof": (
        "🏅 <b>{name}</b>\n\nDaraja: <b>{level}</b>\n✅ To'g'ri javoblar: {correct}\n🎯 Aniqlik: {acc}%\n📝 Yechilgan testlar: {tests}\n",
        "🏅 <b>{name}</b>\n\nSeviye: <b>{level}</b>\n✅ Doğru cevaplar: {correct}\n🎯 Doğruluk: %{acc}\n📝 Çözülen testler: {tests}\n",
        "🏅 <b>{name}</b>\n\nLevel: <b>{level}</b>\n✅ Correct answers: {correct}\n🎯 Accuracy: {acc}%\n📝 Tests taken: {tests}\n",
        "🏅 <b>{name}</b>\n\nУровень: <b>{level}</b>\n✅ Правильных ответов: {correct}\n🎯 Точность: {acc}%\n📝 Пройдено тестов: {tests}\n",
    ),
    "prof_next": (
        "\nKeyingi daraja ({nxt}) uchun yana <b>{need}</b> ta to'g'ri javob:\n{bar}",
        "\nBir sonraki seviye ({nxt}) için <b>{need}</b> doğru cevap daha:\n{bar}",
        "\n<b>{need}</b> more correct answers to reach the next level ({nxt}):\n{bar}",
        "\nДо следующего уровня ({nxt}) ещё <b>{need}</b> правильных ответов:\n{bar}",
    ),
    "prof_max": (
        "\n👑 Siz eng yuqori darajadasiz!",
        "\n👑 En yüksek seviyedesiniz!",
        "\n👑 You are at the highest level!",
        "\n👑 Вы на самом высоком уровне!",
    ),
    # ---- testni yechish
    "quick_none": ("Hali savollar yo'q.", "Henüz soru yok.", "There are no questions yet.", "Вопросов пока нет."),
    "no_running": ("Hozir ishlayotgan test yo'q.", "Şu anda çalışan test yok.", "No test is running.", "Сейчас нет активного теста."),
    "stopping": ("⏹ Test to'xtatilmoqda...", "⏹ Test durduruluyor...", "⏹ Stopping the test...", "⏹ Тест останавливается..."),
    "busy": (
        "Sizda test ishlayapti. To'xtatish: /stop",
        "Zaten bir testiniz çalışıyor. Durdurmak için: /stop",
        "You already have a test running. To stop: /stop",
        "У вас уже идёт тест. Остановить: /stop",
    ),
    "only_private": (
        "Bu tugma faqat shaxsiy chatda ishlaydi.",
        "Bu düğme yalnızca özel sohbette çalışır.",
        "This button only works in a private chat.",
        "Эта кнопка работает только в личном чате.",
    ),
    "test_nf": (
        "Test topilmadi yoki savollar yo'q",
        "Test bulunamadı veya soru yok",
        "Test not found or it has no questions",
        "Тест не найден или в нём нет вопросов",
    ),
    "run_intro": (
        "🏁 <b>{title}</b> boshlandi!\n{total} savol · har biriga {time} soniya\nTo'xtatish: /stop",
        "🏁 <b>{title}</b> başladı!\n{total} soru · her biri için {time} saniye\nDurdurmak için: /stop",
        "🏁 <b>{title}</b> has started!\n{total} questions · {time} seconds each\nTo stop: /stop",
        "🏁 <b>{title}</b> начался!\nВопросов: {total} · по {time} сек. на каждый\nОстановить: /stop",
    ),
    "run_text_cap": (
        "✍️ <b>{i}/{total}.</b> {q}\n\n⏱ {wt} soniya ichida javobni <b>yozib</b> yuboring",
        "✍️ <b>{i}/{total}.</b> {q}\n\n⏱ Cevabı {wt} saniye içinde <b>yazarak</b> gönderin",
        "✍️ <b>{i}/{total}.</b> {q}\n\n⏱ <b>Type</b> your answer within {wt} seconds",
        "✍️ <b>{i}/{total}.</b> {q}\n\n⏱ <b>Напишите</b> ответ в течение {wt} сек.",
    ),
    "time_up": (
        "⏰ Vaqt tugadi. To'g'ri javob: <b>{right}</b>{exp}",
        "⏰ Süre doldu. Doğru cevap: <b>{right}</b>{exp}",
        "⏰ Time's up. Correct answer: <b>{right}</b>{exp}",
        "⏰ Время вышло. Правильный ответ: <b>{right}</b>{exp}",
    ),
    "right": ("🟩 <b>To'g'ri!</b>{exp}", "🟩 <b>Doğru!</b>{exp}", "🟩 <b>Correct!</b>{exp}", "🟩 <b>Верно!</b>{exp}"),
    "wrong": (
        "🟥 <b>Noto'g'ri.</b> To'g'ri javob: <b>{right}</b>{exp}",
        "🟥 <b>Yanlış.</b> Doğru cevap: <b>{right}</b>{exp}",
        "🟥 <b>Wrong.</b> Correct answer: <b>{right}</b>{exp}",
        "🟥 <b>Неверно.</b> Правильный ответ: <b>{right}</b>{exp}",
    ),
    "sleepy": (
        "😴 Ketma-ket 2 ta savolga javob bermadingiz. Test to'xtatildi.",
        "😴 Art arda 2 soruyu cevapsız bıraktınız. Test durduruldu.",
        "😴 You skipped 2 questions in a row. The test was stopped.",
        "😴 Вы пропустили 2 вопроса подряд. Тест остановлен.",
    ),
    "run_err": (
        "⚠️ Testda xatolik yuz berdi. Natijalar hisoblanmoqda...",
        "⚠️ Testte bir hata oluştu. Sonuçlar hesaplanıyor...",
        "⚠️ An error occurred during the test. Calculating results...",
        "⚠️ Произошла ошибка во время теста. Подсчитываем результаты...",
    ),
    "res_stopped": ("⏹ To‘xtatildi", "⏹ Durduruldu", "⏹ Stopped", "⏹ Остановлено"),
    "res_done": ("🏁 Test tugadi", "🏁 Test bitti", "🏁 Test finished", "🏁 Тест завершён"),
    "res_body": (
        "!\n\n✅ To'g'ri: <b>{score}</b> / {total}\n📊 Natija: <b>{percent}%</b>\n{marks}\n\n<b>{title}</b>{note}",
        "!\n\n✅ Doğru: <b>{score}</b> / {total}\n📊 Sonuç: <b>%{percent}</b>\n{marks}\n\n<b>{title}</b>{note}",
        "!\n\n✅ Correct: <b>{score}</b> / {total}\n📊 Result: <b>{percent}%</b>\n{marks}\n\n<b>{title}</b>{note}",
        "!\n\n✅ Верно: <b>{score}</b> / {total}\n📊 Результат: <b>{percent}%</b>\n{marks}\n\n<b>{title}</b>{note}",
    ),
    "tier90": ("🏆 CHEMPION!", "🏆 ŞAMPİYON!", "🏆 CHAMPION!", "🏆 ЧЕМПИОН!"),
    "tier70": ("🥈 Ajoyib natija!", "🥈 Harika sonuç!", "🥈 Great result!", "🥈 Отличный результат!"),
    "tier50": ("🥉 Yaxshi natija!", "🥉 İyi sonuç!", "🥉 Good result!", "🥉 Хороший результат!"),
    "tier0": (
        "💪 Harakat qiling, yana urinib ko'ring!",
        "💪 Çabalayın, tekrar deneyin!",
        "💪 Keep going, try again!",
        "💪 Не сдавайтесь, попробуйте ещё раз!",
    ),
    "mistakes_head": ("❌ <b>Xatolar:</b>", "❌ <b>Hatalar:</b>", "❌ <b>Mistakes:</b>", "❌ <b>Ошибки:</b>"),
    "more_n": ("... va yana {n} ta", "... ve {n} tane daha", "... and {n} more", "... и ещё {n}"),
    "b_again": ("🔁 Qayta yechish", "🔁 Tekrar çöz", "🔁 Try again", "🔁 Пройти снова"),
    "place_note": (
        "🏟 Musobaqadagi o'rningiz: <b>{place}-o'rin</b> ({n} ishtirokchidan)",
        "🏟 Yarışmadaki sıranız: <b>{place}.</b> ({n} katılımcı arasında)",
        "🏟 Your place in the contest: <b>#{place}</b> (out of {n} participants)",
        "🏟 Ваше место в конкурсе: <b>{place}-е</b> (из {n} участников)",
    ),
    "dice_msg": (
        "🎁 Mukofot: omadingizni sinab ko'ring!",
        "🎁 Ödül: şansınızı deneyin!",
        "🎁 Reward: try your luck!",
        "🎁 Награда: испытайте удачу!",
    ),
    # ---- musobaqa (ishtirokchi tomoni)
    "c_notfound": ("Musobaqa topilmadi.", "Yarışma bulunamadı.", "Contest not found.", "Конкурс не найден."),
    "c_closed": (
        "🏁 Bu musobaqa yakunlangan. Natijalar kanalda e'lon qilingan.",
        "🏁 Bu yarışma sona erdi. Sonuçlar kanalda duyuruldu.",
        "🏁 This contest has ended. The results were announced in the channel.",
        "🏁 Этот конкурс завершён. Результаты объявлены в канале.",
    ),
    "c_already": (
        "Siz bu musobaqada qatnashgansiz: <b>{s}/{t}</b>.\nG'olib yakunda kanalda e'lon qilinadi.",
        "Bu yarışmaya zaten katıldınız: <b>{s}/{t}</b>.\nKazanan sonunda kanalda duyurulacak.",
        "You have already taken part in this contest: <b>{s}/{t}</b>.\nThe winner will be announced in the channel at the end.",
        "Вы уже участвовали в этом конкурсе: <b>{s}/{t}</b>.\nПобедитель будет объявлен в канале в конце.",
    ),
    "c_card": (
        "🏆 <b>{title}</b> — musobaqa\n\n🖊 {n} savol · ⏱ har biriga {t} soniya\n⚠️ Faqat <b>bitta</b> urinish. Tezlik ham hisobga olinadi.\n\nTayyor bo'lsangiz, boshlang!",
        "🏆 <b>{title}</b> — yarışma\n\n🖊 {n} soru · ⏱ her biri için {t} saniye\n⚠️ Yalnızca <b>bir</b> deneme hakkı. Hız da hesaba katılır.\n\nHazırsanız başlayın!",
        "🏆 <b>{title}</b> — contest\n\n🖊 {n} questions · ⏱ {t} seconds each\n⚠️ Only <b>one</b> attempt. Speed counts too.\n\nStart when you're ready!",
        "🏆 <b>{title}</b> — конкурс\n\n🖊 Вопросов: {n} · ⏱ по {t} сек. на каждый\n⚠️ Только <b>одна</b> попытка. Скорость тоже учитывается.\n\nНачинайте, когда будете готовы!",
    ),
    "c_alert_closed": (
        "Musobaqa yakunlangan yoki topilmadi.",
        "Yarışma sona erdi veya bulunamadı.",
        "The contest has ended or was not found.",
        "Конкурс завершён или не найден.",
    ),
    "c_alert_already": (
        "Siz allaqachon qatnashgansiz.",
        "Zaten katıldınız.",
        "You have already taken part.",
        "Вы уже участвовали.",
    ),
}


def L(uid=None):
    """Foydalanuvchi tanlagan til (tanlanmagan bo'lsa — o'zbekcha)."""
    return DB["langs"].get(str(uid), "uz") if uid is not None else "uz"


def tx(lang, key, **kw):
    row = T[key]
    s = row[LANG_IDX.get(lang, 0)] or row[0]
    return s.format(**kw) if kw else s


# ============================ TUGMALAR ============================
def lang_kb(arg=""):
    b = InlineKeyboardBuilder()
    for code, label in LANGS.items():
        b.button(text=label, callback_data=f"lang:{code}:{arg}" if arg else f"lang:{code}")
    b.adjust(2)
    return b.as_markup()


def menu_kb(uid=None):
    lang = L(uid)
    b = InlineKeyboardBuilder()
    b.button(text=tx(lang, "b_new"), callback_data="new")
    b.button(text=tx(lang, "b_my"), callback_data="my")
    b.button(text=tx(lang, "b_public"), callback_data="public")
    b.button(text=tx(lang, "b_quick"), callback_data="run:rand")
    b.button(text=tx(lang, "b_profile"), callback_data="profile")
    b.button(text=tx(lang, "b_top"), callback_data="top")
    b.button(text=tx(lang, "b_help"), callback_data="help")
    if BOT_USERNAME:
        b.button(text=tx(lang, "b_addch"),
                 url=f"https://t.me/{BOT_USERNAME}?startchannel=true&admin=post_messages")
        b.button(text=tx(lang, "b_addgr"), url=f"https://t.me/{BOT_USERNAME}?startgroup=true")
    b.button(text=tx(lang, "b_lang"), callback_data="chlang")
    b.adjust(1, 2, 2, 2, 2, 1)
    return b.as_markup()


def list_kb(tests, prefix="view", menu=True, uid=None):
    lang = L(uid)
    b = InlineKeyboardBuilder()
    for t in tests:
        b.button(text=tx(lang, "n_q", title=t["title"], n=len(t["questions"])), callback_data=f"{prefix}:{t['id']}")
    if menu:
        b.button(text=tx(lang, "b_menu"), callback_data="menu")
    b.adjust(1)
    return b.as_markup()


def test_kb(test, user_id):
    lang = L(user_id)
    b = InlineKeyboardBuilder()
    b.button(text=tx(lang, "b_start"), callback_data=f"run:{test['id']}")
    b.button(text=tx(lang, "b_top"), callback_data=f"rate:{test['id']}")
    b.button(text=tx(lang, "b_share"), callback_data=f"share:{test['id']}")
    if BOT_USERNAME:
        b.button(text=tx(lang, "b_group_play"),
                 url=f"https://t.me/{BOT_USERNAME}?startgroup=t_{test['id']}")
    owner = test["owner"] == user_id or is_admin(user_id)
    if owner:
        b.button(text=tx(lang, "b_post"), callback_data=f"post:{test['id']}")
        b.button(text=tx(lang, "b_contest"), callback_data=f"contest:{test['id']}")
        b.button(text=tx(lang, "b_del"), callback_data=f"del:{test['id']}")
    b.button(text=tx(lang, "b_menu"), callback_data="menu")
    b.adjust(2, 2, 2, 2)
    return b.as_markup()


# ============================ HOLATLAR ============================
class New(StatesGroup):
    title = State()
    time = State()
    shuffle = State()
    questions = State()


class Post(StatesGroup):
    channel = State()


class Broadcast(StatesGroup):
    text = State()


# ============================ SAVOLNI O'QISH ============================
OPT_RE = re.compile(r"^\s*(#\s*)?([A-Za-zА-Яа-яЁё])\s*[\)\.]\s*(.*?)\s*$")
NUM_RE = re.compile(r"^\s*(\d{1,3})\s*[\.\)]\s*(.+?)\s*$")


def parse_question(text):
    """Eski format (+ va = belgilari). Xato bo'lsa (None, xato_kaliti) qaytaradi."""
    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
    exp, body = None, []
    for l in lines:
        if l.startswith("?"):
            exp = l[1:].strip()[:200] or None
        else:
            body.append(l)
    if len(body) < 2:
        return None, "e_few"
    q, rest = body[0], body[1:]

    if any(l.startswith("=") for l in rest):  # yozma javobli savol
        if len(q) > 900:
            return None, "e_len900"
        if not all(l.startswith("=") for l in rest):
            return None, "e_text_eq"
        answers = [l[1:].strip() for l in rest if l[1:].strip()]
        if not answers:
            return None, "e_no_ans"
        return {"type": "text", "q": q, "answers": answers, "exp": exp}, None

    if len(rest) < 2:
        return None, "e_two"
    if len(rest) > 10:
        return None, "e_ten"
    if len(q) > 280:
        return None, "e_q280"
    options, correct = [], None
    for l in rest:
        if l.startswith("+"):
            if correct is not None:
                return None, "e_one_correct"
            correct = len(options)
            l = l[1:].strip()
        if not l:
            return None, "e_empty_opt"
        if len(l) > 100:
            return None, "e_opt100"
        options.append(l)
    if correct is None:
        return None, "e_mark"
    return {"type": "quiz", "q": q, "options": options, "correct": correct, "exp": exp}, None


def _new_cur():
    return {"q": [], "opts": [], "correct": [], "exp": None}


def build_bulk_item(cur):
    q = " ".join(cur["q"]).strip()
    if not q:
        return None, "e_noq"
    if len(cur["opts"]) < 2:
        return None, "e_two"
    if len(cur["opts"]) > 10:
        return None, "e_ten"
    if len(q) > 280:
        return None, "e_q280"
    if any(not o for o in cur["opts"]):
        return None, "e_empty_opt"
    if any(len(o) > 100 for o in cur["opts"]):
        return None, "e_opt100"
    if not cur["correct"]:
        return None, "e_hash0"
    if len(cur["correct"]) > 1:
        return None, "e_hash2"
    return {"type": "quiz", "q": q, "options": cur["opts"], "correct": cur["correct"][0],
            "exp": cur["exp"]}, None


def is_lettered(text):
    """Matnda A) B) C) ko'rinishidagi variant qatorlari bormi (ko'p savol formati)."""
    return sum(1 for l in text.split("\n") if OPT_RE.match(l.strip())) >= 2


def parse_bulk(text):
    """Ko'p savol formati: 1. Savol / A) variant / #B) to'g'ri / C) variant # ...
    Qaytaradi: (savollar, [(raqam, xato_kaliti)], savollar_soni)."""
    items, errors = [], []
    st = {"cur": None, "n": 0}

    def finish():
        cur = st["cur"]
        if cur is None:
            return
        st["n"] += 1
        item, err = build_bulk_item(cur)
        if err:
            errors.append((st["n"], err))
        else:
            items.append(item)
        st["cur"] = None

    for raw in text.replace("\r", "").split("\n"):
        line = raw.strip()
        if not line:
            if st["cur"] and st["cur"]["opts"]:
                finish()
            continue
        cur = st["cur"]
        m = OPT_RE.match(line)
        if m:
            if cur is None:
                cur = st["cur"] = _new_cur()
            mark, body = bool(m.group(1)), m.group(3)
            if re.search(r"(^|\s)#$", body):  # oxirdagi # (C# kabi so'zlar belgilanmaydi)
                mark, body = True, body[:-1].rstrip()
            cur["opts"].append(body)
            if mark:
                cur["correct"].append(len(cur["opts"]) - 1)
            continue
        if line.startswith("?") and cur is not None:
            cur["exp"] = line[1:].strip()[:200] or None
            continue
        nm = NUM_RE.match(line)
        if cur is not None and cur["opts"]:
            if nm:  # yangi savol boshlandi
                finish()
                st["cur"] = _new_cur()
                st["cur"]["q"].append(nm.group(2))
            else:  # variantning davomi (uzun qator)
                cur["opts"][-1] = (cur["opts"][-1] + " " + line).strip()
        else:
            if nm and cur is not None and cur["q"]:  # oldingi savolda variant yo'q edi
                finish()
                cur = None
            if cur is None:
                cur = st["cur"] = _new_cur()
            cur["q"].append(nm.group(2) if nm else line)
    finish()
    return items, errors, st["n"]


def parse_many(text):
    """Matndagi barcha savollarni o'qiydi (ko'p savol formati yoki eski format)."""
    if is_lettered(text):
        return parse_bulk(text)
    blocks = [b for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    items, errors = [], []
    for i, blk in enumerate(blocks, 1):
        item, err = parse_question(blk)
        if err:
            errors.append((i, err))
        else:
            items.append(item)
    return items, errors, len(blocks)


def render_errors(lang, errors, many):
    out = []
    for i, key in errors:
        out.append(tx(lang, "err_block", i=i, err=tx(lang, key)) if many else tx(lang, key))
    return out


def decode_bytes(b):
    for enc in ("utf-8-sig", "utf-16", "cp1251"):
        try:
            return b.decode(enc)
        except Exception:
            continue
    return b.decode("latin-1", errors="replace")


# ============================ START / MENYU / YORDAM ============================
def deep_arg(arg):
    """Til tanlash tugmasiga faqat o'zimizning havola parametrlarini olib o'tamiz."""
    return arg if re.fullmatch(r"[tc]_[A-Za-z0-9]{1,12}", arg or "") else ""


async def enter(message: Message, user, arg):
    """Til tanlangach: havola bo'lsa shuni, bo'lmasa imkoniyatlar va bosh menyuni ko'rsatadi."""
    if arg.startswith("t_") and arg[2:] in DB["tests"]:
        await show_test(message, arg[2:], user.id)
        return
    if arg.startswith("c_"):
        await show_contest(message, arg[2:], user.id)
        return
    await message.answer(tx(L(user.id), "welcome", name=esc(user.first_name)), reply_markup=menu_kb(user.id))


@router.message(CommandStart())
async def start(message: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    arg = command.args or ""

    if message.chat.type != "private":  # guruhda
        t = DB["tests"].get(arg[2:]) if arg.startswith("t_") else None
        if t:
            b = InlineKeyboardBuilder()
            b.button(text="▶️ Guruhda boshlash (admin)", callback_data=f"grun:{t['id']}")
            await message.answer(
                f"📋 <b>{esc(t['title'])}</b>\n🖊 {len(t['questions'])} savol · ⏱ {t['time']} soniya\n\n"
                "Boshlash uchun guruh admini tugmani bossin.", reply_markup=b.as_markup())
        else:
            await message.answer("Salom! 👋 Guruhda quiz o'ynash uchun /quiz yozing.\n"
                                 "To'xtatish: /stop")
        return

    DB["users"][str(message.from_user.id)] = message.from_user.full_name
    save_db()
    if str(message.from_user.id) not in DB["langs"]:  # birinchi marta: avval til so'raladi
        await message.answer(LANG_PROMPT, reply_markup=lang_kb(deep_arg(arg)))
        return
    await enter(message, message.from_user, arg)


@router.message(Command("lang"))
async def lang_cmd(message: Message):
    await message.answer(LANG_PROMPT, reply_markup=lang_kb())


@router.callback_query(F.data == "chlang")
async def cb_chlang(cb: CallbackQuery):
    await cb.message.answer(LANG_PROMPT, reply_markup=lang_kb())
    await cb.answer()


@router.callback_query(F.data.startswith("lang:"))
async def cb_lang(cb: CallbackQuery):
    parts = cb.data.split(":", 2)
    code = parts[1] if len(parts) > 1 else ""
    if code not in LANGS:
        await cb.answer()
        return
    DB["langs"][str(cb.from_user.id)] = code
    DB["users"][str(cb.from_user.id)] = cb.from_user.full_name
    save_db()
    await cb.answer("✅")
    await enter(cb.message, cb.from_user, parts[2] if len(parts) > 2 else "")


@router.message(Command("menu"))
async def menu_cmd(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(tx(L(message.from_user.id), "menu_title"), reply_markup=menu_kb(message.from_user.id))


@router.callback_query(F.data == "menu")
async def cb_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.answer(tx(L(cb.from_user.id), "menu_title"), reply_markup=menu_kb(cb.from_user.id))
    await cb.answer()


def help_text(lang):
    return tx(lang, "help_head") + tx(lang, "format_help") + tx(lang, "help_tail")


@router.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(help_text(L(message.from_user.id)), reply_markup=menu_kb(message.from_user.id))


@router.callback_query(F.data == "help")
async def cb_help(cb: CallbackQuery):
    await cb.message.answer(help_text(L(cb.from_user.id)), reply_markup=menu_kb(cb.from_user.id))
    await cb.answer()


@router.message(Command("cancel"))
async def cancel_cmd(message: Message, state: FSMContext):
    lang = L(message.from_user.id)
    await state.clear()
    await message.answer(tx(lang, "cancelled"), reply_markup=ReplyKeyboardRemove())
    await message.answer(tx(lang, "menu_short"), reply_markup=menu_kb(message.from_user.id))


@router.callback_query(F.data == "cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext):
    lang = L(cb.from_user.id)
    await state.clear()
    await cb.message.answer(tx(lang, "cancelled"), reply_markup=ReplyKeyboardRemove())
    await cb.message.answer(tx(lang, "menu_short"), reply_markup=menu_kb(cb.from_user.id))
    await cb.answer()


@router.message(Command("stop"))
async def stop_cmd(message: Message, bot: Bot):
    uid = message.from_user.id
    if message.chat.type == "private":
        lang = L(uid)
        r = running.get(uid)
        if not r:
            await message.answer(tx(lang, "no_running"))
            return
        r["stop"] = True
        if r.get("ev"):
            r["ev"].set()
        await message.answer(tx(lang, "stopping"))
    else:
        g = games.get(message.chat.id)
        if g and await is_chat_admin(bot, message.chat.id, uid):
            g["stop"] = True
            await message.answer("⏹ O'yin keyingi savoldan keyin to'xtatiladi.")


@router.message(Command("quiz"))
async def quiz_cmd(message: Message, bot: Bot):
    if message.chat.type == "private":
        lang = L(message.from_user.id)
        t = build_rand()
        if not t:
            await message.answer(tx(lang, "quick_none"))
            return
        if message.from_user.id in running:
            await message.answer(tx(lang, "busy"))
            return
        launch_private(bot, message.chat.id, message.from_user, t)
        return
    tests = list(DB["tests"].values())[-10:][::-1]
    if not tests:
        await message.answer("Hali testlar yo'q.")
        return
    await message.answer("🎮 <b>Guruh uchun test tanlang</b> (admin bossin):",
                         reply_markup=list_kb(tests, prefix="grun", menu=False))


# ============================ YANGI TEST TUZISH ============================
@router.callback_query(F.data == "new")
async def cb_new(cb: CallbackQuery, state: FSMContext):
    await state.set_state(New.title)
    await cb.message.answer(tx(L(cb.from_user.id), "ask_title"))
    await cb.answer()


@router.message(New.title, F.text, ~F.text.startswith("/"))
async def new_title(message: Message, state: FSMContext):
    lang = L(message.from_user.id)
    await state.update_data(title=message.text.strip()[:60], questions=[])
    b = InlineKeyboardBuilder()
    for s in (10, 15, 20, 30, 45, 60):
        b.button(text=tx(lang, "t_sec", s=s), callback_data=f"time:{s}")
    b.adjust(3)
    await message.answer(tx(lang, "ask_time"), reply_markup=b.as_markup())
    await state.set_state(New.time)


@router.callback_query(New.time, F.data.startswith("time:"))
async def new_time(cb: CallbackQuery, state: FSMContext):
    lang = L(cb.from_user.id)
    await state.update_data(time=int(cb.data.split(":")[1]))
    b = InlineKeyboardBuilder()
    b.button(text=tx(lang, "b_shuf"), callback_data="shuf:1")
    b.button(text=tx(lang, "b_noshuf"), callback_data="shuf:0")
    b.adjust(2)
    await cb.message.answer(tx(lang, "ask_shuffle"), reply_markup=b.as_markup())
    await state.set_state(New.shuffle)
    await cb.answer()


def bulk_kb(lang):
    b = InlineKeyboardBuilder()
    b.button(text=tx(lang, "b_bulk"), callback_data="bulk")
    b.button(text=tx(lang, "b_sample"), callback_data="sample")
    b.adjust(1)
    return b.as_markup()


@router.callback_query(New.shuffle, F.data.startswith("shuf:"))
async def new_shuffle(cb: CallbackQuery, state: FSMContext):
    lang = L(cb.from_user.id)
    await state.update_data(shuffle=cb.data.endswith("1"))
    await state.set_state(New.questions)
    await cb.message.answer(tx(lang, "ask_questions"), reply_markup=poll_reply_kb(lang))
    await cb.message.answer(tx(lang, "bulk_prompt"), reply_markup=bulk_kb(lang))
    await cb.answer()


def poll_reply_kb(lang="uz"):
    """Telegram'ning o'zidagi «Yangi quiz» oynasini ochadigan tugma."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=tx(lang, "poll_btn"), request_poll=KeyboardButtonPollType(type="quiz"))]],
        resize_keyboard=True, is_persistent=True,
        input_field_placeholder=tx(lang, "poll_ph"),
    )


def questions_kb(n, lang="uz"):
    b = InlineKeyboardBuilder()
    b.button(text=tx(lang, "b_done", n=n), callback_data="done")
    b.button(text=tx(lang, "b_bulk"), callback_data="bulk")
    b.button(text=tx(lang, "b_sample"), callback_data="sample")
    b.button(text=tx(lang, "b_undo"), callback_data="undo")
    b.button(text=tx(lang, "b_cancel"), callback_data="cancel")
    b.adjust(1)
    return b.as_markup()


async def add_items(message: Message, state: FSMContext, items, errors, note=""):
    lang = L(message.from_user.id)
    data = await state.get_data()
    qs = data.get("questions", [])
    qs.extend(items)
    await state.update_data(questions=qs)
    text = ""
    if items:
        text += tx(lang, "added", n=len(items), total=len(qs))
        if len(items) == 1:
            it = items[0]
            text += f"\n❓ {esc(it['q'][:150])}\n"
            if it.get("photo"):
                text += tx(lang, "with_photo")
            if qtype(it) == "quiz":
                text += f"✅ {esc(it['options'][it['correct']])}\n"
            else:
                text += f"✍️ {esc(it['answers'][0])}\n"
    if errors:
        text += tx(lang, "errors_head") + "\n".join(f"• {esc(e)}" for e in errors[:10]) + "\n"
        if len(errors) > 10:
            text += tx(lang, "more_n", n=len(errors) - 10) + "\n"
    if note:
        text += f"\nℹ️ {esc(note)}\n"
    text += tx(lang, "next_q")
    await message.answer(text, reply_markup=questions_kb(len(qs), lang))


@router.callback_query(New.questions, F.data == "bulk")
async def cb_bulk(cb: CallbackQuery):
    await cb.message.answer(tx(L(cb.from_user.id), "bulk_info"))
    await cb.answer()


@router.callback_query(New.questions, F.data == "sample")
async def cb_sample(cb: CallbackQuery):
    data = tx(L(cb.from_user.id), "sample_txt").encode("utf-8")
    await cb.message.answer_document(BufferedInputFile(data, filename="questions_sample.txt"))
    await cb.answer()


@router.message(New.questions, F.photo)
async def new_photo_q(message: Message, state: FSMContext):
    lang = L(message.from_user.id)
    if not message.caption:
        await message.answer(tx(lang, "photo_need_caption") + tx(lang, "format_help"))
        return
    items, errors, total = parse_many(message.caption)
    if not items:
        await message.answer("⚠️ " + esc("\n".join(render_errors(lang, errors, total > 1))))
        return
    item = items[0]
    item["photo"] = message.photo[-1].file_id
    await add_items(message, state, [item], [])


def poll_correct_ids(p):
    """Yangi API: correct_option_ids (ro'yxat). Eski API: correct_option_id."""
    ids = getattr(p, "correct_option_ids", None)
    if ids:
        return list(ids)
    one = getattr(p, "correct_option_id", None)
    return [one] if one is not None else []


@router.message(New.questions, F.poll)
async def new_poll_q(message: Message, state: FSMContext):
    lang = L(message.from_user.id)
    p = message.poll
    ids = poll_correct_ids(p)
    if not ids:
        try:
            logging.warning("Poll rad etildi: %s", p.model_dump(exclude_none=True))
        except Exception:
            logging.warning("Poll rad etildi: %r", p)
        await message.answer(tx(lang, "poll_no_correct"))
        return
    note = tx(lang, "note_multi") if len(ids) > 1 else ""
    item = {"type": "quiz", "q": p.question[:280], "options": [o.text[:100] for o in p.options],
            "correct": ids[0], "exp": (p.explanation or "")[:200] or None}
    media = getattr(p, "media", None)
    photos = getattr(media, "photo", None) if media else None
    if photos:
        item["photo"] = photos[-1].file_id
    desc = (getattr(p, "description", None) or "").strip()
    if desc:
        item["desc"] = desc[:1024]
    await add_items(message, state, [item], [], note)


@router.message(New.questions, F.document)
async def new_doc_q(message: Message, state: FSMContext, bot: Bot):
    lang = L(message.from_user.id)
    d = message.document
    if not (d.file_name or "").lower().endswith((".txt", ".md")):
        await message.answer(tx(lang, "doc_type"))
        return
    if d.file_size and d.file_size > 2 * 1024 * 1024:
        await message.answer(tx(lang, "doc_big"))
        return
    try:
        buf = io.BytesIO()
        await bot.download(d, destination=buf)
        text = decode_bytes(buf.getvalue())
    except Exception:
        logging.exception("Fayl o'qilmadi")
        await message.answer(tx(lang, "doc_err"))
        return
    items, errors, total = parse_many(text)
    if not items and not errors:
        await message.answer(tx(lang, "doc_empty"))
        return
    await add_items(message, state, items, render_errors(lang, errors, total > 1))


@router.message(New.questions, F.text, ~F.text.startswith("/"))
async def new_text_q(message: Message, state: FSMContext):
    lang = L(message.from_user.id)
    items, errors, total = parse_many(message.text)
    if not items:
        errs = "\n".join(esc(e) for e in render_errors(lang, errors, total > 1))
        await message.answer("⚠️ " + errs + "\n\n" + tx(lang, "format_help"))
        return
    await add_items(message, state, items, render_errors(lang, errors, total > 1))


@router.callback_query(New.questions, F.data == "undo")
async def cb_undo(cb: CallbackQuery, state: FSMContext):
    lang = L(cb.from_user.id)
    data = await state.get_data()
    qs = data.get("questions", [])
    if not qs:
        await cb.answer(tx(lang, "undo_none"), show_alert=True)
        return
    qs.pop()
    await state.update_data(questions=qs)
    await cb.answer(tx(lang, "undo_ok", n=len(qs)), show_alert=True)


@router.callback_query(New.questions, F.data == "done")
async def cb_done(cb: CallbackQuery, state: FSMContext):
    lang = L(cb.from_user.id)
    data = await state.get_data()
    if not data.get("questions"):
        await cb.answer(tx(lang, "done_none"), show_alert=True)
        return
    tid = secrets.token_hex(4)
    DB["tests"][tid] = {
        "id": tid, "owner": cb.from_user.id, "title": data["title"],
        "time": data["time"], "shuffle": data["shuffle"],
        "questions": data["questions"], "results": {},
    }
    save_db()
    await state.clear()
    await cb.message.answer(tx(lang, "test_ready", title=esc(data["title"])), reply_markup=ReplyKeyboardRemove())
    await show_test(cb.message, tid, cb.from_user.id)
    await cb.answer()


# ============================ TESTLAR RO'YXATI ============================
async def show_test(message: Message, tid, user_id):
    lang = L(user_id)
    t = DB["tests"].get(tid)
    if not t:
        await message.answer(tx(lang, "test_missing"))
        return
    qs = t["questions"]
    n_text = sum(1 for q in qs if qtype(q) == "text")
    n_photo = sum(1 for q in qs if q.get("photo"))
    mode = tx(lang, "m_shuf") if t["shuffle"] else tx(lang, "b_noshuf")
    await message.answer(
        tx(lang, "card", title=esc(t["title"]), n=len(qs), nt=n_text, np=n_photo,
           t=t["time"], mode=mode, k=len(t["results"])),
        reply_markup=test_kb(t, user_id),
    )


@router.message(F.text.regexp(r"^/view_(\w+)"))
async def view_cmd(message: Message):
    tid = message.text.split("_", 1)[1].split()[0]
    await show_test(message, tid, message.from_user.id)


@router.callback_query(F.data == "my")
async def cb_my(cb: CallbackQuery):
    lang = L(cb.from_user.id)
    mine = [t for t in DB["tests"].values() if t["owner"] == cb.from_user.id]
    if not mine:
        await cb.message.answer(tx(lang, "my_none"), reply_markup=menu_kb(cb.from_user.id))
    else:
        await cb.message.answer(tx(lang, "my_head"), reply_markup=list_kb(mine[-20:][::-1], uid=cb.from_user.id))
    await cb.answer()


@router.callback_query(F.data == "public")
async def cb_public(cb: CallbackQuery):
    allt = list(DB["tests"].values())[-20:][::-1]
    await cb.message.answer(tx(L(cb.from_user.id), "pub_head"), reply_markup=list_kb(allt, uid=cb.from_user.id))
    await cb.answer()


@router.callback_query(F.data.startswith("view:"))
async def cb_view(cb: CallbackQuery):
    await show_test(cb.message, cb.data.split(":")[1], cb.from_user.id)
    await cb.answer()


@router.callback_query(F.data.startswith("share:"))
async def cb_share(cb: CallbackQuery):
    tid = cb.data.split(":")[1]
    link = f"https://t.me/{BOT_USERNAME}?start=t_{tid}"
    await cb.message.answer(tx(L(cb.from_user.id), "share_msg", link=link))
    await cb.answer()


@router.callback_query(F.data.startswith("del:"))
async def cb_del(cb: CallbackQuery):
    tid = cb.data.split(":")[1]
    t = DB["tests"].get(tid)
    if t and (t["owner"] == cb.from_user.id or is_admin(cb.from_user.id)):
        del DB["tests"][tid]
        save_db()
        await cb.message.answer(tx(L(cb.from_user.id), "deleted"), reply_markup=menu_kb(cb.from_user.id))
    await cb.answer()


# ============================ REYTING VA PROFIL ============================
@router.callback_query(F.data.startswith("rate:"))
async def cb_rate(cb: CallbackQuery):
    lang = L(cb.from_user.id)
    t = DB["tests"].get(cb.data.split(":")[1])
    if not t or not t["results"]:
        await cb.answer(tx(lang, "rate_none"), show_alert=True)
        return
    top = sorted(t["results"].values(), key=lambda r: r["score"], reverse=True)[:10]
    medals = ["🥇", "🥈", "🥉"]
    text = tx(lang, "rate_head", title=esc(t["title"]))
    for i, r in enumerate(top):
        text += f"{medals[i] if i < 3 else str(i + 1) + '.'} {esc(r['name'])} — {r['score']}/{r['total']}\n"
    await cb.message.answer(text)
    await cb.answer()


def level_name(lang, correct):
    cur, nxt = level_of(correct)
    return tx(lang, f"lv{LEVELS.index(cur)}")


@router.callback_query(F.data == "top")
async def cb_top(cb: CallbackQuery):
    lang = L(cb.from_user.id)
    rows = sorted(DB["stats"].items(), key=lambda x: x[1]["correct"], reverse=True)[:10]
    if not rows:
        await cb.answer(tx(lang, "top_none"), show_alert=True)
        return
    medals = ["🥇", "🥈", "🥉"]
    text = tx(lang, "top_head")
    for i, (uid, s) in enumerate(rows):
        name = DB["users"].get(uid, tx(lang, "anon"))
        text += f"{medals[i] if i < 3 else str(i + 1) + '.'} {esc(name)} — {s['correct']} ({level_name(lang, s['correct'])})\n"
    await cb.message.answer(text)
    await cb.answer()


@router.callback_query(F.data == "profile")
async def cb_profile(cb: CallbackQuery):
    lang = L(cb.from_user.id)
    s = DB["stats"].get(str(cb.from_user.id))
    if not s:
        await cb.message.answer(tx(lang, "prof_none"), reply_markup=menu_kb(cb.from_user.id))
        await cb.answer()
        return
    cur, nxt = level_of(s["correct"])
    acc = round(s["correct"] / s["total"] * 100) if s["total"] else 0
    text = tx(lang, "prof", name=esc(cb.from_user.full_name), level=level_name(lang, s["correct"]),
              correct=s["correct"], acc=acc, tests=s["tests"])
    if nxt:
        need = nxt[0] - s["correct"]
        done = s["correct"] - cur[0]
        span = nxt[0] - cur[0]
        bar = "🟩" * round(done / span * 10) + "⬜" * (10 - round(done / span * 10))
        text += tx(lang, "prof_next", nxt=tx(lang, f"lv{LEVELS.index(nxt)}"), need=need, bar=bar)
    else:
        text += tx(lang, "prof_max")
    await cb.message.answer(text)
    await cb.answer()


# ============================ SAVOL YUBORISH (POLL) ============================
async def send_quiz_poll(bot: Bot, chat_id, question, options, correct, *, anonymous,
                         period=None, explanation=None, photo=None, description=None):
    """Quiz yuboradi. Rasm bo'lsa, u savol bilan BIR xabarda chiqadi (poll media).
    Yangi usul ishlamasa, eski usulga o'tadi: rasm alohida, so'ng quiz."""
    base = dict(type="quiz", is_anonymous=anonymous, explanation=explanation or None)
    if period:
        base["open_period"] = period
    new = dict(base, correct_option_ids=[correct], question_parse_mode=None, explanation_parse_mode=None)
    if photo:
        new["media"] = InputMediaPhoto(media=photo)
    if description:
        new["description"] = description
        new["description_parse_mode"] = None
    try:
        return await bot.send_poll(chat_id, question, options, **new)
    except TelegramRetryAfter:
        raise
    except Exception as e:
        logging.warning("Yangi poll usuli ishlamadi (%s). Eski usulga o'tilmoqda.", e)

    if photo:
        await bot.send_photo(chat_id, photo, caption=esc(description) if description else None)
    elif description:
        await bot.send_message(chat_id, esc(description))
    old = dict(base, correct_option_id=correct)
    try:
        return await bot.send_poll(chat_id, question, options,
                                   question_parse_mode=None, explanation_parse_mode=None, **old)
    except (TypeError, ValueError):
        return await bot.send_poll(chat_id, question, options, **old)


def shuffled(q, do_shuffle):
    opts = list(enumerate(q["options"]))
    if do_shuffle:
        random.shuffle(opts)
    texts = [o for _, o in opts]
    correct = next(k for k, (orig, _) in enumerate(opts) if orig == q["correct"])
    return texts, correct


# ============================ G'OLIB VA O'RINLAR ============================
def ranked_rows(results):
    """results: {uid: {name, score, total, time}} -> eng yaxshisidan boshlab tartiblangan ro'yxat."""
    rows = [{"uid": str(u), **r} for u, r in results.items()]
    rows.sort(key=lambda r: (-r["score"], r.get("time", 0)))
    return rows


def leaderboard_text(title, rows, intro):
    medals = ["🥇", "🥈", "🥉"]
    w = rows[0]
    text = f"{intro}\n\n🏆 <b>{esc(title)}</b>\n\n"
    if w["score"] > 0:
        text += (f"👑 <b>G'olib:</b> <a href=\"tg://user?id={w['uid']}\">{esc(w['name'])}</a> — "
                 f"<b>{w['score']}/{w['total']}</b> · ⏱ {w['time']} s\n")
    else:
        text += "😔 Bu safar to'g'ri javob bergan odam yo'q.\n"
    text += f"👥 <b>Ishtirokchilar:</b> {len(rows)} ta\n\n📋 <b>O'rinlar:</b>\n"
    for i, r in enumerate(rows[:25]):
        mark = medals[i] if i < 3 else f"{i + 1}."
        pct = round(r["score"] / r["total"] * 100) if r["total"] else 0
        text += f"{mark} {esc(r['name'])} — <b>{r['score']}/{r['total']}</b> ({pct}%) · {r['time']} s\n"
    if len(rows) > 25:
        text += f"... va yana {len(rows) - 25} ta ishtirokchi\n"
    if w["score"] > 0:
        text += f"\n🎉 Tabriklaymiz, <b>{esc(w['name'])}</b>!"
    return text


async def celebrate_chat(bot: Bot, chat_id):
    await burst(bot, chat_id, ["✨", "🎉✨🎉", "🎊🎆🎇🎆🎊", "🥳🎈🎉🎈🥳🎁", "🏆🎆🎇🎉🎊🎇🎆🏆"])
    await reward_dice(bot, chat_id)


# ============================ TABRIKLAR ============================
async def burst(bot: Bot, chat_id, frames):
    try:
        m = await bot.send_message(chat_id, frames[0])
        for f in frames[1:]:
            await asyncio.sleep(0.7)
            await m.edit_text(f)
    except Exception:
        pass


async def reward_dice(bot: Bot, chat_id, lang="uz"):
    try:
        await bot.send_message(chat_id, tx(lang, "dice_msg"))
        await bot.send_dice(chat_id, emoji=random.choice(["🎰", "🎯", "🏀", "⚽", "🎳"]))
    except Exception:
        pass


def tier(percent, lang="uz"):
    if percent >= 90:
        return tx(lang, "tier90"), "confetti", ["✨", "🎉✨🎉", "🎊🎆🎇🎆🎊", "🥳🎈🎉🎈🥳🎁", "🏆🎆🎇🎉🎊🎇🎆🏆"]
    if percent >= 70:
        return tx(lang, "tier70"), "fire", ["✨", "🔥✨🔥", "🎉🔥🎊🔥🎉"]
    if percent >= 50:
        return tx(lang, "tier50"), "like", ["👍", "👏👍👏"]
    return tx(lang, "tier0"), "heart", None


async def send_with_effect(bot: Bot, chat_id, text, kb, effect):
    try:
        return await bot.send_message(chat_id, text, reply_markup=kb, message_effect_id=EFFECTS[effect])
    except Exception:
        return await bot.send_message(chat_id, text, reply_markup=kb)


# ============================ TESTNI YECHISH (SHAXSIY CHAT) ============================
@router.poll_answer()
async def on_poll_answer(pa: PollAnswer):
    p = pending.get(pa.poll_id)
    if not p:
        return
    choice = pa.option_ids[0] if pa.option_ids else None
    if p.get("group"):
        if pa.user:
            p["votes"][pa.user.id] = (pa.user.full_name, choice, time.monotonic())
    else:
        p["choice"] = choice
        p["ev"].set()


def launch_private(bot: Bot, chat_id, user, test, contest=None):
    task = asyncio.create_task(run_test(bot, chat_id, user, test, contest))
    tasks.add(task)
    task.add_done_callback(tasks.discard)


@router.callback_query(F.data.startswith("run:"))
async def cb_run(cb: CallbackQuery, bot: Bot):
    lang = L(cb.from_user.id)
    if cb.message.chat.type != "private":
        await cb.answer(tx(lang, "only_private"), show_alert=True)
        return
    t = get_test(cb.data.split(":")[1])
    if not t:
        await cb.answer(tx(lang, "test_nf"), show_alert=True)
        return
    if cb.from_user.id in running:
        await cb.answer(tx(lang, "busy"), show_alert=True)
        return
    await cb.answer()
    launch_private(bot, cb.message.chat.id, cb.from_user, t)


async def run_test(bot: Bot, chat_id, user, test, contest=None):
    uid = user.id
    lang = L(uid)
    running[uid] = {"stop": False, "ev": None, "poll": None}
    qs = list(test["questions"])
    if test["shuffle"]:
        random.shuffle(qs)
    total = len(qs)
    await bot.send_message(chat_id, tx(lang, "run_intro", title=esc(test["title"]), total=total,
                                       time=test["time"]))
    await asyncio.sleep(2)
    score = answered = skipped_row = 0
    mistakes = []
    marks = []
    ctime = 0.0
    try:
        for i, q in enumerate(qs, 1):
            if running[uid]["stop"]:
                break
            if qtype(q) == "quiz":
                texts, correct = shuffled(q, test["shuffle"])
                msg = await send_quiz_poll(bot, chat_id, f"{i}/{total}. {q['q']}"[:300], texts, correct,
                                           anonymous=False, period=test["time"], explanation=q.get("exp"),
                                           photo=q.get("photo"), description=q.get("desc"))
                t_sent = time.monotonic()
                ev = asyncio.Event()
                pending[msg.poll.id] = {"ev": ev, "choice": None}
                running[uid]["ev"] = ev
                try:
                    await asyncio.wait_for(ev.wait(), test["time"] + 2)
                except asyncio.TimeoutError:
                    pass
                choice = pending.pop(msg.poll.id)["choice"]
                dt = time.monotonic() - t_sent
                if running[uid]["stop"]:
                    try:
                        await bot.stop_poll(chat_id, msg.message_id)
                    except Exception:
                        pass
                    break
                if choice is None:
                    skipped_row += 1
                    marks.append("⬜")
                    mistakes.append((q["q"], q["options"][q["correct"]]))
                else:
                    skipped_row = 0
                    answered += 1
                    ok = choice == correct
                    if ok:
                        score += 1
                    else:
                        mistakes.append((q["q"], q["options"][q["correct"]]))
                    marks.append("🟩" if ok else "🟥")
                    if ok:
                        ctime += dt
                    await asyncio.sleep(1.5)
            else:  # yozma javobli savol
                wt = max(test["time"], 30)
                cap = tx(lang, "run_text_cap", i=i, total=total, q=esc(q["q"]), wt=wt)
                if q.get("photo"):
                    await bot.send_photo(chat_id, q["photo"], caption=cap)
                else:
                    await bot.send_message(chat_id, cap)
                t_sent = time.monotonic()
                w = {"ev": asyncio.Event(), "text": None}
                text_waiters[uid] = w
                running[uid]["ev"] = w["ev"]
                try:
                    await asyncio.wait_for(w["ev"].wait(), wt)
                except asyncio.TimeoutError:
                    pass
                text_waiters.pop(uid, None)
                if running[uid]["stop"]:
                    break
                right = q["answers"][0]
                exp = f"\n💡 {esc(q['exp'])}" if q.get("exp") else ""
                if w["text"] is None:
                    skipped_row += 1
                    marks.append("⬜")
                    mistakes.append((q["q"], right))
                    await bot.send_message(chat_id, tx(lang, "time_up", right=esc(right), exp=exp))
                else:
                    skipped_row = 0
                    answered += 1
                    ok = norm(w["text"]) in {norm(a) for a in q["answers"]}
                    if ok:
                        score += 1
                        ctime += time.monotonic() - t_sent
                        marks.append("🟩")
                        await bot.send_message(chat_id, tx(lang, "right", exp=exp))
                    else:
                        marks.append("🟥")
                        mistakes.append((q["q"], right))
                        await bot.send_message(chat_id, tx(lang, "wrong", right=esc(right), exp=exp))
                    await asyncio.sleep(1.5)
            if skipped_row >= 2:
                await bot.send_message(chat_id, tx(lang, "sleepy"))
                break
    except Exception:
        logging.exception("run_test xatosi")
        await bot.send_message(chat_id, tx(lang, "run_err"))
    finally:
        text_waiters.pop(uid, None)
        stopped = running.pop(uid, {}).get("stop")

    percent = round(score / total * 100) if total else 0
    if answered and test["id"] != "rand":
        old = test["results"].get(str(uid))
        if not old or score > old["score"]:
            test["results"][str(uid)] = {"name": user.full_name, "score": score, "total": total}
    if answered:
        s = DB["stats"].setdefault(str(uid), {"correct": 0, "total": 0, "tests": 0})
        s["correct"] += score
        s["total"] += answered
        s["tests"] += 1
        DB["users"][str(uid)] = user.full_name
    save_db()

    contest_note = ""
    if contest is not None and answered:
        contest["results"][str(uid)] = {"name": user.full_name, "score": score,
                                        "total": total, "time": round(ctime, 1)}
        save_db()
        rows = ranked_rows(contest["results"])
        place = next(i for i, r in enumerate(rows, 1) if r["uid"] == str(uid))
        contest_note = "\n\n" + tx(lang, "place_note", place=place, n=len(rows))

    title, effect, frames = tier(percent, lang)
    head = tx(lang, "res_stopped") if stopped else tx(lang, "res_done")
    text = head + tx(lang, "res_body", score=score, total=total, percent=percent,
                     marks="".join(marks), title=title, note=contest_note)
    if mistakes:
        text += "\n\n" + tx(lang, "mistakes_head") + "\n"
        for q_text, right in mistakes[:5]:
            text += f"• {esc(q_text[:70])} → <b>{esc(right)}</b>\n"
        if len(mistakes) > 5:
            text += tx(lang, "more_n", n=len(mistakes) - 5)
    b = InlineKeyboardBuilder()
    b.button(text=tx(lang, "b_again"), callback_data=f"run:{test['id']}")
    if test["id"] != "rand":
        b.button(text=tx(lang, "b_top"), callback_data=f"rate:{test['id']}")
    b.button(text=tx(lang, "b_menu"), callback_data="menu")
    b.adjust(2, 1)
    m = await send_with_effect(bot, chat_id, text, b.as_markup(), effect)
    if percent >= 70 and ReactionTypeEmoji:
        try:
            await bot.set_message_reaction(chat_id, m.message_id, [ReactionTypeEmoji(emoji="🎉")])
        except Exception:
            pass
    if frames and not stopped:
        await burst(bot, chat_id, frames)
    if percent >= 90 and not stopped:
        await reward_dice(bot, chat_id, lang)


# ============================ GURUHDA O'YNASH ============================
@router.callback_query(F.data.startswith("grun:"))
async def cb_grun(cb: CallbackQuery, bot: Bot):
    chat = cb.message.chat
    if chat.type == "private":
        await cb.answer("Bu tugma guruh uchun. Botni guruhga qo'shing.", show_alert=True)
        return
    if not await is_chat_admin(bot, chat.id, cb.from_user.id):
        await cb.answer("Faqat guruh adminlari boshlay oladi.", show_alert=True)
        return
    if chat.id in games:
        await cb.answer("Guruhda o'yin ketyapti. To'xtatish: /stop", show_alert=True)
        return
    t = get_test(cb.data.split(":")[1])
    if not t:
        await cb.answer("Test topilmadi", show_alert=True)
        return
    await cb.answer()
    task = asyncio.create_task(run_group(bot, chat.id, t))
    tasks.add(task)
    task.add_done_callback(tasks.discard)


async def run_group(bot: Bot, chat_id, test):
    qs = [q for q in test["questions"] if qtype(q) == "quiz"]
    if not qs:
        await bot.send_message(chat_id, "Bu testda guruh uchun mos (variantli) savollar yo'q.")
        return
    if test["shuffle"]:
        random.shuffle(qs)
    games[chat_id] = {"stop": False}
    scores = {}
    total = len(qs)
    try:
        await bot.send_message(chat_id, f"🎮 <b>{esc(test['title'])}</b> — guruh o'yini boshlandi!\n"
                                        f"{total} savol · har biriga {test['time']} soniya\n"
                                        "Hamma qatnashishi mumkin. To'xtatish: /stop (admin)")
        await asyncio.sleep(3)
        for i, q in enumerate(qs, 1):
            if games[chat_id]["stop"]:
                break
            texts, correct = shuffled(q, test["shuffle"])
            msg = await send_quiz_poll(bot, chat_id, f"{i}/{total}. {q['q']}"[:300], texts, correct,
                                       anonymous=False, period=test["time"], explanation=q.get("exp"),
                                       photo=q.get("photo"), description=q.get("desc"))
            t0 = time.monotonic()
            pending[msg.poll.id] = {"group": True, "votes": {}}
            await asyncio.sleep(test["time"] + 1.5)
            votes = pending.pop(msg.poll.id)["votes"]
            for uid, (name, ch, tv) in votes.items():
                if ch is None:
                    continue
                row = scores.setdefault(uid, [name, 0, 0.0])
                if ch == correct:
                    row[1] += 1
                    row[2] += max(tv - t0, 0)
    except Exception:
        logging.exception("run_group xatosi")
    finally:
        games.pop(chat_id, None)

    if not scores:
        await bot.send_message(chat_id, "🏁 O'yin tugadi. Hech kim qatnashmadi 😔")
        return
    rows = [{"uid": str(u), "name": n, "score": c, "total": total, "time": round(t, 1)}
            for u, (n, c, t) in scores.items()]
    rows.sort(key=lambda r: (-r["score"], r["time"]))
    await bot.send_message(chat_id, leaderboard_text(test["title"], rows, "🏁 <b>O'yin tugadi!</b>"))
    if rows[0]["score"] > 0:
        await celebrate_chat(bot, chat_id)


# ============================ KANALGA YUBORISH ============================
def parse_channel(text):
    t = text.strip()
    if "t.me/" in t:
        parts = t.split("t.me/")[1].split("?")[0].strip("/").split("/")
        if parts[0] == "c" and len(parts) > 1 and parts[1].isdigit():
            return int("-100" + parts[1])
        t = "@" + parts[0]
    if re.fullmatch(r"-?\d+", t):
        return int(t)
    return t if t.startswith("@") else "@" + t


@router.callback_query(F.data.regexp(r"^(post|contest):"))
async def cb_post(cb: CallbackQuery, state: FSMContext):
    mode, tid = cb.data.split(":")
    t = DB["tests"].get(tid)
    if not t or not (t["owner"] == cb.from_user.id or is_admin(cb.from_user.id)):
        await cb.answer("Ruxsat yo'q", show_alert=True)
        return
    await state.set_state(Post.channel)
    await state.update_data(test_id=tid, mode=mode)
    b = InlineKeyboardBuilder()
    last = DB["channels"].get(str(cb.from_user.id))
    if last:
        b.button(text=f"📣 Oxirgisi: {last}", callback_data="postlast")
    if BOT_USERNAME:
        b.button(text="➕ Botni kanalga qo'shish",
                 url=f"https://t.me/{BOT_USERNAME}?startchannel=true&admin=post_messages")
    b.button(text="❌ Bekor qilish", callback_data="cancel")
    b.adjust(1)
    if mode == "contest":
        head = ("🏆 <b>Kanalda musobaqa</b>\n\n"
                "Kanalga «Ishtirok etish» tugmali e'lon chiqadi. Odamlar botda testni yechadi, "
                "oxirida siz natijani yakunlaysiz va kanalga <b>g'olib, ishtirokchilar va o'rinlar</b> "
                "e'lon qilinadi.\n\n")
        tail = ""
    else:
        head = "📣 <b>Kanalga yuborish</b>\n\n"
        tail = ("\n\nSavollar kanalga anonim quiz ko'rinishida chiqadi. Kanal so'rovlari anonim bo'lgani uchun "
                "g'olibni aniqlab bo'lmaydi. G'olib kerak bo'lsa, «🏆 Kanalda musobaqa» ni tanlang.")
    await cb.message.answer(
        head +
        "1) Botni kanalga <b>admin</b> qiling (xabar yuborish huquqi bilan)\n"
        "2) Kanal username'ini yuboring, masalan: <code>@kanalim</code>\n"
        "    yoki kanaldan istalgan postni shu yerga forward qiling." + tail,
        reply_markup=b.as_markup())
    await cb.answer()


@router.callback_query(Post.channel, F.data == "postlast")
async def cb_postlast(cb: CallbackQuery, state: FSMContext, bot: Bot):
    last = DB["channels"].get(str(cb.from_user.id))
    data = await state.get_data()
    await cb.answer()
    if not last:
        await cb.message.answer("Oxirgi kanal topilmadi. Username yuboring.")
        return
    ch = int(last) if re.fullmatch(r"-?\d+", last) else last
    await go_channel(bot, cb.message, cb.from_user.id, ch, data, state)


@router.message(Post.channel)
async def post_channel(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    ch = None
    fo = getattr(message, "forward_origin", None)
    if fo is not None and getattr(fo, "chat", None):
        ch = fo.chat.id
    elif message.text and not message.text.startswith("/"):
        ch = parse_channel(message.text)
    if ch is None:
        await message.answer("⚠️ Kanal username'ini (@kanalim) yoki forward qilingan post yuboring.")
        return
    await go_channel(bot, message, message.from_user.id, ch, data, state)


async def go_channel(bot: Bot, message: Message, uid, ch, data, state: FSMContext):
    if data.get("mode") == "contest":
        await start_contest(bot, message, uid, ch, data.get("test_id"), state)
    else:
        await do_post(bot, message, uid, ch, data.get("test_id"), state)


async def start_contest(bot: Bot, message: Message, uid, channel, tid, state: FSMContext):
    t = DB["tests"].get(tid)
    await state.clear()
    if not t:
        await message.answer("Test topilmadi.")
        return
    cid = secrets.token_hex(3)
    b = InlineKeyboardBuilder()
    b.button(text="▶️ Ishtirok etish", url=f"https://t.me/{BOT_USERNAME}?start=c_{cid}")
    try:
        m = await bot.send_message(
            channel,
            f"🏆 <b>{esc(t['title'])}</b> — musobaqa!\n\n"
            f"🖊 {len(t['questions'])} savol · ⏱ har biriga {t['time']} soniya\n\n"
            "Ishtirok etish uchun tugmani bosing 👇\n"
            "🥇 G'olib va barcha ishtirokchilar natijasi shu yerda e'lon qilinadi.",
            reply_markup=b.as_markup())
    except Exception as e:
        await message.answer(f"❌ Kanalga yuborib bo'lmadi.\n<code>{esc(e)}</code>\n"
                             "Bot kanalda admin ekanini tekshiring.")
        return
    DB["contests"][cid] = {"id": cid, "test": tid, "channel": channel, "owner": uid,
                           "results": {}, "closed": False, "msg": m.message_id}
    DB["channels"][str(uid)] = str(channel)
    save_db()
    kb = InlineKeyboardBuilder()
    kb.button(text="👥 Hozirgi holat", callback_data=f"cinfo:{cid}")
    kb.button(text="🏁 Yakunlash va e'lon qilish", callback_data=f"cfin:{cid}")
    kb.adjust(1)
    await message.answer("✅ Musobaqa kanalda e'lon qilindi!\n\n"
                         "Vaqt tugagach «🏁 Yakunlash» ni bosing: bot g'olib va o'rinlarni kanalga yuboradi. "
                         "Musobaqalaringiz ro'yxati: /contests", reply_markup=kb.as_markup())


def can_manage(c, uid):
    return c and (c["owner"] == uid or is_admin(uid))


def contest_rows(c):
    return ranked_rows(c["results"])


@router.message(Command("contests"))
async def contests_cmd(message: Message):
    mine = [c for c in DB["contests"].values()
            if not c["closed"] and (c["owner"] == message.from_user.id or is_admin(message.from_user.id))]
    if not mine:
        await message.answer("Ochiq musobaqalar yo'q. Test kartasidagi «🏆 Kanalda musobaqa» orqali oching.")
        return
    b = InlineKeyboardBuilder()
    for c in mine[-15:]:
        t = DB["tests"].get(c["test"])
        b.button(text=f"{t['title'] if t else 'Test'} · {len(c['results'])} kishi", callback_data=f"cinfo:{c['id']}")
    b.adjust(1)
    await message.answer("🏆 <b>Ochiq musobaqalar:</b>", reply_markup=b.as_markup())


@router.callback_query(F.data.startswith("cinfo:"))
async def cb_cinfo(cb: CallbackQuery):
    c = DB["contests"].get(cb.data.split(":")[1])
    if not can_manage(c, cb.from_user.id):
        await cb.answer("Ruxsat yo'q", show_alert=True)
        return
    t = DB["tests"].get(c["test"])
    if not c["results"]:
        await cb.answer("Hali hech kim qatnashmagan.", show_alert=True)
        return
    kb = InlineKeyboardBuilder()
    if not c["closed"]:
        kb.button(text="🏁 Yakunlash va e'lon qilish", callback_data=f"cfin:{c['id']}")
    await cb.message.answer(leaderboard_text(t["title"] if t else "Musobaqa", contest_rows(c), "📊 <b>Hozirgi holat</b>"),
                            reply_markup=kb.as_markup())
    await cb.answer()


@router.callback_query(F.data.startswith("cfin:"))
async def cb_cfin(cb: CallbackQuery, bot: Bot):
    c = DB["contests"].get(cb.data.split(":")[1])
    if not can_manage(c, cb.from_user.id):
        await cb.answer("Ruxsat yo'q", show_alert=True)
        return
    if c["closed"]:
        await cb.answer("Bu musobaqa allaqachon yakunlangan.", show_alert=True)
        return
    if not c["results"]:
        await cb.answer("Hali hech kim qatnashmagan.", show_alert=True)
        return
    t = DB["tests"].get(c["test"])
    text = leaderboard_text(t["title"] if t else "Musobaqa", contest_rows(c), "🏁 <b>Musobaqa yakunlandi!</b>")
    try:
        await bot.send_message(c["channel"], text)
    except Exception as e:
        await cb.message.answer(f"❌ Kanalga yuborib bo'lmadi.\n<code>{esc(e)}</code>")
        await cb.answer()
        return
    c["closed"] = True
    save_db()
    try:
        await bot.edit_message_reply_markup(chat_id=c["channel"], message_id=c["msg"], reply_markup=None)
    except Exception:
        pass
    await cb.message.answer("🏁 Natijalar kanalga e'lon qilindi!")
    await cb.answer()
    if contest_rows(c)[0]["score"] > 0:
        await celebrate_chat(bot, c["channel"])


async def show_contest(message: Message, cid, uid):
    lang = L(uid)
    c = DB["contests"].get(cid)
    t = DB["tests"].get(c["test"]) if c else None
    if not c or not t:
        await message.answer(tx(lang, "c_notfound"))
        return
    if c["closed"]:
        await message.answer(tx(lang, "c_closed"))
        return
    mine = c["results"].get(str(uid))
    if mine:
        await message.answer(tx(lang, "c_already", s=mine["score"], t=mine["total"]))
        return
    b = InlineKeyboardBuilder()
    b.button(text=tx(lang, "b_start"), callback_data=f"crun:{cid}")
    await message.answer(tx(lang, "c_card", title=esc(t["title"]), n=len(t["questions"]), t=t["time"]),
                         reply_markup=b.as_markup())


@router.callback_query(F.data.startswith("crun:"))
async def cb_crun(cb: CallbackQuery, bot: Bot):
    lang = L(cb.from_user.id)
    if cb.message.chat.type != "private":
        await cb.answer(tx(lang, "only_private"), show_alert=True)
        return
    c = DB["contests"].get(cb.data.split(":")[1])
    t = DB["tests"].get(c["test"]) if c else None
    if not c or not t or c["closed"]:
        await cb.answer(tx(lang, "c_alert_closed"), show_alert=True)
        return
    if str(cb.from_user.id) in c["results"]:
        await cb.answer(tx(lang, "c_alert_already"), show_alert=True)
        return
    if cb.from_user.id in running:
        await cb.answer(tx(lang, "busy"), show_alert=True)
        return
    await cb.answer()
    launch_private(bot, cb.message.chat.id, cb.from_user, t, contest=c)


async def do_post(bot: Bot, message: Message, uid, channel, tid, state: FSMContext):
    t = DB["tests"].get(tid)
    await state.clear()
    if not t:
        await message.answer("Test topilmadi.")
        return
    try:
        await bot.get_chat(channel)
    except Exception as e:
        await message.answer(f"❌ Kanal topilmadi yoki bot unga kira olmayapti.\n<code>{esc(e)}</code>")
        return
    await message.answer("⏳ Kanalga yuborilmoqda (har savol orasida 3 soniya)...")
    sent = skipped = 0
    err = None
    for q in t["questions"]:
        if qtype(q) != "quiz":
            skipped += 1
            continue
        for attempt in range(2):
            try:
                texts, correct = shuffled(q, t["shuffle"])
                await send_quiz_poll(bot, channel, q["q"][:300], texts, correct,
                                     anonymous=True, explanation=q.get("exp"),
                                     photo=q.get("photo"), description=q.get("desc"))
                sent += 1
                err = None
                break
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except Exception as e:
                err = str(e)
                break
        if err:
            break
        await asyncio.sleep(3)
    if sent:
        DB["channels"][str(uid)] = str(channel)
        save_db()
    text = f"✅ Kanalga yuborildi: <b>{sent}</b> ta quiz."
    if skipped:
        text += f"\n✍️ {skipped} ta yozma savol kanalga yuborilmadi (faqat variantli savollar chiqadi)."
    if err:
        text += (f"\n\n❌ Xatolik: <code>{esc(err)}</code>\n"
                 "Bot kanalda admin ekanini va xabar yuborish huquqi borligini tekshiring.")
    await message.answer(text, reply_markup=menu_kb(uid))


# ============================ ADMIN PANEL ============================
@router.message(Command("admin"))
async def admin_cmd(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Bu buyruq faqat admin uchun.")
        return
    b = InlineKeyboardBuilder()
    b.button(text="📊 Statistika", callback_data="a_stat")
    b.button(text="📚 Barcha testlar", callback_data="a_tests")
    b.button(text="📢 Hammaga xabar yuborish", callback_data="a_cast")
    b.adjust(1)
    await message.answer("🛠 <b>Admin panel</b>", reply_markup=b.as_markup())


@router.callback_query(F.data == "a_stat")
async def a_stat(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("Ruxsat yo'q", show_alert=True)
        return
    solved = sum(len(t["results"]) for t in DB["tests"].values())
    qn = sum(len(t["questions"]) for t in DB["tests"].values())
    await cb.message.answer(
        "📊 <b>Statistika</b>\n\n"
        f"👥 Foydalanuvchilar: {len(DB['users'])}\n"
        f"📚 Testlar: {len(DB['tests'])}\n"
        f"🖊 Savollar: {qn}\n"
        f"✅ Yechilgan natijalar: {solved}")
    await cb.answer()


@router.callback_query(F.data == "a_tests")
async def a_tests(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("Ruxsat yo'q", show_alert=True)
        return
    allt = list(DB["tests"].values())[-30:][::-1]
    await cb.message.answer("📚 <b>Barcha testlar</b> (ochib, o'chirish mumkin):", reply_markup=list_kb(allt))
    await cb.answer()


@router.callback_query(F.data == "a_cast")
async def a_cast(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("Ruxsat yo'q", show_alert=True)
        return
    await state.set_state(Broadcast.text)
    await cb.message.answer("📢 Yuboriladigan xabarni yozing (matn, rasm yoki video). Bekor qilish: /cancel")
    await cb.answer()


@router.message(Broadcast.text)
async def do_cast(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user.id) or (message.text and message.text.startswith("/")):
        return
    await state.clear()
    ok = fail = 0
    for uid in list(DB["users"]):
        try:
            await bot.copy_message(int(uid), message.chat.id, message.message_id)
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await message.answer(f"✅ Yuborildi: {ok}\n❌ Yetmadi: {fail}")


# ============================ OXIRGI: YOZMA JAVOBLAR / BOSHQA MATNLAR ============================
@router.message(F.poll, F.chat.type == "private")
async def stray_poll(message: Message):
    await message.answer(tx(L(message.from_user.id), "stray_poll"), reply_markup=ReplyKeyboardRemove())


@router.message(F.text, F.chat.type == "private")
async def fallback_text(message: Message):
    if message.text.startswith("/"):
        return
    w = text_waiters.get(message.from_user.id)
    if w and w["text"] is None:
        w["text"] = message.text
        w["ev"].set()
        return
    await message.answer(tx(L(message.from_user.id), "fallback"), reply_markup=menu_kb(message.from_user.id))


# ============================ ISHGA TUSHIRISH ============================
async def main():
    global BOT_USERNAME
    if not TOKEN:
        raise SystemExit("TOKEN topilmadi! BOT_TOKEN o'zgaruvchisini yoki app.py yonidagi token.txt faylini tekshiring.")
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    dp.include_router(router)
    me = await bot.get_me()
    BOT_USERNAME = me.username
    await bot.set_my_commands([
        BotCommand(command="start", description="Bosh menyu"),
        BotCommand(command="quiz", description="Tezkor quiz / guruhda test tanlash"),
        BotCommand(command="stop", description="Testni to'xtatish"),
        BotCommand(command="contests", description="Kanal musobaqalarim"),
        BotCommand(command="cancel", description="Bekor qilish"),
        BotCommand(command="lang", description="Til / Dil / Language / Язык"),
        BotCommand(command="help", description="Yordam"),
    ])
    await bot.delete_webhook(drop_pending_updates=True)
    print(f"✅ Bot ishga tushdi: @{BOT_USERNAME}")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
