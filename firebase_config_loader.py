import os
import json
import firebase_admin
from firebase_admin import credentials, firestore

def initialize_firestore():
    if firebase_admin._apps:
        return firestore.client()

    cred_info = os.getenv("GOOGLE_CREDENTIALS")
    if not cred_info:
        raise ValueError("GOOGLE_CREDENTIALS not set in environment variables.")

    try:
        cred_dict = json.loads(cred_info)
    except json.JSONDecodeError:
        raise ValueError("GOOGLE_CREDENTIALS is not valid JSON.")

    cred = credentials.Certificate(cred_dict)
    firebase_admin.initialize_app(cred)
    return firestore.client()
