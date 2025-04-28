import os
import mysql.connector
from datetime import datetime

# DB config using environment variables from app.py
db_config = {
    'host': os.getenv('DB_HOST', '134.209.149.12'),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', '123'),
    'database': os.getenv('DB_NAME', 'task_db')
}

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

# This function now just logs info and returns a response,
# but doesn't actually send notifications from the server
def log_notification_attempt(task_data, event_type):
    """
    Log information about notification attempts
    task_data should contain: title, assigned_to, assigned_by
    event_type can be: created, updated, deleted, etc.
    """
    try:
        print(f"============ NOTIFICATION LOG - {event_type} ============")
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
        
        # Get username
        username = task_data.get('assigned_to')
        if username:
            print(f"Looking up FCM token for user: {username}")
            fcm_token = get_user_fcm_token(username)
            if fcm_token:
                print(f"FCM token available for user {username}")
                print("Use client-side notification approach")
                return {
                    "success": True, 
                    "message": "Notification info logged (client-side sending)",
                    "fcm_token": fcm_token
                }
            else:
                print(f"No FCM token found for user {username}")
        else:
            print("No username provided in task data")
        
        print("============ NOTIFICATION LOG END ============")
        return {"success": False, "message": "No FCM token available"}
    except Exception as e:
        print(f"Error logging notification: {e}")
        print("============ NOTIFICATION LOG FAILED ============")
        return {"success": False, "error": str(e)}

print("Simplified notification module loaded successfully") 