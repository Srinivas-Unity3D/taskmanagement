import firebase_admin
from firebase_admin import messaging, credentials
import os
import json

def initialize_firebase():
    try:
        # First try to get credentials from environment variable
        firebase_creds = os.getenv('FIREBASE_CREDENTIALS')
        if firebase_creds:
            # Parse the JSON string from environment variable
            cred_dict = json.loads(firebase_creds)
            cred = credentials.Certificate(cred_dict)
        else:
            # Fallback to file-based credentials
            cred = credentials.Certificate('firebase-credentials.json')
        
        if not firebase_admin._apps:  # Only initialize if not already initialized
            firebase_admin.initialize_app(cred)
        print("Firebase initialized successfully")
    except Exception as e:
        print(f"Error initializing Firebase: {e}")
        raise

def send_fcm_notification(task_data, event_type):
    """
    Send FCM notification for task events
    task_data should contain: title, assigned_to, assigned_by
    event_type can be: created, updated, deleted, etc.
    """
    try:
        title = f"Task {event_type.replace('_', ' ').title()}"
        
        # Create body message based on available data
        if event_type == 'updated':
            body = f"Task '{task_data.get('title', 'Unknown')}' has been updated"
            if task_data.get('updated_by'):
                body += f" by {task_data['updated_by']}"
            if task_data.get('status'):
                body += f". Status: {task_data['status']}"
            if task_data.get('priority'):
                body += f", Priority: {task_data['priority']}"
        else:
            body = f"Task '{task_data.get('title', 'Unknown')}'"
            if task_data.get('assigned_to'):
                body += f" assigned to {task_data['assigned_to']}"
            if task_data.get('assigned_by'):
                body += f" by {task_data['assigned_by']}"
            body += f" is {event_type.replace('_', ' ')}."

        # Use assigned_to as topic if available, otherwise use a default topic
        topic = task_data.get('assigned_to', 'default_topic')

        # Add sound and vibration to the notification
        message = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            android=messaging.AndroidConfig(
                priority='high',
                notification=messaging.AndroidNotification(
                    sound='default',
                    priority='high',
                    vibrate_timings=['1s', '0.5s', '1s']
                )
            ),
            apns=messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(
                        sound='default',
                        badge=1
                    )
                )
            ),
            data={
                'event_type': event_type,
                'task_id': str(task_data.get('task_id', '')),
                'click_action': 'FLUTTER_NOTIFICATION_CLICK'
            },
            topic=topic,
        )

        try:
            response = messaging.send(message)
            print(f"FCM notification sent: {response}")
            return {"success": True, "message_id": response}
        except Exception as e:
            print(f"Error sending FCM notification: {e}")
            return {"success": False, "error": str(e)}
    except Exception as e:
        print(f"Error preparing FCM notification: {e}")
        return {"success": False, "error": str(e)}

# Initialize Firebase when the module is imported
initialize_firebase() 