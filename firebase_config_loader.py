import os
import json
import firebase_admin
from firebase_admin import credentials, firestore

def initialize_firebase():
    if not firebase_admin._apps:
        json_str = os.getenv("GOOGLE_CREDENTIALS")
        if not json_str:
            raise Exception("Переменная окружения GOOGLE_CREDENTIALS не установлена")

        # Заменяем \n на настоящие переводы строк в закрытом ключе
        data = json.loads(json_str)
        if "private_key" in data:
            data["private_key"] = data["private_key"].replace("\\n", "\n")

        cred = credentials.Certificate(data)
        firebase_admin.initialize_app(cred)

    return firestore.client()
