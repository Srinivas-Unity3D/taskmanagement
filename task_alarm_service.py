# #!/usr/bin/env python3
# import mysql.connector
# import time
# import logging
# import os
# import json
# import requests
# import datetime
# from dotenv import load_dotenv
# import threading
# import schedule
# import firebase_admin
# from firebase_admin import credentials, messaging
# from apscheduler.schedulers.background import BackgroundScheduler
# from flask import Flask, jsonify

# # Load environment variables
# load_dotenv()

# # Configure logging
# logging.basicConfig(
#     level=logging.INFO,
#     format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
#     handlers=[
#         logging.FileHandler('task_alarm_service.log'),
#         logging.StreamHandler()
#     ]
# )
# logger = logging.getLogger(__name__)

# # Database configuration
# db_config = {
#     'host': os.getenv('DB_HOST', '134.209.149.12'),
#     'user': os.getenv('DB_USER', 'root'),
#     'password': os.getenv('DB_PASSWORD', '123'),
#     'database': os.getenv('DB_NAME', 'task_db')
# }

# # Firebase Cloud Messaging configuration
# FIREBASE_PROJECT_ID = os.getenv('FIREBASE_PROJECT_ID', 'TaskManagement')

# # Initialize Firebase Admin SDK
# try:
#     # Check if credentials file path is provided
#     creds_file = os.getenv('FIREBASE_CREDENTIALS_FILE')
#     creds_json = os.getenv('FIREBASE_CREDENTIALS')
    
#     if creds_file and os.path.exists(creds_file):
#         # Initialize with service account file
#         cred = credentials.Certificate(creds_file)
#         firebase_admin.initialize_app(cred)
#         logger.info(f"Firebase initialized with credentials file: {creds_file}")
#     elif creds_json:
#         # Initialize with credentials JSON string
#         cred_dict = json.loads(creds_json)
#         cred = credentials.Certificate(cred_dict)
#         firebase_admin.initialize_app(cred)
#         logger.info("Firebase initialized with credentials from environment variable")
#     else:
#         logger.error("No Firebase credentials found. Notifications will not work.")
# except Exception as e:
#     logger.error(f"Error initializing Firebase: {e}")

# # Flask app for health check
# health_app = Flask(__name__)

# @health_app.route('/health', methods=['GET'])
# def health():
#     return jsonify({'status': 'ok', 'message': 'Task alarm scheduler is running'}), 200

# def get_db_connection():
#     """Get a database connection"""
#     try:
#         conn = mysql.connector.connect(**db_config)
#         return conn
#     except mysql.connector.Error as e:
#         logger.error(f"Database connection error: {str(e)}")
#         return None

# def get_pending_alarms():
#     """Get all pending alarms that should be triggered"""
#     try:
#         conn = get_db_connection()
#         if not conn:
#             return []
#         cursor = conn.cursor(dictionary=True)
#         now = datetime.datetime.now(datetime.timezone.utc)
#         ist_offset = datetime.timedelta(hours=5, minutes=30)
#         ist_now = now + ist_offset
#         ist_date = ist_now.strftime('%Y-%m-%d')
#         ist_time = ist_now.strftime('%H:%M:%S')
#         ist_datetime = ist_now.strftime('%Y-%m-%d %H:%M:%S')
#         logger.info(f"Current UTC time: {now}")
#         logger.info(f"Current IST time: {ist_now}")
#         logger.info(f"Checking for alarms with: date={ist_date}, time={ist_time}")
        
#         # Use deadline instead of due_date
#         query = """
#         SELECT 
#             a.*,
#             t.title as task_title, 
#             t.description as task_description,
#             t.assigned_by, 
#             t.assigned_to,
#             t.deadline,
#             DATE_FORMAT(t.deadline, '%Y-%m-%d') as formatted_deadline,
#             u.fcm_token
#         FROM 
#             task_alarms a
#         JOIN 
#             tasks t ON a.task_id = t.task_id
#         JOIN 
#             users u ON u.user_id = a.user_id
#         WHERE 
#             a.is_active = 1 
#             AND (
#                 /* For scheduled alarms with next_trigger set */
#                 (
#                     a.next_trigger IS NOT NULL 
#                     AND a.next_trigger <= %s
#                     AND (
#                         a.last_triggered IS NULL 
#                         OR TIME_TO_SEC(TIMEDIFF(%s, a.last_triggered)) > 300  -- At least 5 minutes between triggers
#                     )
#                 )
#                 OR 
#                 /* For first-time alarms that have never triggered */
#                 (
#                     a.next_trigger IS NULL 
#                     AND a.last_triggered IS NULL 
#                     AND (
#                         a.start_date < %s 
#                         OR (a.start_date = %s AND a.start_time <= %s)
#                     )
#                 )
#             )
#             AND u.fcm_token IS NOT NULL
#         """
#         logger.info(f"Executing query with params: datetime={ist_datetime}, date={ist_date}, time={ist_time}")
#         cursor.execute(query, (ist_datetime, ist_datetime, ist_date, ist_date, ist_time))
#         alarms = cursor.fetchall()
        
#         if alarms:
#             logger.info(f"Found {len(alarms)} pending alarms")
#             for alarm in alarms:
#                 logger.info(f"Alarm details: ID={alarm['alarm_id']}, Task={alarm['task_title']}, Assigned by={alarm['assigned_by']}, Next trigger={alarm['next_trigger']} (Assigned to: {alarm['assigned_to']})")
#         else:
#             logger.info("No pending alarms found")
        
#         cursor.close()
#         conn.close()
#         return alarms
#     except mysql.connector.Error as e:
#         logger.error(f"Error getting pending alarms: {str(e)}")
#         return []

# def calculate_next_trigger_time(alarm):
#     """Calculate the next trigger time based on frequency"""
#     try:
#         if not alarm['last_triggered']:
#             # If never triggered, use start date and time
#             start_date = alarm['start_date']
#             start_time = alarm['start_time']
#             if isinstance(start_date, str):
#                 start_date_str = start_date
#             else:
#                 start_date_str = start_date.strftime('%Y-%m-%d')
#             if isinstance(start_time, str):
#                 start_time_str = start_time
#             else:
#                 start_time_str = start_time.strftime('%H:%M:%S')
#             start_datetime_str = f"{start_date_str} {start_time_str}"
#             start_datetime = datetime.datetime.strptime(start_datetime_str, '%Y-%m-%d %H:%M:%S')
#             logger.info(f"First trigger time calculated: {start_datetime}")
#             return start_datetime
#         # Convert last_triggered to datetime
#         last_triggered = alarm['last_triggered']
#         if isinstance(last_triggered, str):
#             last_triggered_dt = datetime.datetime.strptime(
#                 last_triggered, '%Y-%m-%d %H:%M:%S'
#             )
#         else:
#             last_triggered_dt = last_triggered
#         # Calculate next trigger time based on frequency
#         frequency = alarm['frequency']
#         logger.info(f"Calculating next trigger time for frequency: {frequency}")
#         if frequency == '30 minutes':
#             next_trigger = last_triggered_dt + datetime.timedelta(minutes=30)
#         elif frequency == '1 hour':
#             next_trigger = last_triggered_dt + datetime.timedelta(hours=1)
#         elif frequency == '2 hours':
#             next_trigger = last_triggered_dt + datetime.timedelta(hours=2)
#         elif frequency == '4 hours':
#             next_trigger = last_triggered_dt + datetime.timedelta(hours=4)
#         elif frequency == '6 hours':
#             next_trigger = last_triggered_dt + datetime.timedelta(hours=6)
#         elif frequency == '8 hours':
#             next_trigger = last_triggered_dt + datetime.timedelta(hours=8)
#         else:
#             # Default to 1 hour if frequency is not recognized
#             logger.warning(f"Unrecognized frequency: {frequency}, defaulting to 1 hour")
#             next_trigger = last_triggered_dt + datetime.timedelta(hours=1)
#         logger.info(f"Next trigger time calculated: {next_trigger}")
#         return next_trigger
#     except Exception as e:
#         logger.error(f"Error calculating next trigger time: {str(e)}")
#         raise

# def update_alarm_status(alarm_id, last_triggered, next_trigger):
#     """Update alarm status after triggering"""
#     try:
#         conn = get_db_connection()
#         if not conn:
#             return False
            
#         cursor = conn.cursor()
        
#         update_query = """
#         UPDATE task_alarms
#         SET last_triggered = %s, next_trigger = %s
#         WHERE alarm_id = %s
#         """
        
#         cursor.execute(update_query, (
#             last_triggered.strftime('%Y-%m-%d %H:%M:%S'),
#             next_trigger.strftime('%Y-%m-%d %H:%M:%S'),
#             alarm_id
#         ))
        
#         conn.commit()
#         cursor.close()
#         conn.close()
        
#         return True
#     except mysql.connector.Error as e:
#         logger.error(f"Error updating alarm status: {str(e)}")
#         return False

# def send_alarm_notification(alarm):
#     """Send FCM notification to user for the alarm using Firebase Admin SDK"""
#     try:
#         # Double-check if FCM token exists
#         if not alarm.get('fcm_token'):
#             logger.warning(f"No FCM token for alarm ID: {alarm['alarm_id']}, user ID: {alarm.get('user_id', 'unknown')}")
#             # Try to get the FCM token directly from the database as a fallback
#             try:
#                 conn = get_db_connection()
#                 if conn:
#                     cursor = conn.cursor(dictionary=True)
#                     cursor.execute("""
#                         SELECT u.fcm_token 
#                         FROM users u
#                         JOIN task_alarms a ON a.user_id = u.user_id
#                         WHERE a.task_id = %s
#                     """, (alarm['task_id'],))
#                     result = cursor.fetchone()
#                     cursor.close()
#                     conn.close()
                    
#                     if result and result.get('fcm_token'):
#                         logger.info(f"Found FCM token from fallback query: {result['fcm_token'][:10]}...")
#                         alarm['fcm_token'] = result['fcm_token']
#                     else:
#                         logger.warning(f"No FCM token found in fallback query for task ID: {alarm['task_id']}")
#                         return False
#             except Exception as e:
#                 logger.error(f"Error in FCM token fallback lookup: {e}")
#                 return False
        
#         # Log that we're sending an alarm
#         logger.info(f"Sending alarm notification for task: {alarm['task_id']} with FCM token: {alarm['fcm_token'][:10]}...")
        
#         # Get assigned by and deadline information directly from task record
#         assigned_by = alarm.get('assigned_by')
#         if not assigned_by:
#             try:
#                 conn = get_db_connection()
#                 if conn:
#                     cursor = conn.cursor(dictionary=True)
#                     cursor.execute("""
#                         SELECT assigned_by FROM tasks WHERE task_id = %s
#                     """, (alarm['task_id'],))
#                     result = cursor.fetchone()
#                     cursor.close()
#                     conn.close()
#                     if result and result.get('assigned_by'):
#                         assigned_by = result['assigned_by']
#                         logger.info(f"Retrieved assigned_by from database: {assigned_by}")
#                     else:
#                         logger.warning(f"No assigned_by found for task {alarm['task_id']}")
#                         assigned_by = 'Unknown'
#             except Exception as e:
#                 logger.error(f"Error retrieving assigned_by: {e}")
#                 assigned_by = 'Unknown'
        
#         # Format the deadline properly
#         deadline = ''
#         if alarm.get('formatted_deadline'):
#             deadline = alarm['formatted_deadline']
#         elif alarm.get('deadline'):
#             # Try to format the date if it's not already formatted
#             try:
#                 if isinstance(alarm['deadline'], str):
#                     deadline = alarm['deadline']
#                 else:
#                     deadline = alarm['deadline'].strftime('%Y-%m-%d')
#             except Exception as e:
#                 logger.error(f"Error formatting deadline: {e}")
#                 deadline = str(alarm['deadline']) if alarm['deadline'] else ''
        
#         # Get assignee name (use assigned_to)
#         assignee_name = alarm.get('assigned_to', 'You')
        
#         logger.info(f"Task details: assigned_by={assigned_by}, deadline={deadline}, assignee_name={assignee_name}")
        
#         # Use the messaging module from Firebase Admin SDK
#         try:
#             # Create the notification message
#             message = messaging.Message(
#                 notification=messaging.Notification(
#                     title=f"Task Alarm: {alarm['task_title']}",
#                     body=alarm['task_description'] or "Time to check your task!"
#                 ),
#                 data={
#                     "type": "task_alarm",
#                     "task_id": alarm['task_id'],
#                     "alarm_id": alarm['alarm_id'],
#                     "title": alarm['task_title'],
#                     "assigned_by": assigned_by,
#                     "deadline": deadline,
#                     "assignee_name": assignee_name,
#                     "click_action": "FLUTTER_NOTIFICATION_CLICK"
#                 },
#                 android=messaging.AndroidConfig(
#                     priority='high',
#                     notification=messaging.AndroidNotification(
#                         sound='alarm',
#                         channel_id='task_alarms',
#                         priority='max',
#                         visibility='public'
#                     )
#                 ),
#                 apns=messaging.APNSConfig(
#                     payload=messaging.APNSPayload(
#                         aps=messaging.Aps(
#                             sound='alarm.wav',
#                             category='TASK_ALARM',
#                             content_available=True,
#                             mutable_content=True
#                         )
#                     ),
#                     headers={'apns-priority': '10'}
#                 ),
#                 token=alarm['fcm_token']
#             )
            
#             # Send the message
#             response = messaging.send(message)
#             logger.info(f"Successfully sent alarm notification: {response}")
#             return True
#         except Exception as e:
#             logger.error(f"Error sending notification with Firebase Admin SDK: {e}")
#             return False
            
#     except Exception as e:
#         logger.error(f"Error sending alarm notification: {str(e)}")
#         return False

# def process_pending_alarms():
#     """Process all pending alarms that should be triggered now"""
#     logger.info("Checking for pending alarms...")
#     alarms = get_pending_alarms()
    
#     if not alarms:
#         logger.info("No pending alarms found")
#         return
    
#     logger.info(f"Found {len(alarms)} pending alarms to process")
    
#     for alarm in alarms:
#         try:
#             # Make sure we have all required alarm data
#             alarm_id = alarm.get('alarm_id')
#             task_id = alarm.get('task_id')
#             task_title = alarm.get('task_title', 'Unknown Task')
            
#             if not alarm_id or not task_id:
#                 logger.error(f"Missing required alarm data: alarm_id={alarm_id}, task_id={task_id}")
#                 continue
                
#             logger.info(f"Processing alarm ID: {alarm_id} for task: {task_title}")
#             logger.info(f"Alarm details: start_date={alarm['start_date']}, start_time={alarm['start_time']}, frequency={alarm['frequency']}")
            
#             # Send notification
#             logger.info(f"Sending notification for alarm ID: {alarm_id}")
#             notification_sent = send_alarm_notification(alarm)
            
#             if notification_sent:
#                 logger.info(f"Notification sent successfully for alarm ID: {alarm_id}")
                
#                 # Calculate next trigger time
#                 now = datetime.datetime.now()
#                 next_trigger = calculate_next_trigger_time(alarm)
#                 logger.info(f"Calculated next trigger time: {next_trigger} for alarm ID: {alarm_id}")
                
#                 # Update alarm status
#                 updated = update_alarm_status(alarm_id, now, next_trigger)
                
#                 if updated:
#                     logger.info(f"Alarm ID: {alarm_id} processed successfully. Next trigger: {next_trigger}")
#                     # Confirm DB update
#                     try:
#                         conn = get_db_connection()
#                         if conn:
#                             cursor = conn.cursor(dictionary=True)
#                             cursor.execute("SELECT last_triggered, next_trigger FROM task_alarms WHERE alarm_id = %s", (alarm_id,))
#                             row = cursor.fetchone()
#                             logger.info(f"DB check for alarm {alarm_id}: last_triggered={row['last_triggered']}, next_trigger={row['next_trigger']}")
#                             cursor.close()
#                             conn.close()
#                     except Exception as e:
#                         logger.error(f"Error confirming DB update for alarm {alarm_id}: {e}")
#                 else:
#                     logger.error(f"Failed to update alarm ID: {alarm_id}")
#             else:
#                 logger.error(f"Failed to send notification for alarm ID: {alarm_id}")
#         except Exception as e:
#             logger.error(f"Error processing alarm {alarm.get('alarm_id', 'unknown')}: {str(e)}")
#             continue

# # --- APScheduler integration ---
# def start_scheduler():
#     scheduler = BackgroundScheduler()
#     scheduler.add_job(process_pending_alarms, 'interval', minutes=1, id='alarm_job', replace_existing=True)
#     scheduler.start()
#     logger.info('APScheduler started for task alarms - checking every minutes')
#     return scheduler

# if __name__ == "__main__":
#     try:
#         # Start the alarm scheduler
#         scheduler = start_scheduler()

#         # Start the health check HTTP server in a separate thread
#         def run_health_app():
#             health_app.run(host='0.0.0.0', port=5051)
#         threading.Thread(target=run_health_app, daemon=True).start()

#         logger.info('Task alarm service is running. Health check at /health on port 5051.')

#         # Keep the main thread alive
#         while True:
#             time.sleep(60)
#     except KeyboardInterrupt:
#         logger.info("Task alarm service stopped by user")
#     except Exception as e:
#         logger.error(f"Fatal error: {str(e)}") 


#!/usr/bin/env python3
import mysql.connector
import time
import logging
import os
import json
import requests
import datetime
from datetime import timezone
from dotenv import load_dotenv
import threading
import schedule
import firebase_admin
from firebase_admin import credentials, messaging
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('task_alarm_service.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Database configuration
db_config = {
    'host': os.getenv('DB_HOST', '134.209.149.12'),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', '123'),
    'database': os.getenv('DB_NAME', 'task_db')
}

# Firebase Cloud Messaging configuration
FIREBASE_PROJECT_ID = os.getenv('FIREBASE_PROJECT_ID', 'TaskManagement')

# Initialize Firebase Admin SDK
try:
    creds_file = os.getenv('FIREBASE_CREDENTIALS_FILE')
    creds_json = os.getenv('FIREBASE_CREDENTIALS')
    
    if creds_file and os.path.exists(creds_file):
        cred = credentials.Certificate(creds_file)
        firebase_admin.initialize_app(cred)
        logger.info(f"Firebase initialized with credentials file: {creds_file}")
    elif creds_json:
        cred_dict = json.loads(creds_json)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)
        logger.info("Firebase initialized with credentials from environment variable")
    else:
        logger.error("No Firebase credentials found. Notifications will not work.")
except Exception as e:
    logger.error(f"Error initializing Firebase: {e}")

# Flask app for health check
health_app = Flask(__name__)

@health_app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'message': 'Task alarm scheduler is running'}), 200

# CHANGED: Added FREQUENCY_MAP for maintainable frequency handling
FREQUENCY_MAP = {
    '30 minutes': datetime.timedelta(minutes=30),
    '1 hour': datetime.timedelta(hours=1),
    '2 hours': datetime.timedelta(hours=2),
    '4 hours': datetime.timedelta(hours=4),
    '6 hours': datetime.timedelta(hours=6),
    '8 hours': datetime.timedelta(hours=8)
}
DEFAULT_FREQUENCY = '1 hour'

def get_db_connection():
    """Get a database connection"""
    # CHANGED: Added retry logic for reliability
    retries = 3
    for attempt in range(retries):
        try:
            conn = mysql.connector.connect(**db_config)
            logger.debug("Successfully connected to database")
            return conn
        except mysql.connector.Error as e:
            logger.error(f"Database connection error (attempt {attempt+1}/{retries}): {str(e)}")
            if attempt < retries - 1:
                time.sleep(2)
    logger.error("Failed to connect to database after retries")
    return None

def get_pending_alarms():
    """Get all pending alarms that should be triggered"""
    try:
        conn = get_db_connection()
        if not conn:
            logger.error("Failed to connect to database for pending alarms")
            return []
        cursor = conn.cursor(dictionary=True)
        now = datetime.datetime.now(timezone.utc)
        ist_offset = datetime.timedelta(hours=5, minutes=30)
        ist_now = now + ist_offset
        ist_date = ist_now.strftime('%Y-%m-%d')
        ist_time = ist_now.strftime('%H:%M:%S')
        ist_datetime = ist_now.strftime('%Y-%m-%d %H:%M:%S')
        # CHANGED: Reduced logging level to debug for non-critical messages
        logger.debug(f"Current UTC time: {now}")
        logger.debug(f"Current IST time: {ist_now}")
        logger.debug(f"Checking for alarms with: date={ist_date}, time={ist_time}")
        
        # CHANGED: Updated query to fix 1-minute triggering issue
        # - Removed last_triggered condition (TIME_TO_SEC(TIMEDIFF)) to prevent re-triggering
        # - Added 2-minute buffer for next_trigger to align with 2-minute scheduler
        query = """
        SELECT 
            a.*,
            t.title as task_title, 
            t.description as task_description,
            t.assigned_by, 
            t.assigned_to,
            t.deadline,
            DATE_FORMAT(t.deadline, '%Y-%m-%d') as formatted_deadline,
            u.fcm_token
        FROM 
            task_alarms a
        JOIN 
            tasks t ON a.task_id = t.task_id
        JOIN 
            users u ON u.user_id = a.user_id
        WHERE 
            a.is_active = 1 
            AND (
                /* For scheduled alarms with next_trigger set */
                (a.next_trigger IS NOT NULL AND a.next_trigger <= DATE_ADD(%s, INTERVAL 2 MINUTE))
                OR 
                /* For first-time alarms that have never triggered */
                (a.next_trigger IS NULL AND a.last_triggered IS NULL AND (
                    a.start_date < %s 
                    OR (a.start_date = %s AND a.start_time <= %s)
                ))
            )
            AND u.fcm_token IS NOT NULL
        """
        # CHANGED: Reduced logging level to debug
        logger.debug(f"Executing query with params: datetime={ist_datetime}, date={ist_date}, time={ist_time}")
        cursor.execute(query, (ist_datetime, ist_date, ist_date, ist_time))
        alarms = cursor.fetchall()
        
        if alarms:
            logger.info(f"Found {len(alarms)} pending alarms")
            # CHANGED: Added frequency to log and reduced to debug
            for alarm in alarms:
                logger.debug(f"Alarm details: ID={alarm['alarm_id']}, Task={alarm['task_title']}, "
                           f"Assigned by={alarm['assigned_by']}, Frequency={alarm['frequency']}, "
                           f"Next trigger={alarm['next_trigger']} (Assigned to: {alarm['assigned_to']})")
        else:
            # CHANGED: Reduced logging level to debug
            logger.debug("No pending alarms found")
        
        cursor.close()
        conn.close()
        return alarms
    except mysql.connector.Error as e:
        logger.error(f"Error getting pending alarms: {str(e)}")
        return []

def calculate_next_trigger_time(alarm):
    """Calculate the next trigger time based on frequency"""
    try:
        # CHANGED: Added alarm_id for traceability
        alarm_id = alarm.get('alarm_id', 'unknown')
        if not alarm['last_triggered']:
            start_date = alarm['start_date']
            start_time = alarm['start_time']
            if isinstance(start_date, str):
                start_date_str = start_date
            else:
                start_date_str = start_date.strftime('%Y-%m-%d')
            if isinstance(start_time, str):
                start_time_str = start_time
            else:
                start_time_str = start_time.strftime('%H:%M:%S')
            start_datetime_str = f"{start_date_str} {start_time_str}"
            start_datetime = datetime.datetime.strptime(start_datetime_str, '%Y-%m-%d %H:%M:%S')
            logger.info(f"First trigger time calculated for alarm {alarm_id}: {start_datetime}")
            return start_datetime
        last_triggered = alarm['last_triggered']
        if isinstance(last_triggered, str):
            last_triggered_dt = datetime.datetime.strptime(last_triggered, '%Y-%m-%d %H:%M:%S')
        else:
            last_triggered_dt = last_triggered
        frequency = alarm['frequency']
        # CHANGED: Replaced if-elif with FREQUENCY_MAP
        logger.debug(f"Calculating next trigger time for alarm {alarm_id}, frequency: {frequency}")
        delta = FREQUENCY_MAP.get(frequency, FREQUENCY_MAP[DEFAULT_FREQUENCY])
        next_trigger = last_triggered_dt + delta
        logger.info(f"Next trigger time calculated for alarm {alarm_id}: {next_trigger}")
        return next_trigger
    except Exception as e:
        logger.error(f"Error calculating next trigger time for alarm {alarm_id}: {str(e)}")
        raise

def update_alarm_status(alarm_id, last_triggered, next_trigger):
    """Update alarm status after triggering"""
    try:
        conn = get_db_connection()
        if not conn:
            logger.error(f"Failed to update alarm {alarm_id}: No database connection")
            return False
            
        cursor = conn.cursor()
        
        update_query = """
        UPDATE task_alarms
        SET last_triggered = %s, next_trigger = %s
        WHERE alarm_id = %s
        """
        
        cursor.execute(update_query, (
            last_triggered.strftime('%Y-%m-%d %H:%M:%S'),
            next_trigger.strftime('%Y-%m-%d %H:%M:%S'),
            alarm_id
        ))
        
        conn.commit()
        # CHANGED: Added debug logging for confirmation
        logger.debug(f"Updated alarm {alarm_id}: last_triggered={last_triggered}, next_trigger={next_trigger}")
        cursor.close()
        conn.close()
        
        return True
    except mysql.connector.Error as e:
        logger.error(f"Error updating alarm status for alarm {alarm_id}: {str(e)}")
        return False

def send_alarm_notification(alarm):
    """Send FCM notification to user for the alarm using Firebase Admin SDK"""
    try:
        alarm_id = alarm.get('alarm_id', 'unknown')
        # Double-check if FCM token exists
        if not alarm.get('fcm_token'):
            logger.warning(f"No FCM token for alarm ID: {alarm_id}, user ID: {alarm.get('user_id', 'unknown')}")
            try:
                conn = get_db_connection()
                if conn:
                    cursor = conn.cursor(dictionary=True)
                    cursor.execute("""
                        SELECT u.fcm_token 
                        FROM users u
                        JOIN task_alarms a ON a.user_id = u.user_id
                        WHERE a.task_id = %s
                    """, (alarm['task_id'],))
                    result = cursor.fetchone()
                    cursor.close()
                    conn.close()
                    
                    if result and result.get('fcm_token'):
                        logger.info(f"Found FCM token from fallback query for alarm {alarm_id}: {result['fcm_token'][:10]}...")
                        alarm['fcm_token'] = result['fcm_token']
                    else:
                        logger.warning(f"No FCM token found in fallback query for task ID: {alarm['task_id']}")
                        return False
            except Exception as e:
                logger.error(f"Error in FCM token fallback lookup for alarm {alarm_id}: {e}")
                return False
        
        logger.info(f"Sending alarm notification for task: {alarm['task_id']} with FCM token: {alarm['fcm_token'][:10]}...")
        
        assigned_by = alarm.get('assigned_by')
        if not assigned_by:
            try:
                conn = get_db_connection()
                if conn:
                    cursor = conn.cursor(dictionary=True)
                    cursor.execute("""
                        SELECT assigned_by FROM tasks WHERE task_id = %s
                    """, (alarm['task_id'],))
                    result = cursor.fetchone()
                    cursor.close()
                    conn.close()
                    if result and result.get('assigned_by'):
                        assigned_by = result['assigned_by']
                        logger.info(f"Retrieved assigned_by from database for alarm {alarm_id}: {assigned_by}")
                    else:
                        logger.warning(f"No assigned_by found for task {alarm['task_id']}")
                        assigned_by = 'Unknown'
            except Exception as e:
                logger.error(f"Error retrieving assigned_by for alarm {alarm_id}: {e}")
                assigned_by = 'Unknown'
        
        deadline = ''
        if alarm.get('formatted_deadline'):
            deadline = alarm['formatted_deadline']
        elif alarm.get('deadline'):
            try:
                if isinstance(alarm['deadline'], str):
                    deadline = alarm['deadline']
                else:
                    deadline = alarm['deadline'].strftime('%Y-%m-%d')
            except Exception as e:
                logger.error(f"Error formatting deadline for alarm {alarm_id}: {e}")
                deadline = str(alarm['deadline']) if alarm['deadline'] else ''
        
        assignee_name = alarm.get('assigned_to', 'You')
        
        logger.info(f"Task details for alarm {alarm_id}: assigned_by={assigned_by}, deadline={deadline}, assignee_name={assignee_name}")
        
        try:
            message = messaging.Message(
                notification=messaging.Notification(
                    title=f"Task Alarm: {alarm['task_title']}",
                    body=alarm['task_description'] or "Time to check your task!"
                ),
                data={
                    "type": "task_alarm",
                    "task_id": str(alarm['task_id']),
                    "alarm_id": str(alarm['alarm_id']),
                    "title": alarm['task_title'],
                    "assigned_by": assigned_by,
                    "deadline": deadline,
                    "assignee_name": assignee_name,
                    "click_action": "FLUTTER_NOTIFICATION_CLICK"
                },
                android=messaging.AndroidConfig(
                    priority='high',
                    notification=messaging.AndroidNotification(
                        sound='alarm',
                        channel_id='task_alarms',
                        priority='max',
                        visibility='public'
                    )
                ),
                apns=messaging.APNSConfig(
                    payload=messaging.APNSPayload(
                        aps=messaging.Aps(
                            sound='alarm.wav',
                            category='TASK_ALARM',
                            content_available=True,
                            mutable_content=True
                        )
                    ),
                    headers={'apns-priority': '10'}
                ),
                token=alarm['fcm_token']
            )
            
            response = messaging.send(message)
            logger.info(f"Successfully sent alarm notification for alarm {alarm_id}: {response}")
            return True
        except Exception as e:
            logger.error(f"Error sending notification with Firebase Admin SDK for alarm {alarm_id}: {e}")
            return False
            
    except Exception as e:
        logger.error(f"Error sending alarm notification for alarm {alarm_id}: {str(e)}")
        return False

def process_pending_alarms():
    """Process all pending alarms that should be triggered now"""
    # CHANGED: Reduced logging level to debug
    logger.debug("Checking for pending alarms...")
    alarms = get_pending_alarms()
    
    if not alarms:
        # CHANGED: Reduced logging level to debug
        logger.debug("No pending alarms found")
        return
    
    logger.info(f"Found {len(alarms)} pending alarms to process")
    
    for alarm in alarms:
        try:
            alarm_id = alarm.get('alarm_id')
            task_id = alarm.get('task_id')
            task_title = alarm.get('task_title', 'Unknown Task')
            
            if not alarm_id or not task_id:
                logger.error(f"Missing required alarm data: alarm_id={alarm_id}, task_id={task_id}")
                continue
                
            logger.info(f"Processing alarm ID: {alarm_id} for task: {task_title}")
            # CHANGED: Added frequency to log
            logger.info(f"Alarm details: start_date={alarm['start_date']}, start_time={alarm['start_time']}, frequency={alarm['frequency']}")
            
            logger.info(f"Sending notification for alarm ID: {alarm_id}")
            notification_sent = send_alarm_notification(alarm)
            
            if notification_sent:
                logger.info(f"Notification sent successfully for alarm ID: {alarm_id}")
                
                # CHANGED: Use IST for now to match DB
                now = datetime.datetime.now() + datetime.timedelta(hours=5, minutes=30)
                next_trigger = calculate_next_trigger_time(alarm)
                logger.info(f"Calculated next trigger time: {next_trigger} for alarm ID: {alarm_id}")
                
                updated = update_alarm_status(alarm_id, now, next_trigger)
                
                if updated:
                    logger.info(f"Alarm ID: {alarm_id} processed successfully. Next trigger: {next_trigger}")
                    # CHANGED: Moved DB check to debug level
                    try:
                        conn = get_db_connection()
                        if conn:
                            cursor = conn.cursor(dictionary=True)
                            cursor.execute("SELECT last_triggered, next_trigger FROM task_alarms WHERE alarm_id = %s", (alarm_id,))
                            row = cursor.fetchone()
                            logger.debug(f"DB check for alarm {alarm_id}: last_triggered={row['last_triggered']}, next_trigger={row['next_trigger']}")
                            cursor.close()
                            conn.close()
                    except Exception as e:
                        logger.error(f"Error confirming DB update for alarm {alarm_id}: {e}")
                else:
                    logger.error(f"Failed to update alarm ID: {alarm_id}")
            else:
                logger.error(f"Failed to send notification for alarm ID: {alarm_id}")
        except Exception as e:
            logger.error(f"Error processing alarm {alarm_id}: {str(e)}")
            continue

def start_scheduler():
    """Start APScheduler for checking alarms"""
    scheduler = BackgroundScheduler()
    # CHANGED: Changed interval from 1 minute to 2 minutes to reduce database load
    scheduler.add_job(process_pending_alarms, 'interval', minutes=2, id='alarm_job', replace_existing=True)
    scheduler.start()
    # CHANGED: Updated log message to reflect new interval
    logger.info('APScheduler started for task alarms - checking every 2 minutes')
    return scheduler

if __name__ == "__main__":
    try:
        scheduler = start_scheduler()
        def run_health_app():
            health_app.run(host='0.0.0.0', port=5051)
        threading.Thread(target=run_health_app, daemon=True).start()
        logger.info('Task alarm service running. Health check at /health on port 5051')
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Task alarm service stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")