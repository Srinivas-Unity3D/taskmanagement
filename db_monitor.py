#!/usr/bin/env python3
import mysql.connector
import time
import logging
import os
from dotenv import load_dotenv
import subprocess
import sys

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('db_monitor.log'),
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

def check_mysql_service():
    """Check if MySQL service is running"""
    try:
        result = subprocess.run(['systemctl', 'is-active', 'mysql'], 
                              capture_output=True, text=True)
        return result.stdout.strip() == 'active'
    except Exception as e:
        logger.error(f"Error checking MySQL service: {str(e)}")
        return False

def restart_mysql_service():
    """Restart MySQL service"""
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
            
    except mysql.connector.Error as e:
        logger.error(f"Database connection error: {str(e)}")
        return False
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()

def check_database_tables():
    """Check if all required tables exist"""
    required_tables = [
        'users', 'tasks', 'task_audio_notes', 
        'task_notifications', 'task_attachments', 'task_alarms'
    ]
    
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        
        # Get existing tables
        cursor.execute("SHOW TABLES")
        existing_tables = [table[0] for table in cursor.fetchall()]
        
        # Check for missing tables
        missing_tables = [table for table in required_tables if table not in existing_tables]
        
        if missing_tables:
            logger.error(f"Missing tables: {', '.join(missing_tables)}")
            return False
        else:
            logger.info("All required tables exist")
            return True
            
    except mysql.connector.Error as e:
        logger.error(f"Error checking database tables: {str(e)}")
        return False
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()

def restart_application():
    """Restart the application service"""
    try:
        subprocess.run(['systemctl', 'restart', 'task_management'], check=True)
        logger.info("Application service restarted successfully")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to restart application service: {str(e)}")
        return False

def main():
    """Main monitoring loop"""
    while True:
        try:
            # Check MySQL service
            if not check_mysql_service():
                logger.warning("MySQL service is not running, attempting to restart...")
                if restart_mysql_service():
                    time.sleep(10)  # Wait for MySQL to fully start
                else:
                    logger.error("Failed to restart MySQL service")
                    continue
            
            # Check database connection
            if not check_database_connection():
                logger.error("Database connection failed")
                continue
            
            # Check database tables
            if not check_database_tables():
                logger.error("Database tables check failed")
                continue
            
            logger.info("All database checks passed")
            
        except Exception as e:
            logger.error(f"Unexpected error in monitoring loop: {str(e)}")
        
        # Wait before next check
        time.sleep(300)  # Check every 5 minutes

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Monitoring stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        sys.exit(1) 