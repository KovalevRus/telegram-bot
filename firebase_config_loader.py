import os
import json
import firebase_admin
from firebase_admin import credentials, firestore

firebase_app = None
db = None

def initialize_firebase():
    global firebase_app, db

    if firebase_app is not None:
        return db

    firebase_config_json = os.getenv("GOOGLE_CREDENTIALS")
    if not firebase_config_json:
        raise ValueError("Переменная окружения GOOGLE_CREDENTIALS не установлена.")

    try:
        firebase_config = json.loads(firebase_config_json)
        cred = credentials.Certificate(firebase_config)
        firebase_app = firebase_admin.initialize_app(cred)
        db = firestore.client()
        return db
    except Exception as e:
        raise RuntimeError(f"Ошибка при инициализации Firebase: {e}")
