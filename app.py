from flask import Flask, request, jsonify, send_file, Response, send_from_directory
from flask_cors import CORS
import mysql.connector
import uuid
import logging
from datetime import datetime, timedelta
import json
import os
from dotenv import load_dotenv
import base64
import io
from werkzeug.utils import secure_filename
import shutil
from firebase_admin import messaging
import time
import threading
import pytz

# Load environment variables
load_dotenv()

# Configure upload folder
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
AUDIO_FOLDER = os.path.join(UPLOAD_FOLDER, 'audio')
ATTACHMENTS_FOLDER = os.path.join(UPLOAD_FOLDER, 'attachments')
ALLOWED_AUDIO_EXTENSIONS = {'wav', 'mp3', 'm4a', 'ogg'}

# Create upload directories if they don't exist
os.makedirs(AUDIO_FOLDER, exist_ok=True)
os.makedirs(ATTACHMENTS_FOLDER, exist_ok=True)

app = Flask(__name__)
CORS(app, resources={
    r"/*": {
        "origins": "*",
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization", "Accept"]
    }
})

# Configure Flask app
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Production configurations
app.config['DEBUG'] = True  # Enable debug for development
app.config['ENV'] = 'development'  # Set to development environment
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-secret-key')

# Add middleware to validate requests
@app.before_request
def validate_request():
    if request.method in ['POST', 'PUT']:
        if not request.is_json and request.headers.get('Content-Type') != 'application/json':
            return 'Content-Type must be application/json', 400

def notify_task_update(task_data, event_type='task_update'):
    """Notify relevant users about task updates using Firebase Cloud Messaging"""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        assigned_to = task_data.get('assigned_to')
        assigned_by = task_data.get('assigned_by')
        updated_by = task_data.get('updated_by')
        
        # Skip if no target users
        if not assigned_to and not assigned_by:
            logger.info("No users to notify")
            return
            
        # Get sender's role
        cursor.execute("SELECT role FROM users WHERE username = %s", (updated_by or assigned_by,))
        result = cursor.fetchone()
        sender_role = result['role'] if result else 'User'
        
        notification_data = {
            'task': task_data,
            'type': event_type,
            'sender_role': sender_role,
            'timestamp': datetime.now().isoformat()
        }
        
        # Send Firebase notifications to relevant users
        if assigned_to and assigned_to != updated_by:
            logger.info(f"Notifying assigned user: {assigned_to}")
            store_notification(task_data, event_type, assigned_to, sender_role)
            # Send Firebase notification to assigned_to user
            
        if assigned_by and assigned_by != updated_by and assigned_by != assigned_to:
            logger.info(f"Notifying assigner: {assigned_by}")
            store_notification(task_data, event_type, assigned_by, sender_role)
            # Send Firebase notification to assigned_by user
            
    except Exception as e:
        logger.error(f"Error in notify_task_update: {str(e)}")
        raise
        
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# ---------------- MAIN ----------------
if __name__ == '__main__':
    # Initialize the application
    initialize_application()
    
    # Run the server
    app.run(host='0.0.0.0', port=5000, debug=True)