from flask import Flask, request, jsonify, send_file, Response
from flask_cors import CORS
import mysql.connector
import uuid
import logging
from datetime import datetime
from flask_socketio import SocketIO, emit
import json
import os
from dotenv import load_dotenv
import base64
import io
from werkzeug.utils import secure_filename
import shutil

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

socketio = SocketIO(app, 
    cors_allowed_origins="*", 
    ping_timeout=20, 
    ping_interval=10,
    async_mode='threading', 
    logger=True, 
    engineio_logger=True,
    always_connect=True,
    reconnection=True,
    reconnection_attempts=5,
    reconnection_delay=1000,
    reconnection_delay_max=5000
)

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

# Store connected users
connected_users = {}

@socketio.on('connect')
def handle_connect():
    logger.info(f"Client connected: {request.sid}")
    socketio.emit('connect_response', {'status': 'connected', 'sid': request.sid}, room=request.sid)

@socketio.on('disconnect')
def handle_disconnect():
    user = next((username for username, sid in connected_users.items() if sid == request.sid), None)
    if user:
        del connected_users[user]
        logger.info(f"Client disconnected: {user} ({request.sid})")
    else:
        logger.info(f"Unknown client disconnected: {request.sid}")

@socketio.on('register')
def handle_register(username):
    connected_users[username] = request.sid
    logger.info(f"User registered: {username} ({request.sid})")
    socketio.emit('register_response', {'status': 'registered', 'username': username}, room=request.sid)

def notify_task_update(task_data, event_type='task_update'):
    """Notify relevant users about task updates"""
    try:
        logger.info(f"Notifying task update - Type: {event_type}, Task: {task_data}")
        
        # Get the assigned user's socket ID and role
        assigned_to = task_data.get('assigned_to')
        assigned_by = task_data.get('assigned_by')
        
        # Get sender's role
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT role FROM users WHERE username = %s", (assigned_by,))
        sender = cursor.fetchone()
        sender_role = sender['role'] if sender else 'Unknown'
        
        # Prepare notification data
        notification_data = {
            'type': event_type,
            'task': task_data,
            'sender': assigned_by,
            'target_user': assigned_to
        }
        
        # Store notification for assigned user
        if assigned_to:
            store_notification(task_data, event_type, assigned_to, sender_role)
            
            # Send real-time notification if user is connected
            if assigned_to in connected_users:
                logger.info(f"Sending notification to assigned user: {assigned_to}")
                socketio.emit('task_notification', notification_data, room=connected_users[assigned_to])
            else:
                logger.info(f"Assigned user not connected: {assigned_to}")

        # Store notification for assigner if different from assignee
        if assigned_by and assigned_by != assigned_to:
            store_notification(task_data, event_type, assigned_by, sender_role)
            
            # Send real-time notification if user is connected
            if assigned_by in connected_users:
                logger.info(f"Sending notification to assigner: {assigned_by}")
                socketio.emit('task_notification', notification_data, room=connected_users[assigned_by])

        # Broadcast dashboard update to all connected users
        logger.info("Broadcasting dashboard update to all users")
        socketio.emit('dashboard_update', notification_data, broadcast=True)
        
        logger.info("Notification sent successfully")
    except Exception as e:
        logger.error(f"Error in notify_task_update: {e}")
        logger.exception("Full traceback:")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# Create database tables if they don't exist
def init_db():
    try:
        # First connect without database to create it if needed
        temp_config = db_config.copy()
        temp_config.pop('database', None)  # Remove database from config
        conn = mysql.connector.connect(**temp_config)
        cursor = conn.cursor()

        # Create database if it doesn't exist
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {db_config['database']}")
        conn.commit()
        cursor.close()
        conn.close()

        # Now connect to the specific database
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

# Initialize database on startup
def update_tasks_table():
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()

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
                    UPDATE users SET fcm_token = %s WHERE user_id = %s
                """, (data['fcm_token'], user['user_id']))
                conn.commit()
                logger.info(f"Updated FCM token for user {user['username']}")

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
        if not data:
            return jsonify({'error': 'No JSON data provided'}), 400
            
        title = data.get('title')
        description = data.get('description')
        assigned_to = data.get('assigned_to')
        assigned_by = data.get('assigned_by')
        deadline = data.get('deadline')
        priority = data.get('priority')
        status = data.get('status')
        audio_note = data.get('audio_note')
        attachments = data.get('attachments', [])
        alarm_settings = data.get('alarm_settings')

        # Validate required fields
        if not all([title, assigned_to, assigned_by, deadline, priority, status]):
            return jsonify({'error': 'Missing required fields'}), 400

        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()

        # Generate UUID for task_id
        task_id = str(uuid.uuid4())

        # Insert task
        cursor.execute("""
            INSERT INTO tasks (task_id, title, description, assigned_to, assigned_by, deadline, priority, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (task_id, title, description, assigned_to, assigned_by, deadline, priority, status))
        
        # Handle audio note
        if audio_note:
            try:
                audio_id = str(uuid.uuid4())
                # Decode the JSON string first
                audio_note = json.loads(audio_note) if isinstance(audio_note, str) else audio_note
                audio_data = audio_note.get('audio_data', '')  # Base64 audio data
                audio_duration = audio_note.get('duration', 0)
                original_filename = audio_note.get('filename', 'voice_note.wav')
                
                # Ensure filename is secure and unique
                filename = secure_filename(f"{audio_id}_{original_filename}")
                file_path = os.path.join(AUDIO_FOLDER, filename)
                
                # Create uploads/audio directory if it doesn't exist
                os.makedirs(AUDIO_FOLDER, exist_ok=True)
                
                # Decode and save the audio file
                audio_binary = base64.b64decode(audio_data)
                with open(file_path, 'wb') as f:
                    f.write(audio_binary)
                
                # Store the relative path in database
                relative_path = os.path.join('uploads', 'audio', filename)
                cursor.execute("""
                    INSERT INTO task_audio_notes (audio_id, task_id, file_path, duration, file_name, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (audio_id, task_id, relative_path, audio_duration, original_filename, assigned_by))
                
                logger.info(f"Audio note saved with ID: {audio_id} at path: {relative_path}")
            except Exception as e:
                logger.error(f"Error saving audio file: {str(e)}")
                raise

        # Handle attachments from JSON data
        if attachments:
            for attachment_data in attachments:
                try:
                    attachment_id = str(uuid.uuid4())
                    file_name = attachment_data.get('file_name')
                    file_type = attachment_data.get('file_type')
                    file_data = attachment_data.get('file_data')  # Base64 encoded file data
                    
                    if not all([file_name, file_type, file_data]):
                        continue
                    
                    # Ensure filename is secure and unique
                    filename = secure_filename(f"{attachment_id}_{file_name}")
                    file_path = os.path.join(ATTACHMENTS_FOLDER, filename)
                    
                    # Create uploads/attachments directory if it doesn't exist
                    os.makedirs(ATTACHMENTS_FOLDER, exist_ok=True)
                    
                    # Decode and save the file
                    file_binary = base64.b64decode(file_data)
                    with open(file_path, 'wb') as f:
                        f.write(file_binary)
                    
                    # Get file size
                    file_size = os.path.getsize(file_path)
                    
                    # Store the relative path in database
                    relative_path = os.path.join('uploads', 'attachments', filename)
                    cursor.execute("""
                        INSERT INTO task_attachments (
                            attachment_id, task_id, file_name, file_type,
                            file_size, file_path, created_by
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (
                        attachment_id,
                        task_id,
                        file_name,
                        file_type,
                        file_size,
                        relative_path,
                        assigned_by
                    ))
                    
                    logger.info(f"Attachment saved with ID: {attachment_id} at path: {relative_path}")
                except Exception as e:
                    logger.error(f"Error saving attachment: {str(e)}")
                    raise

        # Handle alarm settings
        if alarm_settings:
            alarm_time = alarm_settings.get('alarm_time')
            alarm_type = alarm_settings.get('alarm_type')
            
            if alarm_time and alarm_type:
                cursor.execute("""
                    INSERT INTO task_alarms (task_id, alarm_time, alarm_type)
                    VALUES (%s, %s, %s)
                """, (task_id, alarm_time, alarm_type))

        conn.commit()
        
        # Prepare notification data
        notification_data = {
            'task_id': task_id,
            'title': title,
            'description': description,
            'assigned_to': assigned_to,
            'assigned_by': assigned_by,
            'priority': priority,
            'status': status,
            'deadline': deadline
        }
        
        # Notify users about the new task
        notify_task_update(notification_data, 'task_created')
        
        return jsonify({
            'success': True,
            'message': 'Task created successfully',
            'task_id': task_id
        }), 201

    except Exception as e:
        logger.error(f"Error creating task: {str(e)}")
        if conn:
            conn.rollback()
        return jsonify({
            'success': False,
            'error': str(e)
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

# ---------------- GET AUDIO NOTE ----------------
@app.route('/tasks/<task_id>/audio', methods=['GET'])
def get_audio_note(task_id):
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT audio_id, file_path, duration, file_name
            FROM task_audio_notes
            WHERE task_id = %s
        """, (task_id,))

        audio = cursor.fetchone()
        if audio:
            # Get the full file path
            file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), audio['file_path'])
            
            if not os.path.exists(file_path):
                return jsonify({
                    'success': False,
                    'message': 'Audio file not found on server'
                }), 404
            
            # Stream the file instead of forcing download
            return send_file(
                file_path,
                mimetype='audio/wav',
                as_attachment=False,  # Changed to False to enable streaming
                conditional=True      # Enable conditional requests
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
    try:
        data = request.get_json()
        
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
            SET priority = %s, status = %s, deadline = %s
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
                    audio_id, task_id, file_path, duration, created_by
                )
                VALUES (%s, %s, %s, %s, %s)
            """, (
                audio_id,
                task_id,
                audio_note.get('file_path'),
                audio_note.get('duration', 0),
                updated_by
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
                    attachment.get('file_path', ''),
                    updated_by
                ))

            if attachment_values:
                cursor.executemany("""
                    INSERT INTO task_attachments (
                        attachment_id, task_id, file_name, file_type,
                        file_size, file_path, created_by
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

        conn.commit()

        # Prepare notification data
        notification_data = {
            'task_id': task_id,
            'title': current_task['title'],
            'description': current_task['description'],
            'assigned_to': current_task['assigned_to'],
            'assigned_by': current_task['assigned_by'],
            'priority': priority,
            'status': status,
            'deadline': deadline,
            'updated_by': updated_by,
            'update_time': datetime.now().isoformat()
        }

        # After successful update, notify users
        notify_task_update(notification_data, 'task_updated')

        return jsonify({
            'success': True,
            'message': 'Task updated successfully',
            'task_id': task_id
        }), 200

    except mysql.connector.Error as db_err:
        logger.error(f"Database error in update_task: {db_err}")
        if conn:
            conn.rollback()
        return jsonify({
            'success': False,
            'message': f"Database error: {str(db_err)}",
            'task_id': task_id
        }), 500

    except Exception as e:
        logger.error(f"Error updating task: {str(e)}")
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
            SELECT file_path, duration, created_by, file_name, created_at
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
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()

        notification_id = str(uuid.uuid4())
        task_id = task_data.get('task_id')
        if not task_id:
            logger.error("No task_id found in task_data")
            return

        title = 'Task Updated' if event_type == 'task_updated' else 'New Task Assignment'
        description = f"{task_data['title']} {'updated' if event_type == 'task_updated' else 'assigned'} by {task_data['updated_by'] if event_type == 'task_updated' else task_data['assigned_by']}"

        logger.info(f"Storing notification - Task ID: {task_id}, Target User: {target_user}")

        cursor.execute("""
            INSERT INTO task_notifications (
                id, task_id, title, description, sender_name,
                sender_role, type, target_user
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            notification_id,
            task_id,
            title,
            description,
            task_data['updated_by'] if event_type == 'task_updated' else task_data['assigned_by'],
            sender_role,
            'task',
            target_user
        ))

        conn.commit()
        logger.info(f"Successfully stored notification: {notification_id}")

    except Exception as e:
        logger.error(f"Error storing notification: {e}")
        logger.exception("Full traceback:")
        if conn:
            conn.rollback()
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
        
        if not notification_id or not snooze_until:
            return jsonify({
                'success': False,
                'message': 'Missing required fields: notification_id and snooze_until'
            }), 400
        
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        
        # Update notification status and snooze details
        cursor.execute("""
            UPDATE task_notifications 
            SET status = 'snoozed',
                snooze_until = %s,
                snooze_reason = %s,
                snooze_audio = %s,
                is_read = FALSE
            WHERE id = %s
        """, (snooze_until, reason, audio_note, notification_id))
        
        if cursor.rowcount == 0:
            return jsonify({
                'success': False,
                'message': 'Notification not found'
            }), 404
        
        conn.commit()
        return jsonify({
            'success': True,
            'message': 'Notification snoozed successfully'
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
        
        # Get audio files
        cursor.execute("""
            SELECT file_path
            FROM task_audio_notes
            WHERE task_id = %s
        """, (task_id,))
        
        audio_files = cursor.fetchall()
        
        # Delete audio files from filesystem
        for audio in audio_files:
            file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), audio['file_path'])
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

# ---------------- MAIN ----------------
if __name__ == '__main__':
    # Initialize the application
    initialize_application()
    
    # Run the server
    socketio.run(
        app, 
        host='0.0.0.0', 
        port=5000, 
        debug=True, 
        use_reloader=False
    ) 