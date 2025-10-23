import os
import json
import re
import google.generativeai as genai
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from dotenv import load_dotenv

# === Завантаження токенів ===
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")

# === Дані чату ===
chat_data = {}  # chat_id -> {history, category, test}

# === Клавіатура з українськими кнопками та перекладом англійською ===
main_keyboard = [
    ["🎓 Навчання / Learning", "🌍 Переклад / Translation"],
    ["💻 Програмування / Programming", "🎭 Розваги / Fun"],
    ["📘 Тест з української мови / Ukrainian Test"],
    ["🧠 Новий діалог / New Chat", "❌ Стоп / Stop"]
]
reply_markup = ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True)

# === Очистка тексту від спецсимволів ===
def clean_text(text: str) -> str:
    """
    Убирає *, _, ~, `, #, лишні пробіли, залишає емодзі.
    """
    text = re.sub(r'[*_~`#]', '', text)
    return text.strip()

# === Відправка довгого повідомлення по частинах ===
async def send_long_message(update, text):
    MAX_LEN = 4000
    text = clean_text(text)
    for i in range(0, len(text), MAX_LEN):
        await update.message.reply_text(text[i:i+MAX_LEN])

# === Генерація тесту з української мови ===
def generate_ukrainian_test():
    prompt = (
        "Створи тест з української мови з 3 запитань. "
        "Для кожного питання дай 4 варіанти відповіді, познач правильний. "
        "Видай у форматі JSON: [{\"question\":\"...\", \"options\":[\"...\",\"...\",\"...\",\"...\"], \"answer\":\"...\"}]"
    )
    try:
        response = model.generate_content(prompt)
        text = response.text.strip()
        start = text.find("[")
        end = text.rfind("]") + 1
        json_text = text[start:end]
        test = json.loads(json_text)
        for q in test:
            if "question" not in q or "options" not in q or "answer" not in q:
                raise ValueError("Некоректний формат")
        return test
    except Exception as e:
        print("Fallback тест:", e)
        return [
            {"question": "Як перекласти слово 'місяць'?", "options": ["Sun", "Moon", "Star", "Sky"], "answer": "Moon"},
            {"question": "Переклади слово 'сонце'", "options": ["Sun", "Moon", "Star", "Sky"], "answer": "Sun"},
            {"question": "Переклади 'вода'", "options": ["Water", "Fire", "Earth", "Air"], "answer": "Water"}
        ]

# === Генерація відповіді від Gemini ===
def generate_response(chat_id: int, user_text: str) -> str:
    data = chat_data.get(chat_id, {"history": [], "category": "Общение"})
    history = data["history"]
    category = data["category"]

    system_prompt = {
        "🎓 Навчання": "Ти — викладач української мови или на языке который выберет пользователь. Пояснюй чітко та просто.",
        "🌍 Переклад": "Ти — перекладач. Перекладай текст українською или на языке который выберет пользователь точно.",
        "💻 Програмування": "Ти — програміст. Пояснюй код українською или на языке который выберет пользователь.",
        "🎭 Розваги": "Ти — веселий співрозмовник. Шути українською или на языке который выберет пользователь.",
        "Общение": "Ти — дружній AI."
    }.get(category, "Ти — дружній AI.")

    context = "\n".join(history[-5:])
    prompt = f"{system_prompt}\n\nІсторія діалогу:\n{context}\nКористувач: {user_text}\nБот:"

    try:
        response = model.generate_content(prompt)
        reply = clean_text(response.text.strip() if hasattr(response, "text") else "Не вдалося отримати відповідь")
    except Exception as e:
        reply = f"Помилка Gemini: {e}"

    history.append(f"Користувач: {user_text}")
    history.append(f"Бот: {reply}")
    chat_data[chat_id] = {"history": history, "category": category}
    return reply

# === Обробка тестових відповідей ===
async def handle_test_answer(update: Update, chat_id: int, answer: str):
    test_data = chat_data[chat_id]["test"]
    test = test_data["questions"]
    index = test_data["index"]

    user_answer = answer.strip().lower()
    correct_answer = test[index]["answer"].strip().lower()

    if user_answer == correct_answer:
        test_data["score"] += 1

    test_data["index"] += 1

    if test_data["index"] < len(test):
        q = test[test_data["index"]]
        options = [[o] for o in q["options"]]
        markup = ReplyKeyboardMarkup(options, resize_keyboard=True)
        await update.message.reply_text(clean_text(q["question"]), reply_markup=markup)
    else:
        score = test_data["score"]
        total = len(test)
        if score == total:
            level = "просунутий"
        elif score >= total // 2:
            level = "середній"
        else:
            level = "початковий"

        try:
            program = clean_text(model.generate_content(
                f"Підбери коротку навчальну програму для студента з {level} знаннями української мови."
            ).text.strip())
        except Exception as e:
            program = f"Не вдалося згенерувати програму: {e}"

        result_text = f"📋 Тест завершено.\nРезультат: {score}/{total}\nРівень: {level}\n\n📚 Рекомендована програма:\n{program}"
        await send_long_message(update, result_text)
        await update.message.reply_text("Тест завершено.", reply_markup=reply_markup)
        del chat_data[chat_id]["test"]

# === Обробка повідомлень ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text.strip()

    if chat_data.get(chat_id, {}).get("test"):
        await handle_test_answer(update, chat_id, text)
        return

    # Визначаємо категорію по українській частині кнопки
    if text.startswith("🎓 Навчання"):
        category = "🎓 Навчання"
    elif text.startswith("🌍 Переклад"):
        category = "🌍 Переклад"
    elif text.startswith("💻 Програмування"):
        category = "💻 Програмування"
    elif text.startswith("🎭 Розваги"):
        category = "🎭 Розваги"
    else:
        category = None

    if category:
        chat_data.setdefault(chat_id, {"history": []})
        chat_data[chat_id]["category"] = category
        await update.message.reply_text(f"Категорія встановлена: {category}")
        return

    elif text.startswith("📘 Тест з української мови"):
        await update.message.reply_text("⏳ Генерую тест, зачекай...")
        test = generate_ukrainian_test()
        chat_data[chat_id]["test"] = {"questions": test, "index": 0, "score": 0}
        q = test[0]
        options = [[o] for o in q["options"]]
        markup = ReplyKeyboardMarkup(options, resize_keyboard=True)
        await update.message.reply_text(clean_text(q["question"]), reply_markup=markup)
        return

    elif text.startswith("🧠 Новий діалог"):
        chat_data[chat_id] = {"history": [], "category": "Общение"}
        await update.message.reply_text("🧹 Історія очищена. Почнемо заново.", reply_markup=reply_markup)
        return

    elif text.startswith("❌ Стоп"):
        await update.message.reply_text("🚫 Діалог завершено.", reply_markup=ReplyKeyboardRemove())
        return

    reply = generate_response(chat_id, text)
    await update.message.reply_text(reply, reply_markup=reply_markup)

# === Команда /start ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    chat_data[update.effective_chat.id] = {"history": [], "category": "Общение"}
    await update.message.reply_text(
        f"Привіт, {name}! 🤖 Я UAHelper.\nОбери категорію або спробуй пройти тест з української мови 🇺🇦:",
        reply_markup=reply_markup
    )

# === Запуск бота ===
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ Бот запущено!")
    app.run_polling()

if __name__ == "__main__":
    main()
