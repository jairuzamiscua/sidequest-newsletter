from __future__ import annotations

# =============================
# SideQuest Newsletter Backend
# With PostgreSQL Database Support
# =============================

import os
import re
import html
import json
import traceback
import psycopg2
import base64
import qrcode
import io
import base64
from itsdangerous import URLSafeTimedSerializer
from functools import wraps
import time
import secrets
from urllib.parse import quote
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
from collections import defaultdict
from flask import Flask, request, jsonify, send_from_directory, session, redirect, render_template_string, make_response
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect, validate_csrf, generate_csrf, CSRFError
from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, IntegerField, DecimalField, SelectField, BooleanField, EmailField
from wtforms.validators import DataRequired, Email, Length, Optional, NumberRange

# SINGLE APP CREATION - FIXED!
app = Flask(__name__, static_folder="static")
app.secret_key = os.environ.get('FLASK_SECRET_KEY')
if not app.secret_key:
    raise ValueError("FLASK_SECRET_KEY environment variable must be set!")
CORS(app)

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD') # Change this!
if not ADMIN_PASSWORD:
    raise ValueError("ADMIN_PASSWORD environment variable is not set!")

# Add the limiter RIGHT HERE, after app is created
limiter = Limiter(
    key_func=get_remote_address,  # All named arguments
    app=app,
    default_limits=["1000 per day"]
)

login_attempts = defaultdict(list)

csrf = CSRFProtect(app)
app.config.update(
    WTF_CSRF_TIME_LIMIT=3600,  # 1 hour token expiry
    WTF_CSRF_SSL_STRICT=False,  # Set to True in production with HTTPS only
    WTF_CSRF_CHECK_DEFAULT=False,  # Manual validation for API endpoints
    WTF_CSRF_SECRET_KEY=app.secret_key  # Use same key as Flask session
)

# =============================
# --- CONFIG & GLOBALS FIRST ---
# =============================

try:
    import qrcode
    QR_CODE_AVAILABLE = True
    print("✅ QR code library available")
except ImportError:
    QR_CODE_AVAILABLE = False
    print("⚠️ QR code library not available - using external service fallback")

# ---- Brevo (Sendinblue) SDK ----
try:
    import sib_api_v3_sdk
    from sib_api_v3_sdk.rest import ApiException  # type: ignore
except Exception:  # pragma: no cover
    sib_api_v3_sdk = None  # type: ignore
    ApiException = Exception  # type: ignore

# ---- Brevo settings ----
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
BREVO_LIST_ID = int(os.environ.get("BREVO_LIST_ID", 2))
AUTO_SYNC_TO_BREVO = os.environ.get("AUTO_SYNC_TO_BREVO", "true").lower() in {"1", "true", "yes", "y"}
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "marketing@sidequestcanterbury.com")
SENDER_NAME = os.environ.get("SENDER_NAME", "SideQuest")

# ---- Database configuration ----
DATABASE_URL = os.environ.get("DATABASE_URL")

# =============================
# Database Connection & Setup
# =============================

def get_db_connection():
    """Get database connection"""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print(f"Database connection error: {e}")
        return None

# Replace your init_database() function with this updated version:

# ============================
# INPUT SANITIZATION FUNCTIONS
# ============================

def sanitize_text_input(text, max_length=1000):
    """Sanitize text input to prevent XSS and other attacks"""
    if not text:
        return ""
    
    # Remove null bytes and control characters
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', str(text))
    
    # Limit length
    text = text[:max_length]
    
    # HTML escape
    text = html.escape(text.strip())
    
    return text

def sanitize_email(email):
    """Enhanced email validation and sanitization"""
    if not email:
        return None
        
    email = str(email).strip().lower()
    
    # Remove dangerous characters
    email = re.sub(r'[^\w\.\-@+]', '', email)
    
    # Basic email pattern validation
    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        return None
        
    # Length check
    if len(email) > 254:  # RFC standard
        return None
        
    return email

def sanitize_numeric_input(value, min_val=None, max_val=None):
    """Sanitize numeric input"""
    try:
        num = float(value) if value else 0
        if min_val is not None and num < min_val:
            num = min_val
        if max_val is not None and num > max_val:
            num = max_val
        return num
    except (ValueError, TypeError):
        return 0


# =============================
# LONG-TERM DATABASE SOLUTION
# =============================

def get_current_schema_version():
    """Get the current database schema version"""
    try:
        conn = get_db_connection()
        if not conn:
            return 0
            
        cursor = conn.cursor()
        
        # Check if schema_version table exists
        cursor.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'schema_version'
            );
        """)
        
        table_exists = cursor.fetchone()[0]
        
        if not table_exists:
            # Create schema_version table
            cursor.execute('''
                CREATE TABLE schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    description TEXT
                )
            ''')
            cursor.execute("INSERT INTO schema_version (version, description) VALUES (0, 'Initial schema')")
            conn.commit()
            cursor.close()
            conn.close()
            return 0
        
        # Get current version
        cursor.execute("SELECT MAX(version) FROM schema_version")
        version = cursor.fetchone()[0] or 0
        
        cursor.close()
        conn.close()
        return version
        
    except Exception as e:
        print(f"Error getting schema version: {e}")
        return 0

def apply_migration(version, description, sql_commands):
    """Apply a database migration"""
    try:
        conn = get_db_connection()
        if not conn:
            return False
            
        cursor = conn.cursor()
        
        print(f"🔄 Applying migration {version}: {description}")
        
        # Execute all SQL commands
        for sql in sql_commands:
            print(f"   Executing: {sql[:100]}...")
            cursor.execute(sql)
        
        # Record the migration
        cursor.execute(
            "INSERT INTO schema_version (version, description) VALUES (%s, %s)",
            (version, description)
        )
        
        conn.commit()
        cursor.close()
        conn.close()
        
        print(f"✅ Migration {version} applied successfully")
        return True
        
    except Exception as e:
        print(f"❌ Migration {version} failed: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return False

def run_database_migrations():
    """Run all pending database migrations"""
    current_version = get_current_schema_version()
    print(f"📊 Current database schema version: {current_version}")
    
    # Define all migrations
    migrations = [
        {
            'version': 1,
            'description': 'Add check_in_time column to event_registrations',
            'sql': [
                'ALTER TABLE event_registrations ADD COLUMN IF NOT EXISTS check_in_time TIMESTAMP;'
            ]
        },
        {
            'version': 2, 
            'description': 'Add indexes for better performance',
            'sql': [
                'CREATE INDEX IF NOT EXISTS idx_event_registrations_attended ON event_registrations(attended);',
                'CREATE INDEX IF NOT EXISTS idx_events_event_type ON events(event_type);',
                'CREATE INDEX IF NOT EXISTS idx_subscribers_source ON subscribers(source);'
            ]
        },
        {
            'version': 3,
            'description': 'Add updated_at triggers for events table',
            'sql': [
                '''CREATE OR REPLACE FUNCTION update_updated_at_column()
                   RETURNS TRIGGER AS $$
                   BEGIN
                       NEW.updated_at = CURRENT_TIMESTAMP;
                       RETURN NEW;
                   END;
                   $$ language 'plpgsql';''',
                '''DROP TRIGGER IF EXISTS update_events_updated_at ON events;''',
                '''CREATE TRIGGER update_events_updated_at 
                   BEFORE UPDATE ON events 
                   FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();'''
            ]
        },
        {
            'version': 4,
            'description': 'Add name fields to subscribers table',
            'sql': [
                'ALTER TABLE subscribers ADD COLUMN IF NOT EXISTS first_name VARCHAR(100);',
                'ALTER TABLE subscribers ADD COLUMN IF NOT EXISTS last_name VARCHAR(100);',
                'ALTER TABLE subscribers ADD COLUMN IF NOT EXISTS gaming_handle VARCHAR(50);',
                '''ALTER TABLE subscribers ADD COLUMN IF NOT EXISTS full_name VARCHAR(200) 
                   GENERATED ALWAYS AS (
                       CASE 
                           WHEN first_name IS NOT NULL AND last_name IS NOT NULL 
                           THEN CONCAT(first_name, ' ', last_name)
                           ELSE COALESCE(first_name, email)
                       END
                   ) STORED;''',
                'CREATE INDEX IF NOT EXISTS idx_subscribers_first_name ON subscribers(first_name);',
                'CREATE INDEX IF NOT EXISTS idx_subscribers_last_name ON subscribers(last_name);',
                'CREATE INDEX IF NOT EXISTS idx_subscribers_full_name ON subscribers(full_name);'
            ]
        }
        # Add more migrations here as needed
    ]
    
    # Apply pending migrations
    for migration in migrations:
        if migration['version'] > current_version:
            success = apply_migration(
                migration['version'],
                migration['description'], 
                migration['sql']
            )
            if not success:
                print(f"❌ Failed to apply migration {migration['version']}")
                return False
    
    print("✅ All database migrations completed successfully")
    return True

#==============================
# CSRF Protection and Forms 
#==============================
class SubscriberForm(FlaskForm):
    """Form for adding subscribers with CSRF protection"""
    email = EmailField('Email', validators=[DataRequired(), Email()])
    firstName = StringField('First Name', validators=[DataRequired(), Length(min=2, max=100)])
    lastName = StringField('Last Name', validators=[DataRequired(), Length(min=2, max=100)])
    gamingHandle = StringField('Gaming Handle', validators=[Optional(), Length(min=3, max=50)])
    gdprConsent = BooleanField('GDPR Consent', validators=[DataRequired()])
    source = StringField('Source', default='manual')

class EventForm(FlaskForm):
    """Form for creating/updating events"""
    title = StringField('Title', validators=[DataRequired(), Length(min=3, max=255)])
    event_type = SelectField('Event Type', 
                            choices=[('tournament', 'Tournament'), 
                                   ('game_night', 'Game Night'),
                                   ('special', 'Special Event'),
                                   ('birthday', 'Birthday Party')],
                            validators=[DataRequired()])
    game_title = StringField('Game Title', validators=[Optional(), Length(max=255)])
    date_time = StringField('Date Time', validators=[DataRequired()])  # Will be parsed as datetime
    end_time = StringField('End Time', validators=[Optional()])
    capacity = IntegerField('Capacity', validators=[Optional(), NumberRange(min=0, max=500)])
    description = TextAreaField('Description', validators=[Optional(), Length(max=2000)])
    entry_fee = DecimalField('Entry Fee', validators=[Optional(), NumberRange(min=0, max=1000)], places=2)
    prize_pool = TextAreaField('Prize Pool', validators=[Optional(), Length(max=500)])
    status = SelectField('Status', 
                        choices=[('draft', 'Draft'), ('published', 'Published'), 
                               ('cancelled', 'Cancelled'), ('completed', 'Completed')],
                        default='draft')
    image_url = StringField('Image URL', validators=[Optional(), Length(max=500)])
    requirements = TextAreaField('Requirements', validators=[Optional(), Length(max=1000)])


class EventRegistrationForm(FlaskForm):
    """Form for event registration"""
    email = EmailField('Email', validators=[DataRequired(), Email()])
    player_name = StringField('Player Name', validators=[DataRequired(), Length(min=2, max=255)])
    first_name = StringField('First Name', validators=[Optional(), Length(min=2, max=100)])
    last_name = StringField('Last Name', validators=[Optional(), Length(min=2, max=100)])
    email_consent = BooleanField('Email Consent', default=False)

class AdminActionForm(FlaskForm):
    """Form for admin actions like data clearing"""
    confirmation = StringField('Confirmation', validators=[DataRequired()])
    clear_brevo = BooleanField('Clear Brevo', default=False)

class CampaignForm(FlaskForm):
    """Form for sending email campaigns"""
    subject = StringField('Subject', validators=[DataRequired(), Length(min=3, max=200)])
    body = TextAreaField('Body', validators=[DataRequired(), Length(min=10, max=10000)])
    fromName = StringField('From Name', default='SideQuest', validators=[Length(max=100)])


def csrf_required(f):
    """Decorator that validates CSRF token and form data"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            # Get CSRF token from multiple sources
            csrf_token = (
                request.headers.get('X-CSRFToken') or 
                request.form.get('csrf_token') or 
                (request.json.get('csrf_token') if request.json else None)
            )
            
            if not csrf_token:
                log_activity(f"CSRF token missing for {request.path} from {request.remote_addr}", "warning")
                return jsonify({
                    "success": False, 
                    "error": "CSRF token required",
                    "code": "CSRF_TOKEN_MISSING"
                }), 400
            
            # Validate the token
            validate_csrf(csrf_token)
            
            # Log successful validation for critical operations
            if request.method in ['POST', 'PUT', 'DELETE']:
                log_activity(f"CSRF validation passed for {request.method} {request.path}", "info")
            
            return f(*args, **kwargs)
            
        except CSRFError as e:
            log_activity(f"CSRF validation failed for {request.path}: {str(e)}", "warning")
            return jsonify({
                "success": False,
                "error": "Invalid or expired CSRF token",
                "code": "CSRF_TOKEN_INVALID",
                "message": "Please refresh the page and try again"
            }), 403
            
        except Exception as e:
            log_error(f"CSRF validation error: {e}")
            return jsonify({
                "success": False,
                "error": "CSRF validation failed",
                "code": "CSRF_VALIDATION_ERROR"
            }), 500
    
    return decorated_function

def csrf_form_required(form_class):
    """Decorator that validates both CSRF token and form data using WTForms"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            try:
                # Create form instance
                if request.content_type == 'application/json':
                    form = form_class(data=request.json)
                else:
                    form = form_class()
                
                # Validate form (includes CSRF validation)
                if not form.validate():
                    errors = {}
                    for field, error_list in form.errors.items():
                        errors[field] = error_list
                    
                    log_activity(f"Form validation failed for {request.path}: {errors}", "warning")
                    return jsonify({
                        "success": False,
                        "error": "Form validation failed",
                        "errors": errors
                    }), 400
                
                # Pass validated form data to the route function
                return f(form, *args, **kwargs)
                
            except Exception as e:
                log_error(f"Form validation error: {e}")
                return jsonify({
                    "success": False,
                    "error": "Form processing failed"
                }), 500
        
        return decorated_function
    return decorator

@app.route('/api/csrf-token', methods=['GET'])
def get_csrf_token():
    """Generate and return CSRF token for API requests"""
    try:
        token = generate_csrf()
        return jsonify({
            "success": True,
            "csrf_token": token,
            "expires_in": 3600  # 1 hour
        })
    except Exception as e:
        log_error(f"Error generating CSRF token: {e}")
        return jsonify({"success": False, "error": "Failed to generate token"}), 500

@app.route('/api/csrf-refresh', methods=['POST'])
def refresh_csrf_token():
    """Refresh CSRF token - useful for long-running sessions"""
    try:
        # Validate current token first
        csrf_token = request.headers.get('X-CSRFToken')
        if csrf_token:
            try:
                validate_csrf(csrf_token)
            except:
                pass  # Allow refresh even with expired token
        
        new_token = generate_csrf()
        return jsonify({
            "success": True,
            "csrf_token": new_token,
            "expires_in": 3600
        })
    except Exception as e:
        log_error(f"Error refreshing CSRF token: {e}")
        return jsonify({"success": False, "error": "Failed to refresh token"}), 500



def verify_database_schema():
    """Verify that all required tables and columns exist"""
    try:
        conn = get_db_connection()
        if not conn:
            return False
            
        cursor = conn.cursor()
        
        # Check required tables
        required_tables = ['subscribers', 'events', 'event_registrations', 'activity_log', 'schema_version']
        
        for table in required_tables:
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = %s
                );
            """, (table,))
            
            exists = cursor.fetchone()[0]
            if not exists:
                print(f"❌ Missing required table: {table}")
                return False
        
        # Check required columns in event_registrations
        required_columns = {
            'event_registrations': ['id', 'event_id', 'subscriber_email', 'confirmation_code', 
                                  'registered_at', 'attended', 'check_in_time', 'notes']
        }
        
        for table, columns in required_columns.items():
            cursor.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = %s
            """, (table,))
            
            existing_columns = [row[0] for row in cursor.fetchall()]
            
            for column in columns:
                if column not in existing_columns:
                    print(f"❌ Missing column {column} in table {table}")
                    return False
        
        cursor.close()
        conn.close()
        
        print("✅ Database schema verification passed")
        return True
        
    except Exception as e:
        print(f"❌ Schema verification failed: {e}")
        return False

# NEW LINE OF CODE 20:29 26/08/2025 
@app.route('/privacy')
def privacy_policy():
    """Privacy policy page"""
    return render_template_string(PRIVACY_POLICY_TEMPLATE)

@app.route('/api/gdpr/delete', methods=['POST'])
@csrf_required
def gdpr_delete_request():
    """Handle GDPR data deletion requests"""
    try:
        data = request.json or {}
        email = data.get('email', '').strip().lower()
        
        if not email or not is_valid_email(email):
            return jsonify({"success": False, "error": "Valid email required"}), 400
        
        # Remove from database
        removed = remove_subscriber_from_db(email)
        
        if removed:
            # Remove from Brevo
            brevo_result = remove_from_brevo_contact(email)
            
            log_activity(f"GDPR deletion request processed for {email}", "info")
            
            return jsonify({
                "success": True,
                "message": "Your data has been deleted from our systems"
            })
        else:
            return jsonify({
                "success": False, 
                "error": "Email not found in our records"
            }), 404
            
    except Exception as e:
        log_error(f"GDPR deletion error: {e}")
        return jsonify({"success": False, "error": "Internal error"}), 500

# Add privacy policy template
PRIVACY_POLICY_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Privacy Policy - SideQuest Gaming</title>
    <style>
        body { font-family: -apple-system, sans-serif; line-height: 1.6; max-width: 800px; margin: 0 auto; padding: 20px; background: #1a1a1a; color: #fff; }
        h1, h2 { color: #FFD700; }
        a { color: #FFD700; }
        .contact { background: #2a2a2a; padding: 20px; border-radius: 10px; margin: 20px 0; }
    </style>
</head>
<body>
    <h1>Privacy Policy</h1>
    <p><strong>Last updated:</strong> {{ current_date }}</p>
    
    <h2>Data Controller</h2>
    <div class="contact">
        <p><strong>SideQuest Gaming Cafe</strong><br>
        Canterbury, UK<br>
        Email: marketing@sidequestcanterbury.com</p>
    </div>
    
    <h2>What Data We Collect</h2>
    <ul>
        <li>Name (first and last)</li>
        <li>Email address</li>
        <li>Gaming handle (optional)</li>
        <li>Event registration data</li>
    </ul>
    
    <h2>How We Use Your Data</h2>
    <p>We use your data to:</p>
    <ul>
        <li>Send you gaming event notifications</li>
        <li>Process event registrations</li>
        <li>Send newsletters about gaming activities</li>
        <li>Manage your account and preferences</li>
    </ul>
    
    <h2>Third Party Services</h2>
    <p>We use Brevo (formerly Sendinblue) to send emails. Your data is shared with them for this purpose.</p>
    
    <h2>Your Rights</h2>
    <p>Under GDPR, you have the right to:</p>
    <ul>
        <li>Access your personal data</li>
        <li>Rectify incorrect data</li>
        <li>Erase your data (right to be forgotten)</li>
        <li>Withdraw consent at any time</li>
        <li>Data portability</li>
    </ul>
    
    <h2>Data Retention</h2>
    <p>We keep your data until you unsubscribe or request deletion.</p>
    
    <h2>Contact Us</h2>
    <p>For privacy concerns: <a href="mailto:marketing@sidequestcanterbury.com">marketing@sidequescanterbury.com</a></p>
    
    <div style="margin-top: 30px;">
        <a href="/signup" style="background: #FFD700; color: #1a1a1a; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Back to Signup</a>
    </div>
</body>
</html>
'''

# Update your init_database function to include migrations
def init_database():
    """Initialize database tables and add missing columns"""
    try:
        conn = get_db_connection()
        if not conn:
            print("❌ Could not connect to database")
            return False
            
        cursor = conn.cursor()
        
        # Create core tables first
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS subscribers (
                id SERIAL PRIMARY KEY,
                email VARCHAR(255) UNIQUE NOT NULL,
                date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                source VARCHAR(100) DEFAULT 'manual',
                status VARCHAR(50) DEFAULT 'active'
            )
        ''')
        
        # Add name columns if they don't exist
        try:
            cursor.execute('ALTER TABLE subscribers ADD COLUMN first_name VARCHAR(100);')
            print("✅ Added first_name column")
        except Exception:
            print("ℹ️ first_name column already exists")
            
        try:
            cursor.execute('ALTER TABLE subscribers ADD COLUMN last_name VARCHAR(100);')
            print("✅ Added last_name column")
        except Exception:
            print("ℹ️ last_name column already exists")
            
        try:
            cursor.execute('ALTER TABLE subscribers ADD COLUMN gaming_handle VARCHAR(50);')
            print("✅ Added gaming_handle column")
        except Exception:
            print("ℹ️ gaming_handle column already exists")
        
        # Add computed full_name column (skip if it fails)
        try:
            cursor.execute('''
                ALTER TABLE subscribers ADD COLUMN full_name VARCHAR(200) 
                GENERATED ALWAYS AS (
                    CASE 
                        WHEN first_name IS NOT NULL AND last_name IS NOT NULL 
                        THEN CONCAT(first_name, ' ', last_name)
                        ELSE COALESCE(first_name, email)
                    END
                ) STORED;
            ''')
            print("✅ Added full_name computed column")
        except Exception as e:
            print(f"ℹ️ full_name column issue: {e}")
        
        # Create other tables...
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS activity_log (
                id SERIAL PRIMARY KEY,
                message TEXT NOT NULL,
                type VARCHAR(50) DEFAULT 'info',
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS events (
                id SERIAL PRIMARY KEY,
                title VARCHAR(255) NOT NULL,
                event_type VARCHAR(100) NOT NULL,
                game_title VARCHAR(255),
                date_time TIMESTAMP NOT NULL,
                end_time TIMESTAMP,
                capacity INTEGER DEFAULT 0,
                description TEXT,
                entry_fee DECIMAL(10,2) DEFAULT 0,
                prize_pool TEXT,
                status VARCHAR(50) DEFAULT 'draft',
                image_url TEXT,
                requirements TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_by VARCHAR(100) DEFAULT 'admin'
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS event_registrations (
                id SERIAL PRIMARY KEY,
                event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
                subscriber_email VARCHAR(255) NOT NULL,
                player_name VARCHAR(255),
                confirmation_code VARCHAR(50) UNIQUE NOT NULL,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                attended BOOLEAN DEFAULT FALSE,
                check_in_time TIMESTAMP,
                notes TEXT
            )
        ''')
        
        conn.commit()
        cursor.close()
        conn.close()
        add_gdpr_consent_column()
        
        print("✅ Database initialization completed")
        return True
        
    except Exception as e:
        print(f"❌ Database initialization error: {e}")
        return False

# Also add this backup function for safety
def backup_database_schema():
    """Create a backup of the current database schema"""
    try:
        import subprocess
        import os
        from datetime import datetime
        
        # Only works if you have pg_dump available
        database_url = os.environ.get('DATABASE_URL')
        if not database_url:
            print("⚠️ DATABASE_URL not found, skipping schema backup")
            return True
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_file = f"schema_backup_{timestamp}.sql"
        
        # Create schema-only backup
        result = subprocess.run([
            'pg_dump', '--schema-only', '--no-owner', '--no-privileges', 
            database_url, '-f', backup_file
        ], capture_output=True, text=True)
        
        if result.returncode == 0:
            print(f"✅ Schema backup created: {backup_file}")
            return True
        else:
            print(f"⚠️ Schema backup failed: {result.stderr}")
            return True  # Don't fail initialization for backup issues
            
    except Exception as e:
        print(f"⚠️ Schema backup error: {e}")
        return True  # Don't fail initialization for backup issues

# Add these config variables near the top
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'sidequest2024')  # Change this!

def require_admin_auth(f):
    """Decorator to require admin authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        print(f"Checking auth for {request.path}")  # Debug log
        print(f"Session authenticated: {session.get('admin_authenticated')}")  # Debug log
        
        if not session.get('admin_authenticated'):
            print("Not authenticated - redirecting to login")  # Debug log
            return redirect('/admin/login')
        
        print("Authentication passed")  # Debug log
        return f(*args, **kwargs)
    return decorated_function

@app.route('/admin/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")  # Basic rate limiting
def admin_login():
    if request.method == 'POST':
        client_ip = get_remote_address()
        password = request.form.get('password', '')
        
        # Check rate limiting (3 attempts per 15 minutes)
        now = datetime.now()
        cutoff_time = now - timedelta(minutes=15)
        
        # Clean old attempts
        login_attempts[client_ip] = [
            attempt_time for attempt_time in login_attempts[client_ip] 
            if attempt_time > cutoff_time
        ]
        
        # Check if too many attempts
        if len(login_attempts[client_ip]) >= 3:
            log_activity(f"Rate limited login attempt from {client_ip}", "warning")
            time.sleep(2)  # Slow down the response
            error_html = '<div class="error">Too many failed attempts. Try again in 15 minutes.</div>'
            return LOGIN_TEMPLATE.replace('ERROR_PLACEHOLDER', error_html), 429
        
        if password == ADMIN_PASSWORD:
            session['admin_authenticated'] = True
            session['last_activity'] = datetime.now().isoformat()
            session['login_ip'] = client_ip  # Track login IP
            session.permanent = False
            
            # Clear failed attempts on successful login
            login_attempts[client_ip] = []
            
            log_activity(f"Admin login successful from {client_ip}", "success")
            return redirect('/admin')
        else:
            # Record failed attempt
            login_attempts[client_ip].append(now)
            log_activity(f"Failed login attempt from {client_ip}", "warning")
            
            time.sleep(1)  # Slow down failed attempts
            error_html = '<div class="error">Invalid password</div>'
            return LOGIN_TEMPLATE.replace('ERROR_PLACEHOLDER', error_html)
    
    # Show timeout message if redirected due to session expiry
    timeout_msg = ''
    if request.args.get('timeout'):
        timeout_msg = '<div class="error">Session expired. Please log in again.</div>'
    
    return LOGIN_TEMPLATE.replace('ERROR_PLACEHOLDER', timeout_msg)

@app.route('/admin/logout')
def admin_logout():
    """Admin logout"""
    session.pop('admin_authenticated', None)
    return redirect('/admin/login')

# =============================
# Database Helper Functions
# =============================
def add_subscriber_to_db(email, source, first_name=None, last_name=None, gaming_handle=None, gdpr_consent=False):
    try:
        conn = get_db_connection()
        if not conn:
            print("❌ Database connection failed")
            return False
            
        cursor = conn.cursor()
        print(f"🔍 Adding subscriber: {email} with consent: {gdpr_consent}")
        
        # Enhanced insert with GDPR fields
        cursor.execute("""
            INSERT INTO subscribers (
                email, first_name, last_name, gaming_handle, source, 
                gdpr_consent_given, consent_date
            ) 
            VALUES (%s, %s, %s, %s, %s, %s, %s) 
            ON CONFLICT (email) DO UPDATE SET
                first_name = COALESCE(subscribers.first_name, EXCLUDED.first_name),
                last_name = COALESCE(subscribers.last_name, EXCLUDED.last_name),
                gaming_handle = COALESCE(subscribers.gaming_handle, EXCLUDED.gaming_handle),
                gdpr_consent_given = EXCLUDED.gdpr_consent_given,
                consent_date = EXCLUDED.consent_date
        """, (
            email, first_name, last_name, gaming_handle, source,
            gdpr_consent, datetime.now() if gdpr_consent else None
        ))
        
        rows_affected = cursor.rowcount
        conn.commit()
        cursor.close()
        conn.close()
        
        return rows_affected > 0
        
    except Exception as e:
        print(f"Error adding subscriber to database: {e}")
        return False

def remove_subscriber_from_db(email):
    """Remove subscriber from database"""
    try:
        conn = get_db_connection()
        if not conn:
            return False
            
        cursor = conn.cursor()
        cursor.execute("DELETE FROM subscribers WHERE email = %s", (email,))
        rows_affected = cursor.rowcount
        conn.commit()
        cursor.close()
        conn.close()
        
        return rows_affected > 0
        
    except Exception as e:
        print(f"Error removing subscriber from database: {e}")
        return False

def get_all_subscribers():
    """Get all subscribers from database with name fields"""
    try:
        conn = get_db_connection()
        if not conn:
            return []
            
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT id, email, first_name, last_name, gaming_handle, full_name, 
                   date_added, source, status 
            FROM subscribers 
            ORDER BY date_added DESC
        """)
        subscribers = cursor.fetchall()
        cursor.close()
        conn.close()
        
        return [dict(sub) for sub in subscribers]
        
    except Exception as e:
        print(f"Error getting subscribers from database: {e}")
        return []

def log_activity_to_db(message, activity_type="info"):
    """Add activity to database"""
    try:
        conn = get_db_connection()
        if not conn:
            return
            
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO activity_log (message, type) VALUES (%s, %s)",
            (message, activity_type)
        )
        conn.commit()
        cursor.close()
        conn.close()
        
        print(f"[{activity_type.upper()}] {message}")
        
    except Exception as e:
        print(f"Error logging activity: {e}")

def get_activity_log(limit=20):
    """Get activity log from database"""
    try:
        conn = get_db_connection()
        if not conn:
            return []
            
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "SELECT * FROM activity_log ORDER BY timestamp DESC LIMIT %s",
            (limit,)
        )
        activities = cursor.fetchall()
        cursor.close()
        conn.close()
        
        # Convert to the format expected by frontend
        result = []
        for activity in activities:
            result.append({
                'message': activity['message'],
                'type': activity['type'],
                'timestamp': activity['timestamp'].isoformat()
            })
        
        return result
        
    except Exception as e:
        print(f"Error getting activity log: {e}")
        return []

# =============================
# Helpers
# =============================

def log_activity(message: str, activity_type: str = "info") -> None:
    """Log activity to database"""
    log_activity_to_db(message, activity_type)

def log_error(error: Exception | str, error_type: str = "error") -> None:
    err = str(error)
    log_activity(f"Error: {err}", error_type)
    print(f"Error [{error_type}]: {err}")
    print(f"Traceback: {traceback.format_exc()}")

def is_valid_email(email: str) -> bool:
    try:
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return re.match(pattern, email) is not None
    except Exception:
        return False

# =============================
# Brevo client init
# =============================
configuration = None
api_instance = None
contacts_api = None

if sib_api_v3_sdk is not None and BREVO_API_KEY:
    try:
        configuration = sib_api_v3_sdk.Configuration()
        configuration.api_key['api-key'] = BREVO_API_KEY
        api_client = sib_api_v3_sdk.ApiClient(configuration)
        api_instance = sib_api_v3_sdk.TransactionalEmailsApi(api_client)
        contacts_api = sib_api_v3_sdk.ContactsApi(api_client)
    except Exception as e:  # pragma: no cover
        print(f"❌ Error initializing Brevo API instances: {e}")
        api_instance = None
        contacts_api = None
else:
    if not BREVO_API_KEY:
        print("⚠️  BREVO_API_KEY not set — Brevo features disabled.")

def test_brevo_connection() -> tuple[bool, str, str | None]:
    """Test Brevo API connection with enhanced error handling"""
    if sib_api_v3_sdk is None or configuration is None:
        return False, "Brevo SDK not available", None
    try:
        account_api = sib_api_v3_sdk.AccountApi(sib_api_v3_sdk.ApiClient(configuration))
        account_info = account_api.get_account()
        print("✅ Brevo API connected successfully!")
        print(f"📧 Account email: {getattr(account_info, 'email', None)}")
        return True, "connected", getattr(account_info, 'email', None)
    except ApiException as e:  # type: ignore
        log_error(e, "api_error")
        return False, f"Brevo API Error: {str(e)}", None
    except Exception as e:
        log_error(e, "api_error")
        return False, f"Unexpected error: {str(e)}", None

# =============================
# Brevo list helpers
# =============================

def add_to_brevo_contact(email: str, attributes: dict = None) -> dict:
    """Enhanced function to add contact to Brevo with name attributes"""
    if not AUTO_SYNC_TO_BREVO:
        return {"success": True, "message": "Brevo sync disabled"}
    if not contacts_api:
        return {"success": False, "error": "Brevo API not initialized"}
    
    try:
        # Enhanced contact attributes with names
        contact_attributes = {
            'SOURCE': attributes.get('source', 'web') if attributes else 'web',
            'DATE_ADDED': datetime.now().isoformat(),
        }
        
        # Add name fields if provided
        if attributes:
            if attributes.get('first_name'):
                contact_attributes['FNAME'] = attributes['first_name']
            if attributes.get('last_name'):
                contact_attributes['LNAME'] = attributes['last_name']
            if attributes.get('gaming_handle'):
                contact_attributes['GAMING_HANDLE'] = attributes['gaming_handle']
            
            # Add any additional attributes
            contact_attributes.update({k: v for k, v in attributes.items() 
                                     if k not in ['first_name', 'last_name', 'gaming_handle', 'source']})
        
        create_contact = sib_api_v3_sdk.CreateContact(
            email=email,
            list_ids=[BREVO_LIST_ID],
            attributes=contact_attributes,
            email_blacklisted=False,
            sms_blacklisted=False,
            update_enabled=True,
        )
        
        result = contacts_api.create_contact(create_contact)
        
        # Enhanced logging with names
        name_info = ""
        if attributes and (attributes.get('first_name') or attributes.get('last_name')):
            name_info = f" ({attributes.get('first_name', '')} {attributes.get('last_name', '')})".strip()
        
        log_activity(f"✅ Added {email}{name_info} to Brevo with ID: {getattr(result, 'id', 'unknown')}", "success")
        return {"success": True, "message": f"Added to Brevo", "brevo_id": getattr(result, 'id', None)}
        
    except ApiException as e:
        error_msg = str(e)
        if "duplicate_parameter" in error_msg or "already exists" in error_msg.lower():
            try:
                # Contact exists, try to update with new attributes
                update_contact = sib_api_v3_sdk.UpdateContact(attributes=contact_attributes)
                contacts_api.update_contact(email, update_contact)
                
                log_activity(f"ℹ️ Updated existing contact {email} in Brevo with new attributes", "info")
                return {"success": True, "message": "Contact updated in Brevo"}
            except Exception as e2:
                log_activity(f"⚠️ Error updating existing contact {email}: {str(e2)}", "warning")
                return {"success": True, "message": "Contact already in Brevo"}
        else:
            log_activity(f"❌ Brevo API Error for {email}: {error_msg}", "danger")
            return {"success": False, "error": error_msg}
    except Exception as e:
        log_activity(f"❌ Unexpected error adding {email} to Brevo: {str(e)}", "danger")
        return {"success": False, "error": str(e)}

def remove_from_brevo_contact(email: str) -> dict:
    """🔥 KEY FIX: Remove contact completely from Brevo (not just from list)"""
    if not AUTO_SYNC_TO_BREVO:
        return {"success": True, "message": "Brevo sync disabled"}
    if not contacts_api:
        return {"success": False, "error": "Brevo API not initialized"}
    
    try:
        # Method 1: Try to delete the contact completely
        try:
            contacts_api.delete_contact(email)
            log_activity(f"✅ Completely removed {email} from Brevo contacts", "success")
            return {"success": True, "message": f"Removed {email} from Brevo contacts"}
        except ApiException as e:
            if e.status == 404:
                log_activity(f"ℹ️ Contact {email} not found in Brevo (already removed)", "info")
                return {"success": True, "message": "Contact not found in Brevo (already removed)"}
            else:
                raise  # Re-raise if it's not a 404 error
        
    except ApiException as e:
        # Fallback: Remove from list if delete failed
        try:
            contacts_api.remove_contact_from_list(
                BREVO_LIST_ID,
                sib_api_v3_sdk.RemoveContactFromList(emails=[email])
            )
            log_activity(f"⚠️ Could not delete {email} from Brevo, but removed from list", "warning")
            return {"success": True, "message": f"Removed from list (contact still exists in Brevo)"}
        except Exception as e2:
            log_activity(f"❌ Failed to remove {email} from Brevo list: {str(e2)}", "danger")
            return {"success": False, "error": f"Brevo removal failed: {str(e)}"}
    except Exception as e:
        log_activity(f"❌ Unexpected error removing {email}: {str(e)}", "danger")
        return {"success": False, "error": str(e)}

def bulk_sync_to_brevo(subscribers: list) -> dict:
    """Bulk sync all subscribers to Brevo with rate limiting"""
    if not AUTO_SYNC_TO_BREVO:
        return {"success": False, "error": "Brevo sync disabled"}
    if not contacts_api:
        return {"success": False, "error": "Brevo API not initialized"}
    
    results = {"synced": 0, "errors": 0, "details": []}
    
    try:
        import time
        for subscriber in subscribers:
            try:
                email = subscriber.get('email') if isinstance(subscriber, dict) else subscriber
                source = subscriber.get('source', 'unknown') if isinstance(subscriber, dict) else 'unknown'
                
                result = add_to_brevo_contact(email, {'source': source})
                if result.get("success", False):
                    results["synced"] += 1
                else:
                    results["errors"] += 1
                    results["details"].append(f"{email}: {result.get('error', 'Unknown error')}")
                
                # Rate limiting - wait 100ms between requests
                time.sleep(0.1)
                
            except Exception as e:
                results["errors"] += 1
                results["details"].append(f"{email}: {str(e)}")
        
        log_activity(f"Bulk Brevo sync completed: {results['synced']} synced, {results['errors']} errors", 
                    "success" if results["errors"] == 0 else "warning")
        
        return {"success": True, **results}
        
    except Exception as e:
        log_error(f"Bulk sync failed: {e}")
        return {"success": False, "error": str(e)}

# =============================
# Stats helper
# =============================

def get_signup_stats() -> dict:
    try:
        subscribers = get_all_subscribers()
        now = datetime.now()
        today = now.date()
        week_ago = now - timedelta(days=7)
        
        total_subscribers = len(subscribers)
        today_signups = 0
        week_signups = 0
        
        for sub in subscribers:
            try:
                # Handle both datetime objects and ISO strings
                if isinstance(sub['date_added'], str):
                    signup_date = datetime.fromisoformat(sub['date_added'])
                else:
                    signup_date = sub['date_added']
                    
                if signup_date.date() == today:
                    today_signups += 1
                if signup_date >= week_ago:
                    week_signups += 1
            except (ValueError, KeyError):
                continue
        
        source_counts = defaultdict(int)
        for sub in subscribers:
            source_counts[sub.get('source', 'unknown')] += 1
        
        return {
            "total": total_subscribers,
            "today": today_signups,
            "week": week_signups,
            "sources": dict(source_counts),
        }
    except Exception as e:
        print(f"Error calculating stats: {e}")
        return {"total": 0, "today": 0, "week": 0, "sources": {}}

# =============================
# Middleware logging
# =============================

@app.before_request
def before_request_handler():
    # Session timeout check for admin routes
    if request.path.startswith('/admin') and request.path != '/admin/login':
        if not session.get('admin_authenticated'):
            return redirect('/admin/login')
        
        # Check 30-minute timeout
        last_activity = session.get('last_activity')
        if last_activity:
            last_time = datetime.fromisoformat(last_activity)
            if datetime.now() - last_time > timedelta(minutes=30):
                session.clear()
                return redirect('/admin/login?timeout=1')
        
        session['last_activity'] = datetime.now().isoformat()
    
    # Log non-routine requests
    routine_paths = ['/subscribers', '/stats', '/activity', '/health']
    if request.path not in routine_paths:
        log_activity(f"Request to {request.path} [{request.method}]", "info")

@app.after_request
def log_response_info(response):
    try:
        if response.status_code >= 400:
            log_activity(f"Error response {response.status_code} to {request.path}", "error")
        return response
    except Exception:
        return response

# =============================
# Routes
# =============================

@app.route('/health', methods=['GET'])
@app.route('/health', methods=['GET'])
def health_check():
    """Enhanced health check with Brevo sync status"""
    try:
        brevo_connected, brevo_status, brevo_email = test_brevo_connection()
        db_connected = get_db_connection() is not None
        
        # Test Brevo contact operations
        brevo_ops_working = False
        if brevo_connected and contacts_api:
            try:
                # Test with a dummy email to see if operations work
                test_result = add_to_brevo_contact("test@example.com", {'test': True})
                brevo_ops_working = test_result.get("success", False) or "already exists" in test_result.get("message", "")
            except:
                brevo_ops_working = False
        
        return jsonify({
            "status": "healthy",
            "subscribers_count": len(get_all_subscribers()),
            "brevo_sync_enabled": AUTO_SYNC_TO_BREVO,
            "brevo_status": "connected" if brevo_connected else brevo_status,
            "brevo_operations_working": brevo_ops_working,
            "brevo_email": brevo_email,
            "brevo_list_id": BREVO_LIST_ID,
            "activities": len(get_activity_log(100)),
            "api_instances_initialized": (api_instance is not None and contacts_api is not None),
            "database_connected": db_connected,
            "sync_functions": {
                "add_contact": "add_to_brevo_contact",
                "remove_contact": "remove_from_brevo_contact", 
                "bulk_sync": "bulk_sync_to_brevo"
            }
        })
    except Exception as e:
        error_msg = f"Health check error: {str(e)}"
        log_error(error_msg)
        return jsonify({
            "status": "error",
            "error": error_msg,
            "brevo_status": "error",
            "database_connected": False,
        }), 500

@app.route('/debug/brevo-test/<email>', methods=['POST'])
def debug_brevo_test(email):
    """Debug endpoint to test Brevo operations"""
    try:
        if not is_valid_email(email):
            return jsonify({"error": "Invalid email"}), 400
        
        # Test add
        add_result = add_to_brevo_contact(email, {'source': 'debug_test'})
        
        # Test remove  
        remove_result = remove_from_brevo_contact(email)
        
        return jsonify({
            "email": email,
            "add_result": add_result,
            "remove_result": remove_result,
            "brevo_config": {
                "api_key_set": bool(BREVO_API_KEY),
                "auto_sync": AUTO_SYNC_TO_BREVO,
                "list_id": BREVO_LIST_ID,
                "contacts_api_ready": contacts_api is not None
            }
        })
        
    except Exception as e:
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500

@app.route('/debug/subscribers-sync', methods=['POST']) 
def debug_subscribers_sync():
    """Debug endpoint to sync a few test subscribers"""
    try:
        subscribers = get_all_subscribers()[:5]  # Test with first 5 only
        
        if not subscribers:
            return jsonify({"message": "No subscribers to test"}), 200
        
        result = bulk_sync_to_brevo(subscribers)
        
        return jsonify({
            "test_count": len(subscribers),
            "result": result,
            "test_emails": [sub['email'] for sub in subscribers]
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/subscribers', methods=['GET'])
def get_subscribers():
    try:
        subscribers = get_all_subscribers()
        stats = get_signup_stats()
        
        return jsonify({
            "success": True,
            "subscribers": [sub['email'] for sub in subscribers],
            "subscriber_details": subscribers,  # Now includes name fields
            "count": len(subscribers),
            "stats": stats,
        })
    except Exception as e:
        error_msg = f"Error getting subscribers: {str(e)}"
        print(f"Subscribers error: {traceback.format_exc()}")
        return jsonify({"success": False, "error": error_msg}), 500

def send_welcome_email(email, first_name=None, last_name=None, gaming_handle=None):
    """Send automated welcome email with high deliverability (avoids promotions tab)"""
    if not api_instance:
        log_error("Brevo API not initialized")
        return {"success": False, "error": "Brevo API not configured"}
    
    try:
        # Personalize greeting
        if first_name:
            greeting = f"Hi {first_name}!"
        elif gaming_handle:
            greeting = f"Hey {gaming_handle}!"
        else:
            greeting = "Welcome!"
        
        # Calculate expiry date (7 days from now)
        expiry_date = (datetime.now() + timedelta(days=7)).strftime("%B %d, %Y")
        
        # Create unsubscribe URL
        unsubscribe_url = f"https://sidequest-newsletter-production.up.railway.app/unsubscribe?email={email}"
        
        # TRANSACTIONAL subject line (avoids promotions tab)
        if first_name:
            subject = f"Welcome to SideQuest Canterbury, {first_name} - Account Details & Member Benefits"
        else:
            subject = "Welcome to SideQuest Canterbury - Account Details & Member Benefits"
        
        # Create HTML email content - mobile-optimized with universal CSS
        html_content = f"""
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
    <meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
    <title>Welcome to SideQuest Canterbury</title>
    <!--[if mso]>
    <noscript>
        <xml>
            <o:OfficeDocumentSettings>
                <o:PixelsPerInch>96</o:PixelsPerInch>
            </o:OfficeDocumentSettings>
        </xml>
    </noscript>
    <![endif]-->
</head>
<body style="margin: 0; padding: 0; -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; background-color: #f4f4f4;">
    <table border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color: #f4f4f4;">
        <tr>
            <td align="center" style="padding: 20px 10px;">
                <!-- Main Container -->
                <table border="0" cellpadding="0" cellspacing="0" width="600" style="max-width: 600px; background-color: #ffffff; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                    
                    <!-- Header -->
                    <tr>
                        <td align="center" style="padding: 40px 30px 30px 30px; background-color: #1a1a1a; border-radius: 8px 8px 0 0;">
                            <h1 style="margin: 0; font-family: Arial, Helvetica, sans-serif; font-size: 36px; font-weight: bold; color: #FFD700; text-align: center; letter-spacing: 2px;">
                                WELCOME TO SIDEQUEST
                            </h1>
                        </td>
                    </tr>
                    
                    <!-- Greeting -->
                    <tr>
                        <td style="padding: 30px 30px 20px 30px;">
                            <h2 style="margin: 0 0 20px 0; font-family: Arial, Helvetica, sans-serif; font-size: 24px; color: #333333; font-weight: normal;">
                                {greeting}
                            </h2>
                            <p style="margin: 0 0 20px 0; font-family: Arial, Helvetica, sans-serif; font-size: 16px; line-height: 24px; color: #666666;">
                                Thanks for joining the SideQuest Canterbury community! We're excited to welcome you to our gaming hub and can't wait to see you in store.
                            </p>
                            <p style="margin: 0; font-family: Arial, Helvetica, sans-serif; font-size: 16px; line-height: 24px; color: #666666;">
                                Your account has been successfully created and you now have access to member benefits and event notifications.
                            </p>
                        </td>
                    </tr>
                    
                    <!-- What We Offer -->
                    <tr>
                        <td style="padding: 20px 30px;">
                            <h3 style="margin: 0 0 20px 0; font-family: Arial, Helvetica, sans-serif; font-size: 20px; color: #1a1a1a; font-weight: bold;">
                                Here's What We Have To Offer:
                            </h3>
                            
                            <!-- Facilities List -->
                            <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                <tr>
                                    <td style="padding: 8px 0; border-bottom: 1px solid #e0e0e0;">
                                        <p style="margin: 0; font-family: Arial, Helvetica, sans-serif; font-size: 15px; color: #333333;">
                                            <strong>35 High-Performance PCs</strong> - Latest games and competitive setups
                                        </p>
                                    </td>
                                </tr>
                                <tr>
                                    <td style="padding: 8px 0; border-bottom: 1px solid #e0e0e0;">
                                        <p style="margin: 0; font-family: Arial, Helvetica, sans-serif; font-size: 15px; color: #333333;">
                                            <strong>Console Area with 4 PS5s</strong> - Latest PlayStation exclusives
                                        </p>
                                    </td>
                                </tr>
                                <tr>
                                    <td style="padding: 8px 0; border-bottom: 1px solid #e0e0e0;">
                                        <p style="margin: 0; font-family: Arial, Helvetica, sans-serif; font-size: 15px; color: #333333;">
                                            <strong>2 Professional Driving Rigs</strong> - Racing simulation experience
                                        </p>
                                    </td>
                                </tr>
                                <tr>
                                    <td style="padding: 8px 0; border-bottom: 1px solid #e0e0e0;">
                                        <p style="margin: 0; font-family: Arial, Helvetica, sans-serif; font-size: 15px; color: #333333;">
                                            <strong>VR Gaming Station</strong> - Immersive virtual reality
                                        </p>
                                    </td>
                                </tr>
                                <tr>
                                    <td style="padding: 8px 0; border-bottom: 1px solid #e0e0e0;">
                                        <p style="margin: 0; font-family: Arial, Helvetica, sans-serif; font-size: 15px; color: #333333;">
                                            <strong>Nintendo Switch Setup</strong> - Party games and exclusives
                                        </p>
                                    </td>
                                </tr>
                                <tr>
                                    <td style="padding: 8px 0; border-bottom: 1px solid #e0e0e0;">
                                        <p style="margin: 0; font-family: Arial, Helvetica, sans-serif; font-size: 15px; color: #333333;">
                                            <strong>Premium Bubble Tea Bar</strong> - Fuel your gaming sessions
                                        </p>
                                    </td>
                                </tr>
                                <tr>
                                    <td style="padding: 8px 0;">
                                        <p style="margin: 0; font-family: Arial, Helvetica, sans-serif; font-size: 15px; color: #333333;">
                                            <strong>Study & Chill Zone</strong> - Perfect for work or relaxation
                                        </p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    
                    <!-- Community Features -->
                    <tr>
                        <td style="padding: 20px 30px;">
                            <table border="0" cellpadding="20" cellspacing="0" width="100%" style="background-color: #f8f8f8; border-radius: 8px;">
                                <tr>
                                    <td>
                                        <h3 style="margin: 0 0 15px 0; font-family: Arial, Helvetica, sans-serif; font-size: 18px; color: #1a1a1a; font-weight: bold; text-align: center;">
                                            Community Events You'll Be Notified About:
                                        </h3>
                                        
                                        <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                            <tr>
                                                <td style="padding: 10px 0;">
                                                    <p style="margin: 0; font-family: Arial, Helvetica, sans-serif; font-size: 15px; color: #333333;">
                                                        <strong style="color: #FFD700;">Tournament Events</strong><br/>
                                                        <span style="color: #666666;">Competitive gaming across FPS, FIFA, and board games</span>
                                                    </p>
                                                </td>
                                            </tr>
                                            <tr>
                                                <td style="padding: 10px 0;">
                                                    <p style="margin: 0; font-family: Arial, Helvetica, sans-serif; font-size: 15px; color: #333333;">
                                                        <strong style="color: #FFA500;">Community Nights</strong><br/>
                                                        <span style="color: #666666;">Social gaming sessions and special events</span>
                                                    </p>
                                                </td>
                                            </tr>
                                            <tr>
                                                <td style="padding: 10px 0;">
                                                    <p style="margin: 0; font-family: Arial, Helvetica, sans-serif; font-size: 15px; color: #333333;">
                                                        <strong style="color: #4CAF50;">Member Events</strong><br/>
                                                        <span style="color: #666666;">Exclusive member-only gatherings and previews</span>
                                                    </p>
                                                </td>
                                            </tr>
                                        </table>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    
                    <!-- Member Benefit -->
                    <tr>
                        <td style="padding: 20px 30px;">
                            <table border="0" cellpadding="25" cellspacing="0" width="100%" style="background-color: #FFD700; border-radius: 8px;">
                                <tr>
                                    <td align="center">
                                        <h3 style="margin: 0 0 15px 0; font-family: Arial, Helvetica, sans-serif; font-size: 22px; color: #1a1a1a; font-weight: bold;">
                                            Welcome Member Benefit
                                        </h3>
                                        <p style="margin: 0 0 10px 0; font-family: Arial, Helvetica, sans-serif; font-size: 16px; color: #1a1a1a; font-weight: bold;">
                                            Present this email on your first visit to receive:
                                        </p>
                                        <p style="margin: 0 0 15px 0; font-family: Arial, Helvetica, sans-serif; font-size: 20px; color: #1a1a1a; font-weight: bold;">
                                            30% member discount on any bubble tea
                                        </p>
                                        <p style="margin: 0; font-family: Arial, Helvetica, sans-serif; font-size: 14px; color: #1a1a1a; font-weight: bold;">
                                            Valid until: {expiry_date}
                                        </p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    
                    <!-- CTA Button -->
                    <tr>
                        <td align="center" style="padding: 30px 30px 20px 30px;">
                            <table border="0" cellpadding="0" cellspacing="0">
                                <tr>
                                    <td align="center" style="background-color: #4CAF50; border-radius: 8px;">
                                        <a href="https://sidequesthub.com/home" style="display: inline-block; padding: 20px 30px; font-family: Arial, Helvetica, sans-serif; font-size: 16px; color: #ffffff; text-decoration: none; font-weight: bold;">
                                            Complete Your Account Setup<br/>
                                            <span style="font-size: 14px;">Unlock 30 Minutes Free Gaming Time</span>
                                        </a>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    
                    <!-- Location Button -->
                    <tr>
                        <td align="center" style="padding: 0 30px 30px 30px;">
                            <table border="0" cellpadding="0" cellspacing="0">
                                <tr>
                                    <td align="center" style="background-color: #1a1a1a; border-radius: 8px;">
                                        <a href="https://www.google.com/maps/place/Sidequest+Esport+Hub/@51.2846796,1.0872896,21z/data=!4m15!1m8!3m7!1s0x47deca4c09507c33:0xb2a02aee5030dd48!2sthe+Riverside,+1+Sturry+Rd,+Canterbury+CT1+1BU!3b1!8m2!3d51.2849197!4d1.0879336!16s%2Fg%2F11b8txmdmd!3m5!1s0x47decb26857e3c09:0x63d22a836904507c!8m2!3d51.2845996!4d1.0872413!16s%2Fg%2F11l2p4jsx_?entry=ttu&g_ep=EgoyMDI1MDgyNS4wIKXMDSoASAFQAw%3D%3D" style="display: inline-block; padding: 15px 25px; font-family: Arial, Helvetica, sans-serif; font-size: 16px; color: #FFD700; text-decoration: none; font-weight: bold;">
                                            View Location & Hours
                                        </a>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    
                    <!-- Terms -->
                    <tr>
                        <td style="padding: 0 30px 30px 30px;">
                            <table border="0" cellpadding="15" cellspacing="0" width="100%" style="background-color: #f0f0f0; border-radius: 8px;">
                                <tr>
                                    <td>
                                        <p style="margin: 0 0 10px 0; font-family: Arial, Helvetica, sans-serif; font-size: 13px; color: #666666; font-weight: bold;">
                                            Member Benefit Terms:
                                        </p>
                                        <p style="margin: 0; font-family: Arial, Helvetica, sans-serif; font-size: 13px; color: #666666; line-height: 18px;">
                                            • Valid for first-time members only<br/>
                                            • Present this email on your mobile device in-store<br/>
                                            • One use per member account<br/>
                                            • Valid for 7 days from account creation
                                        </p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    
                    <!-- Footer -->
                    <tr>
                        <td style="padding: 30px 30px 40px 30px; background-color: #f8f8f8; border-radius: 0 0 8px 8px;">
                            <p style="margin: 0 0 20px 0; font-family: Arial, Helvetica, sans-serif; font-size: 16px; color: #1a1a1a; text-align: center; font-weight: bold;">
                                Welcome to the community. See you at SideQuest!
                            </p>
                            
                            <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                <tr>
                                    <td align="center">
                                        <p style="margin: 0 0 15px 0; font-family: Arial, Helvetica, sans-serif; font-size: 14px; color: #666666; line-height: 20px;">
                                            <strong>SideQuest Canterbury Gaming Lounge</strong><br/>
                                            C10, The Riverside, 1 Sturry Rd<br/>
                                            Canterbury CT1 1BU<br/>
                                            01227 915058<br/>
                                            <a href="mailto:marketing@sidequestcanterbury.com" style="color: #4CAF50; text-decoration: none;">marketing@sidequestcanterbury.com</a>
                                        </p>
                                        
                                        <p style="margin: 0 0 15px 0; font-family: Arial, Helvetica, sans-serif; font-size: 13px; color: #666666; line-height: 18px;">
                                            <strong>Opening Hours:</strong><br/>
                                            Sunday: 12-9pm • Monday: 2-9pm • Tuesday-Thursday: Closed<br/>
                                            Friday: 2-9pm • Saturday: 12-9pm
                                        </p>
                                        
                                        <p style="margin: 0; font-family: Arial, Helvetica, sans-serif; font-size: 12px; color: #999999;">
                                            You received this account notification because you subscribed to community updates. 
                                            <a href="{unsubscribe_url}" style="color: #4CAF50; text-decoration: none;">Manage preferences</a>
                                        </p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
        """
        
        # Plain text version
        text_content = f"""
WELCOME TO SIDEQUEST

{greeting}

Thanks for joining the SideQuest Canterbury community! We're excited to welcome you to our gaming hub and can't wait to see you in store.

Your account has been successfully created and you now have access to member benefits and event notifications.

HERE'S WHAT WE HAVE TO OFFER:

GAMING FACILITIES:
- 35 High-Performance PCs with latest games and competitive setups
- Console Area with 4 PS5s - Latest PlayStation exclusives  
- 2 Professional Driving Rigs - Racing simulation experience
- VR Gaming Station - Immersive virtual reality
- Nintendo Switch Setup - Party games and exclusives
- Premium Bubble Tea Bar - Fuel your gaming sessions
- Study & Chill Zone - Perfect for work or relaxation

COMMUNITY EVENTS YOU'LL BE NOTIFIED ABOUT:
- Tournament Events: Competitive gaming across FPS, FIFA, and board games
- Community Nights: Social gaming sessions and special events
- Member Events: Exclusive member-only gatherings and previews

WELCOME MEMBER BENEFIT:
Present this email on your first visit to receive a 30% member discount on any bubble tea.
Valid until: {expiry_date}

COMPLETE YOUR ACCOUNT:
Visit https://sidequesthub.com/home to unlock 30 minutes of free gaming time.

MEMBER BENEFIT TERMS: 
Valid for first-time members only. Present this email on your mobile device in-store. One use per account. Valid for 7 days from account creation.

Welcome to the community. See you at SideQuest!

---
SideQuest Canterbury Gaming Lounge
C10, The Riverside, 1 Sturry Rd, Canterbury CT1 1BU
Phone: 01227 915058
Email: marketing@sidequestcanterbury.com

Opening Hours:
Sunday: 12-9pm • Monday: 2-9pm • Tuesday-Thursday: Closed
Friday: 2-9pm • Saturday: 12-9pm

Manage preferences: {unsubscribe_url}
        """
        
        # Enhanced email configuration for better deliverability
        send_email = sib_api_v3_sdk.SendSmtpEmail(
            sender={"name": SENDER_NAME, "email": SENDER_EMAIL},
            reply_to={"name": "SideQuest Support", "email": SENDER_EMAIL},
            to=[{
                "email": email,
                "name": f"{first_name} {last_name}".strip() if first_name or last_name else ""
            }],
            subject=subject,
            html_content=html_content,
            text_content=text_content,
            tags=["welcome_email", "account_notification", "member_benefits"],
            headers={
                "X-Mailer": "SideQuest Canterbury Member System",
                "List-Unsubscribe": f"<{unsubscribe_url}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
                "X-Entity-Ref-ID": f"welcome-{int(datetime.now().timestamp())}",
                "Importance": "normal",
                "X-Auto-Response-Suppress": "OOF",
                "Precedence": "bulk"
            }
        )
        
        # Send the email
        response = api_instance.send_transac_email(send_email)
        
        return {
            "success": True, 
            "message": "Welcome email sent successfully",
            "message_id": response.message_id if hasattr(response, 'message_id') else None
        }
            
    except Exception as e:
        log_error(f"Error sending welcome email: {str(e)}")
        return {"success": False, "error": f"Error sending welcome email: {str(e)}"}
        
@app.route('/subscribe', methods=['POST'])
@csrf_required
def add_subscriber():
    """Enhanced subscribe route with input sanitization, GDPR compliance, and automated welcome email"""
    try:
        data = request.get_json(silent=True) or {}

        # Sanitize all inputs first
        email = sanitize_email(data.get('email', ''))
        source = sanitize_text_input(data.get('source', 'manual'), 50)
        first_name = sanitize_text_input(data.get('firstName', ''), 100)
        last_name = sanitize_text_input(data.get('lastName', ''), 100)
        gaming_handle = sanitize_text_input(data.get('gamingHandle', ''), 50) if data.get('gamingHandle') else None
        gdpr_consent = bool(data.get('gdprConsent', False))

        # Validate sanitized inputs
        if not email:
            return jsonify({"success": False, "error": "Valid email is required"}), 400

        # GDPR COMPLIANCE CHECK
        if source in ['signup_page_gdpr', 'manual'] and not gdpr_consent:
            return jsonify({
                "success": False,
                "error": "GDPR consent is required to process your personal data"
            }), 400

        # Validate name fields for GDPR sources
        if source in ['signup_page_gdpr'] and (not first_name or not last_name):
            return jsonify({"success": False, "error": "First name and last name are required"}), 400

        if first_name and len(first_name) < 2:
            return jsonify({"success": False, "error": "First name must be at least 2 characters"}), 400

        if last_name and len(last_name) < 2:
            return jsonify({"success": False, "error": "Last name must be at least 2 characters"}), 400

        # Name pattern validation
        if first_name or last_name:
            name_pattern = r'^[a-zA-Z\s\'-]+$'
            if first_name and not re.match(name_pattern, first_name):
                return jsonify({"success": False, "error": "Invalid characters in first name"}), 400
            if last_name and not re.match(name_pattern, last_name):
                return jsonify({"success": False, "error": "Invalid characters in last name"}), 400

        # Gaming handle validation
        if gaming_handle and (len(gaming_handle) < 3 or len(gaming_handle) > 30):
            return jsonify({"success": False, "error": "Gaming handle must be 3-30 characters"}), 400

        # Check if already exists
        existing_subscribers = get_all_subscribers()
        if any(sub['email'] == email for sub in existing_subscribers):
            return jsonify({"success": False, "error": "Email already subscribed"}), 400

        # Add to database with GDPR consent recorded
        if add_subscriber_to_db(email, source, first_name, last_name, gaming_handle, gdpr_consent):
            # Enhanced Brevo sync with names and consent tracking
            brevo_attributes = {
                'source': source,
                'date_added': datetime.now().isoformat(),
                'gdpr_consent_given': 'yes' if gdpr_consent else 'no',
                'consent_date': datetime.now().isoformat() if gdpr_consent else None
            }
            if first_name:
                brevo_attributes['first_name'] = first_name
            if last_name:
                brevo_attributes['last_name'] = last_name
            if gaming_handle:
                brevo_attributes['gaming_handle'] = gaming_handle

            brevo_result = add_to_brevo_contact(email, brevo_attributes)

            # AUTOMATED WELCOME EMAIL - Use your existing function
            welcome_email_result = {"success": False, "message": "Not sent - source not eligible"}
            if source == 'signup_page_gdpr':
                welcome_email_result = send_welcome_email(email, first_name, last_name, gaming_handle)

                if welcome_email_result.get("success"):
                    log_activity(f"✅ Welcome email sent to {email} ({first_name} {last_name})", "success")
                else:
                    log_activity(
                        f"❌ Failed to send welcome email to {email}: {welcome_email_result.get('error')}",
                        "warning"
                    )

            subscriber_info = f"{first_name} {last_name}".strip() if (first_name or last_name) else email
            consent_status = "with GDPR consent" if gdpr_consent else "without explicit consent"
            log_activity(
                f"🎮 New subscriber added: {subscriber_info} ({email}) - Source: {source} - {consent_status}",
                "success"
            )

            return jsonify({
                "success": True,
                "message": "Subscriber added successfully",
                "data": {
                    "email": email,
                    "name": f"{first_name} {last_name}".strip() if (first_name or last_name) else None,
                    "gaming_handle": gaming_handle,
                    "gdpr_consent_given": gdpr_consent,
                    "source": source
                },
                "integrations": {
                    "brevo_synced": brevo_result.get("success", False),
                    "brevo_message": brevo_result.get("message", brevo_result.get("error", "")),
                    "welcome_email_sent": welcome_email_result.get("success", False),
                    "welcome_email_message": welcome_email_result.get("message", welcome_email_result.get("error", "")),
                }
            })
        else:
            return jsonify({"success": False, "error": "Failed to add subscriber to database"}), 500

    except ValueError as e:
        log_error(f"Validation error in add_subscriber: {str(e)}")
        return jsonify({"success": False, "error": "Invalid data format"}), 400
    
    except psycopg2.Error as e:
        log_error(f"Database error in add_subscriber: {str(e)}")
        return jsonify({"success": False, "error": "Database error occurred"}), 500
    
    except Exception as e:
        log_error(f"Unexpected error in add_subscriber: {str(e)}")
        log_error(f"Full traceback: {traceback.format_exc()}")
        return jsonify({"success": False, "error": "An unexpected error occurred"}), 500

def add_gdpr_consent_column():
    """Add GDPR consent tracking columns to subscribers table"""
    try:
        conn = get_db_connection()
        if not conn:
            return False
            
        cursor = conn.cursor()
        
        # Add GDPR consent columns
        try:
            cursor.execute('ALTER TABLE subscribers ADD COLUMN gdpr_consent_given BOOLEAN DEFAULT FALSE;')
            print("✅ Added gdpr_consent_given column")
        except Exception:
            print("ℹ️ gdpr_consent_given column already exists")
            
        try:
            cursor.execute('ALTER TABLE subscribers ADD COLUMN consent_date TIMESTAMP;')
            print("✅ Added consent_date column")
        except Exception:
            print("ℹ️ consent_date column already exists")
            
        try:
            cursor.execute('ALTER TABLE subscribers ADD COLUMN consent_ip VARCHAR(45);')
            print("✅ Added consent_ip column")
        except Exception:
            print("ℹ️ consent_ip column already exists")
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
        
    except Exception as e:
        print(f"Error adding GDPR consent columns: {e}")
        return False


@app.route('/unsubscribe', methods=['POST'])
@csrf_required
def remove_subscriber():
    """🔥 ENHANCED: Remove subscriber from both database AND Brevo"""
    try:
        data = request.json or {}
        email = str(data.get('email', '')).strip().lower()
        
        if not email:
            return jsonify({"success": False, "error": "Email is required"}), 400
        
        # Check if exists
        existing_subscribers = get_all_subscribers()
        if not any(sub['email'] == email for sub in existing_subscribers):
            return jsonify({"success": False, "error": "Email not found"}), 404
        
        # Remove from database
        if remove_subscriber_from_db(email):
            # 🔥 KEY FIX: Remove from Brevo as well!
            brevo_result = remove_from_brevo_contact(email)
            
            log_activity(f"Subscriber removed: {email}", "warning")
            
            return jsonify({
                "success": True,
                "message": "Subscriber removed successfully",
                "email": email,
                "brevo_removed": brevo_result.get("success", False),
                "brevo_message": brevo_result.get("message", brevo_result.get("error", "")),
            })
        else:
            return jsonify({"success": False, "error": "Failed to remove subscriber from database"}), 500
            
    except Exception as e:
        error_msg = f"Error removing subscriber: {str(e)}"
        log_error(error_msg)
        return jsonify({"success": False, "error": error_msg}), 500

@app.route('/stats', methods=['GET'])
def get_stats():
    try:
        stats = get_signup_stats()
        return jsonify({
            "success": True,
            "stats": stats,
            "brevo_sync_status": "✅" if AUTO_SYNC_TO_BREVO else "❌",
        })
    except Exception as e:
        error_msg = f"Error getting stats: {str(e)}"
        print(f"Stats error: {traceback.format_exc()}")
        return jsonify({"success": False, "error": error_msg}), 500

@app.route('/activity', methods=['GET'])
def get_activity():
    try:
        limit = int(request.args.get('limit', 20))
        activities = get_activity_log(limit)
        return jsonify({"success": True, "activity": activities})
    except Exception as e:
        error_msg = f"Error getting activity: {str(e)}"
        print(f"Activity error: {traceback.format_exc()}")
        return jsonify({"success": False, "error": error_msg}), 500

@app.route('/bulk-import', methods=['POST'])
def bulk_import():
    """Enhanced bulk import with name field support"""
    try:
        data = request.json or {}
        emails = data.get('emails', [])
        source = data.get('source', 'import')
        
        if not emails:
            return jsonify({"success": False, "error": "No emails provided"}), 400
        
        added = 0
        errors: list[str] = []
        brevo_synced = 0
        existing_subscribers = get_all_subscribers()
        existing_emails = {sub['email'] for sub in existing_subscribers}
        
        for item in emails:
            try:
                # Handle both string emails and objects with name data
                if isinstance(item, dict):
                    email = str(item.get('email', '')).strip().lower()
                    first_name = item.get('firstName', '').strip() or None
                    last_name = item.get('lastName', '').strip() or None
                    gaming_handle = item.get('gamingHandle', '').strip() or None
                else:
                    email = str(item).strip().lower()
                    first_name = last_name = gaming_handle = None
                
                if not is_valid_email(email):
                    errors.append(f"Invalid email: {email}")
                    continue
                if email in existing_emails:
                    errors.append(f"Already exists: {email}")
                    continue
                
                if add_subscriber_to_db(email, source, first_name, last_name, gaming_handle, False):
                    # Add to Brevo with names
                    brevo_attributes = {'source': source}
                    if first_name:
                        brevo_attributes['first_name'] = first_name
                    if last_name:
                        brevo_attributes['last_name'] = last_name
                    if gaming_handle:
                        brevo_attributes['gaming_handle'] = gaming_handle
                        
                    brevo_result = add_to_brevo_contact(email, brevo_attributes)
                    if brevo_result.get("success", False):
                        brevo_synced += 1
                    else:
                        errors.append(f"Brevo sync failed for {email}: {brevo_result.get('error', 'Unknown error')}")
                    
                    added += 1
                    existing_emails.add(email)
                else:
                    errors.append(f"Database error for {email}")
                    
            except Exception as e:
                errors.append(f"Error processing {email}: {str(e)}")
                continue
        
        log_activity(f"Bulk import: {added} subscribers added, {brevo_synced} synced to Brevo, {len(errors)} errors", "info")
        
        return jsonify({
            "success": True,
            "added": added,
            "brevo_synced": brevo_synced,
            "errors": errors,
            "total_processed": len(emails),
        })
        
    except Exception as e:
        error_msg = f"Error in bulk import: {str(e)}"
        log_error(error_msg)
        return jsonify({"success": False, "error": error_msg}), 500

@app.route('/sync-brevo', methods=['POST'])
@csrf_required
def manual_brevo_sync():
    """Enhanced manual Brevo sync with better feedback"""
    try:
        if not AUTO_SYNC_TO_BREVO:
            return jsonify({"success": False, "error": "Brevo sync is disabled"}), 400
        if not contacts_api:
            return jsonify({"success": False, "error": "Brevo API not initialized"}), 500
        
        print("🔄 Starting manual Brevo sync...")
        log_activity("Starting manual Brevo sync", "info")
        
        subscribers = get_all_subscribers()
        if not subscribers:
            return jsonify({"success": True, "message": "No subscribers to sync", "synced": 0}), 200
        
        # Use the enhanced bulk sync function
        result = bulk_sync_to_brevo(subscribers)
        
        if result.get("success", False):
            log_activity(f"Manual Brevo sync completed: {result['synced']} synced, {result['errors']} errors", 
                        "success" if result["errors"] == 0 else "warning")
            
            return jsonify({
                "success": True,
                "synced": result["synced"],
                "errors": result["errors"],
                "total": len(subscribers),
                "error_details": result["details"][:10],  # Limit error details
                "message": f"Sync completed: {result['synced']}/{len(subscribers)} successful"
            })
        else:
            return jsonify({"success": False, "error": result.get("error", "Sync failed")}), 500
        
    except Exception as e:
        error_msg = f"Error in manual sync: {str(e)}"
        log_error(error_msg)
        return jsonify({"success": False, "error": error_msg}), 500

@app.route('/clear-data', methods=['POST'])
@csrf_required
def clear_all_data():
    """🔥 ENHANCED: Clear data from both database AND Brevo"""
    try:
        data = request.json or {}
        confirmation = data.get('confirmation', '')
        clear_brevo = data.get('clear_brevo', False)  # Optional flag
        
        if confirmation != 'DELETE':
            return jsonify({"success": False, "error": "Invalid confirmation"}), 400
        
        # Get all subscribers before deleting
        subscribers = get_all_subscribers()
        
        # Clear database tables
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM event_registrations")  # Clear registrations first (foreign key)
            cursor.execute("DELETE FROM subscribers")
            cursor.execute("DELETE FROM activity_log")
            cursor.execute("DELETE FROM events")  # Clear events if needed
            count = cursor.rowcount
            conn.commit()
            cursor.close()
            conn.close()
        else:
            return jsonify({"success": False, "error": "Database connection failed"}), 500
        
        brevo_cleared = 0
        
        # Optionally clear from Brevo (be very careful with this!)
        if clear_brevo and AUTO_SYNC_TO_BREVO and contacts_api:
            print("⚠️ CLEARING BREVO CONTACTS - This is irreversible!")
            log_activity("Starting Brevo contact deletion - IRREVERSIBLE!", "danger")
            
            import time
            for subscriber in subscribers:
                try:
                    email = subscriber['email']
                    result = remove_from_brevo_contact(email)
                    if result.get("success", False):
                        brevo_cleared += 1
                    time.sleep(0.1)  # Rate limiting
                except Exception as e:
                    log_error(f"Error clearing {email} from Brevo: {e}")
        
        log_activity(f"ALL DATA CLEARED - database: {len(subscribers)} subscribers, Brevo: {brevo_cleared} contacts", "danger")
        
        return jsonify({
            "success": True,
            "message": f"Cleared {len(subscribers)} subscribers from database" + 
                      (f" and {brevo_cleared} from Brevo" if clear_brevo else ""),
            "database_cleared": len(subscribers),
            "brevo_cleared": brevo_cleared,
            "note": "Database cleared. Brevo contacts " + ("also cleared" if clear_brevo else "not affected")
        })
        
    except Exception as e:
        error_msg = f"Error clearing data: {str(e)}"
        log_error(error_msg)
        return jsonify({"success": False, "error": error_msg}), 500

def add_to_brevo_list(email: str) -> dict:
    """Wrapper for backward compatibility - calls the enhanced function"""
    return add_to_brevo_contact(email, {'source': 'legacy'})

def remove_from_brevo_list(email: str) -> dict:
    """Wrapper for backward compatibility - calls the enhanced function"""
    return remove_from_brevo_contact(email)


# Continue with remaining routes...
@app.route('/send-campaign', methods=['POST'])
@csrf_required
def send_campaign():
    try:
        if not api_instance:
            return jsonify({"success": False, "error": "Email API not initialized"}), 500
        data = request.json or {}
        subject = data.get('subject', '(no subject)')
        body = data.get('body', '')
        from_name = data.get('fromName', SENDER_NAME)
        
        subscribers = get_all_subscribers()
        recipients = [sub['email'] for sub in subscribers]
        
        if not recipients:
            return jsonify({"success": False, "error": "No subscribers to send to"}), 400
        if not body:
            return jsonify({"success": False, "error": "Email body is required"}), 400
        
        to_list = [{"email": email} for email in recipients]
        email = sib_api_v3_sdk.SendSmtpEmail(  # type: ignore
            sender={"name": from_name, "email": SENDER_EMAIL},
            to=to_list,
            subject=subject,
            html_content=body,
        )
        api_response = api_instance.send_transac_email(email)  # type: ignore
        log_activity(f"Campaign sent to {len(recipients)} subscribers", "success")
        return jsonify({"success": True, "sent": len(recipients), "response": str(api_response)})
    except ApiException as e:  # type: ignore
        error_msg = f"Brevo API Error: {str(e)}"
        log_activity(f"Campaign send failed: {error_msg}", "danger")
        return jsonify({"success": False, "error": error_msg}), 500
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        log_activity(f"Campaign send failed: {error_msg}", "danger")
        print(f"Campaign error: {traceback.format_exc()}")
        return jsonify({"success": False, "error": error_msg}), 500

@app.route('/sync-status', methods=['GET'])
def sync_status():
    try:
        activities = get_activity_log(1)
        last_activity = activities[0] if activities else None
        return jsonify({
            "auto_sync_enabled": AUTO_SYNC_TO_BREVO,
            "brevo_list_id": BREVO_LIST_ID,
            "local_subscribers": len(get_all_subscribers()),
            "last_activity": last_activity,
        })
    except Exception as e:
        error_msg = f"Error getting sync status: {str(e)}"
        print(f"Sync status error: {traceback.format_exc()}")
        return jsonify({"success": False, "error": error_msg}), 500

@app.route('/admin')
@require_admin_auth  # Add this decorator
def admin_dashboard():
    # Your existing admin dashboard code
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        dashboard_path = os.path.join(here, 'dashboard.html')
        if os.path.exists(dashboard_path):
            with open(dashboard_path, 'r', encoding='utf-8') as f:
                return f.read()
        else:
            return (
                """
                <h1>Dashboard not found</h1>
                <p>Please place <code>dashboard.html</code> next to <code>backend.py</code>.</p>
                <p>Signup page: <a href="/signup">/signup</a></p>
                <p>API Health: <a href="/health">/health</a></p>
                """,
                404,
            )
    except Exception as e:
        print(f"Error serving admin dashboard: {e}")
        return (
            f"""
            <h1>Error Loading Dashboard</h1>
            <p>Error: {str(e)}</p>
            <p>You can access the signup page at <a href="/signup">/signup</a></p>
            """,
            500,
        )


# Replace your signup_page() function with this updated version:

@app.route('/signup')
def signup_page():
    signup_html = '''<!DOCTYPE html>
<html lang="en">
<head>
   <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Join SideQuest Newsletter</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: linear-gradient(135deg, #1a1a1a 0%, #2d2d2d 100%); color: #ffffff; min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }
        .container { background: linear-gradient(135deg, #2a2a2a 0%, #3a3a3a 100%); padding: 50px; border-radius: 20px; box-shadow: 0 20px 60px rgba(0,0,0,0.5); border: 2px solid #444; max-width: 600px; width: 100%; text-align: center; position: relative; overflow: hidden; }
        .container::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 6px; background: linear-gradient(90deg, #FFD700 0%, #FFA500 100%); }
        .logo { width: 60px; height: 60px; background: linear-gradient(135deg, #FFD700 0%, #FFA500 100%); border-radius: 12px; margin: 0 auto 20px; display: flex; align-items: center; justify-content: center; font-weight: 900; color: #1a1a1a; font-size: 18px; letter-spacing: -1px; box-shadow: 0 8px 25px rgba(255, 215, 0, 0.3); }
        h1 { font-size: 2.5rem; margin-bottom: 15px; background: linear-gradient(135deg, #FFD700 0%, #FFA500 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; font-weight: 800; }
        .subtitle { font-size: 1.2rem; margin-bottom: 30px; color: #cccccc; font-weight: 500; line-height: 1.5; }
        .form-container { margin: 30px 0; text-align: left; }
        .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-bottom: 20px; }
        .form-group { margin-bottom: 20px; }
        label { display: block; margin-bottom: 8px; color: #FFD700; font-weight: 600; font-size: 14px; }
        input[type="text"], input[type="email"] { width: 100%; padding: 16px 20px; border: 2px solid #444; border-radius: 12px; font-size: 16px; background: #1a1a1a; color: #ffffff; transition: all 0.3s ease; font-weight: 500; }
        input[type="text"]:focus, input[type="email"]:focus { outline: none; border-color: #FFD700; box-shadow: 0 0 0 4px rgba(255, 215, 0, 0.2); background: #2a2a2a; }
        .submit-btn { width: 100%; padding: 18px 25px; background: linear-gradient(135deg, #FFD700 0%, #FFA500 100%); border: none; border-radius: 12px; color: #1a1a1a; font-size: 16px; font-weight: 700; cursor: pointer; transition: all 0.3s ease; text-transform: uppercase; letter-spacing: 1px; box-shadow: 0 6px 20px rgba(255, 215, 0, 0.3); }
        .submit-btn:hover { transform: translateY(-2px); box-shadow: 0 10px 30px rgba(255, 215, 0, 0.4); }
        .submit-btn:disabled { opacity: 0.7; cursor: not-allowed; transform: none; }
        .message { margin-top: 20px; padding: 15px 20px; border-radius: 10px; font-weight: 500; opacity: 0; transition: all 0.3s ease; }
        .message.show { opacity: 1; }
        .message.success { background: linear-gradient(135deg, #00ff88 0%, #00cc6a 100%); color: #1a1a1a; border: 2px solid #00ff88; }
        .message.error { background: linear-gradient(135deg, #ff6b35 0%, #ff4757 100%); color: #ffffff; border: 2px solid #ff6b35; }
        .features { margin-top: 40px; text-align: left; }
        .features h3 { color: #FFD700; font-size: 1.1rem; margin-bottom: 15px; font-weight: 600; }
        .feature-list { list-style: none; padding: 0; }
        .feature-list li { padding: 8px 0; color: #cccccc; position: relative; padding-left: 25px; font-size: 14px; }
        .feature-list li::before { content: '⚡'; position: absolute; left: 0; color: #FFD700; font-weight: bold; }
        .footer-links { margin-top: 30px; padding-top: 20px; border-top: 1px solid #444; font-size: 12px; color: #888; text-align: center; }
        .footer-links a { color: #FFD700; text-decoration: none; margin: 0 10px; transition: color 0.3s ease; }
        .optional { color: #aaa; font-size: 12px; margin-left: 5px; }
        
        /* GDPR Consent Styling */
        .gdpr-consent { background: #2a2a2a; border: 2px solid #444; border-radius: 12px; padding: 20px; margin: 25px 0; }
        .consent-checkbox { display: flex; align-items: flex-start; gap: 12px; margin-bottom: 15px; }
        .consent-checkbox input[type="checkbox"] { margin-top: 2px; transform: scale(1.2); accent-color: #FFD700; }
        .consent-text { font-size: 0.9rem; line-height: 1.5; color: #cccccc; }
        .consent-text a { color: #FFD700; text-decoration: underline; }
        .gdpr-title { color: #FFD700; font-weight: 700; font-size: 1rem; margin-bottom: 15px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="logo">SQ</div>
        <h1>Join the Quest</h1>
        <p class="subtitle">Get exclusive gaming updates, events, and special offers delivered straight to your inbox!</p>
        
        <form class="form-container" id="signupForm">
            <div class="form-row">
                <div class="form-group">
                    <label for="firstName">First Name *</label>
                    <input type="text" id="firstName" name="firstName" required>
                </div>
                <div class="form-group">
                    <label for="lastName">Last Name *</label>
                    <input type="text" id="lastName" name="lastName" required>
                </div>
            </div>
            
            <div class="form-group">
                <label for="email">Email Address *</label>
                <input type="email" id="email" name="email" required>
            </div>
            
            <div class="form-group">
                <label for="gamingHandle">Gaming Handle <span class="optional">(optional)</span></label>
                <input type="text" id="gamingHandle" name="gamingHandle" placeholder="Your gamer tag">
            </div>
            
            <!-- GDPR Consent Section -->
            <div class="gdpr-consent">
                <div class="gdpr-title">Data Protection & Privacy</div>
                <div class="consent-checkbox">
                    <input type="checkbox" id="gdprConsent" name="gdprConsent" required>
                    <label for="gdprConsent" class="consent-text">
                        I consent to SideQuest Gaming storing and processing my personal data to send me gaming event updates, newsletters, and promotional communications. I understand that:
                        <ul style="margin: 10px 0; padding-left: 20px;">
                            <li>My data will be stored securely and used only for gaming-related communications</li>
                            <li>I can withdraw consent and unsubscribe at any time</li>
                            <li>I can request deletion of my data at any time</li>
                        </ul>
                        I have read and agree to the <a href="/privacy" target="_blank">Privacy Policy</a>.
                    </label>
                </div>
            </div>
            
            <button type="submit" class="submit-btn" id="submitBtn">Level Up Your Inbox</button>
        </form>
        
        <div id="message" class="message"></div>
        
        <div class="features">
            <h3>What You'll Get:</h3>
            <ul class="feature-list">
                <li>Early access to gaming events & tournaments</li>
                <li>Exclusive member discounts & offers</li>
                <li>Community night invitations</li>
                <li>New location openings & updates</li>
                <li>Gaming tips & industry news</li>
            </ul>
        </div>
        
        <div class="footer-links">
            <a href="https://sidequesthub.com">SideQuest Hub</a> • 
            <a href="/privacy">Privacy Policy</a> •
            <a href="mailto:marketing@sidequestcanterbury">Contact Us</a>
        </div>
    </div>
    
    <script>
   // Initialize CSRF manager for signup page
        class CSRFManager {
            constructor(apiBase = 'https://sidequest-newsletter-production.up.railway.app') {
                this.apiBase = apiBase;
                this.token = null;
                this.tokenExpiry = null;
            }

            async getToken() {
                if (this.token && this.isTokenValid()) {
                    return this.token;
                }
                return await this.fetchNewToken();
            }

            async fetchNewToken() {
                try {
                    const response = await fetch(`${this.apiBase}/api/csrf-token`, {
                        method: 'GET',
                        credentials: 'include',
                        headers: { 'Accept': 'application/json' }
                    });

                    if (!response.ok) throw new Error(`HTTP ${response.status}`);
                    
                    const data = await response.json();
                    if (data.success && data.csrf_token) {
                        this.token = data.csrf_token;
                        this.tokenExpiry = Date.now() + (data.expires_in * 1000) - 60000;
                        return this.token;
                    } else {
                        throw new Error(data.error || 'Failed to get CSRF token');
                    }
                } catch (error) {
                    console.error('CSRF token fetch failed:', error);
                    throw error;
                }
            }

            isTokenValid() {
                return this.token && this.tokenExpiry && Date.now() < this.tokenExpiry;
            }
        }

        const csrfManager = new CSRFManager();

        // Enhanced signup form handler with CSRF
        document.getElementById('signupForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            
            const firstName = document.getElementById('firstName').value.trim();
            const lastName = document.getElementById('lastName').value.trim();
            const email = document.getElementById('email').value.trim();
            const gamingHandle = document.getElementById('gamingHandle').value.trim();
            const gdprConsent = document.getElementById('gdprConsent').checked;
            const messageDiv = document.getElementById('message');
            const submitButton = document.getElementById('submitBtn');
            
            // Client-side validation
            if (!firstName || !lastName || !email) {
                messageDiv.className = 'message error show';
                messageDiv.innerHTML = 'Please fill in all required fields';
                return;
            }
            
            if (!gdprConsent) {
                messageDiv.className = 'message error show';
                messageDiv.innerHTML = 'Please accept our privacy policy to continue';
                return;
            }
            
            if (firstName.length < 2 || lastName.length < 2) {
                messageDiv.className = 'message error show';
                messageDiv.innerHTML = 'Names must be at least 2 characters long';
                return;
            }
            
            submitButton.innerHTML = 'Joining Quest...';
            submitButton.disabled = true;
            
            try {
                // Get CSRF token
                const csrfToken = await csrfManager.getToken();
                
                // Make secure API call with CSRF token
                const response = await fetch('/subscribe', {
                    method: 'POST',
                    credentials: 'include', // Important for session cookies
                    headers: { 
                        'Content-Type': 'application/json',
                        'X-CSRFToken': csrfToken // Add CSRF token header
                    },
                    body: JSON.stringify({ 
                        firstName, 
                        lastName, 
                        email, 
                        gamingHandle: gamingHandle || null,
                        gdprConsent: gdprConsent,
                        source: 'signup_page_gdpr' 
                    })
                });
                
                // Handle CSRF token expiry
                if (response.status === 403) {
                    const errorData = await response.json().catch(() => ({}));
                    if (errorData.code === 'CSRF_TOKEN_INVALID' || errorData.code === 'CSRF_TOKEN_MISSING') {
                        console.log('CSRF token expired, refreshing...');
                        csrfManager.token = null; // Clear old token
                        const newToken = await csrfManager.getToken();
                        
                        // Retry with new token
                        const retryResponse = await fetch('/subscribe', {
                            method: 'POST',
                            credentials: 'include',
                            headers: { 
                                'Content-Type': 'application/json',
                                'X-CSRFToken': newToken
                            },
                            body: JSON.stringify({ 
                                firstName, lastName, email, 
                                gamingHandle: gamingHandle || null,
                                gdprConsent, source: 'signup_page_gdpr' 
                            })
                        });
                        
                        const result = await retryResponse.json();
                        handleSignupResponse(result, firstName);
                        return;
                    }
                }
                
                const result = await response.json();
                handleSignupResponse(result, firstName);
                
            } catch (error) {
                console.error('Network error:', error);
                messageDiv.className = 'message error show';
                messageDiv.innerHTML = 'Network error. Please check your connection and try again.';
            } finally {
                submitButton.innerHTML = 'Level Up Your Inbox';
                submitButton.disabled = false;
            }
        });

        function handleSignupResponse(result, firstName) {
            const messageDiv = document.getElementById('message');
            
            if (result.success) {
                messageDiv.className = 'message success show';
                messageDiv.innerHTML = `🎉 Welcome to the quest, ${firstName}! Check your email for confirmation.`;
                document.getElementById('signupForm').reset();
            } else {
                messageDiv.className = 'message error show';
                messageDiv.innerHTML = result.error || 'Something went wrong. Please try again.';
            }
        }
    </script>
</body>
</html>'''
    response = make_response(signup_html)
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache' 
    response.headers['Expires'] = '0'
    return response

# Add this route to your backend.py file after the existing /signup route

@app.route('/signup/event/<int:event_id>')
@limiter.limit("3 per hour")
def event_signup_page(event_id):
    """Event-specific signup page"""
    try:
        # Get event details
        conn = get_db_connection()
        if not conn:
            return "Database connection failed", 500
            
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                e.*,
                COUNT(r.id) as registration_count,
                CASE 
                    WHEN e.capacity > 0 THEN e.capacity - COUNT(r.id)
                    ELSE NULL
                END as spots_available
            FROM events e
            LEFT JOIN event_registrations r ON e.id = r.event_id
            WHERE e.id = %s
            GROUP BY e.id
        """, (event_id,))
        
        event_data = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if not event_data:
            return "Event not found", 404
        
        # Convert to dictionary for easier access
        event = dict(event_data)
        
        # Format date/time
        event_date = event['date_time']
        if event_date:
            formatted_date = event_date.strftime('%A, %B %d, %Y')
            formatted_time = event_date.strftime('%I:%M %p')
        else:
            formatted_date = "TBD"
            formatted_time = "TBD"
        
        # Check if event is full
        is_full = event['capacity'] > 0 and event['registration_count'] >= event['capacity']
        
        # Generate the HTML for event-specific signup
        event_signup_html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Register for {event['title']} - SideQuest</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
            background: linear-gradient(135deg, #1a1a1a 0%, #2d2d2d 100%); 
            color: #ffffff; 
            min-height: 100vh; 
            display: flex; 
            align-items: center; 
            justify-content: center; 
            padding: 20px; 
        }}
        
        .container {{ 
            background: linear-gradient(135deg, #2a2a2a 0%, #3a3a3a 100%); 
            padding: 40px; 
            border-radius: 20px; 
            box-shadow: 0 20px 60px rgba(0,0,0,0.5); 
            border: 2px solid #FFD700; 
            max-width: 600px; 
            width: 100%; 
            text-align: center; 
            position: relative; 
            overflow: hidden; 
        }}
        
        .container::before {{ 
            content: ''; 
            position: absolute; 
            top: 0; 
            left: 0; 
            right: 0; 
            height: 6px; 
            background: linear-gradient(90deg, #FFD700 0%, #FFA500 100%); 
        }}
        
        .logo {{ 
            width: 60px; 
            height: 60px; 
            background: linear-gradient(135deg, #FFD700 0%, #FFA500 100%); 
            border-radius: 12px; 
            margin: 0 auto 20px; 
            display: flex; 
            align-items: center; 
            justify-content: center; 
            font-weight: 900; 
            color: #1a1a1a; 
            font-size: 18px; 
        }}
        
        .event-badge {{
            display: inline-block;
            padding: 8px 16px;
            border-radius: 20px;
            font-size: 0.8rem;
            font-weight: 700;
            text-transform: uppercase;
            margin-bottom: 20px;
        }}
        
        .badge-tournament {{ background: #FF6B35; color: white; }}
        .badge-game_night {{ background: #4ECDC4; color: #1a1a1a; }}
        .badge-special {{ background: #8B5CF6; color: white; }}
        .badge-birthday {{ background: #FF69B4; color: white; }}
        
        h1 {{ 
            font-size: 2rem; 
            margin-bottom: 10px; 
            background: linear-gradient(135deg, #FFD700 0%, #FFA500 100%); 
            -webkit-background-clip: text; 
            -webkit-text-fill-color: transparent; 
            background-clip: text; 
            font-weight: 800; 
        }}
        
        .event-details {{ 
            background: #1a1a1a; 
            border-radius: 15px; 
            padding: 25px; 
            margin: 25px 0; 
            text-align: left; 
        }}
        
        .detail-row {{ 
            display: flex; 
            justify-content: space-between; 
            margin-bottom: 12px; 
            padding-bottom: 8px; 
            border-bottom: 1px solid #444; 
        }}
        
        .detail-row:last-child {{ border-bottom: none; margin-bottom: 0; }}
        
        .detail-label {{ 
            color: #FFD700; 
            font-weight: 600; 
        }}
        
        .detail-value {{ 
            color: #ffffff; 
            font-weight: 500; 
        }}
        
        .form-container {{ 
            margin: 30px 0; 
            text-align: left; 
        }}
        
        .form-row {{ 
            display: grid; 
            grid-template-columns: 1fr 1fr; 
            gap: 15px; 
            margin-bottom: 20px; 
        }}
        
        .form-group {{ 
            margin-bottom: 20px; 
        }}
        
        label {{ 
            display: block; 
            margin-bottom: 8px; 
            color: #FFD700; 
            font-weight: 600; 
            font-size: 14px; 
        }}
        
        input[type="text"], input[type="email"] {{ 
            width: 100%; 
            padding: 16px 20px; 
            border: 2px solid #444; 
            border-radius: 12px; 
            font-size: 16px; 
            background: #1a1a1a; 
            color: #ffffff; 
            transition: all 0.3s ease; 
            font-weight: 500; 
        }}
        
        input[type="text"]:focus, input[type="email"]:focus {{ 
            outline: none; 
            border-color: #FFD700; 
            box-shadow: 0 0 0 4px rgba(255, 215, 0, 0.2); 
            background: #2a2a2a; 
        }}
        
        /* GDPR Consent Styling */
        .gdpr-consent {{
            background: #2a2a2a;
            border: 2px solid #444;
            border-radius: 12px;
            padding: 20px;
            margin: 20px 0;
        }}

        .gdpr-title {{
            color: #FFD700;
            font-weight: 700;
            font-size: 1rem;
            margin-bottom: 15px;
        }}

        .consent-checkbox {{
            display: flex;
            align-items: flex-start;
            gap: 12px;
        }}

        .consent-checkbox input[type="checkbox"] {{
            margin-top: 2px;
            transform: scale(1.2);
            accent-color: #FFD700;
        }}

        .consent-text {{
            font-size: 0.9rem;
            line-height: 1.5;
            color: #cccccc;
            font-weight: normal;
        }}
        
        .submit-btn {{ 
            width: 100%; 
            padding: 18px 25px; 
            background: linear-gradient(135deg, #FFD700 0%, #FFA500 100%); 
            border: none; 
            border-radius: 12px; 
            color: #1a1a1a; 
            font-size: 16px; 
            font-weight: 700; 
            cursor: pointer; 
            transition: all 0.3s ease; 
            text-transform: uppercase; 
            letter-spacing: 1px; 
            box-shadow: 0 6px 20px rgba(255, 215, 0, 0.3); 
        }}
        
        .submit-btn:hover {{ 
            transform: translateY(-2px); 
            box-shadow: 0 10px 30px rgba(255, 215, 0, 0.4); 
        }}
        
        .submit-btn:disabled {{ 
            opacity: 0.7; 
            cursor: not-allowed; 
            transform: none; 
        }}
        
        .message {{ 
            margin-top: 20px; 
            padding: 15px 20px; 
            border-radius: 10px; 
            font-weight: 500; 
            opacity: 0; 
            transition: all 0.3s ease; 
        }}
        
        .message.show {{ opacity: 1; }}
        
        .message.success {{ 
            background: linear-gradient(135deg, #00ff88 0%, #00cc6a 100%); 
            color: #1a1a1a; 
            border: 2px solid #00ff88; 
        }}
        
        .message.error {{ 
            background: linear-gradient(135deg, #ff6b35 0%, #ff4757 100%); 
            color: #ffffff; 
            border: 2px solid #ff6b35; 
        }}
        
        .capacity-warning {{
            background: linear-gradient(135deg, #ff6b35 0%, #ff4757 100%);
            color: white;
            padding: 20px;
            border-radius: 12px;
            margin-bottom: 20px;
            text-align: center;
            font-weight: 600;
        }}
        
        .spots-remaining {{
            background: linear-gradient(135deg, #FFD700 0%, #FFA500 100%);
            color: #1a1a1a;
            padding: 10px 20px;
            border-radius: 20px;
            font-weight: 700;
            font-size: 0.9rem;
            display: inline-block;
            margin-bottom: 20px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="logo">SQ</div>
        
        <span class="event-badge badge-{event['event_type']}">{event['event_type'].replace('_', ' ').upper()}</span>
        
        <h1>{event['title']}</h1>
        
        {f'<div class="spots-remaining">⚡ Only {event["spots_available"]} spots left!</div>' if event['capacity'] > 0 and event['spots_available'] <= 5 and event['spots_available'] > 0 else ''}
        
        {'<div class="capacity-warning">❌ This event is currently full. You can still register for the waiting list.</div>' if is_full else ''}
        
        <div class="event-details">
            <div class="detail-row">
                <span class="detail-label">📅 Date</span>
                <span class="detail-value">{formatted_date}</span>
            </div>
            <div class="detail-row">
                <span class="detail-label">🕒 Time</span>
                <span class="detail-value">{formatted_time}</span>
            </div>
            {f'<div class="detail-row"><span class="detail-label">🎮 Game</span><span class="detail-value">{event["game_title"]}</span></div>' if event.get('game_title') else ''}
            <div class="detail-row">
                <span class="detail-label">👥 Capacity</span>
                <span class="detail-value">{f"{event['registration_count']}/{event['capacity']}" if event['capacity'] > 0 else f"{event['registration_count']} registered"}</span>
            </div>
            <div class="detail-row">
                <span class="detail-label">💰 Entry Fee</span>
                <span class="detail-value">{"£{:.2f}".format(event['entry_fee']) if event['entry_fee'] > 0 else 'FREE'}</span>
            </div>
            {f'<div class="detail-row"><span class="detail-label">📝 Description</span><span class="detail-value">{event["description"]}</span></div>' if event.get('description') else ''}
        </div>
        
        <form class="form-container" id="registrationForm">
            <div class="form-row">
                <div class="form-group">
                    <label for="firstName">First Name *</label>
                    <input type="text" id="firstName" name="firstName" required>
                </div>
                <div class="form-group">
                    <label for="lastName">Last Name *</label>
                    <input type="text" id="lastName" name="lastName" required>
                </div>
            </div>
            
            <div class="form-group">
                <label for="email">Email Address *</label>
                <input type="email" id="email" name="email" required>
            </div>
            
            <div class="form-group">
                <label for="playerName">Player/Gamer Name</label>
                <input type="text" id="playerName" name="playerName" placeholder="Your gaming handle or preferred name">
            </div>
            
            <!-- GDPR Consent Section -->
            <div class="gdpr-consent">
                <div class="gdpr-title">Newsletter Subscription (Optional)</div>
                <div class="consent-checkbox">
                    <input type="checkbox" id="emailConsent" name="emailConsent">
                    <label for="emailConsent" class="consent-text">
                        I want to receive gaming event updates, newsletters, and promotional communications from SideQuest Gaming. 
                        I understand I can unsubscribe at any time.
                        <br><small>Note: This is separate from your event registration and is optional.</small>
                    </label>
                </div>
            </div>
            
            <button type="submit" class="submit-btn" id="submitBtn">
                {'🎯 Join Waiting List' if is_full else '🎮 Register for Event'}
            </button>
        </form>
        
        <div id="message" class="message"></div>
    </div>
    
    <script>
        document.getElementById('registrationForm').addEventListener('submit', async (e) => {{
            console.log('🔍 Form submission started');
            e.preventDefault();
            
            const firstName = document.getElementById('firstName').value.trim();
            const lastName = document.getElementById('lastName').value.trim();
            const email = document.getElementById('email').value.trim();
            const playerName = document.getElementById('playerName').value.trim() || `${{firstName}} ${{lastName}}`;
            const emailConsent = document.getElementById('emailConsent').checked; // Add consent checkbox
            
            console.log('🔍 Form data collected:', {{ firstName, lastName, email, playerName, emailConsent }});
            
            const messageDiv = document.getElementById('message');
            const submitButton = document.getElementById('submitBtn');
            
            if (!firstName || !lastName || !email) {{
                console.log('❌ Validation failed - missing required fields');
                messageDiv.className = 'message error show';
                messageDiv.innerHTML = '❌ Please fill in all required fields';
                return;
            }}
            
            console.log('✅ Validation passed, making API request...');
            
            submitButton.innerHTML = 'Registering...';
            submitButton.disabled = true;
            
            try {{
                const requestUrl = '/api/events/{event_id}/register-public';
                console.log('🔍 Request URL:', requestUrl);
                
                const requestData = {{ 
                    email, 
                    player_name: playerName,
                    first_name: firstName,
                    last_name: lastName,
                    email_consent: emailConsent  // Include consent in request
                }};
                console.log('🔍 Request data:', requestData);
                
                const response = await fetch(requestUrl, {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(requestData)
                }});
                
                console.log('🔍 Response status:', response.status);
                console.log('🔍 Response ok:', response.ok);
                
                const data = await response.json();
                console.log('🔍 Response data:', data);
                
                if (data.success) {{
                    let successMessage = `🎉 Registration successful!<br>
                        <strong>Confirmation Code: ${{data.confirmation_code}}</strong><br>
                        Please save this code and bring it to the event.`;
                    
                    // Add Discord invitation for tournaments
                    if (data.show_discord && data.discord_invite) {{
                        successMessage += `<br><br>
                            <div style="background: #5865F2; color: white; padding: 15px; border-radius: 10px; margin-top: 15px;">
                                <strong>🎮 Join our Discord community!</strong><br>
                                <a href="${{data.discord_invite}}" target="_blank" style="color: #fff; text-decoration: underline; font-weight: bold;">
                                    ${{data.discord_invite}}
                                </a><br>
                                <small>Connect with other tournament players and get updates!</small>
                            </div>`;
                    }}
                    
                    messageDiv.className = 'message success show';
                    messageDiv.innerHTML = successMessage;
                    
                    document.getElementById('registrationForm').reset();
                    submitButton.innerHTML = '✅ Registered!';
                    
                    console.log('✅ Success message displayed');
                }} else {{
                    console.log('❌ Registration failed:', data.error);
                    messageDiv.className = 'message error show';
                    messageDiv.innerHTML = '❌ ' + (data.error || 'Registration failed');
                    submitButton.innerHTML = '{'🎯 Join Waiting List' if is_full else '🎮 Register for Event'}';
                    submitButton.disabled = false;
                }}
            }} catch (error) {{
                console.error('❌ Network error:', error);
                messageDiv.className = 'message error show';
                messageDiv.innerHTML = '❌ Network error. Please try again later.';
                submitButton.innerHTML = '{'🎯 Join Waiting List' if is_full else '🎮 Register for Event'}';
                submitButton.disabled = false;
            }}
        }});
    </script>
</body>
</html>'''
        
        return event_signup_html
        
    except Exception as e:
        print(f"Error in event signup page: {e}")
        print(f"Traceback: {traceback.format_exc()}")
        return f"Error loading event: {str(e)}", 500

# Add the login template
LOGIN_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SideQuest Admin Login</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #1a1a1a 0%, #2d2d2d 100%);
            color: #ffffff;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .login-container {
            background: linear-gradient(135deg, #2a2a2a 0%, #3a3a3a 100%);
            padding: 40px;
            border-radius: 15px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.5);
            border: 2px solid #FFD700;
            max-width: 400px;
            width: 100%;
            text-align: center;
        }
        .logo {
            width: 60px;
            height: 60px;
            background: linear-gradient(135deg, #FFD700 0%, #FFA500 100%);
            border-radius: 12px;
            margin: 0 auto 20px;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #1a1a1a;
            font-weight: 900;
            font-size: 18px;
        }
        h1 {
            color: #FFD700;
            margin-bottom: 30px;
            font-size: 1.8rem;
        }
        .form-group {
            margin-bottom: 25px;
            text-align: left;
        }
        label {
            display: block;
            margin-bottom: 8px;
            color: #FFD700;
            font-weight: 600;
        }
        input {
            width: 100%;
            padding: 14px 18px;
            border: 2px solid #444;
            border-radius: 10px;
            background: #1a1a1a;
            color: #ffffff;
            font-size: 16px;
            transition: all 0.3s ease;
        }
        input:focus {
            outline: none;
            border-color: #FFD700;
            box-shadow: 0 0 0 3px rgba(255, 215, 0, 0.2);
        }
        .btn {
            width: 100%;
            padding: 16px;
            background: linear-gradient(135deg, #FFD700 0%, #FFA500 100%);
            color: #1a1a1a;
            border: none;
            border-radius: 10px;
            font-weight: 700;
            font-size: 16px;
            cursor: pointer;
            transition: all 0.3s ease;
            text-transform: uppercase;
        }
        .btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 25px rgba(255, 215, 0, 0.4);
        }
        .error {
            background: linear-gradient(135deg, #ff6b35 0%, #ff4757 100%);
            color: #ffffff;
            padding: 12px;
            border-radius: 8px;
            margin-bottom: 20px;
            font-weight: 500;
        }
        .footer {
            margin-top: 30px;
            color: #aaa;
            font-size: 0.9rem;
        }
    </style>
</head>
<body>
    <div class="login-container">
        <div class="logo">SQ</div>
        <h1>Admin Login</h1>
        
        ERROR_PLACEHOLDER
        
        <form method="POST">
            <div class="form-group">
                <label for="password">Password</label>
                <input type="password" id="password" name="password" required autofocus>
            </div>
            <button type="submit" class="btn">🔓 Access Dashboard</button>
        </form>
        
        <div class="footer">
            <p>SideQuest Gaming Cafe</p>
            <p>Canterbury Admin Panel</p>
        </div>
    </div>
</body>
</html>
'''

# Error handlers
@app.errorhandler(400)
def bad_request(error):
    return jsonify({"success": False, "error": "Bad request", "message": str(error)}), 400

@app.errorhandler(401)
def unauthorized(error):
    return jsonify({"success": False, "error": "Unauthorized", "message": str(error)}), 401

@app.errorhandler(403)
def forbidden(error):
    return jsonify({"success": False, "error": "Forbidden", "message": str(error)}), 403

@app.errorhandler(404)
def not_found(error):
    return jsonify({"success": False, "error": "Not found", "message": str(error)}), 404

@app.errorhandler(405)
def method_not_allowed(error):
    return jsonify({"success": False, "error": "Method not allowed", "message": str(error)}), 405

@app.errorhandler(429)
def too_many_requests(error):
    return jsonify({"success": False, "error": "Too many requests", "message": "Please try again later"}), 429

@app.errorhandler(500)
def internal_server_error(error):
    print(f"Server Error: {error}")
    print(f"Traceback: {traceback.format_exc()}")
    return jsonify({"success": False, "error": "Internal server error", "message": "An unexpected error occurred"}), 500

@app.errorhandler(Exception)
def handle_exception(error):
    print(f"Unhandled Exception: {error}")
    print(f"Traceback: {traceback.format_exc()}")
    return jsonify({"success": False, "error": "Server error", "message": "An unexpected error occurred"}), 500

# =============================
# Database Connection
# =============================

def get_db_connection():
    """Get PostgreSQL connection using Railway's DATABASE_URL"""
    try:
        # Railway provides DATABASE_URL automatically
        database_url = os.environ.get('DATABASE_URL')
        if database_url:
            # Railway uses 'postgresql://', but psycopg2 needs 'postgres://'
            if database_url.startswith('postgres://'):
                database_url = database_url.replace('postgres://', 'postgresql://', 1)
            conn = psycopg2.connect(database_url, cursor_factory=RealDictCursor)
        else:
            # Fallback for local development
            conn = psycopg2.connect(
                host=os.environ.get('PGHOST', 'localhost'),
                port=os.environ.get('PGPORT', 5432),
                database=os.environ.get('PGDATABASE', 'sidequest'),
                user=os.environ.get('PGUSER', 'postgres'),
                password=os.environ.get('PGPASSWORD', ''),
                cursor_factory=RealDictCursor
            )
        return conn
    except Exception as e:
        log_error(f"Database connection error: {e}")
        return None

def execute_query(query, params=None, fetch=True):
    """Execute a database query with error handling"""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        if not conn:
            return None
        cursor = conn.cursor()
        cursor.execute(query, params)
        
        if fetch:
            result = cursor.fetchall()
        else:
            conn.commit()
            result = cursor.rowcount
            
        return result
    except Exception as e:
        log_error(f"Query execution error: {e}")
        if conn:
            conn.rollback()
        return None
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def execute_query_one(query, params=None):
    """Execute a query and return the first result"""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        if not conn:
            log_error("Failed to get database connection")
            return None
            
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        log_activity(f"Executing query: {query[:100]}..." if len(query) > 100 else query, "info")
        log_activity(f"With params: {params}", "info")
        
        cursor.execute(query, params)
        
        # For INSERT/UPDATE/DELETE with RETURNING, we need to fetch the result
        if query.strip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) and 'RETURNING' in query.upper():
            result = cursor.fetchone()
            conn.commit()  # Important: commit the transaction
            log_activity(f"Query executed successfully, returning: {result}", "success")
            return dict(result) if result else None
        
        # For SELECT queries
        elif query.strip().upper().startswith('SELECT'):
            result = cursor.fetchone()
            log_activity(f"Query executed successfully, returning: {result}", "success")
            return dict(result) if result else None
        
        # For other queries without RETURNING
        else:
            conn.commit()
            log_activity(f"Query executed successfully, no return data", "success")
            return {"affected_rows": cursor.rowcount}
            
    except psycopg2.Error as e:
        log_error(f"Database error in execute_query_one: {e}")
        log_error(f"Query was: {query}")
        log_error(f"Params were: {params}")
        if conn:
            conn.rollback()
        return None
        
    except Exception as e:
        log_error(f"Unexpected error in execute_query_one: {e}")
        log_error(f"Full traceback: {traceback.format_exc()}")
        if conn:
            conn.rollback()
        return None
        
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# =============================
# Event Management Routes
# =============================

@app.route('/api/events', methods=['GET'])
def get_events():
    """Get all events with optional filtering"""
    try:
        event_type = request.args.get('type', 'all')
        status = request.args.get('status', 'all')
        upcoming_only = request.args.get('upcoming', 'false').lower() == 'true'
        
        query = """
            SELECT 
                e.*,
                COUNT(r.id) as registration_count,
                CASE 
                    WHEN e.capacity > 0 THEN e.capacity - COUNT(r.id)
                    ELSE NULL
                END as spots_available
            FROM events e
            LEFT JOIN event_registrations r ON e.id = r.event_id
            WHERE 1=1
        """
        params = []
        
        if event_type != 'all':
            query += " AND e.event_type = %s"
            params.append(event_type)
            
        if status != 'all':
            query += " AND e.status = %s"
            params.append(status)
            
        if upcoming_only:
            query += " AND e.date_time > CURRENT_TIMESTAMP"
            
        query += " GROUP BY e.id ORDER BY e.date_time ASC"
        
        events = execute_query(query, params)
        
        if events is None:
            return jsonify({"success": False, "error": "Database error"}), 500
            
        # Convert datetime objects to ISO format
        for event in events:
            if event['date_time']:
                event['date_time'] = event['date_time'].isoformat()
            if event['end_time']:
                event['end_time'] = event['end_time'].isoformat()
            if event['created_at']:
                event['created_at'] = event['created_at'].isoformat()
                
        log_activity(f"Retrieved {len(events)} events", "info")
        
        return jsonify({
            "success": True,
            "events": events,
            "count": len(events)
        })
        
    except Exception as e:
        log_error(f"Error getting events: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/events/<int:event_id>', methods=['GET'])
def get_event(event_id):
    """Get single event with registration details"""
    try:
        query = """
            SELECT 
                e.*,
                COUNT(r.id) as registration_count,
                CASE 
                    WHEN e.capacity > 0 THEN e.capacity - COUNT(r.id)
                    ELSE NULL
                END as spots_available,
                ARRAY_AGG(
                    CASE WHEN r.id IS NOT NULL THEN
                        json_build_object(
                            'email', r.subscriber_email,
                            'player_name', r.player_name,
                            'registered_at', r.registered_at,
                            'attended', r.attended
                        )
                    ELSE NULL END
                ) FILTER (WHERE r.id IS NOT NULL) as registrations
            FROM events e
            LEFT JOIN event_registrations r ON e.id = r.event_id
            WHERE e.id = %s
            GROUP BY e.id
        """
        
        event = execute_query_one(query, (event_id,))
        
        if not event:
            return jsonify({"success": False, "error": "Event not found"}), 404
            
        # Convert datetime objects
        if event['date_time']:
            event['date_time'] = event['date_time'].isoformat()
        if event['end_time']:
            event['end_time'] = event['end_time'].isoformat()
        if event['created_at']:
            event['created_at'] = event['created_at'].isoformat()
            
        return jsonify({
            "success": True,
            "event": event
        })
        
    except Exception as e:
        log_error(f"Error getting event {event_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/events/<int:event_id>/cancel', methods=['POST'])
@limiter.limit("3 per hour")  # Prevent abuse
def cancel_event_registration(event_id):
    """Cancel an event registration"""
    try:
        data = request.json or {}
        email = sanitize_email(data.get('email', ''))
        confirmation_code = sanitize_text_input(data.get('confirmation_code', ''), 50)
        reason = sanitize_text_input(data.get('reason', ''), 500)
        
        if not email or not confirmation_code:
            return jsonify({"success": False, "error": "Email and confirmation code are required"}), 400
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"success": False, "error": "Database connection failed"}), 500
            
        cursor = conn.cursor()
        
        # Find registration
        cursor.execute("""
            SELECT r.id, r.subscriber_email, r.player_name, r.cancelled_at, e.title, e.date_time
            FROM event_registrations r
            JOIN events e ON r.event_id = e.id
            WHERE r.event_id = %s AND r.subscriber_email = %s AND r.confirmation_code = %s
        """, (event_id, email, confirmation_code))
        
        registration = cursor.fetchone()
        
        if not registration:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "error": "Invalid confirmation code or email"}), 400
        
        reg_dict = dict(registration)
        
        if reg_dict['cancelled_at']:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "error": "Registration already cancelled"}), 400
        
        # Process cancellation
        cursor.execute("""
            UPDATE event_registrations 
            SET cancelled_at = NOW(), cancellation_reason = %s
            WHERE id = %s
        """, (reason, reg_dict['id']))
        
        # Log to activity_log using your existing structure
        log_activity(f"Cancelled registration: {reg_dict['subscriber_email']} for {reg_dict['title']}", "warning")
        
        conn.commit()
        cursor.close()
        conn.close()
        
        # Send cancellation confirmation email using your existing email system
        send_cancellation_confirmation_email(
            email=reg_dict['subscriber_email'],
            player_name=reg_dict['player_name'],
            event_title=reg_dict['title'],
            event_date=reg_dict['date_time']
        )
        
        return jsonify({
            "success": True,
            "message": "Registration cancelled successfully",
            "event_title": reg_dict['title']
        })
        
    except Exception as e:
        if conn:
            conn.rollback()
        log_error(f"Error cancelling registration: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500
        
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route('/cancel')
def cancellation_page():
    """Public cancellation form page"""
    confirmation_code = request.args.get('code', '')
    
    cancellation_html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cancel Registration - SideQuest Canterbury</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #1a1a1a 0%, #2d2d2d 100%);
            color: #ffffff;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        
        .container {{
            background: linear-gradient(135deg, #2a2a2a 0%, #3a3a3a 100%);
            padding: 40px;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.5);
            border: 2px solid #ff6b35;
            max-width: 500px;
            width: 100%;
            text-align: center;
        }}
        
        .logo {{
            width: 60px;
            height: 60px;
            background: linear-gradient(135deg, #FFD700 0%, #FFA500 100%);
            border-radius: 12px;
            margin: 0 auto 20px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 900;
            color: #1a1a1a;
            font-size: 18px;
        }}
        
        h1 {{
            color: #ff6b35;
            margin-bottom: 20px;
            font-size: 2rem;
            font-weight: 800;
        }}
        
        .form-group {{
            margin-bottom: 20px;
            text-align: left;
        }}
        
        label {{
            display: block;
            margin-bottom: 8px;
            color: #FFD700;
            font-weight: 600;
        }}
        
        input, textarea {{
            width: 100%;
            padding: 16px 20px;
            border: 2px solid #444;
            border-radius: 12px;
            font-size: 16px;
            background: #1a1a1a;
            color: #ffffff;
            transition: all 0.3s ease;
        }}
        
        input:focus, textarea:focus {{
            outline: none;
            border-color: #FFD700;
            box-shadow: 0 0 0 4px rgba(255, 215, 0, 0.2);
        }}
        
        .btn {{
            width: 100%;
            padding: 18px 25px;
            background: linear-gradient(135deg, #ff6b35 0%, #ff4757 100%);
            border: none;
            border-radius: 12px;
            color: #ffffff;
            font-size: 16px;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.3s ease;
            text-transform: uppercase;
        }}
        
        .btn:hover {{
            transform: translateY(-2px);
            box-shadow: 0 10px 30px rgba(255, 107, 53, 0.4);
        }}
        
        .message {{
            margin-top: 20px;
            padding: 15px 20px;
            border-radius: 10px;
            font-weight: 500;
            opacity: 0;
            transition: all 0.3s ease;
        }}
        
        .message.show {{ opacity: 1; }}
        
        .message.success {{
            background: linear-gradient(135deg, #00ff88 0%, #00cc6a 100%);
            color: #1a1a1a;
        }}
        
        .message.error {{
            background: linear-gradient(135deg, #ff6b35 0%, #ff4757 100%);
            color: #ffffff;
        }}
        
        .back-link {{
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #444;
        }}
        
        .back-link a {{
            color: #FFD700;
            text-decoration: none;
            font-weight: 600;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="logo">SQ</div>
        <h1>Cancel Registration</h1>
        
        <div id="message" class="message"></div>
        
        <form id="cancellationForm">
            <div class="form-group">
                <label for="email">Email Address *</label>
                <input type="email" id="email" name="email" required>
            </div>
            
            <div class="form-group">
                <label for="confirmation_code">Confirmation Code *</label>
                <input type="text" id="confirmation_code" name="confirmation_code" 
                       value="{confirmation_code}" required 
                       placeholder="Found in your confirmation email">
            </div>
            
            <div class="form-group">
                <label for="reason">Reason for Cancellation (Optional)</label>
                <textarea id="reason" name="reason" rows="3" 
                          placeholder="Help us improve by sharing why you need to cancel"></textarea>
            </div>
            
            <button type="submit" class="btn">Cancel My Registration</button>
        </form>
        
        <div class="back-link">
            <a href="/">← Back to SideQuest</a>
        </div>
    </div>

    <script>
        document.getElementById('cancellationForm').addEventListener('submit', async function(e) {{
            e.preventDefault();
            
            const formData = new FormData(e.target);
            const data = {{
                email: formData.get('email'),
                confirmation_code: formData.get('confirmation_code'),
                reason: formData.get('reason')
            }};
            
            const messageDiv = document.getElementById('message');
            
            try {{
                const response = await fetch('/api/cancel-registration', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(data)
                }});
                
                const result = await response.json();
                
                if (result.success) {{
                    messageDiv.className = 'message success show';
                    messageDiv.innerHTML = '✅ ' + result.message + '<br><small>You should receive a confirmation email shortly.</small>';
                    e.target.reset();
                }} else {{
                    messageDiv.className = 'message error show';
                    messageDiv.innerHTML = '❌ ' + (result.error || 'Something went wrong');
                }}
            }} catch (error) {{
                messageDiv.className = 'message error show';
                messageDiv.innerHTML = '❌ Network error. Please try again.';
            }}
        }});
    </script>
</body>
</html>'''
    
    response = make_response(cancellation_html)
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response

@app.route('/api/cancel-registration', methods=['POST'])
@limiter.limit("5 per hour")
def cancel_registration_api():
    """API endpoint for cancelling registrations"""
    try:
        data = request.json or {}
        email = sanitize_email(data.get('email', ''))
        confirmation_code = sanitize_text_input(data.get('confirmation_code', ''), 50)
        reason = sanitize_text_input(data.get('reason', ''), 500)
        
        if not email or not confirmation_code:
            return jsonify({"success": False, "error": "Email and confirmation code are required"}), 400
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"success": False, "error": "Database connection failed"}), 500
            
        cursor = conn.cursor()
        
        # Find registration
        cursor.execute("""
            SELECT r.id, r.subscriber_email, r.player_name, r.cancelled_at, e.title, e.date_time, e.id as event_id
            FROM event_registrations r
            JOIN events e ON r.event_id = e.id
            WHERE r.subscriber_email = %s AND r.confirmation_code = %s
        """, (email, confirmation_code))
        
        registration = cursor.fetchone()
        
        if not registration:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "error": "Invalid confirmation code or email"}), 400
        
        reg_dict = dict(registration)
        
        if reg_dict['cancelled_at']:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "error": "Registration already cancelled"}), 400
        
        # Process cancellation
        cursor.execute("""
            UPDATE event_registrations 
            SET cancelled_at = NOW(), cancellation_reason = %s
            WHERE id = %s
        """, (reason, reg_dict['id']))
        
        # Log to activity_log
        log_activity(f"Registration cancelled: {reg_dict['subscriber_email']} for {reg_dict['title']} - Reason: {reason}", "warning")
        
        conn.commit()
        cursor.close()
        conn.close()
        
        # Send cancellation confirmation email
        send_cancellation_confirmation_email(
            email=reg_dict['subscriber_email'],
            player_name=reg_dict['player_name'],
            event_title=reg_dict['title'],
            event_date=reg_dict['date_time']
        )
        
        return jsonify({
            "success": True,
            "message": "Registration cancelled successfully"
        })
        
    except Exception as e:
        if 'conn' in locals():
            conn.rollback()
        log_error(f"Error cancelling registration: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500
        
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'conn' in locals() and conn:
            conn.close()

def send_cancellation_confirmation_email(email, player_name, event_title, event_date):
    """Send cancellation confirmation email using your existing Brevo setup"""
    if not api_instance:
        log_error("Brevo API not initialized")
        return False
        
    try:
        event_date_str = event_date.strftime('%A, %B %d, %Y at %I:%M %p')
        
        subject = f"Cancellation Confirmed - {event_title}"
        
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cancellation Confirmed</title>
</head>
<body style="font-family: Arial, sans-serif; background-color: #f4f4f4; margin: 0; padding: 20px;">
    <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
        
        <!-- Header -->
        <div style="background-color: #1a1a1a; padding: 30px; text-align: center;">
            <div style="width: 60px; height: 60px; background: linear-gradient(135deg, #FFD700 0%, #FFA500 100%); border-radius: 12px; margin: 0 auto 20px; display: flex; align-items: center; justify-content: center; color: #1a1a1a; font-weight: 900; font-size: 18px;">
                SQ
            </div>
            <h1 style="color: #ff6b35; margin: 0; font-size: 24px;">Registration Cancelled</h1>
        </div>
        
        <!-- Content -->
        <div style="padding: 30px;">
            <h2 style="color: #333; margin-bottom: 20px;">Hi {player_name or 'there'},</h2>
            
            <p style="color: #666; line-height: 1.6; margin-bottom: 20px;">
                Your registration has been successfully cancelled for:
            </p>
            
            <div style="background-color: #f8f8f8; padding: 20px; border-radius: 8px; border-left: 4px solid #ff6b35; margin: 20px 0;">
                <h3 style="color: #ff6b35; margin: 0 0 10px 0;">{event_title}</h3>
                <p style="color: #666; margin: 0;">📅 {event_date_str}</p>
            </div>
            
            <div style="background-color: #f0f8ff; padding: 20px; border-radius: 8px; margin: 20px 0;">
                <h3 style="color: #2196F3; margin: 0 0 15px 0;">What happens next?</h3>
                <ul style="color: #666; line-height: 1.6; margin: 0; padding-left: 20px;">
                    <li>Your spot has been freed up for other players</li>
                    <li>If you paid an entry fee, your refund will be processed within 2-3 business days</li>
                    <li>For cash payments, please visit our store during business hours</li>
                    <li>You'll continue to receive updates about other gaming events</li>
                </ul>
            </div>
            
            <p style="color: #666; line-height: 1.6; margin-bottom: 20px;">
                We're sorry you can't make it to this event, but we hope to see you at future gaming sessions!
            </p>
        </div>
        
        <!-- Footer -->
        <div style="background-color: #f8f8f8; padding: 30px; text-align: center;">
            <p style="color: #666; margin: 0 0 15px 0;">
                <strong>SideQuest Canterbury Gaming Cafe</strong><br>
                C10, The Riverside, 1 Sturry Rd, Canterbury CT1 1BU<br>
                📞 01227 915058 | 📧 marketing@sidequestcanterbury.com
            </p>
            <p style="color: #999; font-size: 12px; margin: 0;">
                Questions about your cancellation? Just reply to this email.
            </p>
        </div>
    </div>
</body>
</html>
        """
        
        send_email = sib_api_v3_sdk.SendSmtpEmail(
            sender={"name": SENDER_NAME, "email": SENDER_EMAIL},
            to=[{"email": email, "name": player_name}],
            subject=subject,
            html_content=html_content
        )
        
        api_instance.send_transac_email(send_email)
        log_activity(f"Cancellation confirmation sent to {email} for {event_title}", "info")
        return True
        
    except Exception as e:
        log_error(f"Failed to send cancellation confirmation to {email}: {e}")
        return False

@app.route('/api/events', methods=['POST'])
@csrf_required
def create_event():
    """Create a new event with input sanitization"""
    try:
        data = request.json or {}
        
        # Sanitize all text inputs
        title = sanitize_text_input(data.get('title', ''), 255)
        event_type = sanitize_text_input(data.get('event_type', ''), 50)
        game_title = sanitize_text_input(data.get('game_title', ''), 255)
        description = sanitize_text_input(data.get('description', ''), 2000)
        prize_pool = sanitize_text_input(data.get('prize_pool', ''), 500)
        requirements = sanitize_text_input(data.get('requirements', ''), 1000)
        status = sanitize_text_input(data.get('status', 'draft'), 50)
        image_url = sanitize_text_input(data.get('image_url', ''), 500)
        
        # Sanitize numeric inputs
        capacity = int(sanitize_numeric_input(data.get('capacity', 0), 0, 1000))
        entry_fee = sanitize_numeric_input(data.get('entry_fee', 0), 0, 1000)
        
        # Validate required fields
        if not title or len(title) < 3:
            return jsonify({"success": False, "error": "Title must be at least 3 characters"}), 400
            
        if event_type not in ['tournament', 'game_night', 'special', 'birthday']:
            return jsonify({"success": False, "error": "Invalid event type"}), 400
            
        if status not in ['draft', 'published', 'cancelled', 'completed']:
            return jsonify({"success": False, "error": "Invalid status"}), 400
        
        # Validate and parse dates (keep your existing date parsing)
        try:
            date_time = datetime.fromisoformat(data['date_time'].replace('Z', '+00:00'))
        except Exception as e:
            log_error(f"Date parsing error: {e}")
            return jsonify({"success": False, "error": "Invalid date_time format"}), 400
            
        end_time = None
        if data.get('end_time'):
            try:
                end_time = datetime.fromisoformat(data['end_time'].replace('Z', '+00:00'))
            except:
                pass
                
        # Use your existing database query with sanitized inputs
        query = """
            INSERT INTO events (
                title, event_type, game_title, date_time, end_time,
                capacity, description, entry_fee, prize_pool, status,
                image_url, requirements
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """
        
        params = (
            title,          # sanitized
            event_type,     # sanitized
            game_title,     # sanitized
            date_time,
            end_time,
            capacity,       # sanitized
            description,    # sanitized
            entry_fee,      # sanitized
            prize_pool,     # sanitized
            status,         # sanitized
            image_url,      # sanitized
            requirements    # sanitized
        )
        
        # Keep your existing database execution logic
        result = execute_query_one(query, params)
        
        if result is None:
            log_error("execute_query_one returned None - check database connection and query")
            return jsonify({"success": False, "error": "Database query failed"}), 500
        
        if isinstance(result, dict) and 'id' in result:
            event_id = result['id']
            log_activity(f"Successfully created event: {title} (ID: {event_id})", "success")
            
            return jsonify({
                "success": True,
                "event_id": event_id,
                "message": "Event created successfully"
            })
        else:
            log_error(f"Unexpected result format from execute_query_one: {result}")
            return jsonify({"success": False, "error": "Database query failed"}), 500
            
    except Exception as e:
        log_error(f"Error creating event: {e}")
        return jsonify({"success": False, "error": "Invalid input data"}), 400

@app.route('/api/events/<int:event_id>', methods=['DELETE'])
@csrf_required
def delete_event(event_id):
    """Delete an event"""
    try:
        force = request.args.get('force', 'false').lower() == 'true'
        
        # Check if event has registrations
        registration_check = execute_query_one(
            "SELECT COUNT(*) as count FROM event_registrations WHERE event_id = %s",
            (event_id,)
        )
        
        has_registrations = registration_check and registration_check['count'] > 0
        
        if has_registrations and not force:
            return jsonify({
                "success": False,
                "error": "Cannot delete event with registrations. Use force=true to delete anyway.",
                "has_registrations": True
            }), 400
        
        # If force delete or no registrations, proceed
        if has_registrations and force:
            # Delete registrations first
            delete_registrations_query = "DELETE FROM event_registrations WHERE event_id = %s"
            execute_query_one(delete_registrations_query, (event_id,))
            log_activity(f"Force deleted registrations for event {event_id}", "warning")
        
        # Delete the event
        delete_query = "DELETE FROM events WHERE id = %s RETURNING title"
        result = execute_query_one(delete_query, (event_id,))
        
        if result:
            log_activity(f"Deleted event: {result['title']} (ID: {event_id})", "success")
            return jsonify({
                "success": True,
                "message": f"Event '{result['title']}' deleted successfully"
            })
        else:
            return jsonify({
                "success": False,
                "error": "Event not found"
            }), 404
            
    except Exception as e:
        log_error(f"Error deleting event {event_id}: {e}")
        log_error(f"Full traceback: {traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/events/<int:event_id>', methods=['PUT'])
def update_event(event_id):
    """Update an existing event"""
    try:
        data = request.json or {}
        
        # Check if event exists
        existing_event = execute_query_one("SELECT id FROM events WHERE id = %s", (event_id,))
        if not existing_event:
            return jsonify({"success": False, "error": "Event not found"}), 404
        
        # Parse date_time
        date_time = None
        if data.get('date_time'):
            try:
                date_time = datetime.fromisoformat(data['date_time'].replace('Z', '+00:00'))
            except Exception as e:
                log_error(f"Date parsing error: {e}")
                return jsonify({"success": False, "error": "Invalid date_time format"}), 400
        
        # Parse end_time if provided
        end_time = None
        if data.get('end_time'):
            try:
                end_time = datetime.fromisoformat(data['end_time'].replace('Z', '+00:00'))
            except:
                pass
        
        # Build update query dynamically based on provided fields
        update_fields = []
        params = []
        
        if 'title' in data:
            update_fields.append("title = %s")
            params.append(data['title'])
        
        if 'event_type' in data:
            update_fields.append("event_type = %s")
            params.append(data['event_type'])
            
        if 'game_title' in data:
            update_fields.append("game_title = %s")
            params.append(data.get('game_title'))
            
        if date_time:
            update_fields.append("date_time = %s")
            params.append(date_time)
            
        if 'end_time' in data:
            update_fields.append("end_time = %s")
            params.append(end_time)
            
        if 'capacity' in data:
            update_fields.append("capacity = %s")
            params.append(int(data.get('capacity', 0)))
            
        if 'description' in data:
            update_fields.append("description = %s")
            params.append(data.get('description', ''))
            
        if 'entry_fee' in data:
            update_fields.append("entry_fee = %s")
            params.append(float(data.get('entry_fee', 0)))
            
        if 'prize_pool' in data:
            update_fields.append("prize_pool = %s")
            params.append(data.get('prize_pool'))
            
        if 'status' in data:
            update_fields.append("status = %s")
            params.append(data.get('status', 'draft'))
            
        if 'image_url' in data:
            update_fields.append("image_url = %s")
            params.append(data.get('image_url'))
            
        if 'requirements' in data:
            update_fields.append("requirements = %s")
            params.append(data.get('requirements'))
        
        if not update_fields:
            return jsonify({"success": False, "error": "No fields to update"}), 400
        
        # Add event_id to params for WHERE clause
        params.append(event_id)
        
        query = f"""
            UPDATE events 
            SET {', '.join(update_fields)}, updated_at = NOW()
            WHERE id = %s
            RETURNING id, title
        """
        
        log_activity(f"Updating event {event_id} with query: {query}", "info")
        log_activity(f"Update params: {params}", "info")
        
        result = execute_query_one(query, params)
        
        if result:
            log_activity(f"Successfully updated event: {result['title']} (ID: {event_id})", "success")
            return jsonify({
                "success": True,
                "event_id": event_id,
                "message": f"Event '{result['title']}' updated successfully"
            })
        else:
            log_error("Update query returned no result")
            return jsonify({"success": False, "error": "Failed to update event"}), 500
            
    except Exception as e:
        log_error(f"Error updating event {event_id}: {e}")
        log_error(f"Full traceback: {traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500


# Add this code right after the update_event function ends:

# Replace your register_for_event function with this fixed version:

@app.route('/api/events/<int:event_id>/register', methods=['POST'])
@csrf_required
def register_for_event(event_id):
    """Register a subscriber for an event with confirmation email"""
    conn = None
    cursor = None
    try:
        data = request.json or {}
        email = data.get('email', '').strip().lower()
        player_name = data.get('player_name', '')
        
        if not email:
            return jsonify({"success": False, "error": "Email is required"}), 400
            
        if not is_valid_email(email):
            return jsonify({"success": False, "error": "Invalid email format"}), 400
        
        # Check if event exists and get details
        event_check = execute_query_one("SELECT * FROM events WHERE id = %s", (event_id,))
        if not event_check:
            return jsonify({"success": False, "error": "Event not found"}), 404
        
        event_dict = dict(event_check)
        
        # Auto-add to subscribers if not exists
        subscriber_check = execute_query_one("SELECT email FROM subscribers WHERE email = %s", (email,))
        if not subscriber_check:
            if add_subscriber_to_db(email, 'event_registration', None, None, None, True):
                log_activity(f"Auto-added {email} to subscribers via event registration", "info")

        # Check if already registered
        existing_registration = execute_query_one(
            "SELECT id FROM event_registrations WHERE event_id = %s AND subscriber_email = %s",
            (event_id, email)
        )
        if existing_registration:
            return jsonify({"success": False, "error": "Already registered for this event"}), 400
        
        # Check capacity
        if event_dict['capacity'] > 0:
            current_count = execute_query_one(
                "SELECT COUNT(*) as count FROM event_registrations WHERE event_id = %s",
                (event_id,)
            )
            if current_count and current_count['count'] >= event_dict['capacity']:
                return jsonify({"success": False, "error": "Event is at full capacity"}), 400
        
        # Generate confirmation code
        import random
        import string
        confirmation_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        
        # Manual database handling to ensure commit
        conn = get_db_connection()
        if not conn:
            return jsonify({"success": False, "error": "Database connection failed"}), 500
            
        cursor = conn.cursor()
        
        # Register for event
        register_query = """
            INSERT INTO event_registrations (event_id, subscriber_email, player_name, confirmation_code)
            VALUES (%s, %s, %s, %s)
            RETURNING id
        """
        
        cursor.execute(register_query, (event_id, email, player_name or email.split('@')[0], confirmation_code))
        result = cursor.fetchone()
        
        if result:
            conn.commit()
            
            # Send confirmation email for tournaments
            if event_dict.get('event_type') == 'tournament':
                email_sent = send_simple_tournament_confirmation(
                    email=email,
                    event_data=event_dict,
                    confirmation_code=confirmation_code,
                    player_name=player_name or email.split('@')[0]
                )
                
                log_activity(f"Tournament registration: {email} for {event_dict['title']} - Email sent: {email_sent}", "success")
            else:
                log_activity(f"Event registration: {email} for {event_dict['title']}", "success")
            
            return jsonify({
                "success": True,
                "message": "Registration successful",
                "confirmation_code": confirmation_code,
                "event_title": event_dict['title'],
                "confirmation_email_sent": event_dict.get('event_type') == 'tournament'
            })
        else:
            conn.rollback()
            return jsonify({"success": False, "error": "Registration failed - no result"}), 500
            
    except Exception as e:
        if conn:
            conn.rollback()
        log_error(f"Error registering for event {event_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
        
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# 1. BACKEND FIX - Replace your get_event_attendees function:

# Replace your get_event_attendees function with this SIMPLE version:

# Replace your get_event_attendees function with this ULTRA-DEBUG version:

# Replace your get_event_attendees function with this FIXED version:

@app.route('/api/events/<int:event_id>/attendees', methods=['GET'])
def get_event_attendees(event_id):
    """Get list of attendees for an event - now includes cancellation status"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"success": False, "error": "Database connection failed"}), 500
            
        cursor = conn.cursor()
        
        # Get event details
        cursor.execute("SELECT title FROM events WHERE id = %s", (event_id,))
        event_row = cursor.fetchone()
        
        if not event_row:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "error": "Event not found"}), 404
        
        event_title = event_row['title']
        
        # Get attendees including cancelled registrations
        cursor.execute("""
            SELECT 
                subscriber_email,
                player_name,
                confirmation_code,
                registered_at,
                attended,
                cancelled_at,
                cancellation_reason,
                CASE 
                    WHEN cancelled_at IS NOT NULL THEN 'cancelled'
                    WHEN attended = true THEN 'attended'
                    ELSE 'registered'
                END as status
            FROM event_registrations 
            WHERE event_id = %s 
            ORDER BY cancelled_at ASC, registered_at ASC
        """, (event_id,))
        
        rows = cursor.fetchall()
        
        # Convert to list of dictionaries
        attendees = []
        for row in rows:
            attendee = {
                'subscriber_email': row['subscriber_email'],
                'player_name': row['player_name'],
                'confirmation_code': row['confirmation_code'],
                'registered_at': row['registered_at'].isoformat() if row['registered_at'] else None,
                'attended': row['attended'] if row['attended'] is not None else False,
                'cancelled_at': row['cancelled_at'].isoformat() if row['cancelled_at'] else None,
                'cancellation_reason': row['cancellation_reason'],
                'status': row['status']
            }
            attendees.append(attendee)
        
        cursor.close()
        conn.close()
        
        return jsonify({
            "success": True,
            "attendees": attendees,
            "event_title": event_title,
            "total_count": len(attendees),
            "active_count": len([a for a in attendees if a['status'] != 'cancelled']),
            "cancelled_count": len([a for a in attendees if a['status'] == 'cancelled'])
        })
        
    except Exception as e:
        log_error(f"Error getting event attendees: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500
        
# 2. ALSO ADD THIS DEBUG ENDPOINT to test if registrations exist:

@app.route('/api/events/<int:event_id>/debug', methods=['GET'])
def debug_event_registrations(event_id):
    """Debug endpoint to check event registrations"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
            
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # Check if event exists
        cursor.execute("SELECT * FROM events WHERE id = %s", (event_id,))
        event = cursor.fetchone()
        
        # Check registrations
        cursor.execute("SELECT * FROM event_registrations WHERE event_id = %s", (event_id,))
        registrations = cursor.fetchall()
        
        # Check all registrations
        cursor.execute("SELECT event_id, COUNT(*) as count FROM event_registrations GROUP BY event_id")
        all_registrations = cursor.fetchall()
        
        return jsonify({
            "event_exists": event is not None,
            "event_data": dict(event) if event else None,
            "registrations_for_this_event": [dict(r) for r in registrations],
            "registration_count": len(registrations),
            "all_event_registrations": [dict(r) for r in all_registrations]
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# Replace your existing checkin_attendee route with this improved version:

@app.route('/api/events/<int:event_id>/checkin', methods=['POST'])
def checkin_attendee(event_id):
    """Check in an attendee for an event - SIMPLIFIED VERSION"""
    conn = None
    cursor = None
    try:
        data = request.json or {}
        email = data.get('email', '').strip().lower()
        notes = data.get('notes', '')
        
        if not email:
            return jsonify({"success": False, "error": "Email is required"}), 400
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"success": False, "error": "Database connection failed"}), 500
            
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # Check if registration exists
        cursor.execute(
            "SELECT id, attended, confirmation_code FROM event_registrations WHERE event_id = %s AND subscriber_email = %s",
            (event_id, email)
        )
        registration = cursor.fetchone()
        
        if not registration:
            return jsonify({"success": False, "error": "Registration not found"}), 404
        
        if registration['attended']:
            return jsonify({"success": False, "error": "Already checked in"}), 400
        
        # Check in attendee - REMOVE check_in_time to avoid column error
        cursor.execute("""
            UPDATE event_registrations 
            SET attended = TRUE, notes = %s
            WHERE event_id = %s AND subscriber_email = %s
            RETURNING confirmation_code, attended
        """, (notes, event_id, email))
        
        result = cursor.fetchone()
        
        if result:
            conn.commit()
            log_activity(f"Checked in {email} for event ID {event_id}", "success")
            
            return jsonify({
                "success": True,
                "message": "Check-in successful",
                "confirmation_code": result['confirmation_code'],
                "attended": result['attended']
            })
        else:
            return jsonify({"success": False, "error": "Check-in update failed"}), 500
            
    except Exception as e:
        if conn:
            conn.rollback()
        log_error(f"Error checking in attendee for event {event_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
        
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route('/api/events/calendar', methods=['GET'])
def get_events_calendar():
    """Get events in calendar format"""
    try:
        query = """
            SELECT 
                id,
                title,
                event_type,
                date_time as start,
                end_time as end,
                description,
                CASE 
                    WHEN event_type = 'tournament' THEN '#FF6B35'
                    WHEN event_type = 'game_night' THEN '#4ECDC4'
                    WHEN event_type = 'special' THEN '#FFD700'
                    WHEN event_type = 'birthday' THEN '#FF69B4'
                    ELSE '#FFD700'
                END as color
            FROM events 
            WHERE date_time >= CURRENT_DATE - INTERVAL '30 days'
            ORDER BY date_time ASC
        """
        
        events = execute_query(query)
        
        if events is None:
            return jsonify({"success": False, "error": "Database error"}), 500
        
        # Convert datetime objects to ISO format
        for event in events:
            if event['start']:
                event['start'] = event['start'].isoformat()
            if event['end']:
                event['end'] = event['end'].isoformat()
        
        return jsonify({
            "success": True,
            "events": events
        })
        
    except Exception as e:
        log_error(f"Error getting calendar events: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# =============================
# Event Registration System
# =============================

# Add this function to your backend to verify/fix tables:

def verify_event_tables():
    """Verify and create missing event tables"""
    try:
        conn = get_db_connection()
        if not conn:
            print("❌ Could not connect to database")
            return False
            
        cursor = conn.cursor()
        
        # Check if event_registrations table exists
        cursor.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'event_registrations'
            );
        """)
        
        table_exists = cursor.fetchone()[0]
        
        if not table_exists:
            print("⚠️ event_registrations table missing - creating now...")
            
            # Create event registrations table
            cursor.execute('''
                CREATE TABLE event_registrations (
                    id SERIAL PRIMARY KEY,
                    event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
                    subscriber_email VARCHAR(255) NOT NULL,
                    player_name VARCHAR(255),
                    confirmation_code VARCHAR(50) UNIQUE NOT NULL,
                    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    attended BOOLEAN DEFAULT FALSE,
                    check_in_time TIMESTAMP,
                    notes TEXT
                )
            ''')
            
            # Create index for better performance
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_event_registrations_event_id ON event_registrations(event_id);
            ''')
            
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_event_registrations_email ON event_registrations(subscriber_email);
            ''')
            
            conn.commit()
            print("✅ Created event_registrations table with indexes")
        else:
            print("✅ event_registrations table exists")
            
        cursor.close()
        conn.close()
        return True
        
    except Exception as e:
        print(f"❌ Error verifying event tables: {e}")
        return False

# Then call this function in your init_database() function by adding this line:
# verify_event_tables()

# =============================
# Event Email Automation
# =============================

def schedule_event_emails(event_id, event_date):
    """Schedule automated emails for an event"""
    try:
        # Schedule announcement (immediate)
        schedule_email(event_id, 'announcement', datetime.now())
        
        # Schedule reminders
        if event_date > datetime.now() + timedelta(days=7):
            schedule_email(event_id, 'reminder_week', event_date - timedelta(days=7))
            
        if event_date > datetime.now() + timedelta(days=1):
            schedule_email(event_id, 'reminder_day', event_date - timedelta(days=1))
            
        if event_date > datetime.now() + timedelta(hours=2):
            schedule_email(event_id, 'reminder_hour', event_date - timedelta(hours=2))
            
        log_activity(f"Scheduled emails for event ID: {event_id}", "info")
        
    except Exception as e:
        log_error(f"Error scheduling emails for event {event_id}: {e}")

def schedule_email(event_id, email_type, scheduled_for):
    """Schedule a single email"""
    query = """
        INSERT INTO event_emails (event_id, email_type, scheduled_for)
        VALUES (%s, %s, %s)
        ON CONFLICT DO NOTHING
    """
    execute_query(query, (event_id, email_type, scheduled_for), fetch=False)

def send_registration_confirmation(email, event, confirmation_code):
    """Send registration confirmation email"""
    try:
        if not api_instance:
            return
            
        subject = f"Registration Confirmed: {event['title']}"
        html_content = f"""
        <h2>You're registered for {event['title']}!</h2>
        <p><strong>Date:</strong> {event['date_time']}</p>
        <p><strong>Confirmation Code:</strong> {confirmation_code}</p>
        <p>Show this code at check-in.</p>
        <p>See you at SideQuest Gaming Cafe!</p>
        """
        
        send_email = sib_api_v3_sdk.SendSmtpEmail(
            sender={"name": SENDER_NAME, "email": SENDER_EMAIL},
            to=[{"email": email}],
            subject=subject,
            html_content=html_content
        )
        
        api_instance.send_transac_email(send_email)
        log_activity(f"Sent confirmation email to {email}", "success")
        
    except Exception as e:
        log_error(f"Error sending confirmation email: {e}")

# =============================
# PHASE 2: PUBLIC EVENT SIGNUP - STEP 1
# =============================

@app.route('/api/events/<int:event_id>/public', methods=['GET'])
def get_public_event(event_id):
    """Get public event details for signup page"""
    try:
        query = """
            SELECT 
                e.*,
                COUNT(r.id) as registration_count,
                CASE 
                    WHEN e.capacity > 0 THEN e.capacity - COUNT(r.id)
                    ELSE NULL
                END as spots_available
            FROM events e
            LEFT JOIN event_registrations r ON e.id = r.event_id
            WHERE e.id = %s
            GROUP BY e.id
        """
        
        event = execute_query_one(query, (event_id,))
        
        if not event:
            return jsonify({"success": False, "error": "Event not found"}), 404
        
        # Convert datetime objects
        if event['date_time']:
            event['date_time'] = event['date_time'].isoformat()
        if event['end_time']:
            event['end_time'] = event['end_time'].isoformat()
        if event['created_at']:
            event['created_at'] = event['created_at'].isoformat()
        if event['updated_at']:
            event['updated_at'] = event['updated_at'].isoformat()
            
        return jsonify({
            "success": True,
            "event": event
        })
        
    except Exception as e:
        log_error(f"Error getting public event {event_id}: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500

# Add this API route to handle public registrations (if not already present)

@app.route('/api/events/<int:event_id>/register-public', methods=['POST'])
@limiter.limit("3 per hour")
def register_public_with_confirmation(event_id):
    """Public registration with tournament confirmation email"""
    try:
        data = request.json or {}
        
        # Sanitize inputs
        email = sanitize_email(data.get('email', ''))
        player_name = sanitize_text_input(data.get('player_name', ''), 255)
        first_name = sanitize_text_input(data.get('first_name', ''), 100)
        last_name = sanitize_text_input(data.get('last_name', ''), 100)
        email_consent = bool(data.get('email_consent', False))

        # Validation
        if not email or not player_name or not first_name or not last_name:
            return jsonify({"success": False, "error": "All fields are required"}), 400
            
        if len(first_name) < 2 or len(last_name) < 2 or len(player_name) < 2:
            return jsonify({"success": False, "error": "Names must be at least 2 characters"}), 400

        # Get event details
        conn = get_db_connection()
        if not conn:
            return jsonify({"success": False, "error": "Database connection failed"}), 500
            
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM events WHERE id = %s", (event_id,))
        event = cursor.fetchone()
        
        if not event:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "error": "Event not found"}), 404

        event_dict = dict(event)

        # Check existing registration
        cursor.execute("""
            SELECT id FROM event_registrations
            WHERE event_id = %s AND subscriber_email = %s
        """, (event_id, email))
        
        if cursor.fetchone():
            cursor.close()
            conn.close()
            return jsonify({"success": False, "error": "You are already registered for this event"}), 400

        # Check capacity
        cursor.execute("""
            SELECT COUNT(*) AS current_count
            FROM event_registrations
            WHERE event_id = %s
        """, (event_id,))
        
        row = cursor.fetchone() or {"current_count": 0}
        current_count = int(row["current_count"])
        is_waiting_list = bool(event_dict.get('capacity', 0) > 0 and current_count >= event_dict['capacity'])

        # Generate confirmation code
        import random, string
        confirmation_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

        # Insert registration
        cursor.execute("""
            INSERT INTO event_registrations
                (event_id, subscriber_email, player_name, confirmation_code, registered_at, attended, notes)
            VALUES (%s, %s, %s, %s, NOW(), FALSE, %s)
            RETURNING id
        """, (event_id, email, player_name, confirmation_code,
              ("WAITING LIST" if is_waiting_list else None)))

        reg = cursor.fetchone()
        conn.commit()

        # Handle newsletter subscription
        if email_consent:
            cursor.execute("""
                INSERT INTO subscribers
                    (email, first_name, last_name, source, date_added, status, gdpr_consent_given, consent_date)
                VALUES
                    (%s, %s, %s, %s, NOW(), 'active', %s, %s)
                ON CONFLICT (email) DO UPDATE SET
                    first_name = COALESCE(subscribers.first_name, EXCLUDED.first_name),
                    last_name = COALESCE(subscribers.last_name, EXCLUDED.last_name),
                    gdpr_consent_given = TRUE,
                    consent_date = NOW()
            """, (email, first_name, last_name, 'event_registration', True, datetime.now()))
            conn.commit()

        cursor.close()
        conn.close()

        # Send confirmation email for tournaments
        confirmation_email_sent = False
        if event_dict.get('event_type') == 'tournament':
            confirmation_email_sent = send_simple_tournament_confirmation(
                email=email,
                event_data=event_dict,
                confirmation_code=confirmation_code,
                player_name=player_name
            )

        # Prepare response
        response_data = {
            "success": True,
            "confirmation_code": confirmation_code,
            "is_waiting_list": is_waiting_list,
            "confirmation_email_sent": confirmation_email_sent
        }

        # Add Discord info for tournaments
        if event_dict.get('event_type') == 'tournament':
            response_data.update({
                "show_discord": True,
                "discord_invite": "https://discord.gg/CuwQM7Zwuk"
            })

        return jsonify(response_data)

    except Exception as e:
        log_error(f"Error in public registration: {str(e)}")
        return jsonify({"success": False, "error": "Registration failed"}), 500

@app.route('/api/test-tournament-email', methods=['POST'])
@csrf_required
def test_tournament_email_route():
    """Test route for tournament confirmation emails"""
    try:
        result = test_tournament_confirmation()
        return jsonify({
            "success": result,
            "message": "Test email sent" if result else "Test email failed"
        })
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# Add this function to check if Brevo is properly configured
def test_tournament_confirmation():
    """Test function to verify tournament confirmation emails work"""
    try:
        # Test event data
        test_event = {
            'id': 999,
            'title': 'Test Valorant Tournament',
            'game_title': 'Valorant',
            'date_time': datetime.now() + timedelta(days=7),
            'end_time': datetime.now() + timedelta(days=7, hours=2),
            'event_type': 'tournament',
            'entry_fee': 10
        }
        
        # Send test email
        result = send_simple_tournament_confirmation(
            email="jaiamiscua@gmail.com",
            event_data=test_event,
            confirmation_code="TEST123",
            player_name="TestPlayer"
        )
        
        if result:
            print("✅ Tournament confirmation email test successful")
        else:
            print("❌ Tournament confirmation email test failed")
            
        return result
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False

def generate_calendar_invite(event_data, confirmation_code):
    """Generate .ics calendar invite for the event"""
    try:
        from datetime import datetime
        import uuid
        
        # Parse event datetime
        if isinstance(event_data['date_time'], str):
            event_start = datetime.fromisoformat(event_data['date_time'].replace('Z', '+00:00'))
        else:
            event_start = event_data['date_time']
        
        # Default 2-hour duration if no end time
        if event_data.get('end_time'):
            if isinstance(event_data['end_time'], str):
                event_end = datetime.fromisoformat(event_data['end_time'].replace('Z', '+00:00'))
            else:
                event_end = event_data['end_time']
        else:
            from datetime import timedelta
            event_end = event_start + timedelta(hours=2)
        
        # Format dates for .ics (UTC format)
        start_utc = event_start.strftime('%Y%m%dT%H%M%SZ')
        end_utc = event_end.strftime('%Y%m%dT%H%M%SZ')
        
        # Create unique ID for the event
        uid = f"sidequest-{event_data['id']}-{uuid.uuid4().hex[:8]}@sidequestcanterbury.com"
        
        # Build .ics content
        ics_content = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//SideQuest Canterbury//Tournament Calendar//EN
CALSCALE:GREGORIAN
METHOD:REQUEST
BEGIN:VEVENT
UID:{uid}
DTSTART:{start_utc}
DTEND:{end_utc}
SUMMARY:{event_data['title']}
DESCRIPTION:You're registered for {event_data['title']}!\\n\\nConfirmation Code: {confirmation_code}\\n\\nGame: {event_data.get('game_title', 'TBD')}\\n\\nBring your confirmation code and gaming gear.\\n\\nSideQuest Gaming Cafe\\nCanterbury, UK
LOCATION:SideQuest Gaming Cafe, Canterbury, UK
STATUS:CONFIRMED
SEQUENCE:0
BEGIN:VALARM
TRIGGER:-PT1H
DESCRIPTION:Tournament starts in 1 hour!
ACTION:DISPLAY
END:VALARM
BEGIN:VALARM
TRIGGER:-P1D
DESCRIPTION:Tournament tomorrow - {event_data['title']}
ACTION:DISPLAY
END:VALARM
END:VEVENT
END:VCALENDAR"""
        
        return ics_content.strip()
        
    except Exception as e:
        log_error(f"Error generating calendar invite: {e}")
        return None

def send_simple_tournament_confirmation(email, event_data, confirmation_code, player_name):
    """Send simple tournament confirmation with calendar invite and Discord link"""
    if not api_instance:
        log_error("Brevo API not initialized")
        return False
        
    try:
        # Generate calendar invite
        calendar_invite = generate_calendar_invite(event_data, confirmation_code)
        
        # Format event details
        event_start = datetime.fromisoformat(event_data['date_time']) if isinstance(event_data['date_time'], str) else event_data['date_time']
        event_date = event_start.strftime('%A, %B %d, %Y')
        event_time = event_start.strftime('%I:%M %p')
        
        subject = f"Tournament Registration Confirmed - {event_data['title']}"
        
        # Mobile-optimized HTML template with table-based layout
        html_content = f"""
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
    <meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
    <title>Tournament Registration Confirmed</title>
</head>
<body style="margin: 0; padding: 0; -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; background-color: #f4f4f4;">
    <table border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color: #f4f4f4;">
        <tr>
            <td align="center" style="padding: 20px 10px;">
                <!-- Main Container -->
                <table border="0" cellpadding="0" cellspacing="0" width="600" style="max-width: 600px; background-color: #ffffff; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                    
                    <!-- Header -->
                    <tr>
                        <td align="center" style="padding: 40px 30px 30px 30px; background-color: #1a1a1a; border-radius: 8px 8px 0 0;">
                            <div style="width: 80px; height: 80px; background-color: #FFD700; border-radius: 15px; margin: 0 auto 20px auto; display: table-cell; vertical-align: middle; text-align: center;">
                                <span style="color: #1a1a1a; font-family: Arial, Helvetica, sans-serif; font-weight: bold; font-size: 24px; line-height: 80px;">SQ</span>
                            </div>
                            <h1 style="margin: 0; font-family: Arial, Helvetica, sans-serif; font-size: 28px; font-weight: bold; color: #FFD700; text-align: center;">
                                Tournament Registration Confirmed
                            </h1>
                        </td>
                    </tr>
                    
                    <!-- Greeting -->
                    <tr>
                        <td style="padding: 30px 30px 20px 30px;">
                            <h2 style="margin: 0 0 20px 0; font-family: Arial, Helvetica, sans-serif; font-size: 24px; color: #333333; font-weight: normal;">
                                Hey {player_name}!
                            </h2>
                            <p style="margin: 0; font-family: Arial, Helvetica, sans-serif; font-size: 16px; line-height: 24px; color: #666666;">
                                You're all set for the tournament. Here are your details:
                            </p>
                        </td>
                    </tr>
                    
                    <!-- Event Details -->
                    <tr>
                        <td style="padding: 20px 30px;">
                            <table border="0" cellpadding="20" cellspacing="0" width="100%" style="background-color: #f8f8f8; border-radius: 8px; border-left: 4px solid #FFD700;">
                                <tr>
                                    <td>
                                        <h3 style="margin: 0 0 15px 0; font-family: Arial, Helvetica, sans-serif; font-size: 20px; color: #FFD700; font-weight: bold;">
                                            {event_data['title']}
                                        </h3>
                                        
                                        <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                            <tr>
                                                <td style="padding: 4px 0;">
                                                    <p style="margin: 0; font-family: Arial, Helvetica, sans-serif; font-size: 15px; color: #333333;">
                                                        <strong>Game:</strong> {event_data.get('game_title', 'TBD')}
                                                    </p>
                                                </td>
                                            </tr>
                                            <tr>
                                                <td style="padding: 4px 0;">
                                                    <p style="margin: 0; font-family: Arial, Helvetica, sans-serif; font-size: 15px; color: #333333;">
                                                        <strong>Date:</strong> {event_date}
                                                    </p>
                                                </td>
                                            </tr>
                                            <tr>
                                                <td style="padding: 4px 0;">
                                                    <p style="margin: 0; font-family: Arial, Helvetica, sans-serif; font-size: 15px; color: #333333;">
                                                        <strong>Time:</strong> {event_time}
                                                    </p>
                                                </td>
                                            </tr>
                                            <tr>
                                                <td style="padding: 4px 0;">
                                                    <p style="margin: 0; font-family: Arial, Helvetica, sans-serif; font-size: 15px; color: #333333;">
                                                        <strong>Location:</strong> SideQuest Gaming Cafe, Canterbury
                                                    </p>
                                                </td>
                                            </tr>
                                            <tr>
                                                <td style="padding: 4px 0;">
                                                    <p style="margin: 0; font-family: Arial, Helvetica, sans-serif; font-size: 15px; color: #333333;">
                                                        <strong>Entry:</strong> {'£' + str(event_data["entry_fee"]) if event_data.get('entry_fee', 0) > 0 else 'FREE'}
                                                    </p>
                                                </td>
                                            </tr>
                                        </table>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    
                    <!-- Confirmation Code -->
                    <tr>
                        <td style="padding: 20px 30px;">
                            <table border="0" cellpadding="25" cellspacing="0" width="100%" style="background-color: #FFD700; border-radius: 8px;">
                                <tr>
                                    <td align="center">
                                        <h3 style="margin: 0 0 15px 0; font-family: Arial, Helvetica, sans-serif; font-size: 18px; color: #1a1a1a; font-weight: bold;">
                                            Your Confirmation Code
                                        </h3>
                                        <div style="font-family: monospace; font-size: 28px; font-weight: bold; letter-spacing: 3px; color: #1a1a1a; margin: 10px 0;">
                                            {confirmation_code}
                                        </div>
                                        <p style="margin: 10px 0 0 0; font-family: Arial, Helvetica, sans-serif; font-size: 14px; color: #1a1a1a;">
                                            Show this when you arrive
                                        </p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>

                    <!-- Cancellation Information -->
                    <tr>
                        <td style="padding: 20px 30px;">
                            <table border="0" cellpadding="20" cellspacing="0" width="100%" style="background-color: #f8f8f8; border-radius: 8px;">
                                <tr>
                                    <td>
                                        <h3 style="margin: 0 0 15px 0; font-family: Arial, Helvetica, sans-serif; font-size: 16px; color: #666; font-weight: normal;">
                                            Need to Cancel?
                                        </h3>
                                        <p style="margin: 0 0 15px 0; font-family: Arial, Helvetica, sans-serif; font-size: 14px; color: #666; line-height: 20px;">
                                            If your plans change, you can cancel your registration using the link below:
                                        </p>
                                        <table border="0" cellpadding="0" cellspacing="0">
                                            <tr>
                                                <td align="center" style="background-color: #6b7280; border-radius: 6px;">
                                                    <a href="{window.location.origin}/cancel?code={confirmation_code}" 
                                                    style="display: inline-block; padding: 12px 20px; font-family: Arial, Helvetica, sans-serif; font-size: 14px; color: #ffffff; text-decoration: none; font-weight: bold;">
                                                        Cancel Registration
                                                    </a>
                                                </td>
                                            </tr>
                                        </table>
                                        <p style="margin: 15px 0 0 0; font-family: Arial, Helvetica, sans-serif; font-size: 12px; color: #999;">
                                            Keep this email safe - you'll need your confirmation code to cancel.
                                        </p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    
                    <!-- Discord Community Section -->
                    <tr>
                        <td style="padding: 20px 30px;">
                            <table border="0" cellpadding="25" cellspacing="0" width="100%" style="background-color: #5865F2; border-radius: 8px;">
                                <tr>
                                    <td align="center">
                                        <h3 style="margin: 0 0 15px 0; font-family: Arial, Helvetica, sans-serif; font-size: 18px; color: #ffffff; font-weight: bold;">
                                            Join Our Discord Community
                                        </h3>
                                        <p style="margin: 0 0 20px 0; font-family: Arial, Helvetica, sans-serif; font-size: 16px; color: #ffffff; line-height: 22px;">
                                            Connect with other players, get tournament updates, and join the conversation!
                                        </p>
                                        
                                        <table border="0" cellpadding="0" cellspacing="0">
                                            <tr>
                                                <td align="center" style="background-color: #ffffff; border-radius: 8px;">
                                                    <a href="https://discord.gg/CuwQM7Zwuk" style="display: inline-block; padding: 15px 25px; font-family: Arial, Helvetica, sans-serif; font-size: 16px; color: #5865F2; text-decoration: none; font-weight: bold;">
                                                        Join Discord Server
                                                    </a>
                                                </td>
                                            </tr>
                                        </table>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    
                    <!-- What to Bring -->
                    <tr>
                        <td style="padding: 20px 30px;">
                            <table border="0" cellpadding="20" cellspacing="0" width="100%" style="background-color: #f8f8f8; border-radius: 8px;">
                                <tr>
                                    <td>
                                        <h3 style="margin: 0 0 15px 0; font-family: Arial, Helvetica, sans-serif; font-size: 18px; color: #FFD700; font-weight: bold;">
                                            What to Bring:
                                        </h3>
                                        
                                        <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                            <tr>
                                                <td style="padding: 5px 0;">
                                                    <p style="margin: 0; font-family: Arial, Helvetica, sans-serif; font-size: 15px; color: #333333;">
                                                        • Your confirmation code
                                                    </p>
                                                </td>
                                            </tr>
                                            <tr>
                                                <td style="padding: 5px 0;">
                                                    <p style="margin: 0; font-family: Arial, Helvetica, sans-serif; font-size: 15px; color: #333333;">
                                                        • Positive attitude and competitive spirit
                                                    </p>
                                                </td>
                                            </tr>
                                            {'<tr><td style="padding: 5px 0;"><p style="margin: 0; font-family: Arial, Helvetica, sans-serif; font-size: 15px; color: #333333;">• £' + str(event_data["entry_fee"]) + ' entry fee</p></td></tr>' if event_data.get('entry_fee', 0) > 0 else ''}
                                        </table>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    
                    <!-- Important Notes -->
                    <tr>
                        <td style="padding: 20px 30px;">
                            <table border="0" cellpadding="20" cellspacing="0" width="100%" style="background-color: #e8f5e8; border-radius: 8px; border-left: 4px solid #28a745;">
                                <tr>
                                    <td>
                                        <h3 style="margin: 0 0 15px 0; font-family: Arial, Helvetica, sans-serif; font-size: 18px; color: #28a745; font-weight: bold;">
                                            Important Notes:
                                        </h3>
                                        
                                        <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                            <tr>
                                                <td style="padding: 5px 0;">
                                                    <p style="margin: 0; font-family: Arial, Helvetica, sans-serif; font-size: 15px; color: #333333; line-height: 22px;">
                                                        • Join our Discord for real-time updates and communication during the tournament
                                                    </p>
                                                </td>
                                            </tr>
                                            <tr>
                                                <td style="padding: 5px 0;">
                                                    <p style="margin: 0; font-family: Arial, Helvetica, sans-serif; font-size: 15px; color: #333333; line-height: 22px;">
                                                        • Arrive 15 minutes early for check-in and setup
                                                    </p>
                                                </td>
                                            </tr>
                                            <tr>
                                                <td style="padding: 5px 0;">
                                                    <p style="margin: 0; font-family: Arial, Helvetica, sans-serif; font-size: 15px; color: #333333; line-height: 22px;">
                                                        • Tournament bracket and rules will be posted in Discord
                                                    </p>
                                                </td>
                                            </tr>
                                        </table>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    
                    <!-- Footer -->
                    <tr>
                        <td style="padding: 30px 30px 40px 30px; background-color: #f8f8f8; border-radius: 0 0 8px 8px;">
                            <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                <tr>
                                    <td align="center">
                                        <p style="margin: 0 0 20px 0; font-family: Arial, Helvetica, sans-serif; font-size: 16px; color: #666666; text-align: center;">
                                            Questions? Reply to this email or visit us in Canterbury.
                                        </p>
                                        
                                        <table border="0" cellpadding="0" cellspacing="0">
                                            <tr>
                                                <td align="center" style="background-color: #7289DA; border-radius: 6px; padding: 2px;">
                                                    <a href="https://discord.gg/CuwQM7Zwuk" style="display: inline-block; padding: 10px 20px; font-family: Arial, Helvetica, sans-serif; font-size: 14px; color: #ffffff; text-decoration: none; font-weight: bold;">
                                                        Discord Community
                                                    </a>
                                                </td>
                                            </tr>
                                        </table>
                                        
                                        <p style="margin: 20px 0 0 0; font-family: Arial, Helvetica, sans-serif; font-size: 13px; color: #999999; line-height: 18px; text-align: center;">
                                            SideQuest Gaming Cafe<br/>
                                            Canterbury, UK<br/>
                                            <a href="mailto:marketing@sidequestcanterbury.com" style="color: #4CAF50; text-decoration: none;">marketing@sidequestcanterbury.com</a>
                                        </p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
        """
        
        # Plain text version
        text_content = f"""
TOURNAMENT REGISTRATION CONFIRMED

Hey {player_name}!

You're all set for the tournament. Here are your details:

EVENT DETAILS:
{event_data['title']}
Game: {event_data.get('game_title', 'TBD')}
Date: {event_date}
Time: {event_time}
Location: SideQuest Gaming Cafe, Canterbury
Entry: {'£' + str(event_data["entry_fee"]) if event_data.get('entry_fee', 0) > 0 else 'FREE'}

YOUR CONFIRMATION CODE: {confirmation_code}
Show this when you arrive

JOIN OUR DISCORD COMMUNITY:
Connect with other players, get tournament updates, and join the conversation!
https://discord.gg/CuwQM7Zwuk

WHAT TO BRING:
• Your confirmation code
• Positive attitude and competitive spirit
{'• £' + str(event_data["entry_fee"]) + ' entry fee' if event_data.get('entry_fee', 0) > 0 else ''}

IMPORTANT NOTES:
• Join our Discord for real-time updates and communication during the tournament
• Arrive 15 minutes early for check-in and setup  
• Tournament bracket and rules will be posted in Discord

Questions? Reply to this email or visit us in Canterbury.

---
SideQuest Gaming Cafe
Canterbury, UK
marketing@sidequestcanterbury.com
        """
        
        # Prepare email with attachment
        attachments = []
        if calendar_invite:
            import base64
            calendar_b64 = base64.b64encode(calendar_invite.encode('utf-8')).decode('utf-8')
            attachments = [{
                "name": f"{event_data['title'].replace(' ', '_')}_tournament.ics",
                "content": calendar_b64
            }]
        
        # Send email
        send_email = sib_api_v3_sdk.SendSmtpEmail(
            sender={"name": SENDER_NAME, "email": SENDER_EMAIL},
            to=[{"email": email, "name": player_name}],
            subject=subject,
            html_content=html_content,
            text_content=text_content,
            attachment=attachments if attachments else None
        )
        
        response = api_instance.send_transac_email(send_email)
        log_activity(f"Tournament confirmation sent to {email} for {event_data['title']}", "success")
        return True
        
    except Exception as e:
        log_error(f"Failed to send tournament confirmation to {email}: {e}")
        return False

@app.route('/api/events/<int:event_id>/debug-registration', methods=['POST'])
def debug_registration(event_id):
    """Debug endpoint to check registration flow step by step"""
    try:
        data = request.json or {}
        email = data.get('email', '').strip().lower()
        
        debug_info = {
            "step_1_event_lookup": None,
            "step_2_existing_check": None,
            "step_3_capacity_check": None,
            "step_4_subscriber_add": None,
            "step_5_registration_insert": None,
            "final_result": None
        }
        
        # Step 1: Check if event exists
        event_check = execute_query_one("""
            SELECT id, title, capacity, event_type, status
            FROM events 
            WHERE id = %s
        """, (event_id,))
        
        debug_info["step_1_event_lookup"] = {
            "found": event_check is not None,
            "event_data": dict(event_check) if event_check else None
        }
        
        if not event_check:
            return jsonify({"success": False, "debug": debug_info, "error": "Event not found"})
        
        # Step 2: Check existing registration
        existing_registration = execute_query_one(
            "SELECT id FROM event_registrations WHERE event_id = %s AND subscriber_email = %s",
            (event_id, email)
        )
        
        debug_info["step_2_existing_check"] = {
            "already_registered": existing_registration is not None,
            "registration_id": existing_registration['id'] if existing_registration else None
        }
        
        # Step 3: Check capacity
        current_count = execute_query_one(
            "SELECT COUNT(*) as count FROM event_registrations WHERE event_id = %s",
            (event_id,)
        )
        
        debug_info["step_3_capacity_check"] = {
            "event_capacity": event_check['capacity'],
            "current_registrations": current_count['count'] if current_count else 0,
            "has_space": event_check['capacity'] == 0 or (current_count and current_count['count'] < event_check['capacity'])
        }
        
        # Step 4: Check if subscriber exists
        subscriber_check = execute_query_one("SELECT email FROM subscribers WHERE email = %s", (email,))
        debug_info["step_4_subscriber_add"] = {
            "subscriber_exists": subscriber_check is not None
        }
        
        # Step 5: Try to insert registration (dry run)
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor()
            
            # Generate test confirmation code
            import random
            import string
            test_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            
            try:
                register_query = """
                    INSERT INTO event_registrations (event_id, subscriber_email, player_name, confirmation_code)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id, confirmation_code
                """
                
                cursor.execute(register_query, (event_id, email, f"Test User", test_code))
                result = cursor.fetchone()
                
                if result:
                    conn.rollback()  # Don't actually save the test registration
                    debug_info["step_5_registration_insert"] = {
                        "can_insert": True,
                        "test_id": result['id'] if result else None,
                        "test_code": result['confirmation_code'] if result else None
                    }
                else:
                    debug_info["step_5_registration_insert"] = {
                        "can_insert": False,
                        "error": "No result returned from insert"
                    }
                    
            except Exception as e:
                conn.rollback()
                debug_info["step_5_registration_insert"] = {
                    "can_insert": False,
                    "error": str(e)
                }
                
            cursor.close()
            conn.close()
        
        # Final summary
        debug_info["final_result"] = {
            "should_work": all([
                debug_info["step_1_event_lookup"]["found"],
                not debug_info["step_2_existing_check"]["already_registered"],
                debug_info["step_3_capacity_check"]["has_space"],
                debug_info["step_5_registration_insert"]["can_insert"]
            ])
        }
        
        return jsonify({
            "success": True,
            "event_id": event_id,
            "email": email,
            "debug": debug_info
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "debug": debug_info
        }), 500


# Also add this endpoint to check what's in your database
@app.route('/api/events/<int:event_id>/check-data', methods=['GET'])
def check_event_data(event_id):
    """Check what's actually in the database for this event"""
    try:
        # Get event details
        event = execute_query_one("SELECT * FROM events WHERE id = %s", (event_id,))
        
        # Get all registrations for this event
        registrations = execute_query(
            "SELECT * FROM event_registrations WHERE event_id = %s ORDER BY registered_at DESC",
            (event_id,)
        )
        
        # Get recent subscribers
        recent_subscribers = execute_query(
            "SELECT * FROM subscribers WHERE date_added > NOW() - INTERVAL '24 hours' ORDER BY date_added DESC LIMIT 10"
        )
        
        return jsonify({
            "success": True,
            "event": dict(event) if event else None,
            "registrations": [dict(r) for r in registrations] if registrations else [],
            "registration_count": len(registrations) if registrations else 0,
            "recent_subscribers": [dict(s) for s in recent_subscribers] if recent_subscribers else []
        })
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

#=============================
# SANITY CHECK ENDPOINT
#=============================
def sanitize_email(email):
    """Clean and validate email input"""
    if not email:
        return None
        
    email = str(email).strip().lower()
    
    # Remove dangerous characters but keep valid email chars
    email = re.sub(r'[^\w\.\-@+]', '', email)
    
    # Basic email pattern check
    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        return None
        
    # Length check (RFC 5321 standard)
    if len(email) > 254:
        return None
        
    return email

def sanitize_text_input(text, max_length=1000):
    """Clean text input to prevent XSS"""
    if not text:
        return ""
    
    # Remove null bytes and control characters
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', str(text))
    
    # Limit length
    text = text[:max_length]
    
    # HTML escape to prevent XSS
    text = html.escape(text.strip())
    
    return text

def sanitize_numeric_input(value, min_val=None, max_val=None):
    """Clean and constrain numeric input"""
    try:
        num = float(value) if value else 0
        if min_val is not None and num < min_val:
            num = min_val
        if max_val is not None and num > max_val:
            num = max_val
        return num
    except (ValueError, TypeError):
        return 0

# =============================
# Event Statistics
# =============================

@app.route('/api/events/stats', methods=['GET'])
def get_event_stats():
    """Get event statistics for dashboard"""
    try:
        stats_query = """
            SELECT 
                COUNT(DISTINCT e.id) as total_events,
                COUNT(DISTINCT CASE WHEN e.date_time > CURRENT_TIMESTAMP THEN e.id END) as upcoming_events,
                COUNT(DISTINCT CASE WHEN e.date_time <= CURRENT_TIMESTAMP AND e.status = 'completed' THEN e.id END) as completed_events,
                COUNT(DISTINCT r.id) as total_registrations,
                COUNT(DISTINCT CASE WHEN r.attended = true THEN r.id END) as total_attended,
                AVG(CASE WHEN e.capacity > 0 THEN (e.current_registrations::float / e.capacity * 100) END) as avg_capacity_filled
            FROM events e
            LEFT JOIN event_registrations r ON e.id = r.event_id
            WHERE e.status != 'cancelled'
        """
        
        stats = execute_query_one(stats_query)
        
        # Get popular events
        popular_query = """
            SELECT 
                e.title,
                e.event_type,
                COUNT(r.id) as registration_count
            FROM events e
            LEFT JOIN event_registrations r ON e.id = r.event_id
            GROUP BY e.id, e.title, e.event_type
            ORDER BY registration_count DESC
            LIMIT 5
        """
        
        popular_events = execute_query(popular_query)
        
        # Get revenue stats if needed
        revenue_query = """
            SELECT 
                SUM(e.entry_fee * e.current_registrations) as total_revenue,
                AVG(e.entry_fee * e.current_registrations) as avg_revenue_per_event
            FROM events e
            WHERE e.status = 'completed'
        """
        
        revenue_stats = execute_query_one(revenue_query)
        
        return jsonify({
            "success": True,
            "stats": {
                "total_events": stats['total_events'] or 0,
                "upcoming_events": stats['upcoming_events'] or 0,
                "completed_events": stats['completed_events'] or 0,
                "total_registrations": stats['total_registrations'] or 0,
                "total_attended": stats['total_attended'] or 0,
                "avg_capacity_filled": round(stats['avg_capacity_filled'] or 0, 1),
                "total_revenue": float(revenue_stats['total_revenue'] or 0),
                "avg_revenue_per_event": float(revenue_stats['avg_revenue_per_event'] or 0)
            },
            "popular_events": popular_events or []
        })
        
    except Exception as e:
        log_error(f"Error getting event stats: {e}")
        return jsonify({"success": False, "error": str(e)}), 500 

@app.route('/api/analytics/kpis', methods=['GET'])
def get_analytics_kpis():
    """Get comprehensive KPIs for analytics dashboard"""
    try:
        days = int(request.args.get('days', 30))
        
        # Core subscriber KPIs
        subscriber_kpis = execute_query_one(f"""
            SELECT 
                COUNT(*)::integer as total_subscribers,
                COUNT(CASE WHEN date_added >= CURRENT_DATE - INTERVAL '{days} days' THEN 1 END)::integer as new_subscribers,
                COUNT(CASE WHEN date_added >= CURRENT_DATE - INTERVAL '7 days' THEN 1 END)::integer as weekly_growth,
                COUNT(CASE WHEN date_added >= CURRENT_DATE - INTERVAL '1 day' THEN 1 END)::integer as daily_growth
            FROM subscribers
            WHERE status = 'active' OR status IS NULL
        """)
        
        # Event performance KPIs
        event_kpis = execute_query_one(f"""
            SELECT 
                COUNT(DISTINCT e.id) as total_events,
                COUNT(DISTINCT CASE WHEN e.date_time >= CURRENT_DATE - INTERVAL '{days} days' THEN e.id END) as recent_events,
                COUNT(DISTINCT CASE WHEN e.date_time > CURRENT_TIMESTAMP THEN e.id END) as upcoming_events,
                COUNT(DISTINCT r.id) as total_registrations,
                COUNT(DISTINCT CASE WHEN r.attended = true THEN r.id END) as total_attended,
                COALESCE(AVG(
                    CASE WHEN e.capacity > 0 THEN 
                        (SELECT COUNT(*) FROM event_registrations WHERE event_id = e.id)::float / e.capacity * 100
                    END
                ), 0) as avg_capacity_utilization
            FROM events e
            LEFT JOIN event_registrations r ON e.id = r.event_id
            WHERE e.created_at >= CURRENT_DATE - INTERVAL '{days} days'
        """)
        
        # Revenue KPIs
        revenue_kpis = execute_query_one(f"""
            SELECT 
                COALESCE(SUM(e.entry_fee * attended_counts.attended_count), 0) as total_revenue,
                COALESCE(AVG(e.entry_fee * attended_counts.attended_count), 0) as avg_revenue_per_event,
                COUNT(CASE WHEN e.entry_fee > 0 THEN 1 END) as paid_events,
                COALESCE(SUM(CASE WHEN e.entry_fee > 0 THEN e.entry_fee * attended_counts.attended_count END), 0) as paid_events_revenue
            FROM events e
            LEFT JOIN (
                SELECT event_id, 
                    COUNT(CASE WHEN attended = true THEN 1 END) as attended_count,
                    COUNT(*) as total_registrations
                FROM event_registrations
                GROUP BY event_id
            ) attended_counts ON e.id = attended_counts.event_id
            WHERE e.date_time >= CURRENT_DATE - INTERVAL '{days} days'
        """)
        # Engagement KPIs
        engagement_kpis = execute_query_one(f"""
            SELECT 
                COUNT(DISTINCT s.email) as total_subscribers,
                COUNT(DISTINCT r.subscriber_email) as engaged_subscribers,
                COUNT(DISTINCT CASE WHEN r.attended = true THEN r.subscriber_email END) as active_attendees,
                COALESCE(
                    COUNT(DISTINCT r.subscriber_email)::float / NULLIF(COUNT(DISTINCT s.email), 0) * 100, 0
                ) as engagement_rate,
                COALESCE(
                    COUNT(DISTINCT CASE WHEN r.attended = true THEN r.subscriber_email END)::float / 
                    NULLIF(COUNT(DISTINCT r.subscriber_email), 0) * 100, 0
                ) as attendance_rate
            FROM subscribers s
            LEFT JOIN event_registrations r ON s.email = r.subscriber_email
            LEFT JOIN events e ON r.event_id = e.id
            WHERE s.date_added >= CURRENT_DATE - INTERVAL '{days} days'
            OR e.date_time >= CURRENT_DATE - INTERVAL '{days} days'
        """)
        
        # Popular event types
        event_types = execute_query(f"""
            SELECT 
                event_type,
                COUNT(*) as event_count,
                COUNT(r.id) as total_registrations,
                COALESCE(AVG(
                    CASE WHEN e.capacity > 0 THEN 
                        (SELECT COUNT(*) FROM event_registrations WHERE event_id = e.id)::float / e.capacity * 100
                    END
                ), 0) as avg_capacity_util
            FROM events e
            LEFT JOIN event_registrations r ON e.id = r.event_id
            WHERE e.date_time >= CURRENT_DATE - INTERVAL '{days} days'
            GROUP BY event_type
            ORDER BY total_registrations DESC
        """)
        
        # Growth trend data (last 30 days)
        growth_data = execute_query(f"""
            WITH RECURSIVE date_series AS (
                -- Generate a series of dates for the last N days
                SELECT CURRENT_DATE - INTERVAL '{days} days' AS date_val
                UNION ALL
                SELECT date_val + INTERVAL '1 day'
                FROM date_series
                WHERE date_val < CURRENT_DATE
            ),
            daily_signups AS (
                SELECT 
                    DATE(date_added) as signup_date,
                    COUNT(*) as new_subscribers
                FROM subscribers 
                WHERE date_added >= CURRENT_DATE - INTERVAL '{days} days'
                GROUP BY DATE(date_added)
            ),
            cumulative_data AS (
                SELECT 
                    ds.date_val as date,
                    COALESCE(ds_signup.new_subscribers, 0) as new_subscribers,
                    -- FIXED: Calculate true cumulative including subscribers before the chart period
                    (
                        SELECT COUNT(*) 
                        FROM subscribers s 
                        WHERE DATE(s.date_added) <= ds.date_val
                    ) as cumulative_subscribers
                FROM date_series ds
                LEFT JOIN daily_signups ds_signup ON ds.date_val = ds_signup.signup_date
                ORDER BY ds.date_val
            )
            SELECT 
                date,
                new_subscribers,
                cumulative_subscribers
            FROM cumulative_data
            ORDER BY date
        """, (days,))
        
        # Event registration trend
        registration_trend = execute_query(f"""
            WITH RECURSIVE date_series AS (
                SELECT CURRENT_DATE - INTERVAL '{days} days' AS date_val
                UNION ALL
                SELECT date_val + INTERVAL '1 day'
                FROM date_series
                WHERE date_val < CURRENT_DATE
            ),
            daily_registrations AS (
                SELECT 
                    DATE(r.registered_at) as reg_date,
                    COUNT(*) as registrations
                FROM event_registrations r
                JOIN events e ON r.event_id = e.id
                WHERE r.registered_at >= CURRENT_DATE - INTERVAL '{days} days'
                GROUP BY DATE(r.registered_at)
            )
            SELECT 
                ds.date_val as date,
                COALESCE(dr.registrations, 0) as registrations
            FROM date_series ds
            LEFT JOIN daily_registrations dr ON ds.date_val = dr.reg_date
            ORDER BY ds.date_val
        """)
        
        # Calculate growth rates
        previous_period_subscribers = execute_query_one(f"""
            SELECT COUNT(*) as count
            FROM subscribers
            WHERE date_added >= CURRENT_DATE - INTERVAL '{days*2} days'
            AND date_added < CURRENT_DATE - INTERVAL '{days} days'
        """)
        
        current_new = subscriber_kpis.get('new_subscribers', 0) if subscriber_kpis else 0
        previous_new = previous_period_subscribers.get('count', 0) if previous_period_subscribers else 0
        
        growth_rate = 0
        if previous_new > 0:
            growth_rate = round(((current_new - previous_new) / previous_new) * 100, 1)
        elif current_new > 0:
            growth_rate = 100
            
        # Convert datetime objects to strings for JSON serialization
        for item in growth_data or []:
            if 'date' in item and item['date']:
                item['date'] = item['date'].isoformat()
                
        for item in registration_trend or []:
            if 'date' in item and item['date']:
                item['date'] = item['date'].isoformat()
        
        return jsonify({
            "success": True,
            "kpis": {
                "subscribers": {
                    "total": subscriber_kpis.get('total_subscribers', 0) if subscriber_kpis else 0,
                    "new_this_period": current_new,
                    "weekly_growth": subscriber_kpis.get('weekly_growth', 0) if subscriber_kpis else 0,
                    "daily_growth": subscriber_kpis.get('daily_growth', 0) if subscriber_kpis else 0,
                    "growth_rate": growth_rate
                },
                "events": {
                    "total": event_kpis.get('total_events', 0) if event_kpis else 0,
                    "recent": event_kpis.get('recent_events', 0) if event_kpis else 0,
                    "upcoming": event_kpis.get('upcoming_events', 0) if event_kpis else 0,
                    "total_registrations": event_kpis.get('total_registrations', 0) if event_kpis else 0,
                    "avg_capacity_utilization": round(event_kpis.get('avg_capacity_utilization', 0), 1) if event_kpis else 0
                },
                "revenue": {
                    "total": float(revenue_kpis.get('total_revenue', 0)) if revenue_kpis else 0,
                    "avg_per_event": round(float(revenue_kpis.get('avg_revenue_per_event', 0)), 2) if revenue_kpis else 0,
                    "paid_events": revenue_kpis.get('paid_events', 0) if revenue_kpis else 0,
                    "paid_events_revenue": float(revenue_kpis.get('paid_events_revenue', 0)) if revenue_kpis else 0
                },
                "engagement": {
                    "engagement_rate": round(engagement_kpis.get('engagement_rate', 0), 1) if engagement_kpis else 0,
                    "attendance_rate": round(engagement_kpis.get('attendance_rate', 0), 1) if engagement_kpis else 0,
                    "engaged_subscribers": engagement_kpis.get('engaged_subscribers', 0) if engagement_kpis else 0,
                    "active_attendees": engagement_kpis.get('active_attendees', 0) if engagement_kpis else 0
                }
            },
            "trends": {
                "subscriber_growth": growth_data or [],
                "registration_trend": registration_trend or [],
                "event_types": event_types or []
            }
        })
        
    except Exception as e:
        log_error(f"Error getting analytics KPIs: {e}")
        return jsonify({"success": False, "error": str(e)}), 500



@app.route('/api/analytics/subscriber-data', methods=['GET'])
def get_subscriber_analytics():
    """Get subscriber analytics data"""
    try:
        days = int(request.args.get('days', 30))
        
        # Get growth data
        growth_query = """
            SELECT 
                DATE(date_added) as date,
                COUNT(*) as signups,
                SUM(COUNT(*)) OVER (ORDER BY DATE(date_added)) as cumulative
            FROM subscribers 
            WHERE date_added >= CURRENT_DATE - INTERVAL '%s days'
            GROUP BY DATE(date_added)
            ORDER BY date
        """
        growth_data = execute_query(growth_query, (days,))
        
        # Get engagement metrics
        engagement_query = """
            SELECT 
                COUNT(DISTINCT s.email) as total_subscribers,
                COUNT(DISTINCT r.subscriber_email) as active_subscribers,
                COUNT(DISTINCT CASE WHEN r.attended = true THEN r.subscriber_email END) as attending_subscribers
            FROM subscribers s
            LEFT JOIN event_registrations r ON s.email = r.subscriber_email
            WHERE s.date_added >= CURRENT_DATE - INTERVAL '%s days'
        """
        engagement_data = execute_query_one(engagement_query, (days,))
        
        return jsonify({
            "success": True,
            "growth_data": growth_data or [],
            "engagement_data": engagement_data or {}
        })
        
    except Exception as e:
        log_error(f"Error getting subscriber analytics: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/analytics/event-data', methods=['GET'])
def get_event_analytics():
    """Get event analytics data"""
    try:
        days = int(request.args.get('days', 30))
        
        # Event performance by type
        performance_query = """
            SELECT 
                event_type,
                COUNT(*) as event_count,
                COUNT(r.id) as total_registrations,
                COUNT(CASE WHEN r.attended = true THEN r.id END) as total_attended,
                AVG(CASE WHEN e.capacity > 0 THEN (COUNT(r.id)::float / e.capacity * 100) END) as avg_capacity_util
            FROM events e
            LEFT JOIN event_registrations r ON e.id = r.event_id
            WHERE e.date_time >= CURRENT_DATE - INTERVAL '%s days'
            GROUP BY event_type
        """
        performance_data = execute_query(performance_query, (days,))
        
        # Popular games
        games_query = """
            SELECT 
                game_title,
                COUNT(*) as event_count,
                COUNT(r.id) as total_registrations
            FROM events e
            LEFT JOIN event_registrations r ON e.id = r.event_id
            WHERE e.game_title IS NOT NULL 
            AND e.date_time >= CURRENT_DATE - INTERVAL '%s days'
            GROUP BY game_title
            ORDER BY total_registrations DESC
            LIMIT 10
        """
        games_data = execute_query(games_query, (days,))
        
        # Registration timeline
        timeline_query = """
            SELECT 
                DATE(r.registered_at) as date,
                COUNT(*) as registrations
            FROM event_registrations r
            JOIN events e ON r.event_id = e.id
            WHERE r.registered_at >= CURRENT_DATE - INTERVAL '%s days'
            GROUP BY DATE(r.registered_at)
            ORDER BY date
        """
        timeline_data = execute_query(timeline_query, (days,))
        
        return jsonify({
            "success": True,
            "performance_data": performance_data or [],
            "games_data": games_data or [],
            "timeline_data": timeline_data or []
        })
        
    except Exception as e:
        log_error(f"Error getting event analytics: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/analytics/revenue-data', methods=['GET'])
def get_revenue_analytics():
    """Get revenue analytics data"""
    try:
        days = int(request.args.get('days', 30))
        
        # Revenue by event type
        revenue_query = """
            SELECT 
                e.event_type,
                SUM(e.entry_fee * COUNT(r.id)) as total_revenue,
                AVG(e.entry_fee * COUNT(r.id)) as avg_revenue,
                COUNT(DISTINCT e.id) as event_count
            FROM events e
            LEFT JOIN event_registrations r ON e.id = r.event_id
            WHERE e.date_time >= CURRENT_DATE - INTERVAL '%s days'
            AND e.entry_fee > 0
            GROUP BY e.event_type, e.id
        """
        revenue_data = execute_query(revenue_query, (days,))
        
        # Monthly revenue trend
        monthly_query = """
            SELECT 
                DATE_TRUNC('week', e.date_time) as week,
                SUM(e.entry_fee * COUNT(r.id)) as weekly_revenue
            FROM events e
            LEFT JOIN event_registrations r ON e.id = r.event_id
            WHERE e.date_time >= CURRENT_DATE - INTERVAL '%s days'
            GROUP BY DATE_TRUNC('week', e.date_time), e.id
            ORDER BY week
        """
        monthly_data = execute_query(monthly_query, (days,))
        
        return jsonify({
            "success": True,
            "revenue_by_type": revenue_data or [],
            "monthly_revenue": monthly_data or []
        })
        
    except Exception as e:
        log_error(f"Error getting revenue analytics: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/analytics/insights', methods=['GET'])
def get_analytics_insights():
    """Get detailed analytics insights"""
    try:
        days = int(request.args.get('days', 30))
        
        # Top performing events
        top_events_query = """
            SELECT 
                e.title,
                e.event_type,
                COUNT(r.id) as registration_count,
                COUNT(CASE WHEN r.attended = true THEN r.id END) as attendance_count,
                e.entry_fee * COUNT(r.id) as revenue
            FROM events e
            LEFT JOIN event_registrations r ON e.id = r.event_id
            WHERE e.date_time >= CURRENT_DATE - INTERVAL '%s days'
            GROUP BY e.id, e.title, e.event_type, e.entry_fee
            ORDER BY registration_count DESC
            LIMIT 5
        """
        top_events = execute_query(top_events_query, (days,))
        
        # Peak registration times
        peak_times_query = """
            SELECT 
                EXTRACT(hour FROM r.registered_at) as hour,
                COUNT(*) as registration_count
            FROM event_registrations r
            WHERE r.registered_at >= CURRENT_DATE - INTERVAL '%s days'
            GROUP BY EXTRACT(hour FROM r.registered_at)
            ORDER BY registration_count DESC
            LIMIT 5
        """
        peak_times = execute_query(peak_times_query, (days,))
        
        return jsonify({
            "success": True,
            "top_events": top_events or [],
            "peak_times": peak_times or []
        })
        
    except Exception as e:
        log_error(f"Error getting analytics insights: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

def calculate_growth_rate(subscriber_data):
    """Calculate subscriber growth rate"""
    try:
        if not subscriber_data.get('growth_data'):
            return 0
        
        growth_data = subscriber_data['growth_data']
        if len(growth_data) < 2:
            return 0
            
        current_week = sum(day['signups'] for day in growth_data[-7:])
        previous_week = sum(day['signups'] for day in growth_data[-14:-7])
        
        if previous_week == 0:
            return 100 if current_week > 0 else 0
            
        growth_rate = ((current_week - previous_week) / previous_week) * 100
        return round(growth_rate, 1)
        
    except Exception:
        return 0

def calculate_conversion_rate(subscriber_data, event_data):
    """Calculate conversion rate (subscribers who attend events)"""
    try:
        engagement = subscriber_data.get('engagement_data', {})
        total = engagement.get('total_subscribers', 0)
        active = engagement.get('attending_subscribers', 0)
        
        if total == 0:
            return 0
            
        return round((active / total) * 100, 1)
        
    except Exception:
        return 0

def calculate_engagement_rate(subscriber_data, event_data):
    """Calculate engagement rate (subscribers who register for events)"""
    try:
        engagement = subscriber_data.get('engagement_data', {})
        total = engagement.get('total_subscribers', 0)
        active = engagement.get('active_subscribers', 0)
        
        if total == 0:
            return 0
            
        return round((active / total) * 100, 1)
        
    except Exception:
        return 0

def calculate_avg_revenue(revenue_data):
    """Calculate average revenue per event"""
    try:
        revenue_by_type = revenue_data.get('revenue_by_type', [])
        if not revenue_by_type:
            return 0
            
        total_revenue = sum(item.get('total_revenue', 0) for item in revenue_by_type)
        total_events = sum(item.get('event_count', 0) for item in revenue_by_type)
        
        if total_events == 0:
            return 0
            
        return round(total_revenue / total_events, 2)
        
    except Exception:
        return 0

def calculate_attendance_rate(event_data):
    """Calculate attendance rate"""
    try:
        performance_data = event_data.get('performance_data', [])
        if not performance_data:
            return 0
            
        total_registrations = sum(item.get('total_registrations', 0) for item in performance_data)
        total_attended = sum(item.get('total_attended', 0) for item in performance_data)
        
        if total_registrations == 0:
            return 0
            
        return round((total_attended / total_registrations) * 100, 1)
        
    except Exception:
        return 0

def calculate_capacity_utilization(event_data):
    """Calculate average capacity utilization"""
    try:
        performance_data = event_data.get('performance_data', [])
        if not performance_data:
            return 0
            
        utilizations = [item.get('avg_capacity_util', 0) for item in performance_data if item.get('avg_capacity_util')]
        
        if not utilizations:
            return 0
            
        return round(sum(utilizations) / len(utilizations), 1)
        
    except Exception:
        return 0

# =============================
# Main
# =============================
if __name__ == '__main__':
    try:
        print("🚀 SideQuest Backend starting...")
        print("=" * 50)
        
        # Initialize database
        print("🗄️  Initializing database...")
        if init_database():
            print("✅ Database ready!")
        else:
            print("❌ Database initialization failed!")
        
        # Test Brevo connection
        print("🧪 Testing Brevo API connection...")
        brevo_connected, brevo_status, brevo_email = test_brevo_connection()
        if brevo_connected:
            print(f"✅ Brevo connection successful - {brevo_email}")
        else:
            print(f"❌ Brevo connection failed: {brevo_status}")
            print("⚠️  Email campaigns and sync features may not work")
        
        log_activity("SideQuest Backend started with PostgreSQL", "info")
        
        print(f"📧 Sender email: {SENDER_EMAIL}")
        print(f"🔄 Brevo Auto-Sync: {'ON' if AUTO_SYNC_TO_BREVO else 'OFF'}")
        print(f"📋 Brevo List ID: {BREVO_LIST_ID}")
        print(f"🗄️  Database: PostgreSQL")
        print(f"🌐 Server running on all interfaces")
        print(f"📱 Signup page: http://localhost:4000/signup")
        print(f"🔧 Admin dashboard: http://localhost:4000/admin")
        print(f"📊 API Health check: http://localhost:4000/health")
        print("=" * 50)
        print("✅ SideQuest backend ready! 🎮")
        
        port = int(os.environ.get('PORT', 4000))
        app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
        
    except KeyboardInterrupt:
        print("\n🛑 Server stopped by user")
        log_activity("Server stopped by user", "info")
    except Exception as e:
        print(f"❌ Critical error starting server: {e}")
        print(f"Traceback: {traceback.format_exc()}")
        log_activity(f"Critical startup error: {str(e)}", "danger")
    finally:
        print("🔄 Server shutdown complete")


