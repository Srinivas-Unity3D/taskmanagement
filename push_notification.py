import firebase_admin
from firebase_admin import messaging
import os
import json
from google.oauth2 import service_account

# Load Google Cloud credentials from environment variable
credentials_info = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON')
if credentials_info:
    credentials = service_account.Credentials.from_service_account_info(json.loads(credentials_info))
    firebase_admin.initialize_app(credentials)
else:
    raise Exception("Google Cloud credentials not found in environment variables.")

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
    except Exception as e:
        print(f"Error sending FCM notification: {e}") 