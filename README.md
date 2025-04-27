# Task Management Backend

## Setup Instructions

1. Clone the repository
2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Firebase Setup:
   - Go to Firebase Console
   - Create a new project or select existing project
   - Go to Project Settings > Service Accounts
   - Generate new private key
   - Save the JSON file as `firebase-credentials.json` in the project root
   - DO NOT commit this file to version control

4. Start the server:
```bash
python app.py
```

## Environment Variables
Create a `.env` file with the following variables:
```
DB_HOST=your_db_host
DB_USER=your_db_user
DB_PASSWORD=your_db_password
DB_NAME=your_db_name
SECRET_KEY=your_secret_key
```

## Security Note
- Never commit `firebase-credentials.json` or any other credential files to version control
- Keep your `.env` file secure and never commit it
- Use environment variables for production deployment