# 📦 Telegram Logistics Bot

Проект состоит из:

- 🧠 **Backend**: FastAPI-сервер (REST API)
- 🤖 **Telegram Bot**: для работы с пользователями
- 🛢 **PostgreSQL**: база данных для хранения заявок и пользователей

---

## 🚀 Быстрый запуск

### 🔧 1. Клонируй репозиторий

```bash
git clone https://github.com/your_username/telegram_logistics_bot.git
cd telegram_logistics_bot
```

### 📦 2. Собери и запусти проект

```bash
docker-compose up --build
```

---

## 🔍 Компоненты

| Сервис     | Адрес                  | Назначение                     |
|------------|------------------------|--------------------------------|
| Backend    | http://localhost:8000  | Swagger UI (`/docs`), API      |
| БД         | `localhost:5432`       | PostgreSQL с данными проекта   |
| Бот        | Telegram               | Принимает команды от пользователей |

---

## 🛠 Структура проекта

```plaintext
telegram_bot/
├── backend/            # FastAPI-приложение
│   ├── main.py
│   └── Dockerfile
│
├── bot/                # Telegram Bot
│   ├── main_bot.py
│   └── Dockerfile
│
├── .env                # Переменные окружения
├── docker-compose.yml  # Главный файл запуска
├── requirements.txt    # Общие зависимости
└── README.md
```

---

## 🗃 База данных

После запуска:

- База данных автоматически создаётся через контейнер Postgres
- Все таблицы создаются SQLAlchemy при первом запуске backend'а

---

## ✅ Примеры

### 📬 Создание заявки:

1. Пользователь пишет боту
2. Бот отправляет данные на API
3. Backend сохраняет заявку в PostgreSQL
4. Диспетчер может получить список заявок

---

## 🧹 Остановка и удаление

```bash
docker-compose down -v
```

> Флаг `-v` удаляет volume с базой данных

---
