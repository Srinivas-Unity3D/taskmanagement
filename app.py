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
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'task_db'
}

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
        priority = data['priority']
        status = data.get('status', 'Pending')

        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO tasks (task_id, title, description, assigned_by, assigned_to, deadline, priority, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (task_id, title, description, assigned_by, assigned_to, deadline, priority, status))

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

# ---------------- GET TASKS ----------------
# @app.route('/tasks', methods=['GET'])
# def get_tasks():
#     conn = None
#     cursor = None
#     try:
#         username = request.args.get('username')
#         role = request.args.get('role')

#         if not username or not role:
#             return jsonify({'error': 'Missing username or role parameters'}), 400

#         conn = mysql.connector.connect(**db_config)
#         cursor = conn.cursor(dictionary=True)

#         base_query = """
#             SELECT 
#                 t.task_id,
#                 t.title,
#                 t.description,
#                 t.deadline,
#                 t.priority,
#                 t.status,
#                 t.created_at,
#                 assigner.username AS assigned_by,
#                 assignee.username AS assigned_to
#             FROM tasks t
#             JOIN users assigner ON t.assigned_by = assigner.user_id
#             JOIN users assignee ON t.assigned_to = assignee.user_id
#         """

#         if role in ['Admin', 'Super Admin']:
#             query = f"{base_query} ORDER BY t.deadline ASC"
#             cursor.execute(query)
#         else:
#             query = f"{base_query} WHERE assignee.username = %s ORDER BY t.deadline ASC"
#             cursor.execute(query, (username,))

#         tasks = cursor.fetchall()
#         formatted_tasks = []
#         for task in tasks:
#             formatted_tasks.append({
#                 'task_id': task['task_id'],
#                 'title': task['title'],
#                 'description': task['description'],
#                 'deadline': task['deadline'].strftime('%Y-%m-%d') if task['deadline'] else None,
#                 'priority': task['priority'],
#                 'status': task['status'],
#                 'assigned_by': task['assigned_by'],
#                 'assigned_to': task['assigned_to'],
#                 'created_at': task['created_at'].strftime('%Y-%m-%d %H:%M:%S') if task['created_at'] else None
#             })

#         return jsonify(formatted_tasks), 200

#     except mysql.connector.Error as err:
#         logger.error(f"Database error: {err}")
#         return jsonify({'error': 'Database error'}), 500
#     except Exception as e:
#         logger.error(f"Unexpected error: {e}")
#         return jsonify({'error': 'Server error'}), 500
#     finally:
#         if cursor:
#             cursor.close()
#         if conn:
#             conn.close()

@app.route('/tasks', methods=['GET'])
def get_tasks():
    try:
        # Get query parameters
        username = request.args.get('username')
        role = request.args.get('role')

        if not username or not role:
            return jsonify({'error': 'Missing username or role parameters'}), 400

        # Create new database connection
        conn = mysql.connector.connect(
            host="localhost",
            user="root",
            password="",
            database="task_db"
        )
        cursor = conn.cursor(dictionary=True)

        # Base query
        base_query = """
            SELECT 
                task_id,
                title,
                description,
                deadline,
                priority,
                status,
                assigned_by,
                assigned_to
            FROM tasks
        """

        # Add role-based filtering
        if role in ['Admin', 'Super Admin']:
            query = f"{base_query} ORDER BY deadline ASC"
            cursor.execute(query)
        else:
            query = f"{base_query} WHERE assigned_to = %s OR assigned_by = %s ORDER BY deadline ASC"
            cursor.execute(query, (username, username))

        tasks = cursor.fetchall()

        # Format response
        formatted_tasks = []
        for task in tasks:
            formatted_tasks.append({
                'task_id': task['task_id'],
                'title': task['title'],
                'description': task['description'],
                'deadline': task['deadline'].strftime('%Y-%m-%d') if task['deadline'] else None,
                'priority': task['priority'],
                'status': task['status'],
                'assigned_by': task['assigned_by'],
                'assigned_to': task['assigned_to']
            })

        return jsonify(formatted_tasks), 200

    except mysql.connector.Error as err:
        print(f"Database error: {err}")
        return jsonify({'error': 'Database error', 'details': str(err)}), 500

    except Exception as e:
        print(f"Unexpected error: {e}")
        return jsonify({'error': 'Server error', 'details': str(e)}), 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# ---------------- MAIN ----------------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
