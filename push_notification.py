import firebase_admin
from firebase_admin import messaging, credentials
import os
import json
import mysql.connector
from datetime import datetime

# DB config using environment variables from app.py
db_config = {
    'host': os.getenv('DB_HOST', '134.209.149.12'),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', '123'),
    'database': os.getenv('DB_NAME', 'task_db')
}

def initialize_firebase():
    try:
        print("============ FIREBASE INITIALIZATION START ============")
        # First try to get credentials from environment variable
        firebase_creds = os.getenv('FIREBASE_CREDENTIALS')
        if firebase_creds:
            print("Using Firebase credentials from environment variable")
            # Parse the JSON string from environment variable
            try:
                cred_dict = json.loads(firebase_creds)
                print(f"Successfully parsed Firebase credentials JSON from environment")
                # Log project ID
                if 'project_id' in cred_dict:
                    print(f"Firebase Project ID: {cred_dict['project_id']}")
            except json.JSONDecodeError as e:
                print(f"ERROR: Failed to parse Firebase credentials from environment: {e}")
                raise
            cred = credentials.Certificate(cred_dict)
        else:
            print("Using Firebase credentials from file")
            # Check if file exists
            if not os.path.exists('firebase-credentials.json'):
                print("ERROR: firebase-credentials.json file not found")
                raise FileNotFoundError("firebase-credentials.json file not found")
            
            # Try to read and parse the credentials file
            try:
                with open('firebase-credentials.json', 'r') as f:
                    cred_content = f.read()
                    print(f"Read {len(cred_content)} bytes from credentials file")
                    cred_dict = json.loads(cred_content)  # Validate JSON format
                    print("Credentials file contains valid JSON")
                    # Log project ID
                    if 'project_id' in cred_dict:
                        print(f"Firebase Project ID: {cred_dict['project_id']}")
            except Exception as e:
                print(f"ERROR: Failed to read or parse credentials file: {e}")
                raise
                
            cred = credentials.Certificate('firebase-credentials.json')
        
        if not firebase_admin._apps:  # Only initialize if not already initialized
            print("Initializing Firebase for the first time")
            firebase_admin.initialize_app(cred)
            print("Firebase initialized successfully")
        else:
            print("Firebase was already initialized")
            print(f"Current Firebase apps: {firebase_admin._apps}")
        
        # Test if we can get a FirebaseMessaging instance
        try:
            print(f"Firebase Messaging module name: {messaging.__name__}")
            print("Firebase Messaging is available")
        except Exception as e:
            print(f"Firebase Messaging test failed: {e}")
        
        print("============ FIREBASE INITIALIZATION END ============")
    except Exception as e:
        print(f"ERROR during Firebase initialization: {e}")
        print("============ FIREBASE INITIALIZATION FAILED ============")
        raise

def validate_fcm_token(token):
    """Validates and cleans a FCM token"""
    if not token:
        print("Token is empty or None")
        return None
        
    # Remove any whitespace
    token = token.strip()
    
    # Check token length (FCM tokens are typically very long)
    if len(token) < 50:
        print(f"Token seems too short: {len(token)} chars")
        return None
        
    # Check if token contains invalid characters
    allowed_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:_-")
    if not all(c in allowed_chars for c in token):
        print("Token contains invalid characters")
        return None
        
    # Check if token has the expected structure (typically has a colon)
    if ":" not in token:
        print("Token is missing ':' character which is typical in FCM tokens")
        
    print(f"Token validation passed: {token[:10]}...{token[-5:]}")
    return token

def get_user_fcm_token(username):
    """Get a user's FCM token from the database"""
    conn = None
    cursor = None
    try:
        print(f"Attempting to get FCM token for user: {username}")
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT fcm_token FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()
        
        if user and user['fcm_token']:
            token = user['fcm_token']
            masked_token = token[:10] + "..." + token[-5:] if len(token) > 15 else token
            print(f"Found FCM token for user {username}: {masked_token}")
            
            # Validate token format
            return validate_fcm_token(token)
        
        print(f"No FCM token found for user {username}")
        return None
    except Exception as e:
        print(f"Error getting FCM token: {e}")
        return None
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def send_fcm_notification(task_data, event_type):
    """
    Send FCM notification for task events
    task_data should contain: title, assigned_to, assigned_by
    event_type can be: created, updated, deleted, etc.
    """
    try:
        print(f"============ SENDING NOTIFICATION START - {event_type} ============")
        print(f"Task data: {task_data}")
        
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

        print(f"Notification title: {title}")
        print(f"Notification body: {body}")

        # Simple data payload
        data_payload = {
            'event_type': event_type,
            'task_id': str(task_data.get('task_id', '')),
            'click_action': 'FLUTTER_NOTIFICATION_CLICK',
            'timestamp': datetime.now().isoformat()
        }
        
        # Try to send to direct FCM token first
        username = task_data.get('assigned_to')
        if username:
            print(f"Looking up FCM token for user: {username}")
            fcm_token = get_user_fcm_token(username)
            if fcm_token:
                print(f"Sending direct notification to FCM token for user {username}")
                
                # Create a simpler message structure
                message = messaging.Message(
                    notification=messaging.Notification(
                        title=title,
                        body=body,
                    ),
                    data=data_payload,
                    token=fcm_token,
                )
                
                try:
                    response = messaging.send(message)
                    print(f"FCM notification sent to token: {response}")
                    print("============ SENDING NOTIFICATION SUCCESS ============")
                    return {"success": True, "message_id": response, "method": "token"}
                except Exception as e:
                    print(f"Error sending to token: {e}")
                    if 'InvalidArgument' in str(e) and 'ValidationError' in str(e):
                        print("ERROR: FCM token format is invalid")
                    elif 'NotFound' in str(e):
                        print("ERROR: FCM token is not registered with Firebase")
                    elif 'Unavailable' in str(e):
                        print("ERROR: Firebase messaging service is unavailable")
                    elif 'Unauthenticated' in str(e):
                        print("ERROR: Firebase credentials are invalid")
                    print("Will try topic messaging as fallback")
                    # Fall back to topic messaging
            else:
                print(f"No FCM token found for user {username}, falling back to topic")
        else:
            print("No username provided in task data, falling back to topic")
        
        # Fall back to topic-based messaging
        topic = task_data.get('assigned_to', 'default_topic')
        print(f"Sending notification to topic: {topic}")
        
        # Create a simpler topic message
        message = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            data=data_payload,
            topic=topic,
        )

        try:
            response = messaging.send(message)
            print(f"FCM notification sent to topic: {response}")
            print("============ SENDING NOTIFICATION SUCCESS ============")
            return {"success": True, "message_id": response, "method": "topic"}
        except Exception as e:
            print(f"Error sending FCM notification to topic: {e}")
            if 'InvalidArgument' in str(e) and 'ValidationError' in str(e):
                print("ERROR: Topic name format is invalid")
            elif 'NotFound' in str(e):
                print("ERROR: Topic does not exist")
            elif 'Unavailable' in str(e):
                print("ERROR: Firebase messaging service is unavailable")
            elif 'Unauthenticated' in str(e):
                print("ERROR: Firebase credentials are invalid")
            print("============ SENDING NOTIFICATION FAILED ============")
            return {"success": False, "error": str(e)}
    except Exception as e:
        print(f"Error preparing FCM notification: {e}")
        print("============ SENDING NOTIFICATION FAILED ============")
        return {"success": False, "error": str(e)}

# Initialize Firebase when the module is imported
initialize_firebase() 