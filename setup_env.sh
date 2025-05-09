#!/bin/bash

# Create production environment file
cat > .env << EOL
FLASK_ENV=production
FLASK_DEBUG=0
# Add other production environment variables below
EOL

# Create development environment file
cat > .env.dev << EOL
FLASK_ENV=development
FLASK_DEBUG=1
# Add other development environment variables below
EOL

echo "Environment files created successfully!"
echo "Please add your other environment variables to both .env and .env.dev files" 