#!/usr/bin/env python3
import mysql.connector
import time
import logging
import os
from dotenv import load_dotenv
import subprocess
import sys
from datetime import datetime
from mysql.connector import Error

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    filename='/var/log/task_management_dev/db_monitor.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Add file handler for errors specifically
error_handler = logging.FileHandler('/var/log/task_management_dev/db_errors.log')
error_handler.setLevel(logging.ERROR)
error_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(error_handler)

# Database configuration
db_config = {
    'host': os.getenv('DB_HOST', '134.209.149.12'),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', '123'),
    'database': os.getenv('DB_NAME', 'task_db'),
    'raise_on_warnings': True,
    'autocommit': False,
    'pool_size': 5,
    'connect_timeout': 10,
    'get_warnings': True
}

def get_connection():
    """Get database connection with retry logic"""
    max_retries = 3
    retry_delay = 5  # seconds
    
    for attempt in range(max_retries):
        try:
            conn = mysql.connector.connect(**db_config)
            logger.info("Database connection established successfully")
            return conn
        except Error as e:
            logger.error(f"Database connection attempt {attempt + 1} failed: {str(e)}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                raise
                
def backup_table(cursor, table_name):
    """Create a backup of a table before any schema changes"""
    try:
        # Skip if this is already a backup table
        if '_backup_' in table_name:
            logger.info(f"Skipping backup of backup table: {table_name}")
            return

        timestamp = datetime.now().strftime('%Y%m%d_%H%M')
        backup_table = f"{table_name}_backup_{timestamp}"
        
        # Ensure backup table name isn't too long (MySQL has 64 char limit)
        if len(backup_table) > 64:
            # Truncate the original table name if needed
            max_table_length = 64 - len('_backup_') - len(timestamp)
            table_name = table_name[:max_table_length]
            backup_table = f"{table_name}_backup_{timestamp}"
        
        logger.info(f"Creating backup of table {table_name} as {backup_table}")
        
        # Start transaction
        cursor.execute("START TRANSACTION")
        
        # Create backup table with same structure
        cursor.execute(f"CREATE TABLE IF NOT EXISTS {backup_table} LIKE {table_name}")
        
        # Copy data
        cursor.execute(f"INSERT INTO {backup_table} SELECT * FROM {table_name}")
        
        # Commit transaction
        cursor.execute("COMMIT")
        logger.info(f"Successfully backed up table {table_name}")
        
        # Keep only last 3 backups for each table
        cursor.execute(f"SHOW TABLES LIKE '{table_name}_backup_%'")
        backups = cursor.fetchall()
        if len(backups) > 3:
            backups.sort()
            for old_backup in backups[:-3]:
                cursor.execute(f"DROP TABLE IF EXISTS {old_backup[0]}")
                logger.info(f"Removed old backup table {old_backup[0]}")
                
    except Error as e:
        cursor.execute("ROLLBACK")
        logger.error(f"Error backing up table {table_name}: {str(e)}")
        raise

def check_table_integrity(cursor, table_name):
    """Check table integrity and repair if needed"""
    try:
        logger.info(f"Checking integrity of table {table_name}")
        cursor.execute(f"CHECK TABLE {table_name} EXTENDED")
        result = cursor.fetchone()
        
        if result and result[3] != "OK":
            logger.warning(f"Table {table_name} may have issues: {result[3]}")
            cursor.execute(f"REPAIR TABLE {table_name}")
            logger.info(f"Attempted repair of table {table_name}")
            
    except Error as e:
        logger.error(f"Error checking table integrity for {table_name}: {str(e)}")

def safe_table_operation(conn, cursor, operation):
    """Execute table operations within a transaction"""
    try:
        cursor.execute("SET FOREIGN_KEY_CHECKS=1")
        cursor.execute("START TRANSACTION")
        operation()
        cursor.execute("COMMIT")
        logger.info("Table operation completed successfully")
    except Exception as e:
        cursor.execute("ROLLBACK")
        logger.error(f"Table operation failed, rolling back: {str(e)}")
        raise e
    finally:
        cursor.execute("SET FOREIGN_KEY_CHECKS=1")

def check_mysql_service():
    """Check if MySQL service is running"""
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return result is not None
    except Error as e:
        logger.error(f"MySQL service check failed: {str(e)}")
        return False

def restart_mysql_service():
    """Attempt to restart MySQL service"""
    try:
        subprocess.run(['systemctl', 'restart', 'mysql'], check=True)
        logger.info("MySQL service restarted successfully")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to restart MySQL service: {str(e)}")
        return False

def check_database_connection():
    """Check database connection and basic functionality"""
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        
        # Test basic query
        cursor.execute("SELECT 1")
        result = cursor.fetchone()
        
        if result and result[0] == 1:
            logger.info("Database connection test successful")
            return True
        else:
            logger.error("Database connection test failed")
            return False
            
    except Error as e:
        logger.error(f"Database connection error: {str(e)}")
        return False
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()

def check_database_tables():
    """Check if all required tables exist and have data"""
    required_tables = [
        'users', 'tasks', 'task_audio_notes', 
        'task_notifications', 'task_attachments', 'task_alarms'
    ]
    
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        
        # Get existing tables (excluding backup tables)
        cursor.execute("SHOW TABLES")
        all_tables = [table[0] for table in cursor.fetchall()]
        existing_tables = [table for table in all_tables if '_backup_' not in table]
        
        # Check for missing tables
        missing_tables = [table for table in required_tables if table not in existing_tables]
        
        if missing_tables:
            logger.error(f"Missing tables: {', '.join(missing_tables)}")
            return False
            
        # Check if tables have data
        for table in existing_tables:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = cursor.fetchone()[0]
            if count == 0:
                logger.warning(f"Table {table} is empty")
            else:
                logger.info(f"Table {table} has {count} records")
        
        logger.info("All required tables exist")
        return True
            
    except Error as e:
        logger.error(f"Error checking database tables: {str(e)}")
        return False
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()

def backup_all_tables():
    """Create backups of all tables"""
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        
        # Get all tables (excluding backup tables)
        cursor.execute("SHOW TABLES")
        tables = [table[0] for table in cursor.fetchall() if '_backup_' not in table[0]]
        
        # Backup each table
        for table in tables:
            backup_table(cursor, table)
            
        logger.info("Successfully backed up all tables")
        
    except Error as e:
        logger.error(f"Error during backup: {str(e)}")
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()

def main():
    """Main monitoring loop"""
    last_backup_time = None
    backup_interval = 24 * 60 * 60  # 24 hours in seconds
    
    while True:
        conn = None
        cursor = None
        try:
            # Get connection
            conn = get_connection()
            cursor = conn.cursor()
            
            # Check if it's time for backup
            current_time = time.time()
            if last_backup_time is None or (current_time - last_backup_time) >= backup_interval:
                logger.info("Starting scheduled backup...")
                
                # Get all tables
                cursor.execute("SHOW TABLES")
                tables = [table[0] for table in cursor.fetchall()]
                
                # First check integrity of all tables
                for table in tables:
                    check_table_integrity(cursor, table)
                
                # Then backup all tables
                for table in tables:
                    backup_table(cursor, table)
                    
                last_backup_time = current_time
                logger.info("Scheduled backup completed successfully")
            
            # Check database tables
            if not check_database_tables():
                logger.error("Database tables check failed")
                continue
            
            logger.info("All database checks passed")
            
        except Exception as e:
            logger.error(f"Unexpected error in monitoring loop: {str(e)}")
            if isinstance(e, mysql.connector.Error):
                logger.error(f"MySQL Error Code: {e.errno}")
                logger.error(f"MySQL Error Message: {e.msg}")
        finally:
            try:
                if cursor:
                    cursor.close()
                if conn:
                    conn.close()
            except Exception as e:
                logger.error(f"Error closing database connections: {str(e)}")
        
        # Wait before next check
        time.sleep(300)  # Check every 5 minutes

if __name__ == "__main__":
    try:
        logger.info("Database monitor service starting...")
        main()
    except KeyboardInterrupt:
        logger.info("Database monitor service stopping...")
    except Exception as e:
        logger.critical(f"Fatal error in database monitor: {str(e)}")
        sys.exit(1) 