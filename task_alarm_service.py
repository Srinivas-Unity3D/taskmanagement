#!/usr/bin/env python3
import mysql.connector
import time
import logging
import os
import json
import requests
import datetime
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
    # Check if credentials file path is provided
    creds_file = os.getenv('FIREBASE_CREDENTIALS_FILE')
    creds_json = os.getenv('FIREBASE_CREDENTIALS')
    
    if creds_file and os.path.exists(creds_file):
        # Initialize with service account file
        cred = credentials.Certificate(creds_file)
        firebase_admin.initialize_app(cred)
        logger.info(f"Firebase initialized with credentials file: {creds_file}")
    elif creds_json:
        # Initialize with credentials JSON string
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

def get_db_connection():
    """Get a database connection"""
    try:
        conn = mysql.connector.connect(**db_config)
        return conn
    except mysql.connector.Error as e:
        logger.error(f"Database connection error: {str(e)}")
        return None

def get_pending_alarms():
    """Get all pending alarms that should be triggered"""
    try:
        conn = get_db_connection()
        if not conn:
            return []
            
        cursor = conn.cursor(dictionary=True)
        
        # Get current date and time in UTC
        now = datetime.datetime.now(datetime.timezone.utc)
        
        # Convert UTC to IST (UTC+5:30)
        ist_offset = datetime.timedelta(hours=5, minutes=30)
        ist_now = now + ist_offset
        
        logger.info(f"Current UTC time: {now}")
        logger.info(f"Current IST time: {ist_now}")
        
        # Query for alarms that should be triggered now (using IST time)
        query = """
        SELECT 
            a.alarm_id, a.task_id, a.user_id, a.start_date, a.start_time, 
            a.frequency, a.last_triggered, a.next_trigger, a.is_active,
            t.title as task_title, t.description as task_description,
            u.fcm_token
        FROM 
            task_alarms a
        JOIN 
            tasks t ON a.task_id = t.task_id
        JOIN 
            users u ON a.user_id = u.user_id
        WHERE 
            a.is_active = 1 
            AND a.next_trigger <= %s
            AND u.fcm_token IS NOT NULL
        """
        
        # Use IST time for the query
        cursor.execute(query, (ist_now.strftime('%Y-%m-%d %H:%M:%S'),))
        alarms = cursor.fetchall()
        
        if alarms:
            logger.info(f"Found {len(alarms)} pending alarms")
            for alarm in alarms:
                logger.info(f"Alarm details: ID={alarm['alarm_id']}, Task={alarm['task_title']}, Next trigger={alarm['next_trigger']}")
        else:
            logger.info("No pending alarms found")
        
        cursor.close()
        conn.close()
        
        return alarms
    except mysql.connector.Error as e:
        logger.error(f"Error getting pending alarms: {str(e)}")
        return []

def calculate_next_trigger_time(alarm):
    """Calculate the next trigger time based on frequency"""
    try:
        if not alarm['last_triggered']:
            # If never triggered, use start date and time
            start_datetime_str = f"{alarm['start_date']} {alarm['start_time']}"
            start_datetime = datetime.datetime.strptime(start_datetime_str, '%Y-%m-%d %H:%M:%S')
            logger.info(f"First trigger time calculated: {start_datetime}")
            return start_datetime
        
        # Convert last_triggered to datetime
        last_triggered = datetime.datetime.strptime(
            alarm['last_triggered'], '%Y-%m-%d %H:%M:%S'
        )
        
        # Calculate next trigger time based on frequency
        frequency = alarm['frequency']
        logger.info(f"Calculating next trigger time for frequency: {frequency}")
        
        if frequency == '30 minutes':
            next_trigger = last_triggered + datetime.timedelta(minutes=30)
        elif frequency == '1 hour':
            next_trigger = last_triggered + datetime.timedelta(hours=1)
        elif frequency == '2 hours':
            next_trigger = last_triggered + datetime.timedelta(hours=2)
        elif frequency == '4 hours':
            next_trigger = last_triggered + datetime.timedelta(hours=4)
        elif frequency == '6 hours':
            next_trigger = last_triggered + datetime.timedelta(hours=6)
        elif frequency == '8 hours':
            next_trigger = last_triggered + datetime.timedelta(hours=8)
        else:
            # Default to 1 hour if frequency is not recognized
            logger.warning(f"Unrecognized frequency: {frequency}, defaulting to 1 hour")
            next_trigger = last_triggered + datetime.timedelta(hours=1)
        
        logger.info(f"Next trigger time calculated: {next_trigger}")
        return next_trigger
    except Exception as e:
        logger.error(f"Error calculating next trigger time: {str(e)}")
        raise

def update_alarm_status(alarm_id, last_triggered, next_trigger):
    """Update alarm status after triggering"""
    try:
        conn = get_db_connection()
        if not conn:
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
        cursor.close()
        conn.close()
        
        return True
    except mysql.connector.Error as e:
        logger.error(f"Error updating alarm status: {str(e)}")
        return False

def send_alarm_notification(alarm):
    """Send FCM notification to user for the alarm using Firebase Admin SDK"""
    try:
        if not alarm['fcm_token']:
            logger.warning(f"No FCM token for user ID: {alarm['user_id']}")
            return False
        
        # Log that we're sending an alarm
        logger.info(f"Sending alarm notification for task: {alarm['task_id']} to user: {alarm['user_id']}")
        
        # Use the messaging module from Firebase Admin SDK
        try:
            # Create the notification message
            message = messaging.Message(
                notification=messaging.Notification(
                    title=f"Task Alarm: {alarm['task_title']}",
                    body=alarm['task_description'] or "Time to check your task!"
                ),
                data={
                    "type": "task_alarm",
                    "task_id": alarm['task_id'],
                    "alarm_id": alarm['alarm_id'],
                    "title": alarm['task_title'],
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
            
            # Send the message
            response = messaging.send(message)
            logger.info(f"Successfully sent alarm notification: {response}")
            return True
        except Exception as e:
            logger.error(f"Error sending notification with Firebase Admin SDK: {e}")
            return False
            
    except Exception as e:
        logger.error(f"Error sending alarm notification: {str(e)}")
        return False

def process_pending_alarms():
    """Process all pending alarms that should be triggered now"""
    logger.info("Checking for pending alarms...")
    alarms = get_pending_alarms()
    
    if not alarms:
        logger.info("No pending alarms found")
        return
    
    logger.info(f"Found {len(alarms)} pending alarms to process")
    
    for alarm in alarms:
        try:
            logger.info(f"Processing alarm ID: {alarm['alarm_id']} for task: {alarm['task_title']}")
            logger.info(f"Alarm details: start_date={alarm['start_date']}, start_time={alarm['start_time']}, frequency={alarm['frequency']}")
            
            # Send notification
            notification_sent = send_alarm_notification(alarm)
            
            if notification_sent:
                # Calculate next trigger time
                now = datetime.datetime.now()
                next_trigger = calculate_next_trigger_time(alarm)
                
                # Update alarm status
                updated = update_alarm_status(alarm['alarm_id'], now, next_trigger)
                
                if updated:
                    logger.info(f"Alarm ID: {alarm['alarm_id']} processed successfully. Next trigger: {next_trigger}")
                else:
                    logger.error(f"Failed to update alarm ID: {alarm['alarm_id']}")
            else:
                logger.error(f"Failed to send notification for alarm ID: {alarm['alarm_id']}")
        except Exception as e:
            logger.error(f"Error processing alarm {alarm['alarm_id']}: {str(e)}")
            continue

# --- APScheduler integration ---
def start_scheduler():
    scheduler = BackgroundScheduler()
    # Run every minute to check for pending alarms
    scheduler.add_job(process_pending_alarms, 'interval', minutes=1, id='alarm_job', replace_existing=True)
    scheduler.start()
    logger.info('APScheduler started for task alarms.')
    return scheduler

if __name__ == "__main__":
    try:
        # Start the alarm scheduler
        scheduler = start_scheduler()

        # Start the health check HTTP server in a separate thread
        def run_health_app():
            health_app.run(host='0.0.0.0', port=5051)
        threading.Thread(target=run_health_app, daemon=True).start()

        logger.info('Task alarm service is running. Health check at /health on port 5051.')

        # Keep the main thread alive
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Task alarm service stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}") 