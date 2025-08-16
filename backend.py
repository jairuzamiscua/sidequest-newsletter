from __future__ import annotations

# =============================
# SideQuest Newsletter Backend
# Fixed & hardened for local + Railway deployment
# =============================

import os
import re
import random
import string
import json
import traceback
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
    from sib_api_v3_sdk.rest import ApiException
except Exception:
    sib_api_v3_sdk = None
    ApiException = Exception

# ---- PostgreSQL imports ----
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("❌ psycopg2 not installed. Install with: pip install psycopg2-binary")
    psycopg2 = None
    RealDictCursor = None

# ---- Brevo settings ----
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
BREVO_LIST_ID = int(os.environ.get("BREVO_LIST_ID", 2))
AUTO_SYNC_TO_BREVO = os.environ.get("AUTO_SYNC_TO_BREVO", "true").lower() in {"1", "true", "yes", "y"}
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "jaiamiscua@gmail.com")
SENDER_NAME = os.environ.get("SENDER_NAME", "SideQuest")

# ---- In-memory stores (fallback when DB unavailable) ----
subscribers_data: dict[str, dict] = {}
activity_log: list[dict] = []

# =============================
# Database Connection & Setup
# =============================

def get_db_connection():
    """Get PostgreSQL connection using Railway's DATABASE_URL"""
    if not psycopg2:
        return None
        
    try:
        database_url = os.environ.get('DATABASE_URL')
        if database_url:
            # Fix Railway's postgres:// URL for psycopg2
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
    """Execute a query and return single result"""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        if not conn:
            return None
        cursor = conn.cursor()
        cursor.execute(query, params)
        result = cursor.fetchone()
        return result
    except Exception as e:
        log_error(f"Query execution error: {e}")
        return None
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def init_database():
    """Initialize database tables if they don't exist"""
    if not psycopg2:
        log_activity("PostgreSQL not available - using in-memory storage", "warning")
        return False
        
    try:
        conn = get_db_connection()
        if not conn:
            return False
            
        cursor = conn.cursor()
        
        # Create subscribers table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS subscribers (
                email VARCHAR(255) PRIMARY KEY,
                date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                source VARCHAR(50) DEFAULT 'unknown',
                status VARCHAR(20) DEFAULT 'active'
            )
        """)
        
        # Create events table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id SERIAL PRIMARY KEY,
                title VARCHAR(255) NOT NULL,
                event_type VARCHAR(50) NOT NULL,
                game_title VARCHAR(255),
                date_time TIMESTAMP NOT NULL,
                end_time TIMESTAMP,
                capacity INTEGER DEFAULT 0,
                current_registrations INTEGER DEFAULT 0,
                description TEXT,
                entry_fee DECIMAL(10,2) DEFAULT 0,
                prize_pool VARCHAR(100),
                status VARCHAR(20) DEFAULT 'draft',
                image_url TEXT,
                requirements TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create event registrations table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS event_registrations (
                id SERIAL PRIMARY KEY,
                event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
                subscriber_email VARCHAR(255) NOT NULL,
                player_name VARCHAR(255),
                team_name VARCHAR(255),
                notes TEXT,
                confirmation_code VARCHAR(20),
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                attended BOOLEAN DEFAULT FALSE,
                checked_in_at TIMESTAMP,
                UNIQUE(event_id, subscriber_email)
            )
        """)
        
        # Create event emails table for scheduling
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS event_emails (
                id SERIAL PRIMARY KEY,
                event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
                email_type VARCHAR(50) NOT NULL,
                scheduled_for TIMESTAMP NOT NULL,
                sent_at TIMESTAMP,
                status VARCHAR(20) DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(event_id, email_type)
            )
        """)
        
        conn.commit()
        cursor.close()
        conn.close()
        
        log_activity("Database tables initialized successfully", "success")
        return True
        
    except Exception as e:
        log_error(f"Database initialization error: {e}")
        return False

# =============================
# Helper Functions
# =============================

def log_activity(message: str, activity_type: str = "info") -> None:
    try:
        activity = {
            "message": message,
            "type": activity_type,
            "timestamp": datetime.now().isoformat(),
        }
        activity_log.insert(0, activity)
        if len(activity_log) > 100:
            del activity_log[100:]
        print(f"[{activity_type.upper()}] {message}")
    except Exception as e:
        print(f"Error logging activity: {e}")

def log_error(error: Exception | str, error_type: str = "error") -> None:
    err = str(error)
    log_activity(f"Error: {err}", error_type)
    print(f"Error [{error_type}]: {err}")
    if isinstance(error, Exception):
        print(f"Traceback: {traceback.format_exc()}")

def is_valid_email(email: str) -> bool:
    try:
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return re.match(pattern, email) is not None
    except Exception:
        return False

# =============================
# Brevo client initialization
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
    except Exception as e:
        print(f"❌ Error initializing Brevo API instances: {e}")
        api_instance = None
        contacts_api = None
else:
    if not BREVO_API_KEY:
        print("⚠️  BREVO_API_KEY not set — Brevo features disabled.")

def test_brevo_connection() -> tuple[bool, str, str | None]:
    """Test Brevo API connection"""
    if sib_api_v3_sdk is None or configuration is None:
        return False, "Brevo SDK not available", None
    try:
        account_api = sib_api_v3_sdk.AccountApi(sib_api_v3_sdk.ApiClient(configuration))
        account_info = account_api.get_account()
        print("✅ Brevo API connected successfully!")
        print(f"📧 Account email: {getattr(account_info, 'email', None)}")
        return True, "connected", getattr(account_info, 'email', None)
    except ApiException as e:
        log_error(e, "api_error")
        return False, f"Brevo API Error: {str(e)}", None
    except Exception as e:
        log_error(e, "api_error")
        return False, f"Unexpected error: {str(e)}", None

# =============================
# Brevo Integration Functions
# =============================

def add_to_brevo_list(email: str) -> dict:
    if not AUTO_SYNC_TO_BREVO:
        return {"success": True, "message": "Brevo sync disabled"}
    if not contacts_api:
        return {"success": False, "error": "Brevo API not initialized"}
    try:
        create_contact = sib_api_v3_sdk.CreateContact(
            email=email,
            list_ids=[BREVO_LIST_ID],
            email_blacklisted=False,
            sms_blacklisted=False,
            update_enabled=True,
        )
        contacts_api.create_contact(create_contact)
        log_activity(f"Added {email} to Brevo list {BREVO_LIST_ID}", "success")
        return {"success": True, "message": f"Added to Brevo list {BREVO_LIST_ID}"}
    except ApiException as e:
        error_msg = str(e)
        if "duplicate_parameter" in error_msg or "already exists" in error_msg.lower():
            try:
                contacts_api.add_contact_to_list(
                    BREVO_LIST_ID,
                    sib_api_v3_sdk.AddContactToList(emails=[email])
                )
                log_activity(f"Added existing contact {email} to Brevo list", "success")
                return {"success": True, "message": "Added existing contact to list"}
            except Exception as e2:
                log_activity(f"Error adding existing contact {email}: {str(e2)}", "danger")
                return {"success": True, "message": "Contact already in Brevo"}
        else:
            log_activity(f"Brevo API Error for {email}: {error_msg}", "danger")
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
        contacts_api.remove_contact_from_list(
            BREVO_LIST_ID,
            sib_api_v3_sdk.RemoveContactFromList(emails=[email])
        )
        log_activity(f"Removed {email} from Brevo list {BREVO_LIST_ID}", "success")
        return {"success": True, "message": f"Removed from Brevo list {BREVO_LIST_ID}"}
    except ApiException as e:
        log_activity(f"Brevo API Error removing {email}: {str(e)}", "danger")
        return {"success": False, "error": str(e)}
    except Exception as e:
        log_activity(f"Unexpected error removing {email}: {str(e)}", "danger")
        return {"success": False, "error": str(e)}

# =============================
# Database-aware subscriber functions
# =============================

def get_all_subscribers_from_db():
    """Get all subscribers from database"""
    query = "SELECT email, date_added, source, status FROM subscribers ORDER BY date_added DESC"
    return execute_query(query) or []

def add_subscriber_to_db(email: str, source: str = 'manual'):
    """Add subscriber to database"""
    query = """
        INSERT INTO subscribers (email, source, status) 
        VALUES (%s, %s, 'active') 
        ON CONFLICT (email) DO NOTHING
        RETURNING email
    """
    return execute_query_one(query, (email, source))

def remove_subscriber_from_db(email: str):
    """Remove subscriber from database"""
    query = "DELETE FROM subscribers WHERE email = %s RETURNING email"
    return execute_query_one(query, (email,))

def get_signup_stats() -> dict:
    """Get signup statistics"""
    try:
        # Try database first
        if psycopg2:
            stats_query = """
                SELECT 
                    COUNT(*) as total,
                    COUNT(CASE WHEN date_added::date = CURRENT_DATE THEN 1 END) as today,
                    COUNT(CASE WHEN date_added >= CURRENT_DATE - INTERVAL '7 days' THEN 1 END) as week
                FROM subscribers
            """
            stats = execute_query_one(stats_query)
            
            source_query = "SELECT source, COUNT(*) as count FROM subscribers GROUP BY source"
            sources = execute_query(source_query) or []
            source_counts = {item['source']: item['count'] for item in sources}
            
            if stats:
                return {
                    "total": stats['total'],
                    "today": stats['today'], 
                    "week": stats['week'],
                    "sources": source_counts
                }
        
        # Fallback to in-memory
        now = datetime.now()
        today = now.date()
        week_ago = now - timedelta(days=7)
        total_subscribers = len(subscribers_data)
        today_signups = 0
        week_signups = 0
        
        for data in subscribers_data.values():
            try:
                signup_date = datetime.fromisoformat(data['date_added'])
                if signup_date.date() == today:
                    today_signups += 1
                if signup_date >= week_ago:
                    week_signups += 1
            except (ValueError, KeyError):
                continue
                
        source_counts = defaultdict(int)
        for data in subscribers_data.values():
            source_counts[data.get('source', 'unknown')] += 1
            
        return {
            "total": total_subscribers,
            "today": today_signups,
            "week": week_signups,
            "sources": dict(source_counts),
        }
    except Exception as e:
        log_error(f"Error calculating stats: {e}")
        return {"total": 0, "today": 0, "week": 0, "sources": {}}

# =============================
# Routes - Basic
# =============================

@app.route('/health', methods=['GET'])
def health_check():
    try:
        brevo_connected, brevo_status, brevo_email = test_brevo_connection()
        db_connected = get_db_connection() is not None
        
        return jsonify({
            "status": "healthy",
            "database_connected": db_connected,
            "subscribers_count": len(get_all_subscribers_from_db()) if db_connected else len(subscribers_data),
            "brevo_sync": AUTO_SYNC_TO_BREVO,
            "brevo_status": "connected" if brevo_connected else brevo_status,
            "brevo_email": brevo_email,
            "brevo_list_id": BREVO_LIST_ID,
            "activities": len(activity_log),
            "api_instances_initialized": (api_instance is not None and contacts_api is not None),
        })
    except Exception as e:
        error_msg = f"Health check error: {str(e)}"
        log_error(error_msg)
        return jsonify({
            "status": "error",
            "error": error_msg,
            "brevo_status": "error",
            "database_connected": False,
            "subscribers_count": 0,
        }), 500

# =============================
# Routes - Event Management
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
            if event.get('date_time'):
                event['date_time'] = event['date_time'].isoformat()
            if event.get('end_time'):
                event['end_time'] = event['end_time'].isoformat()
            if event.get('created_at'):
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

@app.route('/api/events', methods=['POST'])
def create_event():
    """Create a new event"""
    try:
        data = request.json or {}
        
        # Validate required fields
        required_fields = ['title', 'event_type', 'date_time']
        for field in required_fields:
            if field not in data:
                return jsonify({"success": False, "error": f"{field} is required"}), 400
        
        # Parse date_time
        try:
            date_time = datetime.fromisoformat(data['date_time'].replace('Z', '+00:00'))
        except:
            return jsonify({"success": False, "error": "Invalid date_time format"}), 400
            
        # Parse end_time if provided
        end_time = None
        if data.get('end_time'):
            try:
                end_time = datetime.fromisoformat(data['end_time'].replace('Z', '+00:00'))
            except:
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
        
        result = execute_query_one(query, params)
        
        if result:
            event_id = result['id']
            log_activity(f"Created event: {data['title']} (ID: {event_id})", "success")
            
            return jsonify({
                "success": True,
                "event_id": event_id,
                "message": "Event created successfully"
            })
        else:
            return jsonify({"success": False, "error": "Failed to create event"}), 500
            
    except Exception as e:
        log_error(f"Error creating event: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# =============================
# Routes - Subscriber Management
# =============================

@app.route('/subscribers', methods=['GET'])
def get_subscribers():
    try:
        # Try database first
        db_subscribers = get_all_subscribers_from_db()
        
        if db_subscribers:
            subscriber_list = []
            for sub in db_subscribers:
                subscriber_list.append({
                    "email": sub['email'],
                    "date_added": sub['date_added'].isoformat() if sub['date_added'] else datetime.now().isoformat(),
                    "source": sub['source'] or 'unknown',
                    "status": sub['status'] or 'active',
                })
        else:
            # Fallback to in-memory
            subscriber_list = []
            for email, data in subscribers_data.items():
                subscriber_list.append({
                    "email": email,
                    "date_added": data.get('date_added', datetime.now().isoformat()),
                    "source": data.get('source', 'unknown'),
                    "status": data.get('status', 'active'),
                })
            subscriber_list.sort(key=lambda x: x['date_added'], reverse=True)
        
        stats = get_signup_stats()
        return jsonify({
            "success": True,
            "subscribers": [item['email'] for item in subscriber_list],
            "subscriber_details": subscriber_list,
            "count": len(subscriber_list),
            "stats": stats,
        })
    except Exception as e:
        error_msg = f"Error getting subscribers: {str(e)}"
        log_error(error_msg)
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
        
        # Try database first
        db_result = add_subscriber_to_db(email, source)
        if db_result is None:
            # Check if already exists in database
            existing = execute_query_one("SELECT email FROM subscribers WHERE email = %s", (email,))
            if existing:
                return jsonify({"success": False, "error": "Email already subscribed"}), 400
            
            # Fallback to in-memory
            if email in subscribers_data:
                return jsonify({"success": False, "error": "Email already subscribed"}), 400
            
            subscribers_data[email] = {
                'date_added': datetime.now().isoformat(),
                'source': source,
                'status': 'active',
            }
        
        brevo_result = add_to_brevo_list(email)
        log_activity(f"New subscriber added: {email} (source: {source})", "success")
        
        return jsonify({
            "success": True,
            "message": "Subscriber added successfully",
            "email": email,
            "brevo_sync": brevo_result.get("success", False),
            "brevo_message": brevo_result.get("message", brevo_result.get("error", "")),
        })
        
    except Exception as e:
        error_msg = f"Error adding subscriber: {str(e)}"
        log_error(error_msg)
        return jsonify({"success": False, "error": error_msg}), 500

@app.route('/unsubscribe', methods=['POST'])
def remove_subscriber():
    try:
        data = request.json or {}
        email = str(data.get('email', '')).strip().lower()
        
        if not email:
            return jsonify({"success": False, "error": "Email is required"}), 400
        
        # Try database first
        db_result = remove_subscriber_from_db(email)
        if db_result is None:
            # Fallback to in-memory
            if email not in subscribers_data:
                return jsonify({"success": False, "error": "Email not found"}), 404
            del subscribers_data[email]
        
        brevo_result = remove_from_brevo_list(email)
        log_activity(f"Subscriber removed: {email}", "danger")
        
        return jsonify({
            "success": True,
            "message": "Subscriber removed",
            "email": email,
            "brevo_sync": brevo_result.get("success", False),
            "brevo_message": brevo_result.get("message", brevo_result.get("error", "")),
        })
        
    except Exception as e:
        error_msg = f"Error removing subscriber: {str(e)}"
        log_error(error_msg)
        return jsonify({"success": False, "error": error_msg}), 500

# =============================
# Additional Routes (keeping existing ones)
# =============================

@app.route('/signup')
def signup_page():
    """Serve the signup page"""
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
            <a href="/admin">Staff Login</a> • 
            <a href="https://sidequesthub.com">SideQuest Hub</a>
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

@app.route('/admin')
def admin_dashboard():
    try:
        return """
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>SideQuest Admin Dashboard</title>
            <style>
                * { margin: 0; padding: 0; box-sizing: border-box; }
                body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #1a1a1a; color: #fff; padding: 20px; }
                .header { background: linear-gradient(135deg, #FFD700 0%, #FFA500 100%); padding: 20px; border-radius: 12px; margin-bottom: 30px; color: #1a1a1a; }
                .header h1 { font-size: 2rem; font-weight: 800; }
                .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; }
                .card { background: #2a2a2a; padding: 24px; border-radius: 12px; border: 2px solid #444; }
                .card h2 { color: #FFD700; margin-bottom: 16px; }
                .btn { background: linear-gradient(135deg, #FFD700 0%, #FFA500 100%); color: #1a1a1a; padding: 12px 24px; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; margin: 8px 4px; text-decoration: none; display: inline-block; }
                .btn:hover { transform: translateY(-2px); }
                .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 16px; margin: 20px 0; }
                .stat { background: #3a3a3a; padding: 16px; border-radius: 8px; text-align: center; }
                .stat-number { font-size: 2rem; font-weight: 800; color: #FFD700; }
                textarea { width: 100%; padding: 12px; border-radius: 8px; border: 2px solid #444; background: #1a1a1a; color: #fff; resize: vertical; min-height: 120px; }
                input[type="text"], input[type="email"] { width: 100%; padding: 12px; border-radius: 8px; border: 2px solid #444; background: #1a1a1a; color: #fff; margin: 8px 0; }
                .message { padding: 12px; border-radius: 8px; margin: 12px 0; }
                .success { background: #0f5132; color: #d1e7dd; border: 1px solid #198754; }
                .error { background: #842029; color: #f8d7da; border: 1px solid #dc3545; }
            </style>
        </head>
        <body>
            <div class="header">
                <h1>🎮 SideQuest Admin Dashboard</h1>
                <p>Manage your gaming cafe newsletter and events</p>
            </div>

            <div class="grid">
                <div class="card">
                    <h2>📊 Quick Stats</h2>
                    <div class="stats" id="statsContainer">
                        <div class="stat">
                            <div class="stat-number" id="totalSubs">-</div>
                            <div>Total Subscribers</div>
                        </div>
                        <div class="stat">
                            <div class="stat-number" id="todaySubs">-</div>
                            <div>Today</div>
                        </div>
                        <div class="stat">
                            <div class="stat-number" id="weekSubs">-</div>
                            <div>This Week</div>
                        </div>
                    </div>
                    <button class="btn" onclick="loadStats()">Refresh Stats</button>
                </div>

                <div class="card">
                    <h2>📧 Add Subscriber</h2>
                    <form onsubmit="addSubscriber(event)">
                        <input type="email" id="newEmail" placeholder="Enter email address" required>
                        <button type="submit" class="btn">Add Subscriber</button>
                    </form>
                    <div id="addMessage"></div>
                </div>

                <div class="card">
                    <h2>📮 Send Campaign</h2>
                    <form onsubmit="sendCampaign(event)">
                        <input type="text" id="subject" placeholder="Email Subject" required>
                        <textarea id="emailBody" placeholder="Email content (HTML supported)"></textarea>
                        <button type="submit" class="btn">Send to All Subscribers</button>
                    </form>
                    <div id="campaignMessage"></div>
                </div>

                <div class="card">
                    <h2>👥 Manage Subscribers</h2>
                    <button class="btn" onclick="loadSubscribers()">View All Subscribers</button>
                    <button class="btn" onclick="exportSubscribers()">Export List</button>
                    <div id="subscribersList"></div>
                </div>

                <div class="card">
                    <h2>🎮 Quick Links</h2>
                    <a href="/signup" class="btn" target="_blank">Signup Page</a>
                    <a href="/health" class="btn" target="_blank">Health Check</a>
                    <button class="btn" onclick="clearAllData()">⚠️ Clear All Data</button>
                </div>
            </div>

            <script>
                async function loadStats() {
                    try {
                        const response = await fetch('/subscribers');
                        const data = await response.json();
                        if (data.success && data.stats) {
                            document.getElementById('totalSubs').textContent = data.stats.total || 0;
                            document.getElementById('todaySubs').textContent = data.stats.today || 0;
                            document.getElementById('weekSubs').textContent = data.stats.week || 0;
                        }
                    } catch (error) {
                        console.error('Error loading stats:', error);
                    }
                }

                async function addSubscriber(event) {
                    event.preventDefault();
                    const email = document.getElementById('newEmail').value;
                    const messageDiv = document.getElementById('addMessage');
                    
                    try {
                        const response = await fetch('/subscribe', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ email, source: 'admin' })
                        });
                        const data = await response.json();
                        
                        if (data.success) {
                            messageDiv.className = 'message success';
                            messageDiv.textContent = 'Subscriber added successfully!';
                            document.getElementById('newEmail').value = '';
                            loadStats();
                        } else {
                            messageDiv.className = 'message error';
                            messageDiv.textContent = data.error || 'Failed to add subscriber';
                        }
                    } catch (error) {
                        messageDiv.className = 'message error';
                        messageDiv.textContent = 'Connection error';
                    }
                }

                async function sendCampaign(event) {
                    event.preventDefault();
                    const subject = document.getElementById('subject').value;
                    const body = document.getElementById('emailBody').value;
                    const messageDiv = document.getElementById('campaignMessage');
                    
                    if (!confirm('Send campaign to all subscribers?')) return;
                    
                    try {
                        // First get subscribers
                        const subsResponse = await fetch('/subscribers');
                        const subsData = await subsResponse.json();
                        
                        if (!subsData.success) {
                            throw new Error('Failed to get subscribers');
                        }
                        
                        const response = await fetch('/send-campaign', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                subject,
                                body,
                                recipients: subsData.subscribers
                            })
                        });
                        const data = await response.json();
                        
                        if (data.success) {
                            messageDiv.className = 'message success';
                            messageDiv.textContent = `Campaign sent to ${data.sent} subscribers!`;
                            document.getElementById('subject').value = '';
                            document.getElementById('emailBody').value = '';
                        } else {
                            messageDiv.className = 'message error';
                            messageDiv.textContent = data.error || 'Failed to send campaign';
                        }
                    } catch (error) {
                        messageDiv.className = 'message error';
                        messageDiv.textContent = 'Error: ' + error.message;
                    }
                }

                async function loadSubscribers() {
                    const container = document.getElementById('subscribersList');
                    container.innerHTML = 'Loading...';
                    
                    try {
                        const response = await fetch('/subscribers');
                        const data = await response.json();
                        
                        if (data.success) {
                            if (data.subscribers.length === 0) {
                                container.innerHTML = '<p>No subscribers yet.</p>';
                            } else {
                                container.innerHTML = `
                                    <h3>Subscribers (${data.subscribers.length})</h3>
                                    <div style="max-height: 300px; overflow-y: auto; margin-top: 10px;">
                                        ${data.subscribers.map(email => `
                                            <div style="display: flex; justify-content: space-between; padding: 8px; border-bottom: 1px solid #444;">
                                                <span>${email}</span>
                                                <button onclick="removeSubscriber('${email}')" style="background: #dc3545; color: white; border: none; padding: 4px 8px; border-radius: 4px; cursor: pointer;">Remove</button>
                                            </div>
                                        `).join('')}
                                    </div>
                                `;
                            }
                        }
                    } catch (error) {
                        container.innerHTML = '<p>Error loading subscribers</p>';
                    }
                }

                async function removeSubscriber(email) {
                    if (!confirm(`Remove ${email}?`)) return;
                    
                    try {
                        const response = await fetch('/unsubscribe', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ email })
                        });
                        const data = await response.json();
                        
                        if (data.success) {
                            loadSubscribers();
                            loadStats();
                        } else {
                            alert('Error: ' + (data.error || 'Failed to remove subscriber'));
                        }
                    } catch (error) {
                        alert('Connection error');
                    }
                }

                function exportSubscribers() {
                    fetch('/subscribers')
                        .then(response => response.json())
                        .then(data => {
                            if (data.success) {
                                const csv = 'Email\\n' + data.subscribers.join('\\n');
                                const blob = new Blob([csv], { type: 'text/csv' });
                                const url = window.URL.createObjectURL(blob);
                                const a = document.createElement('a');
                                a.href = url;
                                a.download = 'sidequest-subscribers.csv';
                                a.click();
                                window.URL.revokeObjectURL(url);
                            }
                        });
                }

                async function clearAllData() {
                    if (!confirm('⚠️ This will delete ALL subscribers! Are you sure?')) return;
                    if (!confirm('This action cannot be undone. Type DELETE to confirm.')) return;
                    
                    try {
                        const response = await fetch('/clear-data', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ confirmation: 'DELETE' })
                        });
                        const data = await response.json();
                        
                        if (data.success) {
                            alert('All data cleared');
                            loadStats();
                            document.getElementById('subscribersList').innerHTML = '';
                        }
                    } catch (error) {
                        alert('Error clearing data');
                    }
                }

                // Load initial stats
                loadStats();
            </script>
        </body>
        </html>
        """
    except Exception as e:
        return f"<h1>Admin Dashboard Error</h1><p>{str(e)}</p>", 500

# =============================
# Additional Missing Routes
# =============================

@app.route('/send-campaign', methods=['POST'])
def send_campaign():
    try:
        if not api_instance:
            return jsonify({"success": False, "error": "Email API not initialized"}), 500
        
        data = request.json or {}
        subject = data.get('subject', '(no subject)')
        body = data.get('body', '')
        from_name = data.get('fromName', SENDER_NAME)
        recipients = data.get('recipients', [])
        
        if not recipients:
            return jsonify({"success": False, "error": "No recipients provided"}), 400
        if not body:
            return jsonify({"success": False, "error": "Email body is required"}), 400
        
        to_list = [{"email": email} for email in recipients]
        email = sib_api_v3_sdk.SendSmtpEmail(
            sender={"name": from_name, "email": SENDER_EMAIL},
            to=to_list,
            subject=subject,
            html_content=body,
        )
        
        api_response = api_instance.send_transac_email(email)
        log_activity(f"Campaign sent to {len(recipients)} subscribers", "success")
        
        return jsonify({"success": True, "sent": len(recipients), "response": str(api_response)})
        
    except Exception as e:
        error_msg = f"Campaign send error: {str(e)}"
        log_error(error_msg)
        return jsonify({"success": False, "error": error_msg}), 500

@app.route('/clear-data', methods=['POST'])
def clear_all_data():
    try:
        data = request.json or {}
        confirmation = data.get('confirmation', '')
        
        if confirmation != 'DELETE':
            return jsonify({"success": False, "error": "Invalid confirmation"}), 400
        
        # Clear database
        if psycopg2:
            execute_query("DELETE FROM subscribers", fetch=False)
        
        # Clear in-memory
        count = len(subscribers_data)
        subscribers_data.clear()
        
        log_activity(f"ALL DATA CLEARED - {count} subscribers removed", "danger")
        
        return jsonify({
            "success": True,
            "message": f"Cleared {count} subscribers",
            "note": "Brevo data not affected - manual cleanup required",
        })
        
    except Exception as e:
        error_msg = f"Error clearing data: {str(e)}"
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
        log_error(error_msg)
        return jsonify({"success": False, "error": error_msg}), 500

# =============================
# Error Handlers
# =============================

@app.errorhandler(404)
def not_found(error):
    return jsonify({"success": False, "error": "Not found"}), 404

@app.errorhandler(500)
def internal_server_error(error):
    log_error(f"Server Error: {error}")
    return jsonify({"success": False, "error": "Internal server error"}), 500

# =============================
# Main Application Startup
# =============================

if __name__ == '__main__':
    try:
        print("🚀 SideQuest Backend starting...")
        print("=" * 50)
        
        # Initialize database
        print("🗄️  Initializing database...")
        if init_database():
            print("✅ Database tables ready")
        else:
            print("⚠️  Database not available - using in-memory storage")
        
        # Test Brevo connection
        print("🧪 Testing Brevo API connection...")
        brevo_connected, brevo_status, brevo_email = test_brevo_connection()
        if brevo_connected:
            print(f"✅ Brevo connection successful - {brevo_email}")
        else:
            print(f"❌ Brevo connection failed: {brevo_status}")
            print("⚠️  Email campaigns may not work")
        
        log_activity("SideQuest Backend started", "info")
        print(f"📧 Sender email: {SENDER_EMAIL}")
        print(f"🔄 Brevo Auto-Sync: {'ON' if AUTO_SYNC_TO_BREVO else 'OFF'}")
        print(f"📋 Brevo List ID: {BREVO_LIST_ID}")
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
        log_error(f"Critical startup error: {str(e)}")
    finally:
        print("🔄 Server shutdown complete")
