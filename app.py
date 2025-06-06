import os
import uuid
import json
import mysql.connector
import traceback
import logging
import socket
import time
import base64
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_file
from flask_socketio import SocketIO, emit
from flask_cors import CORS
from google.oauth2 import service_account
from push_notification import log_notification_attempt, get_user_fcm_token
import threading
import requests
import io
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
from google.auth.transport.requests import Request

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Production configurations
app.config['DEBUG'] = True  # Enable debug for development
app.config['ENV'] = 'development'  # Set to development environment
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-secret-key')

socketio = SocketIO(app, cors_allowed_origins="*", ping_timeout=20, ping_interval=10,
                   async_mode='threading', logger=True, engineio_logger=True)

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

# DB config using environment variables
db_config = {
    'host': os.getenv('DB_HOST', '134.209.149.12'),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', '123'),
    'database': os.getenv('DB_NAME', 'task_db')
}

# Store connected users and their socket IDs
connected_users = {}

@socketio.on('connect')
def handle_connect():
    logger.info('Client connected')

@socketio.on('disconnect')
def handle_disconnect():
    logger.info('Client disconnected')
    # Remove user from connected_users when they disconnect
    for username, user_data in list(connected_users.items()):
        if user_data['socket_id'] == request.sid:
            del connected_users[username]
            logger.info(f'User {username} disconnected')
            break

@socketio.on('register')
def handle_register(data):
    """Register a user with their socket ID and device info"""
    try:
        username = data.get('username')
        device_id = data.get('device_id')
        
        if not username:
            logger.error('No username provided in registration')
            return
        
        logger.info(f'Registering user: {username} with device: {device_id}')
        
        # Store user's socket ID and device info
        connected_users[username] = {
            'socket_id': request.sid,
            'device_id': device_id
        }
        
        logger.info(f'User {username} registered successfully')
        logger.info(f'Current connected users: {connected_users}')
    except Exception as e:
        logger.error(f'Error in handle_register: {e}')
        logger.exception('Full traceback:')

def notify_task_update(task_data, event_type='task_update'):
    """Notify relevant users about task updates"""
    try:
        logger.info(f"Notifying task update - Type: {event_type}, Task: {task_data}")
        
        # Get the assigned user's socket ID and device info
        assigned_to = task_data.get('assigned_to')
        assigned_by = task_data.get('assigned_by')
        updated_by = task_data.get('updated_by')
        
        # Get the sender's device info
        sender_device = None
        if assigned_by in connected_users:
            sender_device = connected_users[assigned_by].get('device_id')
        
        # Prepare notification data with device info
        notification_data = {
            'type': event_type,
            'task': task_data,
            'sender_device': sender_device
        }
        
        # Notify the assigned user
        if assigned_to in connected_users:
            logger.info(f"Sending notification to assigned user: {assigned_to}")
            socketio.emit('task_notification', notification_data, 
                         room=connected_users[assigned_to]['socket_id'])
        else:
            logger.info(f"Assigned user not connected: {assigned_to}")

        # Notify the assigner if different from assignee
        if assigned_by and assigned_by != assigned_to and assigned_by in connected_users:
            logger.info(f"Sending notification to assigner: {assigned_by}")
            socketio.emit('task_notification', notification_data, 
                         room=connected_users[assigned_by]['socket_id'])

        # Broadcast dashboard update to all connected users
        logger.info("Broadcasting dashboard update to all users")
        socketio.emit('dashboard_update', notification_data, broadcast=True)
        
        logger.info("Notification sent successfully")
    except Exception as e:
        logger.error(f"Error in notify_task_update: {e}")
        logger.exception("Full traceback:")

# Create database tables if they don't exist
def init_db():
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()

        # Enable foreign key checks
        cursor.execute("SET FOREIGN_KEY_CHECKS=0")

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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

        # Create audio notes table with duration field
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS task_audio_notes (
                audio_id VARCHAR(36) PRIMARY KEY,
                task_id VARCHAR(36) NOT NULL,
                audio_data LONGBLOB NOT NULL,
                file_name VARCHAR(255) NOT NULL,
                duration INT,
                created_by VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE ON UPDATE CASCADE,
                FOREIGN KEY (created_by) REFERENCES users(username) ON DELETE SET NULL ON UPDATE CASCADE
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
                file_data LONGBLOB NOT NULL,
                created_by VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE ON UPDATE CASCADE,
                FOREIGN KEY (created_by) REFERENCES users(username) ON DELETE SET NULL ON UPDATE CASCADE
            )
        """)

        # Re-enable foreign key checks
        cursor.execute("SET FOREIGN_KEY_CHECKS=1")

        conn.commit()
        logger.info("Database tables initialized successfully")

    except Exception as e:
        logger.error(f"Error initializing database: {str(e)}")
        if conn:
            conn.rollback()
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

        # Add alarm-related columns if they don't exist
        try:
            cursor.execute("""
                ALTER TABLE tasks
                ADD COLUMN IF NOT EXISTS start_date DATE,
                ADD COLUMN IF NOT EXISTS start_time TIME,
                ADD COLUMN IF NOT EXISTS frequency VARCHAR(50)
            """)
            logger.info("Added alarm-related columns")
        except mysql.connector.Error as e:
            if e.errno != 1060:  # Ignore "column already exists" error
                raise e

        # Check if columns exist first
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

            # Add columns one by one to handle cases where one might exist
            try:
                cursor.execute("""
                    ALTER TABLE tasks
                    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                """)
                logger.info("Added created_at column")
            except mysql.connector.Error as e:
                if e.errno != 1060:  # Ignore "column already exists" error
                    raise e

            try:
                cursor.execute("""
                    ALTER TABLE tasks
                    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
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

        # Check if file_name column exists
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM information_schema.columns
            WHERE table_schema = %s
            AND table_name = 'task_audio_notes'
            AND column_name = 'file_name'
        """, (db_config['database'],))

        result = cursor.fetchone()
        if result[0] == 0:  # Column doesn't exist
            logger.info("Adding file_name column to task_audio_notes table...")
            try:
                cursor.execute("""
                    ALTER TABLE task_audio_notes
                    ADD COLUMN file_name VARCHAR(255) NOT NULL DEFAULT 'voice_note.wav'
                """)
                conn.commit()
                logger.info("Successfully added file_name column")
            except mysql.connector.Error as e:
                if e.errno != 1060:  # Ignore "column already exists" error
                    raise e
        else:
            logger.info("file_name column already exists in task_audio_notes table")

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

        cursor.execute("SELECT * FROM users WHERE username = %s", (data['username'],))
        if cursor.fetchone():
            return jsonify({'message': 'User already exists'}), 409

        user_id = str(uuid.uuid4())
        cursor.execute("""
            INSERT INTO users (user_id, username, email, phone, password, role, fcm_token)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            user_id, data['username'], data['email'], data['phone'],
            data['password'], data['role'], data.get('fcm_token', '')
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
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT user_id, username, email, phone, password, role, fcm_token
            FROM users WHERE username = %s AND password = %s
        """, (data['username'], data['password']))

        user = cursor.fetchone()
        if user:
            # Update FCM token if provided
            if 'fcm_token' in data and data['fcm_token']:
                cursor.execute("""
                    UPDATE users SET fcm_token = %s WHERE username = %s
                """, (data['fcm_token'], user['username']))
                conn.commit()
                logger.info(f"Updated FCM token during login for user: {user['username']}")
            
            return jsonify({
                'message': 'Login successful',
                'user_id': user['user_id'],
                'username': user['username'],
                'role': user['role']
            }), 200

        return jsonify({'message': 'Invalid username or password'}), 401
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# ---------------- CREATE TASK ----------------
@app.route('/api/tasks', methods=['POST'])
def create_task():
    conn = None
    cursor = None
    try:
        data = request.get_json()
        logger.info(f"Creating new task: {data}")
        
        # Extract task data
        task_id = str(uuid.uuid4())
        title = data.get('title')
        description = data.get('description')
        assigned_to = data.get('assigned_to')
        assigned_by = data.get('assigned_by')
        deadline = data.get('deadline')
        priority = data.get('priority')
        status = data.get('status', 'pending')
        audio_note = data.get('audio_note')
        attachments = data.get('attachments', [])
        alarm_settings = data.get('alarm_settings')

        # Validate required fields
        if not all([title, assigned_to, assigned_by, deadline, priority]):
            return jsonify({
                'success': False,
                'message': 'Missing required fields'
            }), 400

        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)

        # Insert task
        cursor.execute("""
            INSERT INTO tasks (task_id, title, description, assigned_to, assigned_by, deadline, priority, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (task_id, title, description, assigned_to, assigned_by, deadline, priority, status))
        
        # Handle audio note
        if audio_note:
            audio_id = str(uuid.uuid4())
            audio_data = base64.b64decode(audio_note.get('data', ''))
            audio_duration = audio_note.get('duration', 0)
            file_name = audio_note.get('file_name', 'voice_note.wav')
            
            cursor.execute("""
                INSERT INTO task_audio_notes (audio_id, task_id, audio_data, duration, created_by, file_name)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (audio_id, task_id, audio_data, audio_duration, assigned_by, file_name))

        # Handle attachments
        for attachment in attachments:
            attachment_id = str(uuid.uuid4())
            file_data = base64.b64decode(attachment.get('data', ''))
            file_name = attachment.get('name', 'unnamed_file')
            file_type = attachment.get('type', 'application/octet-stream')
            file_size = attachment.get('size', 0)
            
            cursor.execute("""
                INSERT INTO task_attachments (attachment_id, task_id, file_data, file_name, file_type, file_size, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (attachment_id, task_id, file_data, file_name, file_type, file_size, assigned_by))

        # Handle alarm settings
        if alarm_settings:
            start_date = alarm_settings.get('start_date')
            start_time = alarm_settings.get('start_time')
            frequency = alarm_settings.get('frequency')
            
            cursor.execute("""
                UPDATE tasks
                SET start_date = %s, start_time = %s, frequency = %s
                WHERE task_id = %s
            """, (start_date, start_time, frequency, task_id))

        # Get the created task for notification
        cursor.execute("""
            SELECT task_id, title, description, deadline, priority, status, assigned_by, assigned_to, created_at, updated_at
            FROM tasks 
            WHERE task_id = %s
        """, (task_id,))
        created_task = cursor.fetchone()
        
        conn.commit()

        # Add task_id to the original data for notification purposes
        data['task_id'] = task_id
        
        # After task creation, notify users via WebSocket
        notify_task_update(created_task, event_type='task_created')
        
        # Process FCM notification in background thread
        from push_notification import log_notification_attempt
        notification_result = log_notification_attempt(created_task, 'created')
        
        # If FCM token is available, add notification details to response
        fcm_data = None
        if notification_result.get('success') and notification_result.get('fcm_token'):
            fcm_data = {
                'fcm_token': notification_result.get('fcm_token'),
                'notification': notification_result.get('notification')
            }

        return jsonify({
            'success': True,
            'message': 'Task created successfully',
            'task_id': task_id,
            'fcm_data': fcm_data
        }), 201

    except Exception as e:
        logger.error(f"Error creating task: {str(e)}")
        logger.exception("Full traceback:")
        if conn:
            conn.rollback()
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
    try:
        # Get query parameters
        username = request.args.get('username')
        role = request.args.get('role')

        logger.debug(f"Fetching tasks for username: {username}, role: {role}")

        if not username or not role:
            return jsonify([]), 400  # Return empty list on error

        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)

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
                    t.assigned_to,
                    {timestamp_columns},
                    COUNT(DISTINCT a.attachment_id) as attachment_count,
                    COUNT(DISTINCT an.audio_id) as has_audio
                FROM tasks t
                LEFT JOIN task_attachments a ON t.task_id = a.task_id
                LEFT JOIN task_audio_notes an ON t.task_id = an.task_id
                {where_clause}
                GROUP BY
                    t.task_id, t.title, t.description,
                    t.deadline, t.priority, t.status,
                    t.assigned_by, t.assigned_to
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
                    'assigned_to': task['assigned_to'],
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

        cursor.execute("""
            SELECT file_name, file_type, file_size, file_data
            FROM task_attachments
            WHERE attachment_id = %s
        """, (attachment_id,))

        attachment = cursor.fetchone()
        if attachment:
            # Create a BytesIO object from the binary data
            file_data = io.BytesIO(attachment['file_data'])
            
            # Determine the correct mimetype based on file type
            file_type = attachment['file_type'].lower()
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
                file_data,
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

# ---------------- GET AUDIO NOTE ----------------
@app.route('/tasks/<task_id>/audio', methods=['GET'])
def get_audio_note(task_id):
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT audio_id, audio_data, duration, file_name
            FROM task_audio_notes
            WHERE task_id = %s
        """, (task_id,))

        audio = cursor.fetchone()
        if audio:
            # Create a BytesIO object from the binary data
            audio_data = io.BytesIO(audio['audio_data'])
            
            # Get the filename, default to WAV if not specified
            filename = audio.get('file_name', f'voice_note_{audio["audio_id"]}.wav')
            
            # Send the file with proper mimetype
            return send_file(
                audio_data,
                mimetype='audio/wav',  # Always use WAV format
                as_attachment=True,
                download_name=filename
            )
        else:
            return jsonify({
                'success': False,
                'message': 'Audio note not found'
            }), 404

    except Exception as e:
        logger.error(f"Error fetching audio note: {str(e)}")
        return jsonify({
            'success': False,
            'message': f"Error fetching audio note: {str(e)}"
        }), 500
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

        # First get the user's role
        cursor.execute("SELECT role FROM users WHERE user_id = %s", (user_id,))
        user = cursor.fetchone()

        if not user:
            return jsonify({
                'success': False,
                'message': 'User not found'
            }), 404

        user_role = user['role'].lower()

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
            # For regular users, only show tasks where they are assigner or assignee
            query = base_query + """
                WHERE assigner.user_id = %s OR assignee.user_id = %s
                ORDER BY t.created_at DESC
            """
            cursor.execute(query, (user_id, user_id))

        assignments = cursor.fetchall()

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
        logger.info(f"Updating task {task_id}: {data}")
        
        # Validate required fields
        required_fields = ['priority', 'status', 'deadline', 'updated_by']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'success': False,
                    'message': f'Missing required field: {field}'
                }), 400

        priority = data['priority']
        status = data['status']
        deadline = data['deadline']
        updated_by = data['updated_by']

        # Validate priority
        valid_priorities = ['low', 'medium', 'high', 'urgent']
        if priority not in valid_priorities:
            return jsonify({
                'success': False,
                'message': 'Invalid priority value',
                'valid_priorities': valid_priorities
            }), 400

        # Validate status
        valid_statuses = ['pending', 'in_progress', 'completed', 'snoozed']
        if status not in valid_statuses:
            return jsonify({
                'success': False,
                'message': 'Invalid status value',
                'valid_statuses': valid_statuses
            }), 400

        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)

        # Get current task data
        cursor.execute("""
            SELECT * FROM tasks WHERE task_id = %s
        """, (task_id,))
        current_task = cursor.fetchone()

        if not current_task:
            return jsonify({
                'success': False,
                'message': 'Task not found'
            }), 404

        # Update task
        cursor.execute("""
            UPDATE tasks 
            SET priority = %s, status = %s, deadline = %s, updated_at = CURRENT_TIMESTAMP
            WHERE task_id = %s
        """, (priority, status, deadline, task_id))

        # Handle voice note if provided
        audio_note = data.get('audio_note')
        if audio_note:
            # First, delete existing voice notes for this task
            cursor.execute("""
                DELETE FROM task_audio_notes WHERE task_id = %s
            """, (task_id,))
            
            # Then insert the new voice note
            audio_id = str(uuid.uuid4())
            cursor.execute("""
                INSERT INTO task_audio_notes (
                    audio_id, task_id, audio_data, duration, created_by, file_name
                )
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                audio_id,
                task_id,
                audio_note.get('audio_data'),
                audio_note.get('duration', 0),
                updated_by,
                audio_note.get('file_name', 'voice_note.wav')
            ))

        # Handle attachments if provided
        attachments = data.get('attachments')
        if attachments:
            # First, delete existing attachments for this task
            cursor.execute("""
                DELETE FROM task_attachments WHERE task_id = %s
            """, (task_id,))
            
            # Then insert new attachments
            attachment_values = []
            for attachment in attachments:
                attachment_id = str(uuid.uuid4())
                attachment_values.append((
                    attachment_id,
                    task_id,
                    attachment.get('file_name', ''),
                    attachment.get('file_type', ''),
                    attachment.get('file_size', 0),
                    attachment.get('file_data', ''),
                    updated_by
                ))

            if attachment_values:
                cursor.executemany("""
                    INSERT INTO task_attachments (
                        attachment_id, task_id, file_name, file_type,
                        file_size, file_data, created_by
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, attachment_values)

        # Handle alarm settings if provided
        alarm_settings = data.get('alarm_settings')
        if alarm_settings:
            cursor.execute("""
                UPDATE tasks 
                SET start_date = %s, start_time = %s, frequency = %s
                WHERE task_id = %s
            """, (
                alarm_settings.get('start_date'),
                alarm_settings.get('start_time'),
                alarm_settings.get('frequency'),
                task_id
            ))

        # Get updated task data for notification
        cursor.execute("""
            SELECT task_id, title, description, deadline, priority, status, assigned_by, assigned_to, created_at, updated_at
            FROM tasks 
            WHERE task_id = %s
        """, (task_id,))
        updated_task = cursor.fetchone()
        
        # Add the updated_by field to the task data for notification purposes
        updated_task['updated_by'] = updated_by

        conn.commit()

        # Notify clients via WebSocket
        notify_task_update(updated_task, event_type='task_updated')
        
        # Process FCM notification
        from push_notification import log_notification_attempt
        notification_result = log_notification_attempt(updated_task, 'updated')
        
        # If FCM token is available, add notification details to response
        fcm_data = None
        if notification_result.get('success') and notification_result.get('fcm_token'):
            fcm_data = {
                'fcm_token': notification_result.get('fcm_token'),
                'notification': notification_result.get('notification')
            }

        return jsonify({
            'success': True,
            'message': 'Task updated successfully',
            'task_id': task_id,
            'fcm_data': fcm_data
        }), 200

    except mysql.connector.Error as db_err:
        logger.error(f"Database error in update_task: {db_err}")
        logger.exception("Full traceback:")
        if conn:
            conn.rollback()
        return jsonify({
            'success': False,
            'message': f"Database error: {str(db_err)}",
            'task_id': task_id
        }), 500

    except Exception as e:
        logger.error(f"Error updating task: {str(e)}")
        logger.exception("Full traceback:")
        if conn:
            conn.rollback()
        return jsonify({
            'success': False,
            'message': f"Error updating task: {str(e)}",
            'task_id': task_id
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
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT audio_data, duration, created_by, file_name, created_at
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
                'audio_data': base64.b64encode(note['audio_data']).decode('utf-8'),
                'duration': note['duration'],
                'created_by': note['created_by'],
                'file_name': note['file_name'],
                'created_at': note['created_at'].isoformat() if note['created_at'] else None
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
def get_task_attachments(task_id):
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT 
                attachment_id as id,
                task_id,
                file_name,
                file_type,
                file_size,
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

        cursor.execute("""
            SELECT file_name, file_type, file_data
            FROM task_attachments
            WHERE attachment_id = %s
        """, (attachment_id,))

        attachment = cursor.fetchone()
        if not attachment:
            return jsonify({
                'success': False,
                'message': 'Attachment not found'
            }), 404

        return jsonify({
            'success': True,
            'data': {
                'file_name': attachment['file_name'],
                'file_type': attachment['file_type'],
                'file_data': attachment['file_data']
            }
        }), 200

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

# ---------------- UPDATE FCM TOKEN ----------------
@app.route('/update_fcm_token', methods=['POST'])
def update_fcm_token():
    conn = None
    cursor = None
    try:
        data = request.get_json()
        if 'username' not in data or 'fcm_token' not in data:
            return jsonify({'success': False, 'message': 'Missing required fields'}), 400

        username = data['username']
        fcm_token = data['fcm_token']
        
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()

        # Update the user's FCM token
        cursor.execute("""
            UPDATE users SET fcm_token = %s WHERE username = %s
        """, (fcm_token, username))
        
        if cursor.rowcount == 0:
            return jsonify({'success': False, 'message': 'User not found'}), 404
            
        conn.commit()
        logger.info(f"Updated FCM token for user: {username}")
        
        return jsonify({'success': True, 'message': 'FCM token updated successfully'}), 200
    
    except Exception as e:
        logger.error(f"Error updating FCM token: {str(e)}")
        if conn:
            conn.rollback()
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500
    
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# ---------------- CHECK FCM TOKEN ----------------
@app.route('/check_fcm_token/<username>', methods=['GET'])
def check_fcm_token(username):
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        
        # Query the user's FCM token
        cursor.execute("SELECT fcm_token FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()
        
        if not user:
            return jsonify({'success': False, 'message': 'User not found'}), 404
            
        return jsonify({
            'success': True, 
            'username': username,
            'fcm_token': user['fcm_token'],
            'has_token': user['fcm_token'] is not None and user['fcm_token'] != ''
        }), 200
    
    except Exception as e:
        logger.error(f"Error checking FCM token: {str(e)}")
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500
    
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# ---------------- TEST NOTIFICATION ----------------
@app.route('/test_notification/<username>', methods=['GET'])
def test_notification(username):
    try:
        # First check if Firebase is initialized
        is_initialized = bool(firebase_admin._apps)
        logger.info(f"Firebase initialization status: {is_initialized}")
        
        # Get user information
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()
        
        if not user:
            return jsonify({'success': False, 'message': 'User not found'}), 404
        
        # Create test notification data
        test_data = {
            'task_id': 'test_task_123',
            'title': 'Test Task',
            'description': 'This is a test notification',
            'deadline': datetime.now().isoformat(),
            'priority': 'high',
            'status': 'pending',
            'assigned_by': 'admin',
            'assigned_to': username,
            'updated_by': 'system'
        }
        
        # Attempt to send notification synchronously (not in thread)
        logger.info(f"Attempting to send test notification to {username}")
        result = log_notification_attempt(test_data, 'test')
        
        logger.info(f"FCM notification result: {result}")
        
        return jsonify({
            'success': True,
            'firebase_initialized': is_initialized,
            'user': {
                'username': user['username'],
                'fcm_token': user['fcm_token'],
                'has_token': user['fcm_token'] is not None and user['fcm_token'] != ''
            },
            'notification_result': result
        }), 200
        
    except Exception as e:
        logger.error(f"Error testing notification: {str(e)}")
        logger.exception("Full exception:")
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# ---------------- TEST FCM DIRECT ----------------
@app.route('/test_fcm_direct/<username>', methods=['GET'])
def test_fcm_direct(username):
    try:
        # Get user information
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()
        
        if not user or not user['fcm_token']:
            return jsonify({'success': False, 'message': 'User not found or has no FCM token'}), 404
        
        fcm_token = user['fcm_token']
        
        # Import module here to avoid circular imports
        from push_notification import log_notification_attempt
        
        # Create test notification data
        test_data = {
            'task_id': 'test_task_123',
            'title': 'Test Task',
            'description': 'This is a test notification',
            'deadline': datetime.now().isoformat(),
            'priority': 'high',
            'status': 'pending',
            'assigned_by': 'admin',
            'assigned_to': username,
            'updated_by': 'system'
        }
        
        # Log the notification attempt
        logger.info(f"Logging test notification for {username} with token: {fcm_token[:10]}...{fcm_token[-5:]}")
        result = log_notification_attempt(test_data, 'test')
        logger.info(f"Notification result: {result}")
        
        return jsonify({
            'success': True,
            'fcm_token': fcm_token,
            'notification_result': result
        }), 200
        
    except Exception as e:
        logger.error(f"Error in direct FCM test: {str(e)}")
        logger.exception("Full exception:")
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# ---------------- TEST FIREBASE ----------------
@app.route('/test_firebase', methods=['GET'])
def test_firebase():
    try:
        # Create simple response without requiring Firebase
        test_data = {
            'task_id': 'test123',
            'title': 'Test Notification',
            'description': 'Testing notification system',
            'assigned_to': 'admin',
            'assigned_by': 'system',
            'deadline': datetime.now().isoformat(),
            'priority': 'high',
            'status': 'pending'
        }
        
        # Use the modified function from push_notification.py
        notification_result = log_notification_attempt(test_data, 'test')
        
        return jsonify({
            'success': True,
            'message': 'Client-side Firebase notification approach is active',
            'notification_result': notification_result,
            'credentials_required': False
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500

# ---------------- SEND NOTIFICATION ----------------
@app.route('/send_notification', methods=['POST'])
def send_notification():
    try:
        data = request.get_json()
        username = data.get('username')
        title = data.get('title')
        body = data.get('body')
        payload = data.get('payload', {})
        
        if not all([username, title, body]):
            return jsonify({'success': False, 'message': 'Missing required fields'}), 400
            
        # Get user's FCM token
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT fcm_token FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()
        
        if not user or not user['fcm_token']:
            return jsonify({'success': False, 'message': 'User has no FCM token'}), 404
        
        # Return the token for client to use
        return jsonify({
            'success': True,
            'fcm_token': user['fcm_token'],
            'notification': {
                'title': title,
                'body': body,
                'data': payload
            }
        }), 200
        
    except Exception as e:
        logger.error(f"Error in send_notification: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

# Add this endpoint after the send_notification endpoint
@app.route('/test_client_notification/<username>', methods=['GET'])
def test_client_notification(username):
    try:
        # Get user information
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()
        
        if not user:
            return jsonify({'success': False, 'message': 'User not found'}), 404
        
        # Create test notification data
        test_data = {
            'task_id': 'test_task_123',
            'title': 'Test Client Notification',
            'description': 'This is a test notification using client-side approach',
            'assigned_to': username,
            'assigned_by': 'system',
            'deadline': datetime.now().isoformat(),
            'priority': 'high',
            'status': 'pending'
        }
        
        # Get the FCM token
        fcm_token = user.get('fcm_token')
        
        if not fcm_token:
            return jsonify({
                'success': False,
                'message': 'User has no FCM token registered'
            }), 400
        
        # Prepare notification data for client
        notification_data = {
            'success': True,
            'fcm_token': fcm_token,
            'notification': {
                'title': 'Test Notification',
                'body': f'Hello {username}, this is a test notification!',
                'data': {
                    'task_id': 'test_task_123',
                    'click_action': 'FLUTTER_NOTIFICATION_CLICK',
                    'type': 'test'
                }
            }
        }
        
        return jsonify(notification_data), 200
        
    except Exception as e:
        logger.error(f"Error in test_client_notification: {str(e)}")
        logger.exception("Full exception:")
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# ---------------- FIREBASE TOKEN ----------------
@app.route('/firebase_token', methods=['GET'])
def get_firebase_token():
    """
    Provides a Firebase OAuth token for clients to use with FCM API v1
    This endpoint gets or generates an OAuth access token for FCM
    """
    try:
        # Check if we have a cached token
        cached_token = app.config.get('fcm_oauth_token')
        cached_token_expiry = app.config.get('fcm_oauth_token_expiry', 0)
        
        # If token exists and is valid (with 5-minute buffer)
        current_time = time.time()
        if cached_token and cached_token_expiry > current_time + 300:
            logger.info("Using cached OAuth token for FCM")
            return jsonify({
                'success': True, 
                'token': cached_token,
                'expires_in': int(cached_token_expiry - current_time)
            }), 200
            
        # Get service account credentials from environment variable
        service_account_info = os.getenv('FIREBASE_SERVICE_ACCOUNT')
        if not service_account_info:
            logger.error("FIREBASE_SERVICE_ACCOUNT environment variable not set")
            return jsonify({
                'success': False,
                'message': 'Firebase service account not configured'
            }), 500

        try:
            # Parse the service account JSON
            service_account = json.loads(service_account_info)
            
            # Create credentials from service account
            credentials = service_account.Credentials.from_service_account_info(
                service_account,
                scopes=['https://www.googleapis.com/auth/firebase.messaging']
            )
            
            # Get the access token
            credentials.refresh(Request())
            access_token = credentials.token
            
            # Cache the token for future requests (expires in 50 minutes)
            token_expiry = current_time + 3000  # 50 minutes in seconds
            app.config['fcm_oauth_token'] = access_token
            app.config['fcm_oauth_token_expiry'] = token_expiry
            
            logger.info("Generated new OAuth token for FCM")
            
            return jsonify({
                'success': True, 
                'token': access_token,
                'expires_in': 3000  # 50 minutes
            }), 200
            
        except Exception as e:
            logger.error(f"Error generating OAuth token: {str(e)}")
            return jsonify({
                'success': False,
                'message': f'Error generating OAuth token: {str(e)}'
            }), 500
            
    except Exception as e:
        logger.error(f"Error providing Firebase token: {str(e)}")
        logger.exception("Full exception:")
        return jsonify({
            'success': False,
            'message': f'Error providing Firebase token: {str(e)}'
        }), 500

# ---------------- MAIN ----------------
if __name__ == '__main__':
    # Initialize the application
    initialize_application()
    
    # Run the server
    socketio.run(app, host='0.0.0.0', port=5000, debug=True, use_reloader=False) 