# 🤖 Quiz Bot (Telegram) — fps.ms da 24/7 ishlatish

Test (quiz) tuzish va yechish boti: rasmli savollar, yozma javobli savollar,
ko'p savol yuklash (matn yoki .txt), 4 til (🇺🇿 🇹🇷 🇬🇧 🇷🇺), guruh va kanal musobaqalari, reyting.

## 📁 Fayllar

| Fayl | Nima uchun |
|---|---|
| `app.py` | Botning o'zi (fps.ms aynan shu nomni ishga tushiradi) |
| `requirements.txt` | Kerakli kutubxona (`aiogram`) — server o'zi o'rnatadi |
| `.gitignore` | Token va `data.json` GitHub'ga tushib qolmasligi uchun |
| `.env.example` | Token qanday nomda yozilishini ko'rsatuvchi namuna |
| `README.md` | Shu yo'riqnoma |

> ⚠️ **Token (BotFather bergan uzun kod) hech qachon GitHub'ga yoki `app.py` ichiga yozilmaydi.**
> Token faqat fps.ms panelidagi **Environment** bo'limiga yoziladi.

---

## 1-QADAM. Yangi token oling (xavfsizlik uchun)

Eski token chatlarda ochiq ko'rinib qolgan bo'lishi mumkin.

1. Telegram'da **@BotFather** ga kiring
2. `/revoke` yozing → botni tanlang → yangi token keladi
3. Uni nusxalab qo'ying (keyingi qadamlarda kerak bo'ladi)

## 2-QADAM. GitHub'ga yuklash

1. https://github.com → **New repository** → nom: `quizbot` → **Private** tanlang → **Create**
2. **uploading an existing file** havolasini bosing
3. Shu 4 ta faylni sudrab tashlang: `app.py`, `requirements.txt`, `.gitignore`, `.env.example` (+ `README.md`)
4. **Commit changes** ni bosing

✅ Tayyor. Tokeningiz GitHub'da yo'q.

## 3-QADAM. fps.ms da serverni ochish

1. https://panel.fps.ms/auth/register — ro'yxatdan o'ting
2. https://panel.fps.ms/deploy → **Containers** → **Python** ni tanlang (bitta konteyner bepul)
3. Server ochilgach **Manage** ni bosing

### 3.1 Sozlamalar (**Startup** tabi; ko'rinmasa **More** → Startup)
- **App py file**: `app.py`
- **Runtime Version**: **3.12** yoki undan yuqori (aiogram uchun kamida 3.10 kerak)

### 3.2 Token (**Environment** tabi; ko'rinmasa **More** → Environment)
1. **Add variable**
2. Name: `BOT_TOKEN`
3. Value: yangi tokeningiz
4. **Add**

(Ixtiyoriy) `ADMIN_ID` — admin Telegram ID. Yozmasangiz kodda yozilgan ID ishlatiladi.

### 3.3 Fayllarni yuklash
**Bepul** serverda GitHub'dan avtomatik yuklash yo'q (u pullik tarifda), shuning uchun:
1. **Files** tabini oching
2. `app.py` va `requirements.txt` ni sudrab tashlang
   (yoki SFTP orqali: https://fps.ms/docs/getting-started/connecting-to-sftp)

### 3.4 Ishga tushirish
1. **Console** tabiga o'ting → **Start**
2. Birinchi marta kutubxona o'rnatiladi (1-2 daqiqa)
3. Konsolda `✅ Bot ishga tushdi: @sizning_botingiz` chiqsa — bot ishlayapti 🎉
4. Telegram'da botga `/start` yozing

## 🔁 GitHub'dan avtomatik yangilash (pullik tarif)

Pullik serverda **Deployments** tabida GitHub'ni ulash mumkin:
1. Akkaunt menyusi → **More** → **GitHub** → **Connect GitHub**
2. Server → **More** → **Deployments** → **Connect repository** → repo va `main` branch
3. Endi GitHub'ga har yangi fayl yuklaganingizda bot o'zi yangilanadi

Batafsil: https://fps.ms/docs/containers/deploy-from-github

## ⏰ Bepul serverni uzaytirish

Bepul serverlarda **Expiry Timer** (tugash vaqti) bo'lishi mumkin. Soat tugasa server to'xtaydi (suspended).
Console sahifasidagi **+ Add XX Hours** tugmasi bilan uzaytiring.
To'xtagan serverdagi fayllar 28 kun saqlanadi. Batafsil: https://fps.ms/docs/getting-started/how-server-renewal-works

## 💾 Ma'lumotlar

Testlar, natijalar va foydalanuvchilar `data.json` faylida saqlanadi (bot o'zi yaratadi).
Muhim testlaringiz bo'lsa, **Files** tabidan `data.json` ni vaqti-vaqti bilan yuklab olib, zaxira qiling.

## 🛠 Muammolar

| Xato | Sabab | Yechim |
|---|---|---|
| `can't open file app.py` | Fayl nomi boshqacha | Fayl nomi `app.py` bo'lsin yoki Startup'da nomini o'zgartiring |
| `ModuleNotFoundError: aiogram` | `requirements.txt` yuklanmagan | Uni ham Files'ga yuklang |
| `TOKEN topilmadi!` | `BOT_TOKEN` yozilmagan | Environment tabida qo'shing, so'ng qayta ishga tushiring |
| `Unauthorized` | Token noto'g'ri / eskirgan | BotFather'dan yangi token oling va yangilang |
| `Conflict: terminated by other getUpdates` | Bot boshqa joyda ham ishlayapti | Kompyuterdagi botni o'chiring (bitta token — bitta nusxa) |
| Bot jim | Konsolda xato bor | **Console** tabidagi oxirgi qatorlarni o'qing |

## 💻 Kompyuterda sinash

```
pip install -r requirements.txt
```
`app.py` yonida `token.txt` oching va ichiga faqat tokenni yozing (u `.gitignore` da, GitHub'ga ketmaydi), keyin:
```
python app.py
```
