# Apply eventlet monkey patching first

#example

from gevent import monkey
monkey.patch_all()

from flask import Flask, request, jsonify, send_file, Response, send_from_directory, current_app, g
from flask_cors import CORS
from flask_jwt_extended import JWTManager, get_jwt_identity, jwt_required, create_access_token, create_refresh_token, get_jwt
from flask_socketio import SocketIO, join_room, emit, disconnect
import mysql.connector
from mysql.connector import pooling
import uuid
import logging
from datetime import datetime, timedelta, timezone
from flask_socketio import SocketIO, emit
import json
import os
from dotenv import load_dotenv
import base64
import io
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash, generate_password_hash
import shutil
from firebase_admin import messaging, initialize_app, credentials
import time
import threading
import pytz
import requests
from auth import generate_tokens, jwt_token_required, role_required
import sys
import hashlib
from config.config import Config
import firebase_admin
from functools import wraps
import csv

def verify_password(provided_password, stored_password):
    """Verify the hashed password."""
    if not provided_password or not stored_password:
        return False
    try:
        # Check if the password is stored in scrypt format
        if stored_password.startswith('scrypt$'):
            # Extract parameters from stored password
            parts = stored_password.split('$')
            if len(parts) != 4:
                return False
            salt = base64.b64decode(parts[2])
            stored_hash = base64.b64decode(parts[3])
            
            # Hash the provided password with the same parameters
            key = hashlib.scrypt(
                provided_password.encode(),
                salt=salt,
                n=2**14,  # CPU/memory cost parameter
                r=8,      # Block size parameter
                p=1,      # Parallelization parameter
                dklen=32  # Length of the derived key
            )
            return key == stored_hash
        else:
            # Fallback to Werkzeug's check_password_hash for backward compatibility
            return check_password_hash(stored_password, provided_password)
    except Exception as e:
        logger.error(f"Password verification error: {str(e)}")
        return False

def convert_to_utc(datetime_str):
    """Convert a datetime string from IST to UTC"""
    try:
        dt = datetime.strptime(datetime_str, '%Y-%m-%d %H:%M:%S')
        ist = timezone(timedelta(hours=5, minutes=30))
        dt = dt.replace(tzinfo=ist)
        utc_dt = dt.astimezone(timezone.utc)
        return utc_dt
    except Exception as e:
        logger.error(f"Error converting datetime to UTC: {str(e)}")
        raise

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Initialize Firebase Admin SDK
try:
    # Check if Firebase credentials are available
    if not os.getenv('FIREBASE_CREDENTIALS'):
        logger.error("FIREBASE_CREDENTIALS environment variable not set - Firebase features will be disabled")
        raise ValueError("FIREBASE_CREDENTIALS environment variable not set")
    else:
        # Parse the credentials from environment variable
        with open("firebase-credentials.json") as f: cred_dict = json.load(f)
        cred = credentials.Certificate(cred_dict)
        # Check if Firebase is already initialized
        try:
            firebase_admin.get_app()
            logger.info("Firebase Admin SDK already initialized")
        except ValueError:
            firebase_admin.initialize_app(cred)
            logger.info("Firebase Admin SDK initialized successfully")
except Exception as e:
    logger.error(f"Firebase initialization failed: {str(e)}")
    raise  # Re-raise the exception to prevent the app from starting without Firebase

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

# Environment-based configurations
app.config['DEBUG'] = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
app.config['ENV'] = os.getenv('FLASK_ENV', 'production')
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-secret-key')

# JWT Configuration
# Use environment variables or fallback to old JWT settings
jwt_secret = os.getenv('JWT_SECRET_KEY', 'your_secret_key_here')
app.config['JWT_SECRET_KEY'] = jwt_secret
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=24)  # Default 24 hours
app.config['JWT_REFRESH_TOKEN_EXPIRES'] = timedelta(days=30)  # Default 30 days
app.config['JWT_TOKEN_LOCATION'] = ['headers']
app.config['JWT_HEADER_NAME'] = 'Authorization'
app.config['JWT_HEADER_TYPE'] = 'Bearer'
app.config['JWT_IDENTITY_CLAIM'] = 'sub'
# For debugging only - remove in production
app.config['JWT_DECODE_ALGORITHMS'] = ['HS256']
app.config['PROPAGATE_EXCEPTIONS'] = True

# Print JWT configuration for debugging
logger.info(f"JWT_SECRET_KEY: {'*****' + jwt_secret[-5:] if len(jwt_secret) > 5 else '*****'}")
logger.info(f"JWT_ACCESS_TOKEN_EXPIRES: {app.config['JWT_ACCESS_TOKEN_EXPIRES']}")

# Initialize JWT
jwt = JWTManager(app)

# DB config using environment variables
db_config = {
    'host': os.getenv('DB_HOST', '134.209.149.12'),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', '123'),
    'database': os.getenv('DB_NAME', 'task_db'),
    'pool_name': 'mypool',
    'pool_size': 5,
    'connect_timeout': 10,
    'use_pure': True,
    'autocommit': True,
    'get_warnings': True,
    'raise_on_warnings': True,
    'connection_timeout': 180,
    'pool_reset_session': True
}

# Initialize database first
def init_db():
    try:
        # First connect without database to create it if needed
        temp_config = db_config.copy()
        temp_config.pop('database', None)  # Remove database from config
        conn = mysql.connector.connect(**temp_config)
        cursor = conn.cursor()

        # Create database if it doesn't exist
        try:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS {db_config['database']}")
            conn.commit()
        except mysql.connector.Error as e:
            if e.errno == 1007:  # Database exists
                logger.warning(f"Database already exists: {db_config['database']}")
            else:
                raise

        # Now connect to the specific database
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute(f"USE {db_config['database']}")

        # Enable foreign key checks
        cursor.execute("SET FOREIGN_KEY_CHECKS=0")

        # Create tables if they don't exist
        try:
            # Create users table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id VARCHAR(36) PRIMARY KEY,
                    username VARCHAR(100) UNIQUE NOT NULL,
                    email VARCHAR(255) NOT NULL,
                    phone VARCHAR(20) NOT NULL,
                    password VARCHAR(255) NOT NULL,
                    role VARCHAR(50) NOT NULL,
                    fcm_token VARCHAR(255),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    timezone VARCHAR(50)
                )
            """)

            # Create user_tokens table for JWT token storage
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_tokens (
                    id VARCHAR(36) PRIMARY KEY,
                    user_id VARCHAR(36) NOT NULL,
                    access_token TEXT NOT NULL,
                    refresh_token TEXT NOT NULL,
                    access_token_expires_at TIMESTAMP NOT NULL,
                    refresh_token_expires_at TIMESTAMP NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_revoked BOOLEAN DEFAULT FALSE,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    INDEX idx_user_tokens (user_id, is_revoked)
                )
            """)

            # Create tasks table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id VARCHAR(36) PRIMARY KEY,
                    title VARCHAR(255) NOT NULL,
                    description TEXT,
                    assigned_by VARCHAR(100) NOT NULL,
                    assigned_to VARCHAR(100) NOT NULL,
                    deadline DATETIME NOT NULL,
                    priority ENUM('low', 'medium', 'high', 'urgent') NOT NULL,
                    status ENUM('pending', 'in_progress', 'completed', 'snoozed') NOT NULL DEFAULT 'pending',
                    start_date DATE,
                    start_time TIME,
                    frequency VARCHAR(50),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (assigned_by) REFERENCES users(username) ON DELETE CASCADE ON UPDATE CASCADE,
                    FOREIGN KEY (assigned_to) REFERENCES users(username) ON DELETE CASCADE ON UPDATE CASCADE
                )
            """)

            # Create task_audio_notes table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS task_audio_notes (
                    audio_id VARCHAR(36) PRIMARY KEY,
                    task_id VARCHAR(36) NOT NULL,
                    file_path VARCHAR(255) NOT NULL,
                    file_name VARCHAR(255) NOT NULL DEFAULT 'voice_note.wav',
                    duration INT,
                    created_by VARCHAR(100),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    note_type VARCHAR(20) NOT NULL DEFAULT 'normal',  -- 'normal' or 'snooze'
                    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE,
                    FOREIGN KEY (created_by) REFERENCES users(username) ON DELETE SET NULL ON UPDATE CASCADE
                )
            """)

            # Create task_notifications table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS task_notifications (
                    id VARCHAR(36) PRIMARY KEY,
                    task_id VARCHAR(36) NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    description TEXT,
                    sender_name VARCHAR(100) NOT NULL,
                    sender_role VARCHAR(50) NOT NULL,
                    type VARCHAR(50) NOT NULL,
                    is_read BOOLEAN DEFAULT FALSE,
                    status VARCHAR(50) DEFAULT 'active',
                    snooze_until DATETIME,
                    snooze_reason TEXT,
                    snooze_audio LONGTEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    target_user VARCHAR(100) NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE ON UPDATE CASCADE,
                    FOREIGN KEY (sender_name) REFERENCES users(username) ON DELETE CASCADE ON UPDATE CASCADE,
                    FOREIGN KEY (target_user) REFERENCES users(username) ON DELETE CASCADE ON UPDATE CASCADE
                )
            """)

            # Create attachments table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS task_attachments (
                    attachment_id VARCHAR(36) PRIMARY KEY,
                    task_id VARCHAR(36) NOT NULL,
                    file_name VARCHAR(255) NOT NULL,
                    file_type VARCHAR(50),
                    file_size BIGINT,
                    file_path VARCHAR(255) NOT NULL,
                    created_by VARCHAR(100),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE ON UPDATE CASCADE,
                    FOREIGN KEY (created_by) REFERENCES users(username) ON DELETE SET NULL ON UPDATE CASCADE
                )
            """)

            # Create task_alarms table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS task_alarms (
                    alarm_id VARCHAR(36) PRIMARY KEY,
                    task_id VARCHAR(36) NOT NULL,
                    user_id VARCHAR(36) NOT NULL,
                    start_date DATE NOT NULL,
                    start_time TIME NOT NULL,
                    frequency VARCHAR(50) NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE,
                    last_triggered TIMESTAMP NULL,
                    next_trigger TIMESTAMP NOT NULL,
                    acknowledged BOOLEAN DEFAULT FALSE,
                    acknowledged_at TIMESTAMP NULL,
                    created_by VARCHAR(100),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE ON UPDATE CASCADE,
                    FOREIGN KEY (created_by) REFERENCES users(username) ON DELETE SET NULL ON UPDATE CASCADE,
                    INDEX idx_next_trigger (next_trigger, is_active),
                    INDEX idx_task_id (task_id),
                    INDEX idx_is_active (is_active)
                )
            """)
        except mysql.connector.Error as e:
            if e.errno == 1050:  # Table exists
                logger.warning(f"Table already exists: {e.msg}")
            else:
                raise

        # Re-enable foreign key checks
        cursor.execute("SET FOREIGN_KEY_CHECKS=1")

        conn.commit()
        logger.info("Database tables initialized successfully")

    except Exception as e:
        logger.error(f"Error initializing database: {str(e)}")
        if 'conn' in locals():
            conn.rollback()
        raise
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()

# Now, after the function definition, call init_db()
try:
    init_db()
    logger.info("Database initialized successfully")
except Exception as e:
    logger.error(f"Error initializing database: {str(e)}")
    raise

# Create connection pool after database initialization
try:
    connection_pool = mysql.connector.pooling.MySQLConnectionPool(**db_config)
    logger.info("Database connection pool created successfully")
except Exception as e:
    logger.error(f"Error creating connection pool: {str(e)}")
    raise

def get_db_connection():
    """Get a connection from the pool with retry logic"""
    global connection_pool
    max_retries = 3
    retry_delay = 1  # seconds
    
    for attempt in range(max_retries):
        try:
            connection = connection_pool.get_connection()
            if connection.is_connected():
                return connection
        except mysql.connector.Error as err:
            logger.error(f"Database connection attempt {attempt + 1} failed: {str(err)}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                raise
    
    raise Exception("Failed to get database connection after multiple attempts")

# Add a periodic connection check
def check_db_connection():
    """Periodically check database connection and recreate pool if needed"""
    global connection_pool
    while True:
        try:
            conn = get_db_connection()
            if conn.is_connected():
                conn.close()
                logger.info("Database connection check successful")
            else:
                logger.warning("Database connection lost, attempting to reconnect...")
                connection_pool = mysql.connector.pooling.MySQLConnectionPool(**db_config)
        except Exception as e:
            logger.error(f"Database connection check failed: {str(e)}")
            try:
                connection_pool = mysql.connector.pooling.MySQLConnectionPool(**db_config)
            except Exception as pool_err:
                logger.error(f"Failed to recreate connection pool: {str(pool_err)}")
        
        time.sleep(300)  # Check every 5 minutes

# Start the connection check thread
connection_check_thread = threading.Thread(target=check_db_connection, daemon=True)
connection_check_thread.start()

# Configure Flask app
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Updated SocketIO configuration
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='gevent',  # Changed from gevent to eventlet
    ping_timeout=120,
    ping_interval=25,
    logger=True,
    engineio_logger=True,
    always_connect=True,
    reconnection=True,
    reconnection_attempts=10,
    reconnection_delay=1000,
    reconnection_delay_max=5000,
    max_http_buffer_size=1e8
)

# Add middleware to validate requests
@app.before_request
def validate_request():
    if request.path.startswith('/socket.io/'):
        return  # Skip validation for socket.io requests
    # Allow multipart/form-data for file uploads
    if request.path.startswith('/upload'):
        return
    # Validate content type for non-WebSocket requests
    if request.method in ['POST', 'PUT']:
        if not request.is_json and request.headers.get('Content-Type') != 'application/json':
            return 'Content-Type must be application/json', 400

# Enhanced error handlers for SocketIO
@socketio.on_error()
def error_handler(e):
    logger.error(f"SocketIO error: {str(e)}")
    logger.exception("Socket error traceback:")
    return {'error': str(e)}

@socketio.on_error_default
def default_error_handler(e):
    logger.error(f"SocketIO default error: {str(e)}")
    logger.exception("Socket default error traceback:")
    return {'error': str(e)}

# Initialize connected users dictionary
connected_users = {}
last_heartbeat = {}  # Track last heartbeat for each connection

@socketio.on('connect')
def handle_connect():
    try:
        sid = request.sid
        logger.info(f"Client connected: {sid}")
        # Add client IP logging for debugging
        logger.info(f"Client IP: {request.remote_addr}")
        # Store initial heartbeat timestamp
        last_heartbeat[sid] = time.time()
        return True
    except Exception as e:
        logger.error(f"Error in handle_connect: {str(e)}")
        logger.exception("Full traceback:")
        return False

@socketio.on('disconnect')
def handle_disconnect(sid=None):
    try:
        sid = sid or request.sid
        if not sid:
            logger.error("Invalid disconnect - no SID")
            return
            
        # Find and remove the disconnected user
        username_to_remove = None
        for username, connected_sid in connected_users.items():
            if connected_sid == sid:
                username_to_remove = username
                break
        
        if username_to_remove:
            logger.info(f"User {username_to_remove} disconnected")
            del connected_users[username_to_remove]
        
        # Clean up heartbeat tracking
        if sid in last_heartbeat:
            del last_heartbeat[sid]
            
        logger.info(f"Client disconnected: {sid}")
        logger.info(f"Current connected users: {connected_users}")
    except Exception as e:
        logger.error(f"Error in handle_disconnect: {str(e)}")
        logger.exception("Full traceback:")

@socketio.on('heartbeat')
def handle_heartbeat(data=None):
    """Handle heartbeat events from clients"""
    try:
        sid = request.sid
        last_heartbeat[sid] = time.time()
        return {'status': 'ok', 'timestamp': time.time()}
    except Exception as e:
        logger.error(f"Error in handle_heartbeat: {str(e)}")
        return {'status': 'error', 'message': str(e)}

@socketio.on('register')
def handle_register(data):
    try:
        # Handle both string and dictionary formats
        username = data if isinstance(data, str) else data.get('username')
        if not username:
            logger.error("No username provided for registration")
            return
        
        logger.info(f"Registering user {username} with sid {request.sid}")
        
        # Check if user is already registered with a different sid
        if username in connected_users and connected_users[username] != request.sid:
            old_sid = connected_users[username]
            logger.info(f"User {username} already registered with sid {old_sid}, updating to {request.sid}")
            
            # Clean up old heartbeat if exists
            if old_sid in last_heartbeat:
                del last_heartbeat[old_sid]
        
        connected_users[username] = request.sid
        last_heartbeat[request.sid] = time.time()
        
        logger.info(f"Current connected users: {connected_users}")
        
        socketio.emit('register_response', {
            'status': 'registered',
            'username': username,
            'sid': request.sid
        }, room=request.sid)
    except Exception as e:
        logger.error(f"Error in handle_register: {str(e)}")
        logger.exception("Full traceback:")

# Add timezone configuration
DEFAULT_TIMEZONE = 'Asia/Kolkata'  # Change this to your default timezone

def get_current_time():
    """Get current time in the configured timezone"""
    tz = pytz.timezone(DEFAULT_TIMEZONE)
    return datetime.now(tz)

def convert_to_timezone(dt, to_tz=DEFAULT_TIMEZONE):
    """Convert datetime to specified timezone"""
    try:
        if dt.tzinfo is None:
            dt = pytz.UTC.localize(dt)
        return dt.astimezone(pytz.timezone(to_tz))
    except Exception as e:
        logger.error(f"Error converting timezone: {str(e)}")
        return dt

def notify_task_update(task_data, event_type='task_update'):
    """Notify relevant users about task updates"""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"USE {db_config['database']}")
        
        assigned_to = task_data.get('assigned_to')
        assigned_by = task_data.get('assigned_by')
        updated_by = task_data.get('updated_by')
        
        logger.info(f"Task update notification - Assigned to: {assigned_to}, Assigned by: {assigned_by}, Updated by: {updated_by}")
        logger.info(f"Current connected users: {connected_users}")
        
        # Skip if no target users
        if not assigned_to and not assigned_by:
            logger.info("No users to notify")
            return
            
        # Get sender's role
        cursor.execute("SELECT role FROM users WHERE username = %s", (updated_by or assigned_by,))
        result = cursor.fetchone()
        sender_role = result['role'] if result else 'User'
        logger.info(f"Sender role: {sender_role}")
        
        notification_data = {
            'task': task_data,
            'type': event_type,
            'sender_role': sender_role,
            'timestamp': datetime.now().isoformat()
        }
        
        # Only notify assigned_to if they didn't make the update
        if assigned_to and assigned_to != updated_by:
            logger.info(f"Attempting to notify assigned user: {assigned_to}")
            if assigned_to in connected_users:
                logger.info(f"Found socket for {assigned_to}: {connected_users[assigned_to]}")
                socketio.emit('task_notification', notification_data, room=connected_users[assigned_to])
                logger.info(f"Notification sent to {assigned_to}")
            else:
                logger.warning(f"User {assigned_to} not connected, notification not sent")
            store_notification(task_data, event_type, assigned_to, sender_role)
            logger.info(f"Notification stored for {assigned_to}")
        
        # Only notify assigned_by if they didn't make the update and aren't the assignee
        if assigned_by and assigned_by != updated_by and assigned_by != assigned_to:
            logger.info(f"Attempting to notify assigner: {assigned_by}")
            if assigned_by in connected_users:
                logger.info(f"Found socket for {assigned_by}: {connected_users[assigned_by]}")
                socketio.emit('task_notification', notification_data, room=connected_users[assigned_by])
                logger.info(f"Notification sent to {assigned_by}")
            else:
                logger.warning(f"User {assigned_by} not connected, notification not sent")
            store_notification(task_data, event_type, assigned_by, sender_role)
            logger.info(f"Notification stored for {assigned_by}")
        
        # Broadcast dashboard update to all connected users except the updater
        logger.info("Broadcasting dashboard update")
        for username, sid in connected_users.items():
            if username != updated_by:
                logger.info(f"Sending dashboard update to {username}")
                socketio.emit('dashboard_update', notification_data, room=sid)
                logger.info(f"Dashboard update sent to {username}")
        
    except Exception as e:
        logger.error(f"Error in notify_task_update: {str(e)}")
        logger.exception("Full traceback:")
        raise
        
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# Initialize database on startup
def update_tasks_table():
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute(f"USE {db_config['database']}")

        # Add alarm-related columns if they don't exist
        try:
            # Check if columns exist
            cursor.execute("""
                SELECT COUNT(*) as count
                FROM information_schema.columns
                WHERE table_schema = %s
                AND table_name = 'tasks'
                AND column_name IN ('start_date', 'start_time', 'frequency')
            """, (db_config['database'],))
            
            result = cursor.fetchone()
            if result[0] < 3:  # If any column is missing
                # Add columns one by one
                try:
                    cursor.execute("ALTER TABLE tasks ADD COLUMN start_date DATE")
                except mysql.connector.Error as e:
                    if e.errno != 1060:  # Ignore "column already exists" error
                        raise e
                
                try:
                    cursor.execute("ALTER TABLE tasks ADD COLUMN start_time TIME")
                except mysql.connector.Error as e:
                    if e.errno != 1060:
                        raise e
                
                try:
                    cursor.execute("ALTER TABLE tasks ADD COLUMN frequency VARCHAR(50)")
                except mysql.connector.Error as e:
                    if e.errno != 1060:
                        raise e
                
                conn.commit()
                logger.info("Added alarm-related columns")
        except mysql.connector.Error as e:
            logger.error(f"Error adding alarm columns: {str(e)}")
            raise e

        # Check if timestamp columns exist
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM information_schema.columns
            WHERE table_schema = %s
            AND table_name = 'tasks'
            AND column_name IN ('created_at', 'updated_at')
        """, (db_config['database'],))

        result = cursor.fetchone()
        if result[0] < 2:  # If either column is missing
            logger.info("Adding timestamp columns to tasks table...")

            try:
                cursor.execute("""
                    ALTER TABLE tasks
                    ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                """)
                logger.info("Added created_at column")
            except mysql.connector.Error as e:
                if e.errno != 1060:  # Ignore "column already exists" error
                    raise e

            try:
                cursor.execute("""
                    ALTER TABLE tasks
                    ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                """)
                logger.info("Added updated_at column")
            except mysql.connector.Error as e:
                if e.errno != 1060:  # Ignore "column already exists" error
                    raise e

            conn.commit()
            logger.info("Successfully updated tasks table schema")
        else:
            logger.info("Timestamp columns already exist in tasks table")

    except Exception as e:
        logger.error(f"Error updating tasks table: {str(e)}")
        if conn:
            conn.rollback()
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def update_audio_notes_table():
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute(f"USE {db_config['database']}")

        # Check if note_type column exists
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM information_schema.columns
            WHERE table_schema = %s
            AND table_name = 'task_audio_notes'
            AND column_name = 'note_type'
        """, (db_config['database'],))

        result = cursor.fetchone()
        if result[0] == 0:  # Column doesn't exist
            logger.info("Adding note_type column to task_audio_notes table...")
            try:
                cursor.execute("""
                    ALTER TABLE task_audio_notes
                    ADD COLUMN note_type VARCHAR(20) NOT NULL DEFAULT 'normal'
                """)
                conn.commit()
                logger.info("Successfully added note_type column")
            except mysql.connector.Error as e:
                if e.errno != 1060:  # Ignore "column already exists" error
                    raise e
        else:
            logger.info("note_type column already exists in task_audio_notes table")

    except Exception as e:
        logger.error(f"Error updating task_audio_notes table: {str(e)}")
        if conn:
            conn.rollback()
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# Initialize database on startup
def initialize_application():
    """Initialize the application and database"""
    try:
        # Initialize database tables
        init_db()
        
        # Update tables with any new columns
        update_tasks_table()
        update_audio_notes_table()
        
        logger.info("Application initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing application: {str(e)}")
        raise

@app.route('/')
def home():
    return "API is running. Use /tasks or /create_task"

print("Registering /trigger_alarm_test route")
@app.route('/trigger_alarm_test', methods=['GET'])
def trigger_alarm_test():
    username = request.args.get('username')
    if not username:
        return jsonify({'success': False, 'message': 'username required'}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"USE {db_config['database']}")

        # Get user FCM token
        cursor.execute("SELECT fcm_token FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()
        if not user or not user['fcm_token']:
            return jsonify({'success': False, 'message': 'User or FCM token not found'}), 404

        # Dummy task info for testing
        task_title = "Test Alarm"
        task_description = "This is a test alarm triggered from the browser."
        task_id = "test-alarm-id"

        # Build FCM message
        message = messaging.Message(
            notification=messaging.Notification(
                title=f"Task Alarm: {task_title}",
                body=task_description
            ),
            data={
                "type": "task_alarm",
                "task_id": task_id,
                "title": task_title,
                "immediate_alarm": "true"
            },
            token=user['fcm_token']
        )

        # Send FCM
        response = messaging.send(message)
        return jsonify({'success': True, 'message': 'Alarm triggered', 'fcm_response': response}), 200

    except Exception as e:
        logger.error(f"Error in trigger_alarm_test: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()

# ---------------- SIGNUP ----------------
@app.route('/signup', methods=['POST'])
def signup():
    conn = None
    cursor = None
    try:
        data = request.get_json()
        required_fields = ['username', 'email', 'phone', 'password', 'role']
        if not all(field in data for field in required_fields):
            return jsonify({'message': 'Missing required fields'}), 400

        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"USE {db_config['database']}")

        cursor.execute("SELECT * FROM users WHERE username = %s", (data['username'],))
        if cursor.fetchone():
            return jsonify({'message': 'User already exists'}), 409

        user_id = str(uuid.uuid4())
        # Hash the password using scrypt
        salt = os.urandom(16)
        hashed = hashlib.scrypt(
            data['password'].encode(),
            salt=salt,
            n=2**14,
            r=8,
            p=1,
            dklen=32
        )
        password_hash = f"scrypt$16384$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(hashed).decode()

        cursor.execute("""
            INSERT INTO users (user_id, username, email, phone, password, role, fcm_token, timezone)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            user_id, data['username'], data['email'], data['phone'],
            password_hash, data['role'], data.get('fcm_token', ''), data.get('timezone', DEFAULT_TIMEZONE)
        ))
        conn.commit()
        return jsonify({'message': 'Signup successful'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# ---------------- LOGIN ----------------
@app.route('/login', methods=['POST'])
def login():
    try:
        data = request.get_json()
        username = data.get('username')
        password = data.get('password')
        fcm_token = data.get('fcm_token', '')

        logger.info(f"Login attempt for user: {username}")
        logger.info(f"FCM token provided: {fcm_token[:20]}..." if fcm_token else "No FCM token")

        if not username or not password:
            return jsonify({'message': 'Missing username or password'}), 400

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Get user details
        cursor.execute(
            "SELECT user_id, username, password, role, email, phone FROM users WHERE username = %s",
            (username,)
        )
        user = cursor.fetchone()
        
        logger.debug(f"Found user in database: {user is not None}")

        # Check if user exists and password matches
        if not user:
            logger.warning(f"User not found: {username}")
            return jsonify({'message': 'Invalid username or password'}), 401

        # Verify password using scrypt
        if not verify_password(password, user['password']):
            logger.warning(f"Invalid password for user: {username}")
            return jsonify({'message': 'Invalid username or password'}), 401

        # Update FCM token if provided
        if fcm_token:
            cursor.execute(
                "UPDATE users SET fcm_token = %s WHERE user_id = %s",
                (fcm_token, user['user_id'])
            )
            conn.commit()

        # Generate tokens
        tokens = generate_tokens(user)
        
        # Combine user info with tokens
        response = {
            'user_id': user['user_id'],
            'username': user['username'],
            'email': user['email'],
            'phone': user['phone'],
            'role': user['role'],
            'access_token': tokens['access_token'],
            'refresh_token': tokens['refresh_token'],
            'expires_in': tokens['expires_in']
        }
        
        return jsonify(response), 200

    except Exception as e:
        logger.error(f"Login error: {str(e)}")
        return jsonify({'message': 'An error occurred during login'}), 500

    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()

@app.route('/refresh_token', methods=['POST'])
@jwt_required(refresh=True)
def refresh_token():
    try:
        # Get current user identity from refresh token
        current_user = get_jwt_identity()
        
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Get user details and verify refresh token
        cursor.execute("""
            SELECT user_id, username, role, refresh_token 
            FROM users 
            WHERE username = %s AND refresh_token IS NOT NULL
            AND token_expires_at > NOW()
        """, (current_user,))
        
        user = cursor.fetchone()
        if not user:
            return jsonify({'message': 'Invalid or expired refresh token'}), 401

        # Generate new tokens
        try:
            tokens = generate_tokens(user)
            new_access_token = tokens['access_token']
            new_refresh_token = tokens['refresh_token']
            expires_in = tokens['expires_in']
        except Exception as e:
            logger.error(f"Error generating refresh tokens: {str(e)}")
            raise
        
        # Calculate new expiry time
        token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

        # Update user with new tokens
        cursor.execute("""
            UPDATE users 
            SET access_token = %s,
                refresh_token = %s,
                token_expires_at = %s
            WHERE username = %s
        """, (
            new_access_token,
            new_refresh_token,
            token_expires_at,
            current_user
        ))

        conn.commit()

        return jsonify({
            'access_token': new_access_token,
            'refresh_token': new_refresh_token,
            'expires_in': expires_in
        }), 200

    except Exception as e:
        logger.error(f"Token refresh error: {str(e)}")
        return jsonify({'message': 'An error occurred while refreshing token'}), 500

@app.route('/logout', methods=['POST'])
@jwt_required()
def logout():
    try:
        current_user = get_jwt_identity()
        
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Clear tokens for the user
        cursor.execute("""
            UPDATE users 
            SET access_token = NULL,
                refresh_token = NULL,
                token_expires_at = NULL
            WHERE username = %s
        """, (current_user,))

        conn.commit()

        return jsonify({'message': 'Successfully logged out'}), 200

    except Exception as e:
        logger.error(f"Logout error: {str(e)}")
        return jsonify({'message': 'An error occurred during logout'}), 500

# Add a periodic task to clean up expired tokens
def cleanup_expired_tokens():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Delete expired tokens
        cursor.execute("""
            DELETE FROM user_tokens 
            WHERE refresh_token_expires_at < NOW() 
            OR (is_revoked = TRUE AND created_at < DATE_SUB(NOW(), INTERVAL 30 DAY))
        """)

        conn.commit()
        logger.info("Cleaned up expired tokens")

    except Exception as e:
        logger.error(f"Token cleanup error: {str(e)}")

# Add token cleanup to the initialization
def initialize_application():
    try:
        init_db()
        # Start token cleanup thread
        def run_token_cleanup():
            while True:
                cleanup_expired_tokens()
                time.sleep(3600)  # Run every hour
        
        cleanup_thread = threading.Thread(target=run_token_cleanup, daemon=True)
        cleanup_thread.start()
        
        logger.info("Application initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing application: {str(e)}")
        sys.exit(1)

# ---------------- CREATE TASK ----------------
@app.route('/tasks', methods=['POST'])
@jwt_required()
def create_task():
    conn = None
    cursor = None
    try:
        # Get user identity from token
        current_user = get_jwt_identity()
        logger.info(f"Create task request by user: {current_user}")
        
        data = request.get_json()
        logger.debug(f"Task creation data: {data}")
        
        # Validate required fields
        required_fields = ['title', 'description', 'assigned_to', 'assigned_by', 'priority', 'status', 'deadline']
        for field in required_fields:
            if field not in data:
                logger.warning(f"Missing required field in task creation: {field}")
                return jsonify({
                    'success': False,
                    'message': f'Missing required field: {field}'
                }), 400

        title = data['title']
        description = data['description']
        assigned_to = data['assigned_to']
        assigned_by = data['assigned_by']
        priority = data['priority']
        status = data['status']
        deadline = data['deadline']
        start_date = data.get('start_date')
        start_time = data.get('start_time')
        frequency = data.get('frequency')
        audio_note = data.get('audio_note')
        attachments = data.get('attachments', [])
        alarm_settings = data.get('alarm_settings')
        
        logger.info(f"Creating task: {title} for {assigned_to}, alarm settings: {alarm_settings}")

        # Initialize database connection
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"USE {db_config['database']}")

        # Generate task ID
        task_id = str(uuid.uuid4())

        # Insert task with new columns
        cursor.execute("""
            INSERT INTO tasks (
                task_id, title, description, assigned_to,
                assigned_by, priority, status, deadline,
                start_date, start_time, frequency
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            task_id, "check title", description, assigned_to,
            assigned_by, priority, status, deadline,
            start_date, start_time, frequency
        ))

        # Handle audio note
        if audio_note:
            try:
                logger.info(f"Processing audio note for task update: {task_id}")
                logger.debug(f"Audio note data: {audio_note}")
                
                file_id = audio_note.get('file_id')
                audio_data = audio_note.get('audio_data')
                duration = audio_note.get('duration', 0)
                file_name = audio_note.get('filename', 'voice_note.wav')
                created_by = data.get('updated_by', assigned_by)
                
                logger.info(f"Audio note details - file_id: {file_id}, data length: {'Yes' if audio_data else 'No'}, duration: {duration}, filename: {file_name}")
                
                # Ensure file extension is .wav
                if not file_name.lower().endswith('.wav'):
                    name_parts = file_name.rsplit('.', 1)
                    file_name = name_parts[0] + '.wav'
                    logger.info(f"Changed audio file extension to .wav: {file_name}")
                
                # Check if we have a file_id from a previous upload
                if file_id:
                    # Use the existing file_id as audio_id
                    audio_id = file_id
                    # The file should already be at this location from the upload endpoint
                    audio_path = os.path.join('uploads', 'audio', f"{audio_id}_{file_name}")
                    logger.info(f"Using pre-uploaded audio file with ID: {file_id}, path: {audio_path}")
                    
                    # Insert audio note record
                    cursor.execute("""
                        INSERT INTO task_audio_notes (
                            audio_id, task_id, file_path, duration, 
                            file_name, created_by, note_type
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (
                        audio_id, task_id, audio_path, duration,
                        file_name, created_by, 'normal'
                    ))
                    logger.info(f"Added audio note with existing file_id: {audio_id}")
                
                elif audio_data:
                    # Save audio file
                    audio_id = str(uuid.uuid4())
                    audio_path = os.path.join(AUDIO_FOLDER, f"{audio_id}_{file_name}")
                    logger.info(f"Saving direct audio data with new ID: {audio_id}, path: {audio_path}")
                    
                    os.makedirs(AUDIO_FOLDER, exist_ok=True)
                    with open(audio_path, 'wb') as f:
                        f.write(base64.b64decode(audio_data))
                    
                    # Insert audio note record
                    cursor.execute("""
                        INSERT INTO task_audio_notes (
                            audio_id, task_id, file_path, duration, 
                            file_name, created_by, note_type
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (
                        audio_id, task_id, audio_path, duration,
                        file_name, created_by, 'normal'
                    ))
                    logger.info(f"Added audio note with direct data: {audio_id}")
                else:
                    logger.warning(f"Audio note provided but missing both file_id and audio_data")
            except Exception as e:
                logger.error(f"Error handling audio note: {str(e)}")
                logger.exception("Audio note error details:")
                raise

        # Handle attachments
        if attachments:
            logger.info(f"Processing {len(attachments)} attachments for new task: {task_id}")
            for i, attachment in enumerate(attachments):
                try:
                    logger.info(f"Processing attachment {i+1}/{len(attachments)}")
                    logger.debug(f"Attachment data: {attachment}")
                    
                    file_id = attachment.get('file_id')
                    file_data = attachment.get('file_data')
                    file_name = attachment.get('file_name')
                    file_type = attachment.get('file_type')
                    
                    logger.info(f"Attachment info - file_id: {file_id}, has_data: {'Yes' if file_data else 'No'}, name: {file_name}, type: {file_type}")
                    
                    # Use existing file_id if provided (from previous upload)
                    if file_id and file_name:
                        # Use the file_id as attachment_id
                        attachment_id = file_id
                        
                        # Get the file path based on the upload endpoint pattern
                        attachment_path = os.path.join('uploads', 'attachments', f"{attachment_id}_{file_name}")
                        logger.info(f"Using pre-uploaded attachment file with ID: {file_id}, path: {attachment_path}")
                        
                        # Insert attachment record
                        cursor.execute("""
                            INSERT INTO task_attachments (
                                attachment_id, task_id, file_name,
                                file_path, file_type, file_size,
                                created_by
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """, (
                            attachment_id,
                            task_id,
                            file_name,
                            attachment_path,
                            file_type,
                            0,  # File size unknown but not critical
                            assigned_by
                        ))
                        logger.info(f"Added attachment with existing file_id: {attachment_id}")
                    
                    # Handle direct file data if provided instead
                    elif file_data and file_name:
                        # Generate a new UUID for this attachment
                        attachment_id = str(uuid.uuid4())
                        logger.info(f"Creating new attachment with ID: {attachment_id} for file: {file_name}")
                        
                        # Save attachment file
                        attachment_path = os.path.join(ATTACHMENTS_FOLDER, f"{attachment_id}_{file_name}")
                        os.makedirs(ATTACHMENTS_FOLDER, exist_ok=True)
                        with open(attachment_path, 'wb') as f:
                            f.write(base64.b64decode(file_data))
                        
                        # Get file size
                        file_size = os.path.getsize(attachment_path)
                        logger.info(f"Saved attachment file, size: {file_size} bytes")
                        
                        # Insert attachment record
                        cursor.execute("""
                            INSERT INTO task_attachments (
                                attachment_id, task_id, file_name,
                                file_path, file_type, file_size,
                                created_by
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """, (
                            attachment_id,
                            task_id,
                            file_name,
                            attachment_path,
                            file_type,
                            file_size,
                            assigned_by
                        ))
                        logger.info(f"Added attachment with direct data: {attachment_id}")
                    else:
                        logger.warning("Attachment missing both file_id and file_data, skipping")
                except Exception as e:
                    logger.error(f"Error handling attachment: {str(e)}")
                    logger.exception("Attachment error details:")
                    continue

        # Handle alarm settings
        if alarm_settings:
            try:
                logger.info(f"Processing alarm settings: {alarm_settings}")
                
                # Validate required fields
                required_fields = ['start_date', 'start_time', 'frequency']
                missing_fields = [field for field in required_fields if field not in alarm_settings]
                if missing_fields:
                    logger.error(f"Missing required alarm fields: {missing_fields}")
                    raise ValueError(f"Missing required alarm fields: {missing_fields}")
                
                # Validate date format
                try:
                    datetime.strptime(alarm_settings['start_date'], '%Y-%m-%d')
                except ValueError:
                    logger.error(f"Invalid start_date format: {alarm_settings['start_date']}")
                    raise ValueError("Invalid start_date format. Expected YYYY-MM-DD")
                
                # Validate time format
                try:
                    datetime.strptime(alarm_settings['start_time'], '%H:%M:%S')
                except ValueError:
                    logger.error(f"Invalid start_time format: {alarm_settings['start_time']}")
                    raise ValueError("Invalid start_time format. Expected HH:MM:SS")
                
                # Validate frequency
                valid_frequencies = ['30 minutes', '1 hour', '2 hours', '4 hours', '6 hours', '8 hours']
                if alarm_settings['frequency'] not in valid_frequencies:
                    logger.error(f"Invalid frequency: {alarm_settings['frequency']}")
                    raise ValueError(f"Invalid frequency. Must be one of: {valid_frequencies}")
                
                # Calculate next trigger time in UTC
                start_datetime_str = f"{alarm_settings['start_date']} {alarm_settings['start_time']}"
                start_datetime = convert_to_utc(start_datetime_str)
                
                logger.info(f"Original start datetime (IST): {start_datetime_str}")
                logger.info(f"Converted to UTC: {start_datetime}")
                
                # Generate alarm ID and insert
                alarm_id = str(uuid.uuid4())
                # Look up user_id for assigned_to
                cursor.execute("SELECT user_id FROM users WHERE username = %s", (assigned_to,))
                user_row = cursor.fetchone()
                user_id = user_row['user_id'] if user_row else None
                if not user_id:
                    logger.error(f"Could not find user_id for assigned_to: {assigned_to}")
                    raise ValueError(f"Could not find user_id for assigned_to: {assigned_to}")
                cursor.execute("""
                    INSERT INTO task_alarms (
                        alarm_id, task_id, start_date, start_time,
                        frequency, created_by, is_active, next_trigger, user_id
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s, %s)
                """, (
                    alarm_id,
                    task_id,
                    alarm_settings['start_date'],
                    alarm_settings['start_time'],
                    alarm_settings['frequency'],
                    assigned_by,
                    start_datetime,
                    user_id
                ))
                logger.info(f"Successfully created alarm with ID: {alarm_id} and user_id: {user_id}")
            except Exception as e:
                logger.error(f"Error creating alarm: {str(e)}")
                raise

        conn.commit()
        
        # Notify about task creation
        try:
            notify_task_update({
                'task_id': task_id,
                'title': title,
                'description': description,
                'assigned_to': assigned_to,
                'assigned_by': assigned_by,
                'priority': priority,
                'status': status,
                'deadline': deadline
            }, 'task_created')
        except Exception as e:
            logger.error(f"Error sending task creation notification: {str(e)}")


        # Send notification to assigned_to
        try:
            # Fetch assigned_to's FCM token and assigned_by's username
            cursor.execute("""
                SELECT u1.fcm_token, u2.username AS creator_name
                FROM users u1, users u2
                WHERE u1.username = %s AND u2.username = %s
            """, (assigned_to, assigned_by))
            result = cursor.fetchone()
            
            if not result or not result['fcm_token']:
                logger.warning(f"No FCM token found for user_id: {assigned_to}")
            else:
                token = result['fcm_token']
                creator_name = result['creator_name']
                
                # Truncate description for notification
                truncated_description = description[:80] + "..." if len(description) > 80 else description
                
                # Construct notification message
                notification_title = f"Task Created: {title}"
                notification_body = (
                    f"{creator_name} has created a new task: \"{title}\". "
                    f"Description: {truncated_description}. "
                    f"Status: {status.capitalize()}. "
                    "Please review in the Task Management App."
                )
                
                # Build FCM message
                message = messaging.Message(
                    notification=messaging.Notification(
                        title=notification_title,
                        body=notification_body
                    ),
                    token=token,
                    data={
                        'task_id': str(task_id),
                        'priority': priority,
                        'status': status,
                        'deadline': deadline,
                        'assigned_to': assigned_to,
                        'assigned_by': assigned_by,
                        'creator_name': creator_name
                    },
                    android=messaging.AndroidConfig(
                        notification=messaging.AndroidNotification(
                            # channel_id='high_importance_channel'
                            channel_id='high_importance_channel'
                        )
                    )
                )
                
                # Send notification
                logger.info(f"Sending creation notification to user_id: {assigned_to}, token: {token[:10]}...")
                response = messaging.send(message)
                logger.info(f"Notification sent successfully: {response}")
        
        except Exception as e:
            logger.error(f"Error sending task creation notification: {str(e)}")
            # Continue execution even if notification fails

        conn.commit()
        
        return jsonify({
            'success': True,
            'message': 'Task created successfully',
            'task_id': task_id
        }), 201
        
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Error creating task: {str(e)}")
        return jsonify({
            'success': False,
            'message': f"Error creating task: {str(e)}"
        }), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# ---------------- GET TASKS ----------------
@app.route('/tasks', methods=['GET'])
def get_tasks():
    conn = None
    cursor = None
    try:
        # Get query parameters
        username = request.args.get('username')
        role = request.args.get('role')

        logger.debug(f"Fetching tasks for username: {username}, role: {role}")

        if not username or not role:
            return jsonify([]), 400  # Return empty list on error

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"USE {db_config['database']}")

        try:
            # First, check if the columns exist
            cursor.execute("""
                SELECT COUNT(*) as count
                FROM information_schema.columns
                WHERE table_schema = %s
                AND table_name = 'tasks'
                AND column_name IN ('created_at', 'updated_at')
            """, (db_config['database'],))

            result = cursor.fetchone()
            has_timestamp_columns = result['count'] == 2

            # Base query with joins to get audio notes and attachments
            base_query = """
                SELECT DISTINCT
                    t.task_id,
                    t.title,
                    t.description,
                    t.deadline,
                    t.priority,
                    t.status,
                    t.assigned_by,
                    assigner.role as assigned_by_role,
                    t.assigned_to,
                    assignee.role as assigned_to_role,
                    {timestamp_columns},
                    COUNT(DISTINCT a.attachment_id) as attachment_count,
                    COUNT(DISTINCT an.audio_id) as has_audio
                FROM tasks t
                JOIN users assigner ON t.assigned_by = assigner.username
                JOIN users assignee ON t.assigned_to = assignee.username
                LEFT JOIN task_attachments a ON t.task_id = a.task_id
                LEFT JOIN task_audio_notes an ON t.task_id = an.task_id
                {where_clause}
                GROUP BY 
                    t.task_id, t.title, t.description,
                    t.deadline, t.priority, t.status,
                    t.assigned_by, assigner.role, t.assigned_to, assignee.role
                    {group_by_timestamps}
                ORDER BY t.deadline ASC
            """

            timestamp_columns = """
                t.created_at as created_at,
                t.updated_at as updated_at
            """ if has_timestamp_columns else """
                CURRENT_TIMESTAMP as created_at,
                CURRENT_TIMESTAMP as updated_at
            """

            group_by_timestamps = ", t.created_at, t.updated_at" if has_timestamp_columns else ""

            # Add role-based filtering
            if role.lower() in ['admin', 'super admin']:
                where_clause = ""
            else:
                where_clause = "WHERE t.assigned_to = %s OR t.assigned_by = %s"

            # Format the complete query
            query = base_query.format(
                timestamp_columns=timestamp_columns,
                where_clause=where_clause,
                group_by_timestamps=group_by_timestamps
            )

            logger.debug(f"Executing query: {query}")

            # Execute the query
            if role.lower() in ['admin', 'super admin']:
                cursor.execute(query)
            else:
                cursor.execute(query, (username, username))

            tasks = cursor.fetchall()
            logger.debug(f"Found {len(tasks)} tasks")

            # Get attachments for tasks with attachments
            task_attachments = {}
            if tasks:
                task_ids_with_attachments = [
                    task['task_id'] for task in tasks
                    if task['attachment_count'] > 0
                ]

                if task_ids_with_attachments:
                    placeholders = ', '.join(['%s'] * len(task_ids_with_attachments))
                    cursor.execute(f"""
                        SELECT task_id,
                               JSON_ARRAYAGG(
                                   JSON_OBJECT(
                                       'attachment_id', attachment_id,
                                       'file_name', file_name
                                   )
                               ) as attachments
                        FROM task_attachments
                        WHERE task_id IN ({placeholders})
                        GROUP BY task_id
                    """, task_ids_with_attachments)

                    for row in cursor.fetchall():
                        task_attachments[row['task_id']] = row['attachments']

            # Format response
            formatted_tasks = []
            for task in tasks:
                formatted_task = {
                    'task_id': task['task_id'],
                    'title': task['title'],
                    'description': task['description'],
                    'deadline': task['deadline'].strftime('%Y-%m-%d %H:%M:%S') if task['deadline'] else None,
                    'priority': task['priority'],
                    'status': task['status'],
                    'assigned_by': task['assigned_by'],
                    'assigned_by_role': task['assigned_by_role'],
                    'assigned_to': task['assigned_to'],
                    'assigned_to_role': task['assigned_to_role'],
                    'created_at': task['created_at'].strftime('%Y-%m-%d %H:%M:%S') if task['created_at'] else None,
                    'updated_at': task['updated_at'].strftime('%Y-%m-%d %H:%M:%S') if task['updated_at'] else None,
                    'has_audio': bool(task['has_audio']),
                    'attachments': task_attachments.get(task['task_id'], [])
                }
                formatted_tasks.append(formatted_task)

            return jsonify(formatted_tasks), 200

        except mysql.connector.Error as db_err:
            logger.error(f"Database error in get_tasks: {db_err}")
            return jsonify([]), 500  # Return empty list on error

    except Exception as e:
        logger.error(f"Unexpected error in get_tasks: {e}")
        return jsonify([]), 500  # Return empty list on error
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# ---------------- GET USERS ----------------
@app.route('/users', methods=['GET'])
def get_users():
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute(f"USE {db_config['database']}")
        cursor.execute("SELECT username FROM users")
        users = [row[0] for row in cursor.fetchall()]
        return jsonify(users), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# ---------------- GET ATTACHMENT ----------------
@app.route('/attachments/<attachment_id>', methods=['GET'])
@jwt_required()
def get_attachment(attachment_id):
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"USE {db_config['database']}")

        cursor.execute("""
            SELECT file_name, file_type, file_size, file_path
            FROM task_attachments
            WHERE attachment_id = %s
        """, (attachment_id,))

        attachment = cursor.fetchone()
        if attachment:
            # Get the full file path
            file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), attachment['file_path'])
            
            if not os.path.exists(file_path):
                return jsonify({
                    'success': False,
                    'message': 'Attachment file not found on server'
                }), 404
            
            # Determine the correct mimetype based on file type
            file_type = attachment['file_type'].lower() if attachment['file_type'] else ''
            if file_type in ['jpg', 'jpeg']:
                mimetype = 'image/jpeg'
            elif file_type == 'png':
                mimetype = 'image/png'
            elif file_type == 'pdf':
                mimetype = 'application/pdf'
            elif file_type in ['doc', 'docx']:
                mimetype = 'application/msword'
            elif file_type in ['xls', 'xlsx']:
                mimetype = 'application/vnd.ms-excel'
            elif file_type == 'txt':
                mimetype = 'text/plain'
            else:
                mimetype = 'application/octet-stream'
            
            # Send the file with proper mimetype
            return send_file(
                file_path,
                mimetype=mimetype,
                as_attachment=True,
                download_name=attachment['file_name']
            )
        else:
            return jsonify({
                'success': False,
                'message': 'Attachment not found'
            }), 404

    except Exception as e:
        logger.error(f"Error fetching attachment: {str(e)}")
        return jsonify({
            'success': False,
            'message': f"Error fetching attachment: {str(e)}"
        }), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# ---------------- GET AUDIO NOTE (RESTful) ----------------
@app.route('/tasks/<task_id>/audio', methods=['GET'])
@jwt_required()
def get_task_audio_notes(task_id):
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"USE {db_config['database']}")
        cursor.execute("""
            SELECT audio_id, file_path, duration, created_by, file_name, created_at, note_type
            FROM task_audio_notes
            WHERE task_id = %s
            ORDER BY created_at DESC
        """, (task_id,))
        notes = cursor.fetchall()
        if not notes:
            return jsonify({'message': 'No audio notes found for this task'}), 404
        formatted_notes = []
        for note in notes:
            formatted_note = {
                'audio_id': note['audio_id'],
                'file_path': note['file_path'],
                'duration': note['duration'],
                'created_by': note['created_by'],
                'file_name': note['file_name'],
                'created_at': note['created_at'].isoformat() if note['created_at'] else None,
                'note_type': note.get('note_type')
            }
            formatted_notes.append(formatted_note)
        return jsonify(formatted_notes), 200
    except Exception as e:
        logger.error(f"Error fetching audio notes: {str(e)}")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# ---------------- GET TASK ASSIGNMENTS ----------------
@app.route('/tasks/assignments/<user_id>', methods=['GET'])
def get_task_assignments(user_id):
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"USE {db_config['database']}")

        # First get the user's role and username
        cursor.execute("SELECT role, username FROM users WHERE user_id = %s", (user_id,))
        user = cursor.fetchone()

        if not user:
            logger.warning(f"User not found: {user_id}")
            return jsonify({
                'success': False,
                'message': 'User not found'
            }), 404

        user_role = user['role'].lower()
        username = user['username']

        # Base query with all fields
        base_query = """
            SELECT
                t.task_id,
                t.title as task_name,
                t.description,
                t.deadline as due_date,
                t.priority,
                t.status as current_task,
                assigner.user_id as assigner_id,
                assigner.username as assigner_name,
                assigner.role as assigner_role,
                assignee.user_id as assignee_id,
                assignee.username as assignee_name,
                assignee.role as assignee_role
            FROM tasks t
            JOIN users assigner ON t.assigned_by = assigner.username
            JOIN users assignee ON t.assigned_to = assignee.username
        """

        # If user is admin or super admin, show all tasks
        if user_role in ['admin', 'super admin']:
            query = base_query + " ORDER BY t.created_at DESC"
            cursor.execute(query)
        else:
            # For regular users, show tasks where they are the assigner
            query = base_query + """
                WHERE t.assigned_by = %s AND t.assigned_to != %s
                ORDER BY t.created_at DESC
            """
            cursor.execute(query, (username, username))

        assignments = cursor.fetchall()
        logger.info(f"Found {len(assignments)} assignments for user {username} with role {user_role}")

        # Format dates for JSON serialization
        for assignment in assignments:
            if assignment['due_date']:
                assignment['due_date'] = assignment['due_date'].isoformat()

        return jsonify({
            'success': True,
            'assignments': assignments
        })

    except Exception as e:
        logger.error(f"Error in get_task_assignments: {str(e)}")
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# ---------------- UPDATE TASK ----------------
@app.route('/tasks/<task_id>', methods=['PUT'])
@jwt_required()
def update_task(task_id):
    conn = None
    cursor = None
    try:
        # Get user identity from token
        current_user = get_jwt_identity()
        logger.info(f"Update task request by user: {current_user} for task: {task_id}")
        
        data = request.get_json()
        logger.debug(f"Task update data: {data}")
        
        # Validate required fields
        required_fields = ['title', 'description', 'assigned_to', 'assigned_by', 'priority', 'status', 'deadline','currentUser']
        for field in required_fields:
            if field not in data:
                logger.warning(f"Missing required field in task update: {field}")
                return jsonify({
                    'success': False,
                    'message': f'Missing required field: {field}',
                    'task_id': task_id
                }), 400

        title = data['title']
        description = data['description']
        assigned_to = data['assigned_to']
        assigned_by = data['assigned_by']
        priority = data['priority'].lower()  # Convert to lowercase for consistency
        status = data['status'].lower()  # Convert to lowercase for consistency
        deadline = data['deadline']
        current_user = data['currentUser']  # Required field for notification logic
        audio_note = data.get('audio_note')
        attachments = data.get('attachments', [])
        alarm_settings = data.get('alarm_settings')

        # Validate priority
        valid_priorities = ['low', 'medium', 'high', 'urgent']
        if priority not in valid_priorities:
            return jsonify({
                'success': False,
                'message': f'Invalid priority: {priority}. Must be one of {valid_priorities}',
                'task_id': task_id
            }), 400

        # Validate status
        valid_statuses = ['pending', 'in_progress', 'completed', 'snoozed']
        if status not in valid_statuses:
            return jsonify({
                'success': False,
                'message': f'Invalid status: {status}. Must be one of {valid_statuses}',
                'task_id': task_id
            }), 400

        # Initialize database connection
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"USE {db_config['database']}")

        # Update task
        cursor.execute("""
            UPDATE tasks
            SET title = %s,
                description = %s,
                assigned_to = %s,
                assigned_by = %s,
                priority = %s,
                status = %s,
                deadline = %s,
                updated_at = NOW()
            WHERE task_id = %s
        """, (
            title, description, assigned_to,
            assigned_by, priority, status, deadline,
            task_id
        ))

        # Handle audio note
        if audio_note:
            try:
                logger.info(f"Processing audio note for task update: {task_id}")
                logger.debug(f"Audio note data: {audio_note}")
                
                file_id = audio_note.get('file_id')
                audio_data = audio_note.get('audio_data')
                duration = audio_note.get('duration', 0)
                file_name = audio_note.get('filename', 'voice_note.wav')
                created_by = data.get('updated_by', assigned_by)
                
                logger.info(f"Audio note details - file_id: {file_id}, data length: {'Yes' if audio_data else 'No'}, duration: {duration}, filename: {file_name}")
                
                # Ensure file extension is .wav
                if not file_name.lower().endswith('.wav'):
                    name_parts = file_name.rsplit('.', 1)
                    file_name = name_parts[0] + '.wav'
                    logger.info(f"Changed audio file extension to .wav: {file_name}")
                
                # Check if we have a file_id from a previous upload
                if file_id:
                    # Use the existing file_id as audio_id
                    audio_id = file_id
                    # The file should already be at this location from the upload endpoint
                    audio_path = os.path.join('uploads', 'audio', f"{audio_id}_{file_name}")
                    logger.info(f"Using pre-uploaded audio file with ID: {file_id}, path: {audio_path}")
                    
                    # Insert audio note record
                    cursor.execute("""
                        INSERT INTO task_audio_notes (
                            audio_id, task_id, file_path, duration, 
                            file_name, created_by, note_type
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (
                        audio_id, task_id, audio_path, duration,
                        file_name, created_by, 'normal'
                    ))
                    logger.info(f"Added audio note with existing file_id: {audio_id}")
                
                elif audio_data:
                    # Save audio file
                    audio_id = str(uuid.uuid4())
                    audio_path = os.path.join(AUDIO_FOLDER, f"{audio_id}_{file_name}")
                    logger.info(f"Saving direct audio data with new ID: {audio_id}, path: {audio_path}")
                    
                    os.makedirs(AUDIO_FOLDER, exist_ok=True)
                    with open(audio_path, 'wb') as f:
                        f.write(base64.b64decode(audio_data))
                    
                    # Insert audio note record
                    cursor.execute("""
                        INSERT INTO task_audio_notes (
                            audio_id, task_id, file_path, duration, 
                            file_name, created_by, note_type
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (
                        audio_id, task_id, audio_path, duration,
                        file_name, created_by, 'normal'
                    ))
                    logger.info(f"Added audio note with direct data: {audio_id}")
                else:
                    logger.warning(f"Audio note provided but missing both file_id and audio_data")
            except Exception as e:
                logger.error(f"Error handling audio note: {str(e)}")
                logger.exception("Audio note error details:")
                raise

        # Handle attachments
        if attachments:
            logger.info(f"Processing {len(attachments)} attachments for task: {task_id}")
            for i, attachment in enumerate(attachments):
                try:
                    logger.info(f"Processing attachment {i+1}/{len(attachments)}")
                    logger.debug(f"Attachment data: {attachment}")
                    
                    file_id = attachment.get('file_id')
                    file_data = attachment.get('file_data')
                    file_name = attachment.get('file_name')
                    file_type = attachment.get('file_type')
                    
                    logger.info(f"Attachment info - file_id: {file_id}, has_data: {'Yes' if file_data else 'No'}, name: {file_name}, type: {file_type}")
                    
                    # Use existing file_id if provided (from previous upload)
                    if file_id and file_name:
                        # Use the file_id as attachment_id
                        attachment_id = file_id
                        
                        # Get the file path based on the upload endpoint pattern
                        attachment_path = os.path.join('uploads', 'attachments', f"{attachment_id}_{file_name}")
                        logger.info(f"Using pre-uploaded attachment file with ID: {file_id}, path: {attachment_path}")
                        
                        # Insert attachment record
                        cursor.execute("""
                            INSERT INTO task_attachments (
                                attachment_id, task_id, file_name,
                                file_path, file_type, file_size,
                                created_by
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """, (
                            attachment_id,
                            task_id,
                            file_name,
                            attachment_path,
                            file_type,
                            0,  # File size unknown but not critical
                            assigned_by
                        ))
                        logger.info(f"Added attachment with existing file_id: {attachment_id}")
                    
                    # Handle direct file data if provided instead
                    elif file_data and file_name:
                        # Generate a new UUID for this attachment
                        attachment_id = str(uuid.uuid4())
                        logger.info(f"Creating new attachment with ID: {attachment_id} for file: {file_name}")
                        
                        # Save attachment file
                        attachment_path = os.path.join(ATTACHMENTS_FOLDER, f"{attachment_id}_{file_name}")
                        os.makedirs(ATTACHMENTS_FOLDER, exist_ok=True)
                        with open(attachment_path, 'wb') as f:
                            f.write(base64.b64decode(file_data))
                        
                        # Get file size
                        file_size = os.path.getsize(attachment_path)
                        logger.info(f"Saved attachment file, size: {file_size} bytes")
                        
                        # Insert attachment record
                        cursor.execute("""
                            INSERT INTO task_attachments (
                                attachment_id, task_id, file_name,
                                file_path, file_type, file_size,
                                created_by
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """, (
                            attachment_id,
                            task_id,
                            file_name,
                            attachment_path,
                            file_type,
                            file_size,
                            assigned_by
                        ))
                        logger.info(f"Added attachment with direct data: {attachment_id}")
                    else:
                        logger.warning("Attachment missing both file_id and file_data, skipping")
                except Exception as e:
                    logger.error(f"Error handling attachment: {str(e)}")
                    logger.exception("Attachment error details:")
                    continue

        # Handle alarm settings
        if alarm_settings:
            try:
                logger.info(f"Processing alarm settings update: {alarm_settings}")
                
                # Validate required fields
                required_fields = ['start_date', 'start_time', 'frequency']
                missing_fields = [field for field in required_fields if field not in alarm_settings]
                if missing_fields:
                    logger.error(f"Missing required alarm fields: {missing_fields}")
                    raise ValueError(f"Missing required alarm fields: {missing_fields}")
                
                # Validate date format
                try:
                    datetime.strptime(alarm_settings['start_date'], '%Y-%m-%d')
                except ValueError:
                    logger.error(f"Invalid start_date format: {alarm_settings['start_date']}")
                    raise ValueError("Invalid start_date format. Expected YYYY-MM-DD")
                
                # Validate time format
                try:
                    datetime.strptime(alarm_settings['start_time'], '%H:%M:%S')
                except ValueError:
                    logger.error(f"Invalid start_time format: {alarm_settings['start_time']}")
                    raise ValueError("Invalid start_time format. Expected HH:MM:SS")
                
                # Validate frequency
                valid_frequencies = ['30 minutes', '1 hour', '2 hours', '4 hours', '6 hours', '8 hours']
                if alarm_settings['frequency'] not in valid_frequencies:
                    logger.error(f"Invalid frequency: {alarm_settings['frequency']}")
                    raise ValueError(f"Invalid frequency. Must be one of: {valid_frequencies}")
                
                # Calculate next trigger time in UTC
                start_datetime_str = f"{alarm_settings['start_date']} {alarm_settings['start_time']}"
                start_datetime = convert_to_utc(start_datetime_str)
                
                logger.info(f"Original start datetime (IST): {start_datetime_str}")
                logger.info(f"Converted to UTC: {start_datetime}")
                
                # Delete existing alarm settings
                cursor.execute("""
                    DELETE FROM task_alarms
                    WHERE task_id = %s
                """, (task_id,))
                
                # Insert new alarm settings
                alarm_id = str(uuid.uuid4())
                cursor.execute("""
                    INSERT INTO task_alarms (
                        alarm_id, task_id, start_date, start_time,
                        frequency, created_by, is_active, next_trigger
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s)
                """, (
                    alarm_id,
                    task_id,
                    alarm_settings['start_date'],
                    alarm_settings['start_time'],
                    alarm_settings['frequency'],
                    assigned_by,
                    start_datetime
                ))
                
                logger.info(f"Successfully updated alarm with ID: {alarm_id}")
                
            except Exception as e:
                logger.error(f"Error updating alarm: {str(e)}")
                raise

        conn.commit()
        
        # Notify about task update
        try:
            notify_task_update({
                'task_id': task_id,
                'title': title,
                'description': description,
                'assigned_to': assigned_to,
                'assigned_by': assigned_by,
                'priority': priority,
                'status': status,
                'deadline': deadline,
                'updated_by': data.get('updated_by')
            }, 'task_updated')
        except Exception as e:
            logger.error(f"Error sending task update notification: {str(e)}")


        # Send notification based on current_user
        recipient_id = assigned_by if current_user == assigned_to else assigned_to
        try:
            # Fetch recipient's FCM token and updater's username
            cursor.execute("""
                SELECT u1.fcm_token, u2.username AS updater_name
                FROM users u1, users u2
                WHERE u1.username = %s AND u2.username = %s
            """, (recipient_id, current_user))
            result = cursor.fetchone()
            
            if not result or not result['fcm_token']:
                logger.warning(f"No FCM token found for user_id: {recipient_id}")
            else:
                token = result['fcm_token']
                updater_name = result['updater_name']
                
                # Truncate description for notification
                truncated_description = description[:80] + "..." if len(description) > 80 else description
                
                # Construct notification message
                notification_title = f"Task Updated: {title}"
                notification_body = (
                    f"{updater_name} has updated the task: \"{title}\". "
                    f"Description: {truncated_description}. "
                    f"Status: {status.capitalize()}. "
                    "Please review in the Task Management App."
                )
                
                # Build FCM message
                message = messaging.Message(
                    notification=messaging.Notification(
                        title=notification_title,
                        body=notification_body
                    ),
                    token=token,
                    data={
                        'task_id': str(task_id),
                        'priority': priority,
                        'status': status,
                        'deadline': deadline,
                        'assigned_to': assigned_to,
                        'assigned_by': assigned_by,
                        'updater_name': updater_name
                    },
                    android=messaging.AndroidConfig(
                        notification=messaging.AndroidNotification(
                            channel_id='high_importance_channel'
                        )
                    )
                )
                
                # Send notification
                logger.info(f"Sending update notification to user_id: {recipient_id}, token: {token[:10]}...")
                response = messaging.send(message)
                logger.info(f"Notification sent successfully: {response}")
        
        except Exception as e:
            logger.error(f"Error sending task update notification: {str(e)}")
            # Continue execution even if notification fails

        conn.commit()

        
        
        return jsonify({
            'success': True,
            'message': 'Task updated successfully',
            'task_id': task_id
        }), 200
        
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Error updating task: {str(e)}")
        return jsonify({
            'success': False,
            'message': f"Error updating task: {str(e)}"
        }), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# ---------------- GET TASK VOICE NOTES ----------------
@app.route('/api/tasks/<task_id>/voice-notes', methods=['GET'])
@jwt_required()
def get_task_voice_notes(task_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"USE {db_config['database']}")
        cursor.execute("""
            SELECT file_path, duration, created_by, file_name, created_at, note_type
            FROM task_audio_notes
            WHERE task_id = %s
            ORDER BY created_at DESC
        """, (task_id,))
        notes = cursor.fetchall()
        if not notes:
            return jsonify({'message': 'No voice notes found for this task'}), 404
        formatted_notes = []
        for note in notes:
            formatted_note = {
                'file_path': note['file_path'],
                'duration': note['duration'],
                'created_by': note['created_by'],
                'file_name': note.get('file_name'),
                'created_at': note['created_at'].isoformat() if note['created_at'] else None,
                'note_type': note.get('note_type')
            }
            formatted_notes.append(formatted_note)
        return jsonify(formatted_notes), 200
    except Exception as e:
        logger.error(f"Error fetching voice notes: {str(e)}")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# ---------------- GET TASK ATTACHMENTS ----------------
@app.route('/tasks/<task_id>/attachments', methods=['GET'])
@jwt_required()
def get_task_attachments(task_id):
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"USE {db_config['database']}")

        cursor.execute("""
            SELECT 
                attachment_id as id,
                task_id,
                file_name,
                file_type,
                file_size,
                file_path,
                created_by,
                created_at
            FROM task_attachments
            WHERE task_id = %s
        """, (task_id,))

        attachments = cursor.fetchall()
        
        # Convert datetime objects to string
        for attachment in attachments:
            if attachment['created_at']:
                attachment['created_at'] = attachment['created_at'].strftime('%Y-%m-%d %H:%M:%S')

        return jsonify({
            'success': True,
            'attachments': attachments
        }), 200

    except Exception as e:
        logger.error(f"Error fetching attachments: {str(e)}")
        return jsonify({
            'success': False,
            'message': f"Error fetching attachments: {str(e)}",
            'attachments': []
        }), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# ---------------- DOWNLOAD ATTACHMENT ----------------
@app.route('/attachments/<attachment_id>/download', methods=['GET'])
@jwt_required()
def download_attachment(attachment_id):
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"USE {db_config['database']}")

        cursor.execute("""
            SELECT file_name, file_type, file_path
            FROM task_attachments
            WHERE attachment_id = %s
        """, (attachment_id,))

        attachment = cursor.fetchone()
        if not attachment or not attachment['file_path']:
            return jsonify({
                'success': False,
                'message': 'Attachment not found or file path is missing'
            }), 404

        file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), attachment['file_path'])
        
        if not os.path.exists(file_path):
            return jsonify({
                'success': False,
                'message': 'Attachment file not found on server'
            }), 404

        return send_file(
            file_path,
            as_attachment=True,
            download_name=attachment['file_name']
        )

    except Exception as e:
        logger.error(f"Error downloading attachment: {str(e)}")
        return jsonify({
            'success': False,
            'message': f"Error downloading attachment: {str(e)}"
        }), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# ---------------- MARK NOTIFICATION AS READ ----------------
@app.route('/notifications/mark_read/<notification_id>', methods=['POST'])
def mark_notification_read(notification_id):
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute(f"USE {db_config['database']}")
        cursor.execute("""
            UPDATE task_notifications SET is_read = 1 WHERE id = %s
        """, (notification_id,))
        conn.commit()
        return jsonify({'success': True, 'message': 'Notification marked as read'})
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# ---------------- GET NOTIFICATIONS ----------------
@app.route('/tasks/notifications', methods=['GET'])
def get_notifications():
    try:
        # Get query parameters
        user_id = request.args.get('user_id')
        username = request.args.get('username')

        logger.info(f"Fetching notifications for user_id: {user_id}, username: {username}")

        if not user_id or not username:
            return jsonify({
                'success': False,
                'message': 'Missing user_id or username parameter'
            }), 400

        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"USE {db_config['database']}")

        # Get user's role
        cursor.execute("SELECT role FROM users WHERE user_id = %s", (user_id,))
        user = cursor.fetchone()
        if not user:
            return jsonify({
                'success': False,
                'message': 'User not found'
            }), 404

        # Get notifications for the user
        cursor.execute("""
            SELECT 
                n.id,
                n.task_id,
                n.title,
                n.description,
                n.sender_name,
                n.sender_role,
                n.type,
                n.is_read,
                n.created_at,
                t.priority,
                t.status,
                t.deadline
            FROM task_notifications n
            LEFT JOIN tasks t ON n.task_id = t.task_id
            WHERE n.target_user = %s
            ORDER BY n.created_at DESC
            LIMIT 50
        """, (username,))

        notifications = cursor.fetchall()
        logger.info(f"Found {len(notifications)} notifications for user {username}")

        # Format timestamps and add additional task info
        formatted_notifications = []
        for notification in notifications:
            formatted_notification = dict(notification)
            if notification['created_at']:
                formatted_notification['created_at'] = notification['created_at'].isoformat()
            if notification['deadline']:
                formatted_notification['deadline'] = notification['deadline'].isoformat()
            formatted_notifications.append(formatted_notification)

        return jsonify({
            'success': True,
            'notifications': formatted_notifications
        }), 200

    except Exception as e:
        logger.error(f"Error fetching notifications: {str(e)}")
        logger.exception("Full traceback:")
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def store_notification(task_data, event_type, target_user, sender_role):
    """Store notification in database"""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(f"USE {db_config['database']}")

        # Get the user who made the update
        updated_by = task_data.get('updated_by')
        
        # Don't store notification if target_user is the one who made the update
        if updated_by and target_user == updated_by:
            logger.info(f"Skipping notification for updater: {updated_by}")
            return

        notification_id = str(uuid.uuid4())
        task_id = task_data.get('task_id')
        if not task_id:
            logger.error("No task_id found in task_data")
            return

        # title = 'Task Updated' if event_type == 'task_updated' else 'New Task Assignment'
        title = task_data.get('title')
        description = f"{task_data['title']} {'updated' if event_type == 'task_updated' else 'assigned'} by {task_data['updated_by'] if event_type == 'task_updated' else task_data['assigned_by']}"
        sender_name = task_data['updated_by'] if event_type == 'task_updated' else task_data['assigned_by']

        # Skip if sender is the target
        if sender_name == target_user:
            logger.info(f"Skipping notification as sender is target: {target_user}")
            return

        logger.info(f"Storing notification - Task ID: {task_id}, Target User: {target_user}")

        cursor.execute("""
            INSERT INTO task_notifications (
                id, task_id, title, description, sender_name,
                sender_role, type, target_user, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
        """, (
            notification_id,
            task_id,
            title,
            description,
            sender_name,
            sender_role,
            event_type,
            target_user
        ))

        conn.commit()
        logger.info(f"Notification stored successfully - ID: {notification_id}")

    except mysql.connector.Error as db_err:
        logger.error(f"Database error in store_notification: {db_err}")
        if conn:
            conn.rollback()
        raise

    except Exception as e:
        logger.error(f"Error storing notification: {str(e)}")
        if conn:
            conn.rollback()
        raise

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# ---------------- SNOOZE NOTIFICATION ----------------
@app.route('/notifications/snooze', methods=['POST'])
def snooze_notification():
    conn = None
    cursor = None
    try:
        data = request.get_json()
        notification_id = data.get('notification_id')
        snooze_until = data.get('snooze_until')
        reason = data.get('reason')
        audio_note = data.get('audio_note')
        updated_by = data.get('updated_by')
        
        if not notification_id or not snooze_until:
            return jsonify({
                'success': False,
                'message': 'Missing required fields: notification_id and snooze_until'
            }), 400
        
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"USE {db_config['database']}")
        
        # Get the related task_id from the notification
        cursor.execute("SELECT task_id, title FROM task_notifications WHERE id = %s", (notification_id,))
        notif = cursor.fetchone()
        if not notif or not notif['task_id']:
            return jsonify({'success': False, 'message': 'Notification or related task not found'}), 404
        task_id = notif['task_id']

        # Get current task description
        cursor.execute("SELECT description FROM tasks WHERE task_id = %s", (task_id,))
        task = cursor.fetchone()
        if not task:
            return jsonify({'success': False, 'message': 'Task not found'}), 404
        old_description = task['description'] or ''

        # Prepare snooze reason with date/time
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        reason_text = f"Snoozed on {now_str}: {reason}" if reason else None
        new_description = old_description
        if reason_text:
            new_description = (old_description + '\n' if old_description else '') + reason_text

        # Update the task: set status to snoozed, append reason
        cursor.execute("""
            UPDATE tasks SET status = 'snoozed', description = %s, updated_at = NOW() WHERE task_id = %s
        """, (new_description, task_id))

        # If audio_note is present, save as snooze audio note
        if audio_note and audio_note.get('audio_data'):
            audio_id = str(uuid.uuid4())
            audio_data = audio_note.get('audio_data')
            duration = audio_note.get('duration', 0)
            file_name = audio_note.get('filename', 'voice_note.wav')
            created_by = updated_by or None
            audio_path = os.path.join(AUDIO_FOLDER, f"{audio_id}_{file_name}")
            os.makedirs(AUDIO_FOLDER, exist_ok=True)
            with open(audio_path, 'wb') as f:
                f.write(base64.b64decode(audio_data))
            cursor.execute("""
                INSERT INTO task_audio_notes (
                    audio_id, task_id, file_path, duration, 
                    file_name, created_by, note_type
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                audio_id, task_id, audio_path, duration,
                file_name, created_by, 'snooze'
            ))

        # Update notification status and snooze details
        cursor.execute("""
            UPDATE task_notifications 
            SET status = 'snoozed',
                snooze_until = %s,
                snooze_reason = %s,
                snooze_audio = %s,
                is_read = FALSE
            WHERE id = %s
        """, (snooze_until, reason, audio_note.get('audio_data') if audio_note else None, notification_id))
        
        if cursor.rowcount == 0:
            return jsonify({
                'success': False,
                'message': 'Notification not found'
            }), 404
        
        conn.commit()

        # Emit dashboard update event (if using SocketIO)
        try:
            notify_task_update({
                'task_id': task_id,
                'status': 'snoozed',
                'description': new_description,
                'updated_by': updated_by
            }, 'task_snoozed')
        except Exception as e:
            logger.error(f"Error sending dashboard update after snooze: {str(e)}")

        try:
            notify_task_update({
                'task_id': task_id,
                'status': 'snoozed',
                'description': new_description,
                'updated_by': updated_by
            }, 'task_snoozed')
        except Exception as e:
            logger.error(f"Error sending dashboard update after snooze: {str(e)}")

        # Send FCM notification to updated_by
        try:
            cursor.execute(
            "SELECT fcm_token FROM users WHERE username = (SELECT assigned_by FROM tasks WHERE task_id = %s)",
            (task_id,)  # ← Note the comma to make it a tuple
            )
            result = cursor.fetchone()
            
            if not result or not result['fcm_token']:
                logger.warning(f"No FCM token found for user_id: {updated_by}")
            else:
                token = result['fcm_token']
                # updater_name = result['updater_name']
                
                # Truncate description for notification
                truncated_description = new_description[:80] + "..." if len(new_description) > 80 else new_description
                
                # Construct notification message
                notification_title = f"Task Snoozed: {notif['title']}"
                notification_body = (
                    f"{updated_by} has snoozed the task: \"{notif['title']}\". "
                    f"Description: {truncated_description}. "
                    f"Snoozed until: {snooze_until}. "
                    "Please review in the Task Management App."
                )
                
                # Build FCM message
                message = messaging.Message(
                    notification=messaging.Notification(
                        title=notification_title,
                        body=notification_body
                    ),
                    token=token,
                    data={
                        'task_id': str(task_id),
                        'notification_id': str(notification_id),
                        'status': 'snoozed',
                        'snooze_until': snooze_until,
                        'snooze_reason': reason or ''
                    },
                    android=messaging.AndroidConfig(
                        notification=messaging.AndroidNotification(
                            channel_id='high_importance_channel'
                        )
                    )
                )
                
                # Send notification
                logger.info(f"Sending snooze notification to user_id: {updated_by}, token: {token[:10]}...")
                response = messaging.send(message)
                logger.info(f"Notification sent successfully: {response}")
        
        except Exception as e:
            logger.error(f"Error sending snooze notification: {str(e)}")
            # Continue execution even if notification fails

        conn.commit()
        

        return jsonify({
            'success': True,
            'message': 'Notification snoozed and task updated successfully'
        })
        
    except Exception as e:
        logger.error(f"Error snoozing notification: {str(e)}")
        if conn:
            conn.rollback()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

#----------------SNOOZE ALARM------------------------------
@app.route('/notifications/send_snooze_alert', methods=['POST'])
def send_snooze_alert():
    conn = None
    cursor = None
    try:
        data = request.get_json()
        task_id = data.get('task_id')

        if not task_id:
            return jsonify({
                'success': False,
                'message': 'Missing required field: task_id'
            }), 400

        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"USE {db_config['database']}")

        # Step 1: Get assigned_by, assigned_to, and title from tasks
        cursor.execute("""
            SELECT assigned_by, assigned_to, title 
            FROM tasks 
            WHERE task_id = %s
        """, (task_id,))
        task = cursor.fetchone()

        if not task:
            return jsonify({'success': False, 'message': 'Task not found'}), 404

        assigned_by = task['assigned_by']
        assigned_to = task['assigned_to']
        title = task['title']

        # Step 2: Get FCM token of assigned_by user
        cursor.execute("SELECT fcm_token FROM users WHERE username = %s", (assigned_by,))
        user = cursor.fetchone()

        if not user or not user['fcm_token']:
            return jsonify({'success': False, 'message': 'FCM token not found for assigned_by user'}), 404

        token = user['fcm_token']

        # Optional: Truncate title if too long
        truncated_title = title[:80] + '...' if len(title) > 80 else title

        # Step 3: Build and send FCM notification
        notification_title = f"Task Snoozed: {title}"
        notification_body = f"{assigned_to} has snoozed the task: \"{truncated_title}\". Please check the task in the app."

        message = messaging.Message(
            notification=messaging.Notification(
                title=notification_title,
                body=notification_body
            ),
            token=token,
            data={
                'task_id': str(task_id),
                'status': 'snoozed',
                'assigned_by': assigned_by,
                'assigned_to': assigned_to
            },
            android=messaging.AndroidConfig(
                notification=messaging.AndroidNotification(
                    channel_id='high_importance_channel'
                )
            )
        )

        response = messaging.send(message)
        logger.info(f"FCM Notification sent to {assigned_by}: {response}")

        return jsonify({
            'success': True,
            'message': 'Snooze alert sent to assigned_by user successfully'
        })

    except Exception as e:
        logger.error(f"Error sending snooze alert: {str(e)}")
        if conn:
            conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()



def cleanup_task_files(task_id):
    """Clean up files associated with a task when it's deleted"""
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"USE {db_config['database']}")
        
        # Get audio files
        cursor.execute("""
            SELECT file_path
            FROM task_audio_notes
            WHERE task_id = %s
        """, (task_id,))
        
        audio_files = cursor.fetchall()
        
        # Delete audio files from filesystem
        for audio in audio_files:
            file_path = os.path.join(UPLOAD_FOLDER, audio['file_path'])
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logger.info(f"Deleted audio file: {file_path}")
            except Exception as e:
                logger.error(f"Error deleting audio file {file_path}: {str(e)}")
        
    except Exception as e:
        logger.error(f"Error cleaning up task files: {str(e)}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# ---------------- GET FCM TOKEN ----------------
@app.route('/get_fcm_token', methods=['POST'])
def get_fcm_token():
    conn = None
    cursor = None
    try:
        # Extract username from the request body
        data = request.get_json()
        username = data.get('username') if data else None
        if not username:
            return jsonify({'message': 'Username is required'}), 400

        # Connect to the database
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"USE {db_config['database']}")

        # Look up the userid for the given username
        cursor.execute("SELECT user_id, fcm_token FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()
        if not user:
            return jsonify({'message': f'User {username} not found'}), 404

        # Return both user_id and fcm_token (fcm_token might be null)
        return jsonify({
            'user_id': user['user_id'],
            'fcm_token': user['fcm_token']
        }), 200

    except Exception as e:
        logger.error(f"Error fetching FCM token: {str(e)}")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# ---------------- UPDATE FCM TOKEN ----------------
@app.route('/update_fcm_token', methods=['POST'])
def update_fcm_token():
    conn = None
    cursor = None
    try:
        data = request.get_json()
        username = data.get('username')
        fcm_token = data.get('fcm_token')
        
        if not username or not fcm_token:
            return jsonify({
                'success': False,
                'message': 'User ID and FCM token are required'
            }), 400
            
        logger.info(f"Updating FCM token for user ID: {username}")
        
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute(f"USE {db_config['database']}")
        
        cursor.execute("""
            UPDATE users 
            SET fcm_token = %s
            WHERE username = %s
        """, (fcm_token, username))
        
        if cursor.rowcount == 0:
            logger.warning(f"No user found with ID: {username}")
            return jsonify({
                'success': False,
                'message': 'User not found'
            }), 404
            
        conn.commit()
        logger.info(f"FCM token updated successfully for username: {username}")
        
        return jsonify({
            'success': True,
            'message': 'FCM token updated successfully'
        })
        
    except Exception as e:
        logger.error(f"Error updating FCM token: {str(e)}")
        if conn:
            conn.rollback()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500
        
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# ---------------- UPLOAD FILE ----------------
@app.route('/upload', methods=['POST'])
@jwt_required()
def upload_file():
    try:
        # Accept any content type for uploads
        if request.content_type and 'multipart/form-data' in request.content_type:
            logger.info("Received multipart/form-data upload")
            
            # Log request details for debugging
            logger.debug(f"Request headers: {dict(request.headers)}")
            logger.debug(f"Request content type: {request.content_type}")
            logger.debug(f"Request files: {request.files.keys()}")
            logger.debug(f"Request form: {request.form.keys()}")
            
            # Support both single file upload ('file') and multiple files ('files[]')
            files_to_process = []
            if 'file' in request.files:
                # Single file upload format
                files_to_process.append(request.files['file'])
                logger.debug("Processing single file upload with key 'file'")
            elif 'files[]' in request.files:
                # Multiple files upload format (legacy)
                files_to_process.extend(request.files.getlist('files[]'))
                logger.debug(f"Processing multiple files upload with key 'files[]', count: {len(files_to_process)}")
            else:
                logger.warning("No valid file field found in request")
                return jsonify({
                    'success': False,
                    'message': 'No files provided'
                }), 400
            
            logger.debug(f"Number of files to process: {len(files_to_process)}")
            
            file_type = request.form.get('type', 'attachment')
            logger.debug(f"File type: {file_type}")
            
            uploaded_files = []
            
            # Define allowed extensions - accept all file types for now
            allowed_extensions = ALLOWED_AUDIO_EXTENSIONS if file_type == 'audio' else {'pdf', 'doc', 'docx', 'xls', 'xlsx', 'txt', 'jpg', 'jpeg', 'png', 'mp3', 'wav'}
            
            for file in files_to_process:
                if file.filename == '':
                    logger.warning("Empty filename in request")
                    continue
                    
                # Check file extension
                extension = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
                logger.debug(f"File extension: {extension}")
                
                if extension not in allowed_extensions:
                    logger.warning(f"Extension {extension} not in allowed extensions: {allowed_extensions}")
                    return jsonify({
                        'success': False,
                        'message': f'File type .{extension} is not allowed'
                    }), 400
                    
                # Generate unique ID for the file
                file_id = str(uuid.uuid4())
                
                # Determine upload folder based on type
                upload_folder = AUDIO_FOLDER if file_type == 'audio' else ATTACHMENTS_FOLDER
                logger.debug(f"Upload folder: {upload_folder}")
                
                # Ensure audio files use .wav extension
                if file_type == 'audio' and not file.filename.lower().endswith('.wav'):
                    original_filename = file.filename
                    filename_parts = original_filename.rsplit('.', 1)
                    new_filename = f"{filename_parts[0]}.wav"
                    logger.info(f"Converting audio file extension: {original_filename} -> {new_filename}")
                    file.filename = new_filename
                
                # Ensure filename is secure and unique
                filename = secure_filename(f"{file_id}_{file.filename}")
                file_path = os.path.join(upload_folder, filename)
                logger.debug(f"File will be saved to: {file_path}")
                
                # Create directory if it doesn't exist
                os.makedirs(upload_folder, exist_ok=True)
                
                # Save file in chunks
                chunk_size = 8192  # 8KB chunks
                with open(file_path, 'wb') as f:
                    while True:
                        chunk = file.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                
                # Get relative path for database
                relative_path = os.path.join('uploads', 'audio' if file_type == 'audio' else 'attachments', filename)
                
                # Create URL path for the file
                url_path = '/' + relative_path.replace('\\', '/')
                
                # Get file size
                file_size = os.path.getsize(file_path)
                logger.debug(f"File saved successfully. Size: {file_size} bytes")
                
                file_info = {
                    'file_id': file_id,
                    'file_path': relative_path,
                    'file_name': file.filename,
                    'file_type': extension,
                    'file_size': file_size,
                    'file_url': url_path  # Add URL for direct access
                }
                
                uploaded_files.append(file_info)
            
            logger.info(f"Successfully uploaded {len(uploaded_files)} files")
            
            # For single file upload, return a simpler response
            if len(uploaded_files) == 1 and 'file' in request.files:
                return jsonify({
                    'success': True,
                    'file_url': uploaded_files[0]['file_url'],
                    'file_id': uploaded_files[0]['file_id'],
                    'file_name': uploaded_files[0]['file_name']
                }), 200
            
            # For multiple files, return the full list
            return jsonify({
                'success': True,
                'files': uploaded_files
            }), 200
    except Exception as e:
        logger.error(f"Error uploading files: {str(e)}", exc_info=True)  # Include stack trace
        return jsonify({
            'success': False,
            'message': f"Error uploading files: {str(e)}"
        }), 500

@app.route('/tasks/<task_id>', methods=['GET'])
def get_task(task_id):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"USE {db_config['database']}")
        # Get task details
        cursor.execute("SELECT * FROM tasks WHERE task_id = %s", (task_id,))
        task = cursor.fetchone()
        if not task:
            return jsonify({'success': False, 'message': 'Task not found'}), 404

        # Convert any datetime/timedelta fields to string
        for k, v in task.items():
            if isinstance(v, (datetime,)):
                task[k] = v.isoformat()
            elif isinstance(v, timedelta):
                task[k] = str(v)

        # Get alarm settings
        cursor.execute("SELECT start_date, start_time, frequency FROM task_alarms WHERE task_id = %s", (task_id,))
        alarm = cursor.fetchone()
        if alarm:
            # Convert any datetime/timedelta fields in alarm
            for k, v in alarm.items():
                if isinstance(v, (datetime,)):
                    alarm[k] = v.isoformat()
                elif isinstance(v, timedelta):
                    alarm[k] = str(v)
            task['alarm_settings'] = alarm
        else:
            task['alarm_settings'] = None

        return jsonify({'success': True, 'task': task}), 200
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Error fetching task: {str(e)}")
        return jsonify({'success': False, 'message': f"Error fetching task: {str(e)}"}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# Serve audio files from uploads/audio
@app.route('/uploads/audio/<path:filename>')
def serve_audio(filename):
    return send_from_directory(AUDIO_FOLDER, filename)

# ---------------- DOWNLOAD AUDIO FILE ----------------
@app.route('/api/tasks/<task_id>/audio/<audio_id>/download', methods=['GET'])
@jwt_required()
def download_audio_file(task_id, audio_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"USE {db_config['database']}")
        cursor.execute("""
            SELECT file_path, file_name 
            FROM task_audio_notes 
            WHERE task_id = %s AND audio_id = %s
        """, (task_id, audio_id))
        
        audio = cursor.fetchone()
        if not audio:
            return jsonify({
                'success': False, 
                'message': 'Audio note not found'
            }), 404
            
        file_path = audio['file_path']
        file_name = audio['file_name']
        
        # Log detailed information for debugging
        app.logger.info(f"Attempting to serve audio file: {file_path}")
        app.logger.info(f"Full OS path: {os.path.abspath(file_path)}")
        
        if not os.path.exists(file_path):
            app.logger.error(f"File not found at path: {file_path}")
            return jsonify({
                'success': False, 
                'message': 'Audio file not found on server'
            }), 404
            
        return send_file(
            file_path, 
            mimetype="audio/wav", 
            as_attachment=True,
            download_name=file_name
        )
        
    except Exception as e:
        app.logger.error(f"Error serving audio file: {str(e)}")
        return jsonify({
            'success': False, 
            'message': f'Error serving audio file: {str(e)}'
        }), 500
    finally:
        cursor.close()
        conn.close()

# ---------------- SNOOZE ALARM ----------------
@app.route('/tasks/<task_id>/snooze_alarm', methods=['POST'])
@jwt_required()
def snooze_alarm(task_id):
    conn = None
    cursor = None
    try:
        data = request.get_json()
        alarm_id = data.get('alarm_id')
        snooze_until = data.get('snooze_until')
        reason = data.get('reason', '')
        audio_note = data.get('audio_note')
        
        logger.info(f"Snoozing alarm {alarm_id} for task {task_id} until {snooze_until}")
        
        if not alarm_id or not snooze_until:
            return jsonify({
                'success': False,
                'message': 'Missing required fields: alarm_id and snooze_until'
            }), 400
            
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Get current user info
        claims = get_jwt()
        username = claims.get('username', 'Unknown')
        
        # Get current task description
        cursor.execute("SELECT description FROM tasks WHERE task_id = %s", (task_id,))
        task = cursor.fetchone()
        if not task:
            return jsonify({'success': False, 'message': 'Task not found'}), 404
        old_description = task['description'] or ''

        # Prepare snooze reason with date/time
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        reason_text = f"Snoozed on {now_str}: {reason}" if reason else f"Snoozed on {now_str}"
        new_description = old_description
        if reason_text:
            new_description = (old_description + '\n' if old_description else '') + reason_text

        # Update the task: set status to snoozed, append reason
        cursor.execute("""
            UPDATE tasks SET status = 'snoozed', description = %s, updated_at = NOW(), updated_by = %s
            WHERE task_id = %s
        """, (new_description, username, task_id))

        # Parse snooze_until as datetime
        try:
            snooze_datetime = datetime.fromisoformat(snooze_until.replace('Z', '+00:00'))
            logger.info(f"Parsed snooze_until as {snooze_datetime}")
        except Exception as e:
            logger.error(f"Error parsing snooze_until date: {e}")
            return jsonify({'success': False, 'message': f'Invalid date format: {e}'}), 400
        
        # Update the alarm to set next_trigger to the snooze time
        cursor.execute("""
            UPDATE task_alarms 
            SET next_trigger = %s,
                last_updated = NOW(),
                snooze_count = snooze_count + 1
            WHERE alarm_id = %s
        """, (snooze_datetime, alarm_id))
        
        if cursor.rowcount == 0:
            return jsonify({
                'success': False,
                'message': 'Alarm not found'
            }), 404
            
        # If audio_note is present, save as snooze audio note
        if audio_note and audio_note.get('audio_data'):
            audio_id = str(uuid.uuid4())
            audio_data = audio_note.get('audio_data')
            duration = audio_note.get('duration', 0)
            file_name = audio_note.get('filename', 'voice_note.wav')
            created_by = username
            audio_path = os.path.join(AUDIO_FOLDER, f"{audio_id}_{file_name}")
            os.makedirs(AUDIO_FOLDER, exist_ok=True)
            with open(audio_path, 'wb') as f:
                f.write(base64.b64decode(audio_data))
            cursor.execute("""
                INSERT INTO task_audio_notes (
                    audio_id, task_id, file_path, duration, 
                    file_name, created_by, note_type
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                audio_id, task_id, audio_path, duration,
                file_name, created_by, 'snooze'
            ))

        conn.commit()

        # Emit dashboard update event (if using SocketIO)
        try:
            notify_task_update({
                'task_id': task_id,
                'status': 'snoozed',
                'description': new_description,
                'updated_by': username
            }, 'task_snoozed')
        except Exception as e:
            logger.error(f"Error sending dashboard update after snooze: {str(e)}")
            
        # Send FCM notification to other assignees about this task
        try:
            cursor.execute("""
                SELECT u.fcm_token, u.username
                FROM users u
                JOIN task_assignments ta ON u.user_id = ta.user_id 
                WHERE ta.task_id = %s AND u.username != %s
            """, (task_id, username))
            
            assignees = cursor.fetchall()
            
            for assignee in assignees:
                if not assignee['fcm_token']:
                    continue
                    
                token = assignee['fcm_token']
                
                # Truncate description for notification
                truncated_description = new_description[:80] + "..." if len(new_description) > 80 else new_description
                
                # Get task title
                cursor.execute("SELECT title FROM tasks WHERE task_id = %s", (task_id,))
                task_result = cursor.fetchone()
                task_title = task_result['title'] if task_result else 'Unknown Task'
                
                # Construct notification message
                notification_title = f"Task Alarm Snoozed: {task_title}"
                notification_body = (
                    f"{username} has snoozed a task alarm: \"{task_title}\". "
                    f"Reason: {reason}. "
                    f"Snoozed until: {snooze_until}. "
                )
                
                # Build FCM message
                message = messaging.Message(
                    notification=messaging.Notification(
                        title=notification_title,
                        body=notification_body
                    ),
                    token=token,
                    data={
                        'task_id': str(task_id),
                        'alarm_id': str(alarm_id),
                        'status': 'snoozed',
                        'snooze_until': snooze_until,
                        'snooze_reason': reason or ''
                    },
                    android=messaging.AndroidConfig(
                        notification=messaging.AndroidNotification(
                            channel_id='high_importance_channel'
                        )
                    )
                )
                
                # Send notification
                logger.info(f"Sending snooze notification to user: {assignee['username']}, token: {token[:10]}...")
                response = messaging.send(message)
                logger.info(f"Notification sent successfully: {response}")
        
        except Exception as e:
            logger.error(f"Error sending snooze notification: {str(e)}")
            # Continue execution even if notification fails

        return jsonify({
            'success': True,
            'message': 'Alarm snoozed and task updated successfully'
        })
        
    except Exception as e:
        logger.error(f"Error snoozing alarm: {str(e)}")
        if conn:
            conn.rollback()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# ---------------- ACKNOWLEDGE ALARM ----------------
@app.route('/tasks/<task_id>/acknowledge_alarm', methods=['POST'])
@jwt_required()
def acknowledge_alarm(task_id):
    conn = None
    cursor = None
    try:
        data = request.get_json()
        alarm_id = data.get('alarm_id')
        
        logger.info(f"Acknowledging alarm {alarm_id} for task {task_id}")
        
        if not alarm_id:
            return jsonify({
                'success': False,
                'message': 'Missing required field: alarm_id'
            }), 400
            
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Get current user info
        claims = get_jwt()
        username = claims.get('username', 'Unknown')
        
        # Get current task description
        cursor.execute("SELECT description FROM tasks WHERE task_id = %s", (task_id,))
        task = cursor.fetchone()
        if not task:
            return jsonify({'success': False, 'message': 'Task not found'}), 404
        
        # Update the task status to in_progress
        cursor.execute("""
            UPDATE tasks SET status = 'in_progress', updated_at = NOW(), updated_by = %s
            WHERE task_id = %s
        """, (username, task_id))

        # Update the alarm to mark it as acknowledged
        cursor.execute("""
            UPDATE task_alarms 
            SET is_active = FALSE,
                acknowledged_at = NOW(),
                last_updated = NOW()
            WHERE alarm_id = %s
        """, (alarm_id,))
        
        if cursor.rowcount == 0:
            return jsonify({
                'success': False,
                'message': 'Alarm not found'
            }), 404
        
        conn.commit()
        
        # Emit dashboard update event
        try:
            notify_task_update({
                'task_id': task_id,
                'status': 'in_progress',
                'updated_by': username
            }, 'task_updated')
        except Exception as e:
            logger.error(f"Error sending dashboard update after alarm acknowledge: {str(e)}")
        
        return jsonify({
            'success': True,
            'message': 'Alarm acknowledged successfully'
        })
        
    except Exception as e:
        logger.error(f"Error acknowledging alarm: {str(e)}")
        if conn:
            conn.rollback()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route('/tasks/download', methods=['GET'])
@jwt_required()
def download_my_tasks():
    import csv
    import io
    from flask import make_response, request
    current_user = get_jwt_identity()
    logger.info(f"Download request from user: {current_user}")
    
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"USE {db_config['database']}")
        
        # Get tasks with assigned_by information
        cursor.execute("""
            SELECT 
                t.title, 
                t.description, 
                t.deadline, 
                t.priority, 
                t.status, 
                t.assigned_by,
                u.role as assigned_by_role
            FROM tasks t
            LEFT JOIN users u ON t.assigned_by = u.username
            WHERE t.assigned_to = %s
        """, (current_user,))
        
        tasks = cursor.fetchall()
        logger.info(f"Found {len(tasks)} tasks for user {current_user}")
        logger.info(f"Sample task data: {tasks[0] if tasks else 'No tasks'}")
        
        if not tasks:
            return jsonify({
                'success': False,
                'message': 'No tasks found for the current user'
            }), 404
            
        si = io.StringIO()
        writer = csv.writer(si)
        
        # Write header
        writer.writerow(['Title', 'Description', 'Deadline', 'Priority', 'Status', 'Assigned By', 'Assigned By Role'])
        
        # Write data rows
        for task in tasks:
            logger.info(f"Writing task to CSV: {task}")
            row = [
                task.get('title', ''),
                task.get('description', ''),
                task.get('deadline', ''),
                task.get('priority', ''),
                task.get('status', ''),
                task.get('assigned_by', ''),
                task.get('assigned_by_role', '')
            ]
            logger.info(f"CSV row: {row}")
            writer.writerow(row)
            
        output = make_response(si.getvalue())
        output.headers["Content-Disposition"] = "attachment; filename=tasks.csv"
        output.headers["Content-type"] = "text/csv"
        
        logger.info("CSV file generated successfully")
        return output
        
    except Exception as e:
        logger.error(f"Error downloading tasks: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# ---------------- MAIN ----------------
if __name__ == '__main__':
    # Initialize the application
    initialize_application()
    
    def check_stale_connections():
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM active_connections WHERE last_heartbeat < NOW() - INTERVAL 5 MINUTE")
            stale_connections = cursor.fetchall()
            
            for connection in stale_connections:
                cursor.execute("DELETE FROM active_connections WHERE connection_id = %s", (connection[0],))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            if stale_connections:
                logger.info(f"Cleaned up {len(stale_connections)} stale connections")
        except Exception as e:
            logger.error(f"Error checking stale connections: {str(e)}")
    
    def start_heartbeat_checker():
        def run_checker():
            while True:
                time.sleep(60)  # Check every minute
                check_stale_connections()
        
        checker_thread = threading.Thread(target=run_checker, daemon=True)
        checker_thread.start()
        logger.info("Started heartbeat checker thread")

    # Start the heartbeat checker before server starts
    start_heartbeat_checker()
    
    # Set port based on environment
    env = os.getenv('FLASK_ENV', 'production')
    port = 5001 if env == 'development' else 5000
    
    # Run the server using socketio.run() with eventlet
    print(f'Server starting on http://0.0.0.0:{port} in {env} mode')
    socketio.run(app, host='0.0.0.0', port=port, debug=(env == 'development'))

# Print all registered routes for debugging (always runs, even with Gunicorn)
for rule in app.url_map.iter_rules():
    print(f"Registered route: {rule}")



