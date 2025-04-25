from flask import Flask, request, jsonify
from flask_cors import CORS
import mysql.connector
import uuid
import logging
from datetime import datetime

app = Flask(__name__)
CORS(app)

# Optional: Setup logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# DB config to reuse
db_config = {
    'host': '134.209.149.12',
    'user': 'root',
    'password': '123',
    'database': 'task_db'
}

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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)

        # Create audio notes table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS task_audio_notes (
                audio_id VARCHAR(36) PRIMARY KEY,
                task_id VARCHAR(36) NOT NULL,
                audio_data LONGBLOB NOT NULL,
                duration INT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
            )
        """)

        # Create attachments table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS task_attachments (
                attachment_id VARCHAR(36) PRIMARY KEY,
                task_id VARCHAR(36) NOT NULL,
                file_name VARCHAR(255) NOT NULL,
                file_type VARCHAR(100) NOT NULL,
                file_size INT NOT NULL,
                file_data LONGBLOB NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
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
init_db()

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

        required_fields = ['title', 'assigned_by', 'assigned_to', 'deadline', 'priority']
        if not all(field in data for field in required_fields):
            return jsonify({'success': False, 'message': 'Missing required fields'}), 400

        task_id = str(uuid.uuid4())
        title = data['title']
        description = data.get('description', '')
        assigned_by = data['assigned_by']
        assigned_to = data['assigned_to']
        deadline = data['deadline']
        priority = data['priority'].lower()
        status = data.get('status', 'pending').lower()
        audio_note = data.get('audio_note')  # Base64 encoded audio file
        attachments = data.get('attachments', [])  # List of file attachments

        # Validate priority
        valid_priorities = ['low', 'medium', 'high', 'urgent']
        if priority not in valid_priorities:
            return jsonify({'success': False, 'message': 'Invalid priority value'}), 400

        # Validate status
        valid_statuses = ['pending', 'in_progress', 'completed', 'snoozed']
        if status not in valid_statuses:
            return jsonify({'success': False, 'message': 'Invalid status value'}), 400

        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()

        # Insert task
        cursor.execute("""
            INSERT INTO tasks (
                task_id, title, description, assigned_by, assigned_to, 
                deadline, priority, status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            task_id, title, description, assigned_by, assigned_to,
            deadline, priority, status
        ))

        # Insert audio note if provided
        if audio_note:
            audio_id = str(uuid.uuid4())
            cursor.execute("""
                INSERT INTO task_audio_notes (
                    audio_id, task_id, audio_data
                )
                VALUES (%s, %s, %s)
            """, (audio_id, task_id, audio_note))

        # Insert attachments if any
        if attachments:
            attachment_values = []
            for attachment in attachments:
                attachment_id = str(uuid.uuid4())
                attachment_values.append((
                    attachment_id,
                    task_id,
                    attachment['file_name'],
                    attachment['file_type'],
                    attachment['file_size'],
                    attachment['file_data']  # Base64 encoded file data
                ))

            cursor.executemany("""
                INSERT INTO task_attachments (
                    attachment_id, task_id, file_name, file_type, 
                    file_size, file_data
                )
                VALUES (%s, %s, %s, %s, %s, %s)
            """, attachment_values)

        conn.commit()
        logger.info(f"Task created successfully: {task_id}")
        return jsonify({
            'success': True,
            'message': 'Task created successfully',
            'task_id': task_id
        }), 201
    except Exception as e:
        logger.error(f"Error creating task: {str(e)}")
        return jsonify({'success': False, 'message': f"Error creating task: {str(e)}"}), 500
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
            return jsonify({'error': 'Missing username or role parameters'}), 400

        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)

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
                t.created_at,
                t.updated_at,
                GROUP_CONCAT(DISTINCT 
                    CASE 
                        WHEN a.attachment_id IS NOT NULL 
                        THEN JSON_OBJECT('attachment_id', a.attachment_id, 'file_name', a.file_name)
                        ELSE NULL 
                    END
                ) as attachments,
                MAX(CASE WHEN an.audio_id IS NOT NULL THEN 1 ELSE 0 END) as has_audio
            FROM tasks t
            LEFT JOIN task_attachments a ON t.task_id = a.task_id
            LEFT JOIN task_audio_notes an ON t.task_id = an.task_id
        """

        # Add role-based filtering
        if role.lower() in ['admin', 'super admin']:
            query = f"{base_query} GROUP BY t.task_id, t.title, t.description, t.deadline, t.priority, t.status, t.assigned_by, t.assigned_to, t.created_at, t.updated_at ORDER BY t.deadline ASC"
            logger.debug("Executing admin query")
            cursor.execute(query)
        else:
            query = f"{base_query} WHERE t.assigned_to = %s OR t.assigned_by = %s GROUP BY t.task_id, t.title, t.description, t.deadline, t.priority, t.status, t.assigned_by, t.assigned_to, t.created_at, t.updated_at ORDER BY t.deadline ASC"
            logger.debug(f"Executing user query for {username}")
            cursor.execute(query, (username, username))

        tasks = cursor.fetchall()
        logger.debug(f"Found {len(tasks)} tasks")

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
                'attachments': []
            }

            # Parse attachments if present
            if task['attachments']:
                try:
                    # Split the concatenated JSON strings and parse each one
                    attachment_strings = task['attachments'].split(',')
                    formatted_task['attachments'] = [
                        eval(attachment_str) for attachment_str in attachment_strings
                        if attachment_str != 'NULL' and attachment_str.strip()
                    ]
                except Exception as e:
                    logger.error(f"Error parsing attachments for task {task['task_id']}: {str(e)}")
                    formatted_task['attachments'] = []

            formatted_tasks.append(formatted_task)

        return jsonify({
            'success': True,
            'tasks': formatted_tasks
        }), 200

    except mysql.connector.Error as err:
        logger.error(f"Database error in get_tasks: {err}")
        return jsonify({
            'success': False,
            'error': 'Database error',
            'message': str(err)
        }), 500
    except Exception as e:
        logger.error(f"Unexpected error in get_tasks: {e}")
        return jsonify({
            'success': False,
            'error': 'Server error',
            'message': str(e)
        }), 500
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

# ---------------- MAIN ----------------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True) 