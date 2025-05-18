import os
import json
import firebase_admin
from firebase_admin import credentials, firestore

def initialize_firebase():
    if firebase_admin._apps:
        return firestore.client()

    firebase_credentials_json = os.environ.get("FIREBASE_CREDENTIALS_JSON")

    if not firebase_credentials_json:
        raise ValueError("FIREBASE_CREDENTIALS_JSON environment variable is not set.")

    try:
        firebase_credentials_dict = json.loads(firebase_credentials_json)
    except json.JSONDecodeError:
        raise ValueError("FIREBASE_CREDENTIALS_JSON is not valid JSON.")

    cred = credentials.Certificate(firebase_credentials_dict)
    firebase_admin.initialize_app(cred)

    return firestore.client()
