from flask import Flask, request, jsonify, send_file, Response, send_from_directory
from flask_cors import CORS
import mysql.connector
from mysql.connector import pooling
import uuid
import logging
from datetime import datetime, timedelta
from flask_socketio import SocketIO, emit
import json
import os
from dotenv import load_dotenv
import base64
import io
from werkzeug.utils import secure_filename
import shutil
from firebase_admin import messaging, initialize_app, credentials
import time
import threading
import pytz  # Add this import at the top
import requests

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,  # Set to DEBUG for more detailed logs
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
    # Check if Firebase is already initialized
    if not os.getenv('FIREBASE_CREDENTIALS'):
        logger.warning("FIREBASE_CREDENTIALS environment variable not set - Firebase features will be disabled")
    else:
        # Parse the credentials from environment variable
        cred_dict = json.loads(os.getenv('FIREBASE_CREDENTIALS'))
        cred = credentials.Certificate(cred_dict)
        initialize_app(cred)
        logger.info("Firebase Admin SDK initialized successfully")
except Exception as e:
    logger.warning(f"Firebase initialization skipped: {str(e)}")
    # Don't raise the error, just log it and continue

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

            # Create tasks table with optional alarm fields
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
                    start_date DATE,
                    start_time TIME,
                    frequency VARCHAR(50),
                    next_alarm_time TIME,
                    acknowledged BOOLEAN DEFAULT FALSE,
                    acknowledged_at TIMESTAMP NULL,
                    created_by VARCHAR(100),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE ON UPDATE CASCADE,
                    FOREIGN KEY (created_by) REFERENCES users(username) ON DELETE SET NULL ON UPDATE CASCADE
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
    async_mode='eventlet',  # Changed to eventlet mode for compatibility
    ping_timeout=60,
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
    # Validate content type for non-WebSocket requests
    if request.method in ['POST', 'PUT']:
        if not request.is_json and request.headers.get('Content-Type') != 'application/json':
            return 'Content-Type must be application/json', 400

# Enhanced error handlers for SocketIO
@socketio.on_error()
def error_handler(e):
    logger.error(f"SocketIO error: {str(e)}")
    return {'error': str(e)}

@socketio.on_error_default
def default_error_handler(e):
    logger.error(f"SocketIO default error: {str(e)}")
    return {'error': str(e)}

# Initialize connected users dictionary
connected_users = {}

@socketio.on('connect')
def handle_connect():
    try:
        logger.info(f"Client connected: {request.sid}")
        return True
    except Exception as e:
        logger.error(f"Error in handle_connect: {str(e)}")
        return False

@socketio.on('disconnect')
def handle_disconnect():
    try:
        sid = request.sid
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
        
        logger.info(f"Client disconnected: {sid}")
        logger.info(f"Current connected users: {connected_users}")
    except Exception as e:
        logger.error(f"Error in handle_disconnect: {str(e)}")

@socketio.on('register')
def handle_register(data):
    try:
        # Handle both string and dictionary formats
        username = data if isinstance(data, str) else data.get('username')
        if not username:
            logger.error("No username provided for registration")
            return
        
        logger.info(f"Registering user {username} with sid {request.sid}")
        connected_users[username] = request.sid
        logger.info(f"Current connected users: {connected_users}")
        
        socketio.emit('register_response', {
            'status': 'registered',
            'username': username,
            'sid': request.sid
        }, room=request.sid)
    except Exception as e:
        logger.error(f"Error in handle_register: {str(e)}")

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
        cursor.execute("""
            INSERT INTO users (user_id, username, email, phone, password, role, fcm_token, timezone)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            user_id, data['username'], data['email'], data['phone'],
            data['password'], data['role'], data.get('fcm_token', ''), data.get('timezone', DEFAULT_TIMEZONE)
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
    conn = None
    cursor = None
    try:
        data = request.get_json()
        logger.info(f"Login attempt for user: {data.get('username')}")
        
        if not data or not data.get('username') or not data.get('password'):
            return jsonify({'message': 'Username and password are required'}), 400

        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"USE {db_config['database']}")

        # First verify user credentials
        cursor.execute("""
            SELECT user_id, username, email, phone, password, role, fcm_token
            FROM users WHERE username = %s AND password = %s
        """, (data['username'], data['password']))

        user = cursor.fetchone()
        if not user:
            logger.warning(f"Invalid login attempt for user: {data.get('username')}")
            return jsonify({'message': 'Invalid username or password'}), 401

        # Update FCM token if provided
        fcm_token = data.get('fcm_token')
        if fcm_token:
            logger.info(f"Updating FCM token for user: {user['username']}")
            try:
                cursor.execute("""
                    UPDATE users 
                    SET fcm_token = %s,
                        updated_at = NOW()
                    WHERE user_id = %s
                """, (fcm_token, user['user_id']))
                conn.commit()
                logger.info(f"FCM token updated successfully for user: {user['username']}")
            except Exception as e:
                logger.error(f"Failed to update FCM token for user {user['username']}: {str(e)}")
                # Don't fail the login if FCM update fails
                conn.rollback()
        else:
            logger.warning(f"No FCM token provided for user: {user['username']}")

        return jsonify({
            'message': 'Login successful',
            'user_id': user['user_id'],
            'username': user['username'],
            'role': user['role'],
            'fcm_token_updated': bool(fcm_token)
        }), 200

    except Exception as e:
        logger.error(f"Login error: {str(e)}")
        return jsonify({'message': f'Login failed: {str(e)}'}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# ---------------- CREATE TASK ----------------
@app.route('/tasks', methods=['POST'])
def create_task():
    conn = None
    cursor = None
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['title', 'description', 'assigned_to', 'assigned_by', 'priority', 'status', 'deadline']
        for field in required_fields:
            if field not in data:
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
        audio_note = data.get('audio_note')
        attachments = data.get('attachments', [])
        alarm_settings = data.get('alarm_settings')

        # Initialize database connection
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"USE {db_config['database']}")

        # Generate task ID
        task_id = str(uuid.uuid4())

        # Insert task
        cursor.execute("""
            INSERT INTO tasks (
                task_id, title, description, assigned_to,
                assigned_by, priority, status, deadline
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            task_id, title, description, assigned_to,
            assigned_by, priority, status, deadline
        ))

        # Handle audio note
        if audio_note:
            try:
                audio_id = str(uuid.uuid4())
                audio_data = audio_note.get('audio_data')
                duration = audio_note.get('duration', 0)
                file_name = audio_note.get('filename', 'voice_note.wav')
                created_by = data.get('updated_by', assigned_by)
                
                if audio_data:
                    # Save audio file
                    audio_path = os.path.join(AUDIO_FOLDER, f"{audio_id}_{file_name}")
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
                    logger.info(f"Added audio note: {audio_id}")
            except Exception as e:
                logger.error(f"Error handling audio note: {str(e)}")
                raise

        # Handle attachments
        if attachments:
            for attachment in attachments:
                try:
                    attachment_id = str(uuid.uuid4())
                    file_data = attachment.get('file_data')
                    file_name = attachment.get('file_name')
                    file_type = attachment.get('file_type')
                    
                    if file_data and file_name:
                        # Save attachment file
                        attachment_path = os.path.join(ATTACHMENTS_FOLDER, f"{attachment_id}_{file_name}")
                        os.makedirs(ATTACHMENTS_FOLDER, exist_ok=True)
                        with open(attachment_path, 'wb') as f:
                            f.write(base64.b64decode(file_data))
                        
                        # Get file size
                        file_size = os.path.getsize(attachment_path)
                        
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
                        logger.info(f"Added attachment: {attachment_id}")
                except Exception as e:
                    logger.error(f"Error handling attachment: {str(e)}")
                    continue

        # Handle alarm settings
        if alarm_settings:
            alarm_id = str(uuid.uuid4())
            cursor.execute("""
                INSERT INTO task_alarms (
                    alarm_id, task_id, start_date, start_time,
                    frequency, created_by
                )
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                alarm_id,
                task_id,
                alarm_settings.get('start_date'),
                alarm_settings.get('start_time'),
                alarm_settings.get('frequency'),
                assigned_by
            ))

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
def update_task(task_id):
    conn = None
    cursor = None
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['title', 'description', 'assigned_to', 'assigned_by', 'priority', 'status', 'deadline']
        for field in required_fields:
            if field not in data:
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
                audio_id = str(uuid.uuid4())
                audio_data = audio_note.get('audio_data')
                duration = audio_note.get('duration', 0)
                file_name = audio_note.get('filename', 'voice_note.wav')
                created_by = data.get('updated_by', assigned_by)
                
                if audio_data:
                    # Save audio file
                    audio_path = os.path.join(AUDIO_FOLDER, f"{audio_id}_{file_name}")
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
                    logger.info(f"Added audio note: {audio_id}")
            except Exception as e:
                logger.error(f"Error handling audio note: {str(e)}")
                raise

        # Handle attachments
        if attachments:
            for attachment in attachments:
                try:
                    attachment_id = str(uuid.uuid4())
                    file_data = attachment.get('file_data')
                    file_name = attachment.get('file_name')
                    file_type = attachment.get('file_type')
                    
                    if file_data and file_name:
                        # Save attachment file
                        attachment_path = os.path.join(ATTACHMENTS_FOLDER, f"{attachment_id}_{file_name}")
                        os.makedirs(ATTACHMENTS_FOLDER, exist_ok=True)
                        with open(attachment_path, 'wb') as f:
                            f.write(base64.b64decode(file_data))
                        
                        # Get file size
                        file_size = os.path.getsize(attachment_path)
                        
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
                        logger.info(f"Added attachment: {attachment_id}")
                except Exception as e:
                    logger.error(f"Error handling attachment: {str(e)}")
                    continue

        # Handle alarm settings
        if alarm_settings:
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
                    frequency, created_by
                )
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                alarm_id,
                task_id,
                alarm_settings.get('start_date'),
                alarm_settings.get('start_time'),
                alarm_settings.get('frequency'),
                assigned_by
            ))

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

# ---------------- GET TASK ATTACHMENTS ----------------
@app.route('/tasks/<task_id>/attachments', methods=['GET'])
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

        file_path = os.path.join(UPLOAD_FOLDER, attachment['file_path'])
        
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

        title = 'Task Updated' if event_type == 'task_updated' else 'New Task Assignment'
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
        cursor.execute("SELECT task_id FROM task_notifications WHERE id = %s", (notification_id,))
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
        user_id = data.get('user_id')
        fcm_token = data.get('fcm_token')
        
        if not user_id or not fcm_token:
            return jsonify({
                'success': False,
                'message': 'User ID and FCM token are required'
            }), 400
            
        logger.info(f"Updating FCM token for user ID: {user_id}")
        
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute(f"USE {db_config['database']}")
        
        cursor.execute("""
            UPDATE users 
            SET fcm_token = %s,
                updated_at = NOW()
            WHERE user_id = %s
        """, (fcm_token, user_id))
        
        if cursor.rowcount == 0:
            logger.warning(f"No user found with ID: {user_id}")
            return jsonify({
                'success': False,
                'message': 'User not found'
            }), 404
            
        conn.commit()
        logger.info(f"FCM token updated successfully for user ID: {user_id}")
        
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
def upload_file():
    try:
        if 'files[]' not in request.files:
            return jsonify({
                'success': False,
                'message': 'No files provided'
            }), 400
        
        files = request.files.getlist('files[]')
        file_type = request.form.get('type', 'attachment')
        uploaded_files = []
        
        # Define allowed extensions
        allowed_extensions = ALLOWED_AUDIO_EXTENSIONS if file_type == 'audio' else {'pdf', 'doc', 'docx', 'xls', 'xlsx', 'txt', 'jpg', 'jpeg', 'png'}
        
        for file in files:
            if file.filename == '':
                continue
                
            # Check file extension
            extension = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
            if extension not in allowed_extensions:
                return jsonify({
                    'success': False,
                    'message': f'File type .{extension} is not allowed'
                }), 400
                
            # Generate unique ID for the file
            file_id = str(uuid.uuid4())
            
            # Determine upload folder based on type
            upload_folder = AUDIO_FOLDER if file_type == 'audio' else ATTACHMENTS_FOLDER
            
            # Ensure filename is secure and unique
            filename = secure_filename(f"{file_id}_{file.filename}")
            file_path = os.path.join(upload_folder, filename)
            
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
            
            # Get file size
            file_size = os.path.getsize(file_path)
            
            uploaded_files.append({
                'file_id': file_id,
                'file_path': relative_path,
                'file_name': file.filename,
                'file_type': extension,
                'file_size': file_size
            })
        
        return jsonify({
            'success': True,
            'files': uploaded_files
        }), 200
        
    except Exception as e:
        logger.error(f"Error uploading files: {str(e)}")
        return jsonify({
            'success': False,
            'message': f"Error uploading files: {str(e)}"
        }), 500

# Serve audio files from uploads/audio
@app.route('/uploads/audio/<path:filename>')
def serve_audio(filename):
    try:
        # Get the absolute path to the audio file
        audio_path = os.path.join(UPLOAD_FOLDER, 'audio', filename)
        
        # Check if file exists
        if not os.path.exists(audio_path):
            logger.error(f"Audio file not found: {audio_path}")
            return jsonify({
                'success': False,
                'message': 'Audio file not found'
            }), 404
            
        # Determine the correct mimetype based on file extension
        extension = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
        if extension == 'wav':
            mimetype = 'audio/wav'
        elif extension == 'mp3':
            mimetype = 'audio/mpeg'
        elif extension == 'm4a':
            mimetype = 'audio/mp4'
        elif extension == 'ogg':
            mimetype = 'audio/ogg'
        else:
            mimetype = 'application/octet-stream'
            
        # Send the file with proper mimetype
        return send_file(
            audio_path,
            mimetype=mimetype,
            as_attachment=False,
            download_name=filename
        )
    except Exception as e:
        logger.error(f"Error serving audio file: {str(e)}")
        return jsonify({
            'success': False,
            'message': f"Error serving audio file: {str(e)}"
        }), 500

# ---------------- MARK ALL NOTIFICATIONS AS READ ----------------
@app.route('/notifications/accept_all', methods=['POST'])
def accept_all_notifications():
    conn = None
    cursor = None
    try:
        data = request.get_json()
        username = data.get('username')
        if not username:
            return jsonify({'success': False, 'message': 'Username required'}), 400

        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute(f"USE {db_config['database']}")
        cursor.execute("""
            UPDATE task_notifications SET is_read = 1 WHERE target_user = %s
        """, (username,))
        conn.commit()
        return jsonify({'success': True, 'message': 'All notifications marked as read'})
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def calculate_next_alarm_time(start_time, frequency):
    """Calculate the next alarm time based on start time and frequency"""
    try:
        # Parse start time
        start_datetime = datetime.strptime(start_time, '%H:%M:%S')
        
        # Get current time
        now = datetime.now()
        current_time = now.time()
        
        # Calculate next alarm time
        if frequency == '30 minutes':
            interval = timedelta(minutes=30)
        elif frequency == '1 hour':
            interval = timedelta(hours=1)
        elif frequency == '2 hours':
            interval = timedelta(hours=2)
        elif frequency == '4 hours':
            interval = timedelta(hours=4)
        elif frequency == '6 hours':
            interval = timedelta(hours=6)
        elif frequency == '8 hours':
            interval = timedelta(hours=8)
        else:
            return None
            
        # Find next alarm time
        next_alarm = start_datetime
        while next_alarm.time() <= current_time:
            next_alarm += interval
            
        return next_alarm.strftime('%H:%M:%S')
    except Exception as e:
        logger.error(f"Error calculating next alarm time: {str(e)}")
        return None

@app.route('/tasks/<task_id>/schedule_alarm', methods=['POST'])
def schedule_alarm(task_id):
    conn = None
    cursor = None
    try:
        data = request.get_json()
        start_date = data.get('start_date')
        start_time = data.get('start_time')
        frequency = data.get('frequency')
        
        if not all([start_date, start_time, frequency]):
            return jsonify({
                'success': False,
                'message': 'Missing required fields: start_date, start_time, frequency'
            }), 400
            
        # Validate frequency
        valid_frequencies = ['30min', '1hour', '2hours', '4hours']
        if frequency not in valid_frequencies:
            return jsonify({
                'success': False,
                'message': f'Invalid frequency. Must be one of: {", ".join(valid_frequencies)}'
            }), 400
            
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"USE {db_config['database']}")
        
        # Get task details
        cursor.execute("""
            SELECT t.task_id, t.title, t.assigned_to, u.fcm_token, u.timezone
            FROM tasks t
            JOIN users u ON t.assigned_to = u.username
            WHERE t.task_id = %s
        """, (task_id,))
        
        task = cursor.fetchone()
        if not task:
            return jsonify({
                'success': False,
                'message': 'Task not found'
            }), 404
            
        if not task['fcm_token']:
            return jsonify({
                'success': False,
                'message': 'Assignee does not have FCM token registered'
            }), 400
            
        # Calculate next alarm time
        next_alarm_time = calculate_next_alarm_time(start_time, frequency)
        if not next_alarm_time:
            return jsonify({
                'success': False,
                'message': 'Error calculating next alarm time'
            }), 500
            
        # Convert start time to user's timezone
        user_timezone = task.get('timezone', DEFAULT_TIMEZONE)
        try:
            start_datetime = datetime.strptime(f"{start_date} {start_time}", '%Y-%m-%d %H:%M:%S')
            start_datetime = convert_to_timezone(start_datetime, to_tz=user_timezone)
            start_time = start_datetime.strftime('%H:%M:%S')
        except Exception as e:
            logger.error(f"Error converting timezone: {str(e)}")
            
        # Update or insert alarm settings
        cursor.execute("""
            INSERT INTO task_alarms (
                alarm_id, task_id, start_date, start_time,
                frequency, next_alarm_time, created_by,
                acknowledged, acknowledged_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                start_date = VALUES(start_date),
                start_time = VALUES(start_time),
                frequency = VALUES(frequency),
                next_alarm_time = VALUES(next_alarm_time),
                acknowledged = VALUES(acknowledged),
                acknowledged_at = VALUES(acknowledged_at)
        """, (
            str(uuid.uuid4()),
            task_id,
            start_date,
            start_time,
            frequency,
            next_alarm_time,
            task['assigned_to'],
            False,
            None
        ))
        
        conn.commit()
        
        # Send FCM notification for the first alarm
        message = messaging.Message(
            notification=messaging.Notification(
                title=f"Task Reminder: {task['title']}",
                body=f"Alarm set for {start_time} with {frequency} frequency"
            ),
            data={
                'type': 'alarm',
                'task_id': task_id,
                'alarm_time': start_time,
                'frequency': frequency
            },
            android=messaging.AndroidConfig(
                priority='high',
                notification=messaging.AndroidNotification(
                    priority='max',
                    sound='default',
                    channel_id='task_alarms',
                    importance='high',
                    visibility='public',
                    default_sound=True,
                    default_vibrate_timings=True,
                    default_light_settings=True
                )
            ),
            apns=messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(
                        sound='default',
                        badge=1,
                        content_available=True
                    )
                )
            ),
            token=task['fcm_token']
        )
        
        try:
            messaging.send(message)
            logger.info(f"Alarm scheduled for task {task_id} with FCM token {task['fcm_token']}")
        except Exception as e:
            logger.error(f"Error sending FCM notification: {str(e)}")
            # Don't fail the request if FCM fails
            
        return jsonify({
            'success': True,
            'message': 'Alarm scheduled successfully',
            'next_alarm_time': next_alarm_time
        })
        
    except Exception as e:
        logger.error(f"Error scheduling alarm: {str(e)}")
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

@app.route('/tasks/<task_id>/acknowledge_alarm', methods=['POST'])
def acknowledge_alarm(task_id):
    conn = None
    cursor = None
    try:
        data = request.get_json()
        alarm_id = data.get('alarm_id')
        
        if not alarm_id:
            return jsonify({
                'success': False,
                'message': 'Alarm ID is required'
            }), 400
            
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute(f"USE {db_config['database']}")
        
        # Mark alarm as acknowledged
        cursor.execute("""
            UPDATE task_alarms 
            SET acknowledged = TRUE,
                acknowledged_at = NOW()
            WHERE alarm_id = %s AND task_id = %s
        """, (alarm_id, task_id))
        
        if cursor.rowcount == 0:
            return jsonify({
                'success': False,
                'message': 'Alarm not found'
            }), 404
            
        conn.commit()
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

def alarm_service():
    """Background service to check and trigger alarms"""
    while True:
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute(f"USE {db_config['database']}")
            
            # Get current time
            now = datetime.now()
            current_time = now.strftime('%H:%M:%S')
            
            # Find alarms that need to be triggered
            cursor.execute("""
                SELECT ta.*, t.title, t.assigned_to, u.fcm_token, u.timezone
                FROM task_alarms ta
                JOIN tasks t ON ta.task_id = t.task_id
                JOIN users u ON t.assigned_to = u.username
                WHERE ta.next_alarm_time <= %s
                AND (ta.acknowledged = FALSE OR ta.acknowledged IS NULL)
                AND t.status != 'completed'
            """, (current_time,))
            
            alarms = cursor.fetchall()
            
            if alarms:
                logger.info(f"Found {len(alarms)} alarms to trigger")
                
                for alarm in alarms:
                    try:
                        # Convert alarm time to user's timezone
                        user_timezone = alarm['timezone'] or 'UTC'
                        alarm_time = datetime.combine(
                            datetime.today(),
                            alarm['next_alarm_time']
                        )
                        user_alarm_time = convert_to_timezone(alarm_time, user_timezone)
                        
                        # Try to send FCM notification if Firebase is configured
                        if os.getenv('FIREBASE_CREDENTIALS') and alarm['fcm_token']:
                            try:
                                message = messaging.Message(
                                    notification=messaging.Notification(
                                        title=f"Task Reminder: {alarm['title']}",
                                        body=f"Time to check your task!"
                                    ),
                                    data={
                                        'type': 'alarm',
                                        'task_id': alarm['task_id'],
                                        'alarm_id': alarm['alarm_id']
                                    },
                                    token=alarm['fcm_token']
                                )
                                messaging.send(message)
                                logger.info(f"Alarm triggered for task {alarm['task_id']}")
                            except Exception as e:
                                logger.warning(f"Failed to send FCM notification: {str(e)}")
                        
                        # Calculate and update next alarm time
                        next_alarm_time = calculate_next_alarm_time(
                            alarm['next_alarm_time'].strftime('%H:%M:%S'),
                            alarm['frequency']
                        )
                        
                        if next_alarm_time:
                            cursor.execute("""
                                UPDATE task_alarms
                                SET next_alarm_time = %s,
                                    acknowledged = FALSE
                                WHERE alarm_id = %s
                            """, (next_alarm_time, alarm['alarm_id']))
                            conn.commit()
                            
                    except Exception as e:
                        logger.error(f"Error processing alarm {alarm['alarm_id']}: {str(e)}")
                        continue
                        
        except Exception as e:
            logger.error(f"Error in alarm service: {str(e)}")
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
            
        # Sleep for 30 seconds before next check
        time.sleep(30)

# Start alarm service in a separate thread
alarm_thread = threading.Thread(target=alarm_service, daemon=True)
alarm_thread.start()

# Add a health check endpoint
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'connected_clients': len(connected_users)
    })

# ---------------- DOWNLOAD AUDIO NOTE ----------------
@app.route('/api/tasks/<task_id>/audio/<audio_id>/download', methods=['GET'])
def download_audio_note(task_id, audio_id):
    conn = None
    cursor = None
    try:
        logger.info(f"Attempting to download audio note - Task ID: {task_id}, Audio ID: {audio_id}")
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"USE {db_config['database']}")

        # Get audio note details
        cursor.execute("""
            SELECT file_path, file_name, audio_id
            FROM task_audio_notes
            WHERE task_id = %s AND audio_id = %s
        """, (task_id, audio_id))

        audio_note = cursor.fetchone()
        if not audio_note:
            logger.error(f"Audio note not found - Task ID: {task_id}, Audio ID: {audio_id}")
            return jsonify({
                'success': False,
                'message': 'Audio note not found'
            }), 404

        file_path = audio_note['file_path']
        logger.info(f"File path from DB: {file_path}")

        # Try absolute path first
        abs_exists = os.path.exists(file_path)
        logger.info(f"Absolute path exists: {abs_exists}")

        # Try relative path from project root
        rel_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), file_path.lstrip('/'))
        rel_exists = os.path.exists(rel_path)
        logger.info(f"Relative path checked: {rel_path}, exists: {rel_exists}")

        if abs_exists:
            serve_path = file_path
        elif rel_exists:
            serve_path = rel_path
        else:
            logger.error(f"Audio file not found at absolute or relative path. Absolute: {file_path}, Relative: {rel_path}")
            return jsonify({
                'success': False,
                'message': f'Audio file not found. Checked absolute: {file_path}, relative: {rel_path}'
            }), 404

        extension = audio_note['file_name'].rsplit('.', 1)[1].lower() if '.' in audio_note['file_name'] else ''
        if extension == 'wav':
            mimetype = 'audio/wav'
        elif extension == 'mp3':
            mimetype = 'audio/mpeg'
        elif extension == 'm4a':
            mimetype = 'audio/mp4'
        elif extension == 'ogg':
            mimetype = 'audio/ogg'
        else:
            mimetype = 'application/octet-stream'

        logger.info(f"Sending file: {serve_path} with mimetype: {mimetype}")
        return send_file(
            serve_path,
            mimetype=mimetype,
            as_attachment=False,
            download_name=audio_note['file_name']
        )

    except Exception as e:
        logger.error(f"Error downloading audio note: {str(e)}")
        return jsonify({
            'success': False,
            'message': f"Error downloading audio note: {str(e)}"
        }), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route('/alarms/test_trigger', methods=['GET'])
def test_alarm_trigger():
    """Test endpoint to manually trigger an alarm for a user's device"""
    try:
        # Get task_id and username from query parameters
        task_id = request.args.get('task_id')
        username = request.args.get('username')
        
        if not task_id or not username:
            return jsonify({
                'success': False,
                'message': 'Missing required parameters: task_id and username'
            }), 400
            
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"USE {db_config['database']}")
        
        # Get task and user details
        cursor.execute("""
            SELECT t.task_id, t.title, t.description, u.user_id, u.fcm_token
            FROM tasks t
            JOIN users u ON t.assigned_to = u.username
            WHERE t.task_id = %s AND u.username = %s
        """, (task_id, username))
        
        result = cursor.fetchone()
        if not result:
            return jsonify({
                'success': False,
                'message': f'Task not found or user {username} is not assigned to this task'
            }), 404
            
        if not result['fcm_token']:
            return jsonify({
                'success': False,
                'message': f'User {username} does not have a registered FCM token'
            }), 400
            
        # Prepare notification data with special flags for immediate alarm
        notification = {
            "title": f"🔔 IMMEDIATE ALARM TEST: {result['title']}",
            "body": result['description'] or "Immediate alarm test - should play sound now!",
            "sound": "alarm"
        }
        
        # Prepare data payload with immediate flag
        data = {
            "type": "task_alarm",
            "task_id": result['task_id'],
            "alarm_id": f"test_{uuid.uuid4()}",
            "title": result['title'],
            "click_action": "FLUTTER_NOTIFICATION_CLICK",
            "immediate_alarm": "true",  # Special flag to indicate immediate alarm
            "play_sound_now": "true",   # Flag to trigger immediate sound playback
            "urgent": "true",           # Flag to indicate urgency
            "test_timestamp": str(int(time.time())),  # Add timestamp for testing
            "alarm_mode": "immediate"   # Set alarm mode to immediate
        }
        
        # Prepare FCM message with highest priority settings
        message = {
            "to": result['fcm_token'],
            "notification": notification,
            "data": data,
            "priority": "high",
            "time_to_live": 0,  # Expire immediately if not delivered
            "android": {
                "priority": "high",
                "ttl": "0s",   # Zero TTL for immediate delivery
                "notification": {
                    "sound": "alarm",
                    "channel_id": "task_alarms",
                    "priority": "max",
                    "visibility": "public",
                    "default_sound": False,
                    "default_vibrate_timings": False,
                    "vibrate_timings": ["0.1s", "0.1s", "0.1s", "0.1s", "0.1s"],
                    "notification_count": 1
                }
            },
            "apns": {
                "headers": {
                    "apns-priority": "10",  # Highest priority
                    "apns-push-type": "alert"
                },
                "payload": {
                    "aps": {
                        "sound": "alarm.wav",
                        "content-available": 1,  # Trigger silent push to wake app
                        "mutable-content": 1,    # Allow app to modify notification
                        "badge": 1,
                        "priority": 10,
                        "interruption-level": "critical"  # Highest interruption level
                    }
                }
            }
        }
        
        # Send FCM request
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"key={FCM_SERVER_KEY}"
        }
        
        logger.info(f"Sending immediate alarm test notification to {username}")
        logger.info(f"FCM token: {result['fcm_token'][:20]}...")
        logger.info(f"Message data: {data}")
        
        response = requests.post(
            FCM_API_URL,
            data=json.dumps(message),
            headers=headers
        )
        
        if response.status_code == 200:
            logger.info(f"Immediate alarm test sent successfully to {username}")
            return jsonify({
                'success': True,
                'message': f'Immediate alarm test sent successfully to {username}',
                'fcm_response': json.loads(response.text)
            })
        else:
            logger.error(f"Failed to send immediate alarm test: {response.text}")
            return jsonify({
                'success': False,
                'message': f'Failed to send FCM notification: {response.text}'
            }), 500
            
    except Exception as e:
        logger.error(f"Error in test_alarm_trigger: {str(e)}")
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# ---------------- MAIN ----------------
if __name__ == '__main__':
    # Initialize the application
    initialize_application()
    
    # Run the server with gevent
    from gevent import pywsgi
    from geventwebsocket.handler import WebSocketHandler
    
    # Set port based on environment
    env = os.getenv('FLASK_ENV', 'production')
    port = 5001 if env == 'development' else 5000
    
    server = pywsgi.WSGIServer(
        ('0.0.0.0', port),
        app,
        handler_class=WebSocketHandler
    )
    print(f'Server starting on http://0.0.0.0:{port} in {env} mode')
    server.serve_forever() 