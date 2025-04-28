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

def log_notification_attempt(task_data, event_type):
    """
    Process notification for task events and prepare data for FCM
    
    Parameters:
    - task_data: Dictionary containing task information
    - event_type: String indicating the event (created, updated, etc.)
    
    Returns:
    - Dictionary with notification information and FCM token if available
    """
    try:
        print(f"============ NOTIFICATION PROCESSING - {event_type} ============")
        print(f"Task data: {task_data}")
        
        # Extract task information
        task_id = task_data.get('task_id', '')
        title = task_data.get('title', 'Unknown Task')
        priority = task_data.get('priority', 'medium')
        status = task_data.get('status', 'pending')
        assigned_to = task_data.get('assigned_to', '')
        assigned_by = task_data.get('assigned_by', '')
        updated_by = task_data.get('updated_by', '')
        deadline = task_data.get('deadline', '')
        
        # Format deadline if available
        deadline_str = ""
        if deadline:
            try:
                # Try to parse the deadline as a datetime object
                if isinstance(deadline, str):
                    deadline_dt = datetime.fromisoformat(deadline.replace('Z', '+00:00'))
                    deadline_str = deadline_dt.strftime("%b %d, %Y at %I:%M %p")
                else:
                    deadline_str = str(deadline)
            except Exception as e:
                print(f"Error formatting deadline: {e}")
                deadline_str = str(deadline)
        
        # Create notification title based on event type
        notification_title = ""
        if event_type == 'created':
            notification_title = "New Task Assigned"
        elif event_type == 'updated':
            notification_title = "Task Updated"
        elif event_type == 'completed':
            notification_title = "Task Completed"
        elif event_type == 'deleted':
            notification_title = "Task Removed"
        else:
            notification_title = f"Task {event_type.replace('_', ' ').title()}"
        
        # Create notification body with relevant information
        notification_body = ""
        if event_type == 'created':
            notification_body = f"'{title}' assigned to you by {assigned_by}"
            if priority in ['high', 'urgent']:
                notification_body += f" (Priority: {priority.upper()})"
            if deadline_str:
                notification_body += f" - Due: {deadline_str}"
        elif event_type == 'updated':
            notification_body = f"Task '{title}' has been updated"
            if updated_by:
                notification_body += f" by {updated_by}"
            notification_body += f" - Status: {status.replace('_', ' ').title()}"
            if priority in ['high', 'urgent']:
                notification_body += f", Priority: {priority.upper()}"
        elif event_type == 'completed':
            notification_body = f"Task '{title}' has been marked as completed"
            if updated_by:
                notification_body += f" by {updated_by}"
        elif event_type == 'deleted':
            notification_body = f"Task '{title}' has been removed"
        else:
            notification_body = f"Task '{title}' has been {event_type.replace('_', ' ')}"
        
        print(f"Notification title: {notification_title}")
        print(f"Notification body: {notification_body}")
        
        # Create data payload for FCM
        data_payload = {
            'event_type': event_type,
            'task_id': str(task_id),
            'title': title,
            'priority': priority,
            'status': status,
            'click_action': 'FLUTTER_NOTIFICATION_CLICK',
            'timestamp': datetime.now().isoformat()
        }
        
        # Get the FCM token for the assigned user
        if assigned_to:
            print(f"Looking up FCM token for user: {assigned_to}")
            fcm_token = get_user_fcm_token(assigned_to)
            if fcm_token:
                print(f"FCM token available for user {assigned_to}")
                print("Notification ready for client-side sending")
                return {
                    "success": True, 
                    "message": "Notification prepared for client-side sending",
                    "fcm_token": fcm_token,
                    "notification": {
                        "title": notification_title,
                        "body": notification_body,
                        "data": data_payload
                    }
                }
            else:
                print(f"No FCM token found for user {assigned_to}")
        else:
            print("No username provided in task data")
        
        print("============ NOTIFICATION PROCESSING COMPLETE ============")
        return {"success": False, "message": "No FCM token available"}
    except Exception as e:
        print(f"Error processing notification: {e}")
        print("============ NOTIFICATION PROCESSING FAILED ============")
        return {"success": False, "error": str(e)}

print("Enhanced notification module loaded successfully") 