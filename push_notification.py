import firebase_admin
from firebase_admin import messaging, credentials
import os
import json
from google.oauth2 import service_account

def initialize_firebase():
    try:
        # Try to use the credentials file
        cred = credentials.Certificate('firebase-credentials.json')
        firebase_admin.initialize_app(cred)
        print("Firebase initialized successfully")
    except Exception as e:
        print(f"Error initializing Firebase: {e}")
        raise

def send_fcm_notification(task_data, event_type):
    title = f"Task {event_type.replace('_', ' ').title()}"
    body = f"Task '{task_data['title']}' assigned to {task_data['assigned_to']} by {task_data['assigned_by']} is {event_type.replace('_', ' ')}."
    topic = task_data.get('assigned_to', 'default_topic')

    message = messaging.Message(
        notification=messaging.Notification(
            title=title,
            body=body,
        ),
        topic=topic,
    )

    try:
        response = messaging.send(message)
        print(f"FCM notification sent: {response}")
        return {"success": True, "message_id": response}
    except Exception as e:
        print(f"Error sending FCM notification: {e}")
        return {"success": False, "error": str(e)}

# Initialize Firebase when the module is imported
initialize_firebase() 