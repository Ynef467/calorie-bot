import os
import sqlite3
import requests
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command
from aiogram.types import Message
from dotenv import load_dotenv
from datetime import date
from aiogram.filters.state import StateFilter

# Загрузка переменных окружения
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
EDAMAM_APP_ID = os.getenv("EDAMAM_APP_ID")
EDAMAM_APP_KEY = os.getenv("EDAMAM_APP_KEY")

# Инициализация бота
bot = Bot(token=TELEGRAM_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot=bot, storage=storage)

# Создание базы данных SQLite
conn = sqlite3.connect("users.db")
cursor = conn.cursor()
cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        weight REAL,
        height REAL,
        age INTEGER,
        gender TEXT,
        goal TEXT,
        daily_calories REAL
    )
""")
cursor.execute("""
    CREATE TABLE IF NOT EXISTS meals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        food_text TEXT,
        calories REAL,
        date TEXT
    )
""")
conn.commit()

# Состояния для сбора данных
class UserData(StatesGroup):
    weight = State()
    height = State()
    age = State()
    gender = State()
    goal = State()

# Обработчик команды /start
@dp.message(Command("start"))
async def start_command(message: Message, state: FSMContext):
    await message.reply("Привет! Я бот для подсчета калорий. Давай начнем. Какой у тебя вес (кг)?")
    await state.set_state(UserData.weight)

# Сбор веса
@dp.message(StateFilter(UserData.weight))
async def process_weight(message: Message, state: FSMContext):
    try:
        weight = float(message.text)
        await state.update_data(weight=weight)
        await message.reply("Отлично! Какой у тебя рост (см)?")
        await state.set_state(UserData.height)
    except ValueError:
        await message.reply("Пожалуйста, введи вес в формате числа (например, 70.5).")

# Сбор роста
@dp.message(StateFilter(UserData.height))
async def process_height(message: Message, state: FSMContext):
    try:
        height = float(message.text)
        await state.update_data(height=height)
        await message.reply("Сколько тебе лет?")
        await state.set_state(UserData.age)
    except ValueError:
        await message.reply("Пожалуйста, введи рост в формате числа (например, 170).")

# Сбор возраста
@dp.message(StateFilter(UserData.age))
async def process_age(message: Message, state: FSMContext):
    try:
        age = int(message.text)
        await state.update_data(age=age)
        await message.reply("Какой у тебя пол? (мужской/женский)")
        await state.set_state(UserData.gender)
    except ValueError:
        await message.reply("Пожалуйста, введи возраст в формате числа (например, 25).")

# Сбор пола
@dp.message(StateFilter(UserData.gender))
async def process_gender(message: Message, state: FSMContext):
    gender = message.text.lower()
    if gender in ["мужской", "женский"]:
        await state.update_data(gender=gender)
        await message.reply("Какая у тебя цель? (похудение/поддержание/набор)")
        await state.set_state(UserData.goal)
    else:
        await message.reply("Пожалуйста, выбери 'мужской' или 'женский'.")
        
# Сбор цели и расчет калорий
@dp.message(StateFilter(UserData.goal))
async def process_goal(message: Message, state: FSMContext):
    goal = message.text.lower()
    if goal in ["похудение", "поддержание", "набор"]:
        user_data = await state.get_data()
        weight = user_data["weight"]
        height = user_data["height"]
        age = user_data["age"]
        gender = user_data["gender"]

        # Формула Миффлина-Сан Жеора
        if gender == "мужской":
            bmr = 10 * weight + 6.25 * height - 5 * age + 5
        else:
            bmr = 10 * weight + 6.25 * height - 5 * age - 161

        # Умножаем на коэффициент активности (1.4 для примера)
        daily_calories = bmr * 1.4

        # Корректировка по цели
        if goal == "похудение":
            daily_calories -= 500
        elif goal == "набор":
            daily_calories += 500

        # Сохранение в базу
        cursor.execute(
            "INSERT OR REPLACE INTO users (user_id, weight, height, age, gender, goal, daily_calories) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (message.from_user.id, weight, height, age, gender, goal, daily_calories)
        )
        conn.commit()

        await message.reply(f"Готово! Твоя норма калорий: {daily_calories:.0f} ккал/день. Теперь присылай, что ты съел, и я посчитаю калории.")
        await state.finish()
    else:
        await message.reply("Пожалуйста, выбери 'похудение', 'поддержание' или 'набор'.")

# Обработчик сообщений о еде
@dp.message()
async def process_food(message: Message):
    user_id = message.from_user.id
    food_text = message.text

    # Запрос к Edamam Nutrition API
    url = "https://api.edamam.com/api/nutrition-data"
    params = {
        "app_id": EDAMAM_APP_ID,
        "app_key": EDAMAM_APP_KEY,
        "ingr": food_text
    }
    response = requests.get(url, params=params)
    
    if response.status_code == 200:
        data = response.json()
        calories = data.get("calories", 0)

        # Сохранение в базу
        cursor.execute(
            "INSERT INTO meals (user_id, food_text, calories, date) VALUES (?, ?, ?, ?)",
            (user_id, food_text, calories, str(date.today()))
        )
        conn.commit()

        # Получение нормы калорий пользователя
        cursor.execute("SELECT daily_calories FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        if result:
            daily_calories = result[0]

            # Подсчет съеденных калорий за день
            cursor.execute(
                "SELECT SUM(calories) FROM meals WHERE user_id = ? AND date = ?",
                (user_id, str(date.today()))
            )
            total_calories = cursor.fetchone()[0] or 0

            remaining_calories = daily_calories - total_calories
            await message.reply(
                f"Ты съел: {food_text} ({calories:.0f} ккал).\n"
                f"Всего за день: {total_calories:.0f} ккал.\n"
                f"Осталось: {remaining_calories:.0f} ккал."
            )
        else:
            await message.reply("Сначала настрой свои данные с помощью /start.")
    else:
        await message.reply("Не удалось распознать еду. Попробуй написать точнее (например, '100 г куриной грудки').")

# Запуск бота
async def on_startup(_):
    print("Бот запущен!")

async def on_shutdown(_):
    conn.close()
    print("Соединение с базой данных закрыто.")

import asyncio

async def main():
    await dp.start_polling(bot, skip_updates=True, on_startup=on_startup, on_shutdown=on_shutdown)

if __name__ == "__main__":
    asyncio.run(main())