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

# Create directory for Firebase credentials
mkdir -p credentials

echo
echo "Please place your Firebase service account JSON file in the 'credentials' directory"
echo "File should be named 'firebase-credentials.json'"
echo

# Wait for user confirmation
read -p "Press Enter after you've placed the file... " -r

# Check if file exists
if [ ! -f "credentials/firebase-credentials.json" ]; then
    echo "Error: credentials/firebase-credentials.json not found!"
    exit 1
fi

# Update .env file with the Firebase credentials path
if grep -q "FIREBASE_CREDENTIALS_FILE=" .env; then
    # Replace existing path
    sed -i "s|FIREBASE_CREDENTIALS_FILE=.*|FIREBASE_CREDENTIALS_FILE=$(pwd)/credentials/firebase-credentials.json|g" .env
else
    # Add path if it doesn't exist
    echo "FIREBASE_CREDENTIALS_FILE=$(pwd)/credentials/firebase-credentials.json" >> .env
fi

echo "Firebase credentials path has been added to .env file"

# Encode the JSON file as a string to add to environment
FIREBASE_CREDENTIALS=$(cat credentials/firebase-credentials.json | tr -d '\n' | sed 's/"/\\"/g')
if grep -q "FIREBASE_CREDENTIALS=" .env; then
    # Replace existing credentials
    sed -i "s|FIREBASE_CREDENTIALS=.*|FIREBASE_CREDENTIALS=\"$FIREBASE_CREDENTIALS\"|g" .env
else
    # Add credentials if they don't exist
    echo "FIREBASE_CREDENTIALS=\"$FIREBASE_CREDENTIALS\"" >> .env
fi

echo "Firebase credentials have been added to .env file"

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
echo "Your server is now configured to use Firebase Cloud Messaging with Admin SDK"
echo "Next steps:"
echo "1. Test Firebase integration with your app"
echo "2. Check server logs for any Firebase-related errors"
echo "3. Update your Flutter app to use the secure HTTPS connection"
echo 