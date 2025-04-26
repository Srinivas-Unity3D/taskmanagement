from flask import Flask, request, jsonify
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
        
        # Get the assigned user's socket ID
        assigned_to = task_data.get('assigned_to')
        assigned_by = task_data.get('assigned_by')
        
        # Notify the assigned user
        if assigned_to in connected_users:
            logger.info(f"Sending notification to assigned user: {assigned_to}")
            socketio.emit('task_notification', {
                'type': event_type,
                'task': task_data
            }, room=connected_users[assigned_to])
        else:
            logger.info(f"Assigned user not connected: {assigned_to}")

        # Notify the assigner if different from assignee
        if assigned_by and assigned_by != assigned_to and assigned_by in connected_users:
            logger.info(f"Sending notification to assigner: {assigned_by}")
            socketio.emit('task_notification', {
                'type': event_type,
                'task': task_data
            }, room=connected_users[assigned_by])

        # Broadcast dashboard update to all connected users
        logger.info("Broadcasting dashboard update to all users")
        socketio.emit('dashboard_update', {
            'type': event_type,
            'task': task_data
        }, broadcast=True)
        
        logger.info("Notification sent successfully")
    except Exception as e:
        logger.error(f"Error in notify_task_update: {e}")
        logger.exception("Full traceback:")

# Create database tables if they don't exist
def init_db():
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()

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
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)

        # Create audio notes table with duration field
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS task_audio_notes (
                audio_id VARCHAR(36) PRIMARY KEY,
                task_id VARCHAR(36) NOT NULL,
                audio_data LONGBLOB NOT NULL,
                duration INT,
                created_by VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE,
                FOREIGN KEY (created_by) REFERENCES users(username)
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
                FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE,
                FOREIGN KEY (created_by) REFERENCES users(username)
            )
        """)

        conn.commit()
        logger.info("Database tables initialized successfully")

    except Exception as e:
        logger.error(f"Error initializing database: {str(e)}")
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

init_db()
update_tasks_table()

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
@app.route('/create_task', methods=['POST'])
def create_task():
    conn = None
    cursor = None
    try:
        data = request.get_json()
        logger.debug(f"Received data: {data}")

        # Required fields
        required_fields = ['title', 'assigned_by', 'assigned_to', 'deadline', 'priority']
        if not all(field in data for field in required_fields):
            return jsonify({
                'success': False,
                'message': 'Missing required fields',
                'required_fields': required_fields
            }), 400

        task_id = str(uuid.uuid4())
        title = data['title']
        description = data.get('description', '')  # Optional
        assigned_by = data['assigned_by']
        assigned_to = data['assigned_to']
        deadline = data['deadline']
        priority = data['priority'].lower()
        status = data.get('status', 'pending').lower()

        # Initialize alarm fields as None
        start_date = None
        start_time = None
        frequency = None

        # Handle alarm settings if present
        alarm_settings = data.get('alarm_settings')
        if alarm_settings and isinstance(alarm_settings, dict):
            try:
                # Only process if both start_date and start_time are present
                if 'start_date' in alarm_settings and 'start_time' in alarm_settings:
                    start_date_str = alarm_settings['start_date']
                    start_time_str = alarm_settings['start_time']

                    # Parse and validate the date
                    try:
                        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
                    except ValueError as e:
                        return jsonify({
                            'success': False,
                            'message': f'Invalid date format. Expected YYYY-MM-DD, got: {start_date_str}'
                        }), 400

                    # Parse and validate the time
                    try:
                        # This will raise ValueError if format is incorrect
                        datetime.strptime(start_time_str, '%H:%M:%S')
                        start_time = start_time_str
                    except ValueError as e:
                        return jsonify({
                            'success': False,
                            'message': f'Invalid time format. Expected HH:MM:SS, got: {start_time_str}'
                        }), 400

                    frequency = alarm_settings.get('frequency')
            except Exception as e:
                logger.error(f"Error processing alarm settings: {e}")
                return jsonify({
                    'success': False,
                    'message': f'Error processing alarm settings: {str(e)}'
                }), 400

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
        cursor = conn.cursor()

        # Insert task with optional alarm settings
        cursor.execute("""
            INSERT INTO tasks (
                task_id, title, description, assigned_by, assigned_to,
                deadline, priority, status, start_date, start_time, frequency
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            task_id, title, description, assigned_by, assigned_to,
            deadline, priority, status, start_date, start_time, frequency
        ))

        # Handle optional voice note if provided
        audio_note = data.get('audio_note')
        if audio_note:
            audio_id = str(uuid.uuid4())
            cursor.execute("""
                INSERT INTO task_audio_notes (
                    audio_id, task_id, audio_data, duration, created_by
                )
                VALUES (%s, %s, %s, %s, %s)
            """, (
                audio_id,
                task_id,
                audio_note.get('audio_data'),
                audio_note.get('duration', 0),
                assigned_by
            ))

        # Handle optional attachments if provided
        attachments = data.get('attachments', [])
        if attachments:
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
                    assigned_by
                ))

            if attachment_values:
                cursor.executemany("""
                    INSERT INTO task_attachments (
                        attachment_id, task_id, file_name, file_type,
                        file_size, file_data, created_by
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, attachment_values)

        conn.commit()
        logger.info(f"Task created successfully: {task_id}")

        # After successful task creation, notify users
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

        return jsonify({
            'success': True,
            'message': 'Task created successfully',
            'task_id': task_id
        }), 201

    except mysql.connector.Error as db_err:
        logger.error(f"Database error in create_task: {db_err}")
        if conn:
            conn.rollback()
        return jsonify({
            'success': False,
            'message': f"Database error: {str(db_err)}"
        }), 500
    except Exception as e:
        logger.error(f"Unexpected error in create_task: {e}")
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
            return jsonify({
                'success': True,
                'data': {
                    'file_name': attachment['file_name'],
                    'file_type': attachment['file_type'],
                    'file_size': attachment['file_size'],
                    'file_data': attachment['file_data'],
                }
            }), 200
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
            SELECT audio_id, audio_data, duration
            FROM task_audio_notes
            WHERE task_id = %s
        """, (task_id,))

        audio = cursor.fetchone()
        if audio:
            return jsonify({
                'success': True,
                'data': {
                    'audio_id': audio['audio_id'],
                    'audio_data': audio['audio_data'],
                    'duration': audio['duration'],
                }
            }), 200
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
                    audio_id, task_id, audio_data, duration, created_by
                )
                VALUES (%s, %s, %s, %s, %s)
            """, (
                audio_id,
                task_id,
                audio_note.get('audio_data'),
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
@app.route('/tasks/<task_id>/voice_notes', methods=['GET'])
def get_task_voice_notes(task_id):
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT 
                audio_id as id,
                task_id,
                audio_data,
                duration,
                created_by,
                created_at
            FROM task_audio_notes
            WHERE task_id = %s
        """, (task_id,))

        voice_notes = cursor.fetchall()
        
        # Convert datetime objects to string and encode binary data
        formatted_notes = []
        for note in voice_notes:
            formatted_note = {
                'id': note['id'],
                'task_id': note['task_id'],
                'audio_data': base64.b64encode(note['audio_data']).decode('utf-8') if note['audio_data'] else None,
                'duration': note['duration'],
                'created_by': note['created_by'],
                'created_at': note['created_at'].strftime('%Y-%m-%d %H:%M:%S') if note['created_at'] else None
            }
            formatted_notes.append(formatted_note)

        return jsonify({
            'success': True,
            'voice_notes': formatted_notes
        }), 200

    except Exception as e:
        logger.error(f"Error fetching voice notes: {str(e)}")
        return jsonify({
            'success': False,
            'message': f"Error fetching voice notes: {str(e)}",
            'voice_notes': []
        }), 500
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

# ---------------- MAIN ----------------
if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True, use_reloader=False) 