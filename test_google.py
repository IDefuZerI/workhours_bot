import gspread
from google.oauth2.service_account import Credentials

# Google Sheet ID (заміни на свій)
SHEET_ID = "1XfSuBBlJIvmXUTXt16qP8XvxOrp5h-wcs6zRfMHrdWk"

# Авторизація
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
creds = Credentials.from_service_account_file("service_account.json", scopes=SCOPES)
gc = gspread.authorize(creds)
sheet = gc.open_by_key(SHEET_ID)

# Ім’я користувача (для тесту)
user_name = "Denys"

# Перевіряємо чи є листок
try:
    user_sheet = sheet.worksheet(user_name)
except gspread.exceptions.WorksheetNotFound:
    user_sheet = sheet.add_worksheet(title=user_name, rows="1000", cols="8")
    user_sheet.append_row(["Дата", "Ім'я", "Username", "User ID", "Початок", "Кінець", "Обід", "Відпрацьовано"])

# Додаємо тестовий рядок
user_sheet.append_row(["27.10.2025", "Denys", "@kopyl", "12345", "07:00", "18:00", "Так", "10.5"])

print("✅ Дані успішно додано!")
