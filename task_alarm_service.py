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
FCM_API_URL = "https://fcm.googleapis.com/fcm/send"
FCM_SERVER_KEY = os.getenv('FCM_SERVER_KEY', '')  # Get this from your Firebase project settings

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
        
        # Get current date and time
        now = datetime.datetime.now()
        current_date = now.strftime('%Y-%m-%d')
        current_time = now.strftime('%H:%M:%S')
        
        # Query for alarms that should be triggered now
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
        
        cursor.execute(query, (now.strftime('%Y-%m-%d %H:%M:%S'),))
        alarms = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        return alarms
    except mysql.connector.Error as e:
        logger.error(f"Error getting pending alarms: {str(e)}")
        return []

def calculate_next_trigger_time(alarm):
    """Calculate the next trigger time based on frequency"""
    if not alarm['last_triggered']:
        # If never triggered, use start date and time
        start_datetime_str = f"{alarm['start_date']} {alarm['start_time']}"
        start_datetime = datetime.datetime.strptime(start_datetime_str, '%Y-%m-%d %H:%M:%S')
        return start_datetime
    
    # Convert last_triggered to datetime
    last_triggered = datetime.datetime.strptime(
        alarm['last_triggered'], '%Y-%m-%d %H:%M:%S'
    )
    
    # Calculate next trigger time based on frequency
    frequency = alarm['frequency']
    
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
        next_trigger = last_triggered + datetime.timedelta(hours=1)
    
    return next_trigger

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
    """Send FCM notification to user for the alarm"""
    try:
        if not alarm['fcm_token']:
            logger.warning(f"No FCM token for user ID: {alarm['user_id']}")
            return False
        
        # Prepare notification data
        notification = {
            "title": f"Task Alarm: {alarm['task_title']}",
            "body": alarm['task_description'],
            "sound": "alarm"
        }
        
        # Prepare data payload
        data = {
            "type": "task_alarm",
            "task_id": alarm['task_id'],
            "alarm_id": alarm['alarm_id'],
            "title": alarm['task_title'],
            "click_action": "FLUTTER_NOTIFICATION_CLICK"
        }
        
        # Prepare FCM message
        message = {
            "to": alarm['fcm_token'],
            "notification": notification,
            "data": data,
            "priority": "high",
            "android": {
                "priority": "high",
                "notification": {
                    "sound": "alarm",
                    "channel_id": "task_alarms"
                }
            },
            "apns": {
                "headers": {
                    "apns-priority": "10"
                },
                "payload": {
                    "aps": {
                        "sound": "alarm.wav"
                    }
                }
            }
        }
        
        # Send FCM request
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"key={FCM_SERVER_KEY}"
        }
        
        response = requests.post(
            FCM_API_URL,
            data=json.dumps(message),
            headers=headers
        )
        
        if response.status_code == 200:
            logger.info(f"Notification sent successfully to user ID: {alarm['user_id']}")
            return True
        else:
            logger.error(f"Failed to send notification: {response.text}")
            return False
    except Exception as e:
        logger.error(f"Error sending notification: {str(e)}")
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
        logger.info(f"Processing alarm ID: {alarm['alarm_id']} for task: {alarm['task_title']}")
        
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

def run_scheduler():
    """Run the scheduler process"""
    logger.info("Starting task alarm scheduler...")
    
    # Schedule to run every minute
    schedule.every(1).minutes.do(process_pending_alarms)
    
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    try:
        # Initial check for pending alarms
        process_pending_alarms()
        
        # Start scheduler
        run_scheduler()
    except KeyboardInterrupt:
        logger.info("Task alarm service stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}") 