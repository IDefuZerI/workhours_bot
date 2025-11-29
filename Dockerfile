# Вибираємо Python 3.11
FROM python:3.11.8-slim

# Встановлюємо робочу директорію
WORKDIR /app

# Копіюємо файли проекту
COPY . /app

# Встановлюємо залежності
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Вказуємо команду запуску бота
CMD ["python", "workhours_bot.py"]
