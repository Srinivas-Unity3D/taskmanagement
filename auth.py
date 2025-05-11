from flask import request, jsonify
from flask_jwt_extended import (
    JWTManager, create_access_token, create_refresh_token,
    jwt_required, get_jwt_identity, decode_token
)
from functools import wraps
import os
import logging
from datetime import datetime, timedelta

# Configure logging
logger = logging.getLogger(__name__)

# JWT Manager instance to be initialized with Flask app
jwt = JWTManager()

def jwt_token_required(fn):
    """
    Custom decorator to validate JWT token with error handling
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header:
            return jsonify({'message': 'Authorization header is missing'}), 401
        
        try:
            # Extract token from header
            if ' ' not in auth_header:
                return jsonify({'message': 'Invalid token format'}), 401
            
            token_type, token = auth_header.split(' ', 1)
            if token_type.lower() != 'bearer':
                return jsonify({'message': 'Invalid token type, Bearer expected'}), 401
            
            # Use flask_jwt_extended to verify token
            user_id = get_jwt_identity()
            if not user_id:
                return jsonify({'message': 'Invalid or expired token'}), 401
                
            # All good, proceed with the function
            return fn(*args, **kwargs)
        except Exception as e:
            logger.error(f"JWT verification error: {str(e)}")
            return jsonify({'message': f'Token verification failed: {str(e)}'}), 401
    
    return wrapper

def generate_tokens(user_data):
    """
    Generate access and refresh tokens for a user
    
    Parameters:
    user_data (dict): User information to include in token claims
    
    Returns:
    dict: Contains access_token and refresh_token
    """
    try:
        # Create access token - shorter lived
        expires = timedelta(hours=24)  # Adjust as needed
        access_token = create_access_token(
            identity=user_data.get('user_id'),
            additional_claims={
                'username': user_data.get('username'),
                'role': user_data.get('role'),
                'token_type': 'access'
            },
            expires_delta=expires
        )
        
        # Create refresh token - longer lived
        refresh_expires = timedelta(days=30)  # Adjust as needed
        refresh_token = create_refresh_token(
            identity=user_data.get('user_id'),
            additional_claims={
                'username': user_data.get('username'),
                'token_type': 'refresh'
            },
            expires_delta=refresh_expires
        )
        
        return {
            'access_token': access_token,
            'refresh_token': refresh_token,
            'expires_in': int(expires.total_seconds())
        }
    except Exception as e:
        logger.error(f"Token generation error: {str(e)}")
        raise
        
def role_required(required_roles):
    """
    Decorator to restrict endpoint access based on user role
    
    Parameters:
    required_roles (list or str): Role(s) allowed to access the endpoint
    """
    def decorator(fn):
        @wraps(fn)
        @jwt_required()
        def wrapper(*args, **kwargs):
            # Get current user's role from token
            claims = get_jwt_identity()
            user_role = claims.get('role', 'user')
            
            # Convert to list if string provided
            if isinstance(required_roles, str):
                roles = [required_roles]
            else:
                roles = required_roles
                
            # Check if user role is allowed
            if user_role not in roles:
                return jsonify({
                    'message': 'Access denied: Insufficient permissions'
                }), 403
                
            # Role is valid, proceed with function
            return fn(*args, **kwargs)
        return wrapper
    return decorator 