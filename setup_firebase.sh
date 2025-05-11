#!/bin/bash

# Exit on error
set -e

echo "===== Firebase Integration Setup ====="
echo "This script will set up Firebase integration for your backend server"

# Check for .env file
if [ ! -f .env ]; then
    echo "Creating .env file..."
    bash setup_env.sh
else
    echo ".env file already exists"
fi

# Request Firebase server key
echo
echo "Please enter your Firebase Server Key (found in Firebase Console > Project Settings > Cloud Messaging > Server key):"
read -r FCM_SERVER_KEY

# Update .env file with the Firebase server key
if grep -q "FCM_SERVER_KEY=" .env; then
    # Replace existing FCM_SERVER_KEY
    sed -i "s|FCM_SERVER_KEY=.*|FCM_SERVER_KEY=$FCM_SERVER_KEY|g" .env
else
    # Add FCM_SERVER_KEY if it doesn't exist
    echo "FCM_SERVER_KEY=$FCM_SERVER_KEY" >> .env
fi

echo "Firebase Server Key has been added to .env file"

# Check if requirements are installed
echo "Installing required packages..."
pip install -r requirements.txt

# Restart the services
echo "Restarting services..."
sudo systemctl restart task_management.service 
sudo systemctl restart task_alarm_service.service

# Verify services are running
echo "Verifying services..."
sudo systemctl status task_management.service --no-pager | grep Active
sudo systemctl status task_alarm_service.service --no-pager | grep Active

echo
echo "===== Firebase Integration Completed ====="
echo "Your server is now configured to use Firebase Cloud Messaging"
echo "Next steps:"
echo "1. Test Firebase integration with your app"
echo "2. Check server logs for any Firebase-related errors"
echo "3. Update your Flutter app to use the secure HTTPS connection"
echo 