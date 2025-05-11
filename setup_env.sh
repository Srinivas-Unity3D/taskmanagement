#!/bin/bash

# Create production environment file
cat > .env << EOL
FLASK_ENV=production
FLASK_DEBUG=0

# Database Configuration
DB_HOST=134.209.149.12
DB_USER=root
DB_PASSWORD=123
DB_NAME=task_db

# Firebase Configuration
FIREBASE_PROJECT_ID=TaskManagement
FCM_SERVER_KEY=REPLACE_THIS_WITH_YOUR_ACTUAL_SERVER_KEY

# Server Settings
PORT=5001
ENABLE_HTTPS=true
SSL_CERT_PATH=/etc/nginx/ssl/nginx.crt
SSL_KEY_PATH=/etc/nginx/ssl/nginx.key

# Security
JWT_SECRET_KEY=your_super_secret_jwt_key_for_authentication
JWT_ACCESS_TOKEN_EXPIRES=7d
EOL

# Create development environment file
cat > .env.dev << EOL
FLASK_ENV=development
FLASK_DEBUG=1

# Database Configuration
DB_HOST=134.209.149.12
DB_USER=root
DB_PASSWORD=123
DB_NAME=task_db

# Firebase Configuration
FIREBASE_PROJECT_ID=TaskManagement
FCM_SERVER_KEY=REPLACE_THIS_WITH_YOUR_ACTUAL_SERVER_KEY

# Server Settings
PORT=5001
ENABLE_HTTPS=false

# Security
JWT_SECRET_KEY=dev_jwt_secret_key
JWT_ACCESS_TOKEN_EXPIRES=30d
EOL

echo "Environment files created successfully!"
echo "IMPORTANT: Replace the FCM_SERVER_KEY with your actual Firebase Server Key"
echo "Get your server key from Firebase Console > Project Settings > Cloud Messaging > Server key" 