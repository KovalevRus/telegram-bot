import os
import json
import tempfile
import firebase_admin
from firebase_admin import credentials

def initialize_firebase():
    if firebase_admin._apps:
        return  # Firebase уже инициализирован

    firebase_json = os.getenv("FIREBASE_JSON")
    if not firebase_json:
        raise RuntimeError("FIREBASE_JSON не установлена")

    # Сохраняем содержимое переменной во временный файл
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as temp_file:
        temp_file.write(firebase_json)
        temp_path = temp_file.name

    cred = credentials.Certificate(temp_path)
    firebase_admin.initialize_app(cred)
