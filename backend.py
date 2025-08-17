from __future__ import annotations

# =============================
# SideQuest Newsletter Backend
# With PostgreSQL Database Support
# =============================

import os
import re
import json
import traceback
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
from collections import defaultdict

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# =============================
# --- CONFIG & GLOBALS FIRST ---
# =============================

app = Flask(__name__, static_folder="static")
CORS(app)

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
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "jaiamiscua@gmail.com")
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

def init_database():
    """Initialize database tables"""
    try:
        conn = get_db_connection()
        if not conn:
            print("❌ Could not connect to database")
            return False
            
        cursor = conn.cursor()
        
        # Create subscribers table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS subscribers (
                id SERIAL PRIMARY KEY,
                email VARCHAR(255) UNIQUE NOT NULL,
                date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                source VARCHAR(100) DEFAULT 'manual',
                status VARCHAR(50) DEFAULT 'active'
            )
        ''')
        
        # Create activity log table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS activity_log (
                id SERIAL PRIMARY KEY,
                message TEXT NOT NULL,
                type VARCHAR(50) DEFAULT 'info',
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create events table
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
        
        # Create event registrations table
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
        
        # Create event emails table for automated reminders
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS event_emails (
                id SERIAL PRIMARY KEY,
                event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
                email_type VARCHAR(100) NOT NULL,
                scheduled_for TIMESTAMP NOT NULL,
                sent BOOLEAN DEFAULT FALSE,
                sent_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(event_id, email_type)
            )
        ''')
        
        # Create indexes for better performance
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_events_date_time ON events(date_time);
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_events_status ON events(status);
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_event_registrations_event_id ON event_registrations(event_id);
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_event_registrations_email ON event_registrations(subscriber_email);
        ''')
        
        conn.commit()
        cursor.close()
        conn.close()
        
        print("✅ Database tables initialized successfully (including events)")
        return True
        
    except Exception as e:
        print(f"❌ Database initialization error: {e}")
        return False

# =============================
# Database Helper Functions
# =============================

def add_subscriber_to_db(email, source='manual'):
    """Add subscriber to database"""
    try:
        conn = get_db_connection()
        if not conn:
            return False
            
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO subscribers (email, source) VALUES (%s, %s) ON CONFLICT (email) DO NOTHING",
            (email, source)
        )
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
    """Get all subscribers from database"""
    try:
        conn = get_db_connection()
        if not conn:
            return []
            
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM subscribers ORDER BY date_added DESC")
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

def add_to_brevo_list(email: str) -> dict:
    if not AUTO_SYNC_TO_BREVO:
        return {"success": True, "message": "Brevo sync disabled"}
    if not contacts_api:
        return {"success": False, "error": "Brevo API not initialized"}
    try:
        create_contact = sib_api_v3_sdk.CreateContact(  # type: ignore
            email=email,
            list_ids=[BREVO_LIST_ID],
            email_blacklisted=False,
            sms_blacklisted=False,
            update_enabled=True,
        )
        contacts_api.create_contact(create_contact)
        log_activity(f"Added {email} to Brevo list {BREVO_LIST_ID}", "success")
        return {"success": True, "message": f"Added to Brevo list {BREVO_LIST_ID}"}
    except ApiException as e:  # type: ignore
        error_msg = str(e)
        if "duplicate_parameter" in error_msg or "already exists" in error_msg.lower():
            try:
                contacts_api.add_contact_to_list(  # type: ignore
                    BREVO_LIST_ID,
                    sib_api_v3_sdk.AddContactToList(emails=[email])  # type: ignore
                )
                log_activity(f"Added existing contact {email} to Brevo list", "success")
                return {"success": True, "message": "Added existing contact to list"}
            except Exception as e2:
                log_activity(f"Error adding existing contact {email}: {str(e2)}", "danger")
                return {"success": True, "message": "Contact already in Brevo"}
        else:
            log_activity(f"Brevo API Error for {email}: {error_msg}", "danger")
            print(f"Brevo API Error: {error_msg}")
            return {"success": False, "error": error_msg}
    except Exception as e:
        log_activity(f"Unexpected error adding {email} to Brevo: {str(e)}", "danger")
        return {"success": False, "error": str(e)}

def remove_from_brevo_list(email: str) -> dict:
    if not AUTO_SYNC_TO_BREVO:
        return {"success": True, "message": "Brevo sync disabled"}
    if not contacts_api:
        return {"success": False, "error": "Brevo API not initialized"}
    try:
        contacts_api.remove_contact_from_list(  # type: ignore
            BREVO_LIST_ID,
            sib_api_v3_sdk.RemoveContactFromList(emails=[email])  # type: ignore
        )
        log_activity(f"Removed {email} from Brevo list {BREVO_LIST_ID}", "success")
        return {"success": True, "message": f"Removed from Brevo list {BREVO_LIST_ID}"}
    except ApiException as e:  # type: ignore
        log_activity(f"Brevo API Error removing {email}: {str(e)}", "danger")
        print(f"Brevo API Error: {e}")
        return {"success": False, "error": str(e)}
    except Exception as e:
        log_activity(f"Unexpected error removing {email}: {str(e)}", "danger")
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
def log_request_info():
    # Only log non-routine requests to avoid spam
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
def health_check():
    try:
        brevo_connected, brevo_status, brevo_email = test_brevo_connection()
        
        # Test database connection
        db_connected = get_db_connection() is not None
        
        return jsonify({
            "status": "healthy",
            "subscribers_count": len(get_all_subscribers()),
            "brevo_sync": AUTO_SYNC_TO_BREVO,
            "brevo_status": "connected" if brevo_connected else brevo_status,
            "brevo_email": brevo_email,
            "brevo_list_id": BREVO_LIST_ID,
            "activities": len(get_activity_log(100)),
            "api_instances_initialized": (api_instance is not None and contacts_api is not None),
            "database_connected": db_connected,
        })
    except Exception as e:
        error_msg = f"Health check error: {str(e)}"
        print(f"Health check error: {traceback.format_exc()}")
        return jsonify({
            "status": "error",
            "error": error_msg,
            "brevo_status": "error",
            "database_connected": False,
        }), 500

@app.route('/subscribers', methods=['GET'])
def get_subscribers():
    try:
        subscribers = get_all_subscribers()
        stats = get_signup_stats()
        
        return jsonify({
            "success": True,
            "subscribers": [sub['email'] for sub in subscribers],
            "subscriber_details": subscribers,
            "count": len(subscribers),
            "stats": stats,
        })
    except Exception as e:
        error_msg = f"Error getting subscribers: {str(e)}"
        print(f"Subscribers error: {traceback.format_exc()}")
        return jsonify({"success": False, "error": error_msg}), 500

@app.route('/subscribe', methods=['POST'])
def add_subscriber():
    try:
        data = request.json or {}
        email = str(data.get('email', '')).strip().lower()
        source = data.get('source', 'manual')
        
        if not email:
            return jsonify({"success": False, "error": "Email is required"}), 400
        if not is_valid_email(email):
            return jsonify({"success": False, "error": "Invalid email format"}), 400
        
        # Check if already exists
        existing_subscribers = get_all_subscribers()
        if any(sub['email'] == email for sub in existing_subscribers):
            return jsonify({"success": False, "error": "Email already subscribed"}), 400
        
        # Add to database
        if add_subscriber_to_db(email, source):
            brevo_result = add_to_brevo_list(email)
            log_activity(f"New subscriber added: {email} (source: {source})", "success")
            
            return jsonify({
                "success": True,
                "message": "Subscriber added successfully",
                "email": email,
                "brevo_sync": brevo_result.get("success", False),
                "brevo_message": brevo_result.get("message", brevo_result.get("error", "")),
            })
        else:
            return jsonify({"success": False, "error": "Failed to add subscriber to database"}), 500
            
    except Exception as e:
        error_msg = f"Error adding subscriber: {str(e)}"
        print(f"Add subscriber error: {traceback.format_exc()}")
        log_activity(f"Failed to add subscriber: {error_msg}", "danger")
        return jsonify({"success": False, "error": error_msg}), 500

@app.route('/unsubscribe', methods=['POST'])
def remove_subscriber():
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
            brevo_result = remove_from_brevo_list(email)
            log_activity(f"Subscriber removed: {email}", "danger")
            
            return jsonify({
                "success": True,
                "message": "Subscriber removed",
                "email": email,
                "brevo_sync": brevo_result.get("success", False),
                "brevo_message": brevo_result.get("message", brevo_result.get("error", "")),
            })
        else:
            return jsonify({"success": False, "error": "Failed to remove subscriber from database"}), 500
            
    except Exception as e:
        error_msg = f"Error removing subscriber: {str(e)}"
        print(f"Remove subscriber error: {traceback.format_exc()}")
        log_activity(f"Failed to remove subscriber: {error_msg}", "danger")
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
    try:
        data = request.json or {}
        emails = data.get('emails', [])
        source = data.get('source', 'import')
        
        if not emails:
            return jsonify({"success": False, "error": "No emails provided"}), 400
        
        added = 0
        errors: list[str] = []
        existing_subscribers = get_all_subscribers()
        existing_emails = {sub['email'] for sub in existing_subscribers}
        
        for email in emails:
            try:
                email = str(email).strip().lower()
                if not is_valid_email(email):
                    errors.append(f"Invalid email: {email}")
                    continue
                if email in existing_emails:
                    errors.append(f"Already exists: {email}")
                    continue
                
                if add_subscriber_to_db(email, source):
                    brevo_result = add_to_brevo_list(email)
                    if not brevo_result.get("success", False):
                        errors.append(f"Brevo sync failed for {email}: {brevo_result.get('error', 'Unknown error')}")
                    added += 1
                    existing_emails.add(email)
                else:
                    errors.append(f"Database error for {email}")
                    
            except Exception as e:
                errors.append(f"Error processing {email}: {str(e)}")
                continue
        
        log_activity(f"Bulk import: {added} subscribers added, {len(errors)} errors", "info")
        
        return jsonify({
            "success": True,
            "added": added,
            "errors": errors,
            "total_processed": len(emails),
        })
        
    except Exception as e:
        error_msg = f"Error in bulk import: {str(e)}"
        print(f"Bulk import error: {traceback.format_exc()}")
        log_activity(f"Bulk import failed: {error_msg}", "danger")
        return jsonify({"success": False, "error": error_msg}), 500

@app.route('/sync-brevo', methods=['POST'])
def manual_brevo_sync():
    try:
        if not AUTO_SYNC_TO_BREVO:
            return jsonify({"success": False, "error": "Brevo sync is disabled"}), 400
        if not contacts_api:
            return jsonify({"success": False, "error": "Brevo API not initialized"}), 500
        
        subscribers = get_all_subscribers()
        success_count = 0
        error_count = 0
        errors: list[str] = []
        
        for subscriber in subscribers:
            try:
                email = subscriber['email']
                result = add_to_brevo_list(email)
                if result.get("success", False):
                    success_count += 1
                else:
                    error_count += 1
                    errors.append(f"{email}: {result.get('error', 'Unknown error')}")
            except Exception as e:
                error_count += 1
                errors.append(f"{email}: {str(e)}")
        
        log_activity(f"Manual Brevo sync: {success_count} success, {error_count} errors", "info")
        
        return jsonify({
            "success": True,
            "synced": success_count,
            "errors": error_count,
            "error_details": errors,
        })
        
    except Exception as e:
        error_msg = f"Error in manual sync: {str(e)}"
        print(f"Manual sync error: {traceback.format_exc()}")
        log_activity(f"Manual sync failed: {error_msg}", "danger")
        return jsonify({"success": False, "error": error_msg}), 500

@app.route('/clear-data', methods=['POST'])
def clear_all_data():
    try:
        data = request.json or {}
        confirmation = data.get('confirmation', '')
        if confirmation != 'DELETE':
            return jsonify({"success": False, "error": "Invalid confirmation"}), 400
        
        # Clear database tables
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM subscribers")
            cursor.execute("DELETE FROM activity_log")
            count = cursor.rowcount
            conn.commit()
            cursor.close()
            conn.close()
        else:
            return jsonify({"success": False, "error": "Database connection failed"}), 500
        
        log_activity(f"ALL DATA CLEARED - database tables emptied", "danger")
        
        return jsonify({
            "success": True,
            "message": f"Cleared all data from database",
            "note": "Brevo data not affected - manual cleanup required",
        })
    except Exception as e:
        error_msg = f"Error clearing data: {str(e)}"
        print(f"Clear data error: {traceback.format_exc()}")
        log_activity(f"Failed to clear data: {error_msg}", "danger")
        return jsonify({"success": False, "error": error_msg}), 500

# Continue with remaining routes...
@app.route('/send-campaign', methods=['POST'])
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
def admin_dashboard():
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
        .container { background: linear-gradient(135deg, #2a2a2a 0%, #3a3a3a 100%); padding: 50px; border-radius: 20px; box-shadow: 0 20px 60px rgba(0,0,0,0.5); border: 2px solid #444; max-width: 500px; width: 100%; text-align: center; position: relative; overflow: hidden; }
        .container::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 6px; background: linear-gradient(90deg, #FFD700 0%, #FFA500 100%); }
        .logo { width: 60px; height: 60px; background: linear-gradient(135deg, #FFD700 0%, #FFA500 100%); border-radius: 12px; margin: 0 auto 20px; display: flex; align-items: center; justify-content: center; font-weight: 900; color: #1a1a1a; font-size: 18px; letter-spacing: -1px; box-shadow: 0 8px 25px rgba(255, 215, 0, 0.3); }
        h1 { font-size: 2.5rem; margin-bottom: 15px; background: linear-gradient(135deg, #FFD700 0%, #FFA500 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; font-weight: 800; }
        .subtitle { font-size: 1.2rem; margin-bottom: 30px; color: #cccccc; font-weight: 500; line-height: 1.5; }
        .form-container { margin: 30px 0; }
        input[type="email"] { width: 100%; padding: 18px 25px; border: 2px solid #444; border-radius: 12px; font-size: 16px; background: #1a1a1a; color: #ffffff; transition: all 0.3s ease; font-weight: 500; margin-bottom: 20px; }
        input[type="email"]:focus { outline: none; border-color: #FFD700; box-shadow: 0 0 0 4px rgba(255, 215, 0, 0.2); background: #2a2a2a; }
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
        .footer-links { margin-top: 30px; padding-top: 20px; border-top: 1px solid #444; font-size: 12px; color: #888; }
        .footer-links a { color: #FFD700; text-decoration: none; margin: 0 10px; transition: color 0.3s ease; }
    </style>
</head>
<body>
    <div class="container">
        <div class="logo">SQ</div>
        <h1>Join the Quest</h1>
        <p class="subtitle">Get exclusive gaming updates, events, and special offers delivered straight to your inbox!</p>
        <form class="form-container" id="signupForm">
            <input type="email" id="email" placeholder="Enter your email address" required>
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
            <a href="#" onclick="showPrivacyInfo()">Privacy</a>
        </div>
    </div>
    <script>
        document.getElementById('signupForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const email = document.getElementById('email').value;
            const messageDiv = document.getElementById('message');
            const submitButton = document.getElementById('submitBtn');
            submitButton.innerHTML = 'Joining Quest...';
            submitButton.disabled = true;
            try {
                const response = await fetch('/subscribe', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email, source: 'web' })
                });
                const data = await response.json();
                if (data.success) {
                    messageDiv.className = 'message success show';
                    messageDiv.innerHTML = '🎮 Welcome to the SideQuest community!';
                    document.getElementById('email').value = '';
                    submitButton.innerHTML = '✅ Quest Joined!';
                    setTimeout(() => { submitButton.innerHTML = 'Level Up Your Inbox'; submitButton.disabled = false; }, 3000);
                } else {
                    messageDiv.className = 'message error show';
                    messageDiv.innerHTML = '❌ ' + (data.error || 'Something went wrong');
                    submitButton.innerHTML = 'Level Up Your Inbox';
                    submitButton.disabled = false;
                }
            } catch (error) {
                messageDiv.className = 'message error show';
                messageDiv.innerHTML = '❌ Connection error. Please try again later.';
                submitButton.innerHTML = 'Level Up Your Inbox';
                submitButton.disabled = false;
            }
        });
    </script>
</body>
</html>'''
    return signup_html

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

@app.route('/api/events', methods=['POST'])
def create_event():
    """Create a new event"""
    try:
        data = request.json or {}
        
        # Log the incoming request
        log_activity(f"Received create_event request: {data}", "info")
        
        # Validate required fields
        required_fields = ['title', 'event_type', 'date_time']
        for field in required_fields:
            if field not in data:
                log_error(f"Missing required field: {field}")
                return jsonify({"success": False, "error": f"{field} is required"}), 400
        
        # Parse date_time
        try:
            date_time = datetime.fromisoformat(data['date_time'].replace('Z', '+00:00'))
            log_activity(f"Parsed date_time: {date_time}", "info")
        except Exception as e:
            log_error(f"Date parsing error: {e}")
            return jsonify({"success": False, "error": "Invalid date_time format"}), 400
            
        # Parse end_time if provided
        end_time = None
        if data.get('end_time'):
            try:
                end_time = datetime.fromisoformat(data['end_time'].replace('Z', '+00:00'))
                log_activity(f"Parsed end_time: {end_time}", "info")
            except Exception as e:
                log_error(f"End time parsing error: {e}")
                pass
                
        query = """
            INSERT INTO events (
                title, event_type, game_title, date_time, end_time,
                capacity, description, entry_fee, prize_pool, status,
                image_url, requirements
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """
        
        params = (
            data['title'],
            data['event_type'],
            data.get('game_title'),
            date_time,
            end_time,
            data.get('capacity', 0),
            data.get('description', ''),
            data.get('entry_fee', 0),
            data.get('prize_pool'),
            data.get('status', 'draft'),
            data.get('image_url'),
            data.get('requirements')
        )
        
        log_activity(f"About to execute query: {query}", "info")
        log_activity(f"With params: {params}", "info")
        
        # Execute the query and get detailed feedback
        result = execute_query_one(query, params)
        
        log_activity(f"Query result type: {type(result)}", "info")
        log_activity(f"Query result value: {result}", "info")
        
        if result is None:
            log_error("execute_query_one returned None - check database connection and query")
            return jsonify({"success": False, "error": "Database query failed - check logs"}), 500
        
        if isinstance(result, dict) and 'id' in result:
            event_id = result['id']
            log_activity(f"Successfully created event: {data['title']} (ID: {event_id})", "success")
            
            # Verify the event was actually inserted
            verify_query = "SELECT id, title FROM events WHERE id = %s"
            verification = execute_query_one(verify_query, (event_id,))
            
            if verification:
                log_activity(f"Event verification successful: {verification}", "success")
            else:
                log_error(f"Event was not found after insert! ID: {event_id}")
                return jsonify({"success": False, "error": "Event creation failed - not found after insert"}), 500
            
            return jsonify({
                "success": True,
                "event_id": event_id,
                "message": "Event created successfully"
            })
        else:
            log_error(f"Unexpected result format from execute_query_one: {result}")
            return jsonify({"success": False, "error": "Unexpected database response format"}), 500
            
    except Exception as e:
        log_error(f"Error creating event: {e}")
        log_error(f"Full traceback: {traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/events/<int:event_id>', methods=['DELETE'])
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
def register_for_event(event_id):
    """Register a subscriber for an event"""
    try:
        data = request.json or {}
        email = data.get('email', '').strip().lower()
        player_name = data.get('player_name', '')
        
        if not email:
            return jsonify({"success": False, "error": "Email is required"}), 400
            
        if not is_valid_email(email):
            return jsonify({"success": False, "error": "Invalid email format"}), 400
        
        # Check if event exists
        event_check = execute_query_one("SELECT id, title, capacity FROM events WHERE id = %s", (event_id,))
        if not event_check:
            return jsonify({"success": False, "error": "Event not found"}), 404
        
        # Auto-add to subscribers if not exists (this allows anyone to register)
        subscriber_check = execute_query_one("SELECT email FROM subscribers WHERE email = %s", (email,))
        if not subscriber_check:
            # Add to subscribers automatically
            if add_subscriber_to_db(email, 'event_registration'):
                log_activity(f"Auto-added {email} to subscribers via event registration", "info")
            else:
                log_activity(f"Failed to auto-add {email} to subscribers, but allowing registration", "warning")
        
        # Check if already registered
        existing_registration = execute_query_one(
            "SELECT id FROM event_registrations WHERE event_id = %s AND subscriber_email = %s",
            (event_id, email)
        )
        if existing_registration:
            return jsonify({"success": False, "error": "Already registered for this event"}), 400
        
        # Check capacity
        if event_check['capacity'] > 0:
            current_count = execute_query_one(
                "SELECT COUNT(*) as count FROM event_registrations WHERE event_id = %s",
                (event_id,)
            )
            if current_count and current_count['count'] >= event_check['capacity']:
                return jsonify({"success": False, "error": "Event is at full capacity"}), 400
        
        # Generate confirmation code
        import random
        import string
        confirmation_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        
        # Register for event
        register_query = """
            INSERT INTO event_registrations (event_id, subscriber_email, player_name, confirmation_code)
            VALUES (%s, %s, %s, %s)
            RETURNING id
        """
        
        result = execute_query_one(register_query, (event_id, email, player_name or email.split('@')[0], confirmation_code))
        
        if result:
            log_activity(f"Registered {email} for event: {event_check['title']}", "success")
            return jsonify({
                "success": True,
                "message": "Registration successful",
                "confirmation_code": confirmation_code,
                "event_title": event_check['title']
            })
        else:
            log_error("Registration query failed - check if event_registrations table exists")
            return jsonify({"success": False, "error": "Registration failed - database issue"}), 500
            
    except Exception as e:
        log_error(f"Error registering for event {event_id}: {e}")
        log_error(f"Full traceback: {traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/events/<int:event_id>/attendees', methods=['GET'])
def get_event_attendees(event_id):
    """Get list of attendees for an event"""
    try:
        # Check if event exists
        event_check = execute_query_one("SELECT id, title FROM events WHERE id = %s", (event_id,))
        if not event_check:
            return jsonify({"success": False, "error": "Event not found"}), 404
        
        # Get attendees
        attendees_query = """
            SELECT 
                subscriber_email,
                player_name,
                confirmation_code,
                registered_at,
                attended,
                check_in_time,
                notes
            FROM event_registrations 
            WHERE event_id = %s 
            ORDER BY registered_at ASC
        """
        
        attendees = execute_query(attendees_query, (event_id,))
        
        if attendees is None:
            return jsonify({"success": False, "error": "Database error"}), 500
        
        # Convert datetime objects to ISO format
        for attendee in attendees:
            if attendee['registered_at']:
                attendee['registered_at'] = attendee['registered_at'].isoformat()
            if attendee['check_in_time']:
                attendee['check_in_time'] = attendee['check_in_time'].isoformat()
        
        return jsonify({
            "success": True,
            "attendees": attendees,
            "event_title": event_check['title'],
            "total_count": len(attendees)
        })
        
    except Exception as e:
        log_error(f"Error getting attendees for event {event_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/events/<int:event_id>/checkin', methods=['POST'])
def checkin_attendee(event_id):
    """Check in an attendee for an event"""
    try:
        data = request.json or {}
        email = data.get('email', '').strip().lower()
        notes = data.get('notes', '')
        
        if not email:
            return jsonify({"success": False, "error": "Email is required"}), 400
        
        # Check if registration exists
        registration_check = execute_query_one(
            "SELECT id, attended FROM event_registrations WHERE event_id = %s AND subscriber_email = %s",
            (event_id, email)
        )
        
        if not registration_check:
            return jsonify({"success": False, "error": "Registration not found"}), 404
        
        if registration_check['attended']:
            return jsonify({"success": False, "error": "Already checked in"}), 400
        
        # Check in attendee
        checkin_query = """
            UPDATE event_registrations 
            SET attended = TRUE, check_in_time = CURRENT_TIMESTAMP, notes = %s
            WHERE event_id = %s AND subscriber_email = %s
            RETURNING confirmation_code
        """
        
        result = execute_query_one(checkin_query, (notes, event_id, email))
        
        if result:
            log_activity(f"Checked in {email} for event ID {event_id}", "success")
            return jsonify({
                "success": True,
                "message": "Check-in successful",
                "confirmation_code": result['confirmation_code']
            })
        else:
            return jsonify({"success": False, "error": "Check-in failed"}), 500
            
    except Exception as e:
        log_error(f"Error checking in attendee for event {event_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

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


