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
    from sib_api_v3_sdk.rest import ApiException  # type: ignore
except Exception:  # pragma: no cover
    sib_api_v3_sdk = None  # type: ignore
    ApiException = Exception  # type: ignore

# ---- Brevo settings (KEEPING USER KEY FOR NOW as requested) ----
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
BREVO_LIST_ID = int(os.environ.get("BREVO_LIST_ID", 2))
AUTO_SYNC_TO_BREVO = os.environ.get("AUTO_SYNC_TO_BREVO", "true").lower() in {"1", "true", "yes", "y"}
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "jaiamiscua@gmail.com")
SENDER_NAME = os.environ.get("SENDER_NAME", "SideQuest")

# ---- In-memory stores ----
subscribers_data: dict[str, dict] = {}
activity_log: list[dict] = []

# =============================
# Helpers
# =============================

# Fixed Event Management System for Gaming Cafe

import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
import random
import string
import traceback
import re

# =============================
# Database Connection
# =============================

def get_db_connection():
    """Get PostgreSQL connection using Railway's DATABASE_URL"""
    try:
        # Railway provides DATABASE_URL automatically
        database_url = os.environ.get('DATABASE_URL')
        if database_url:
            # Railway uses 'postgres://', but psycopg2 needs 'postgresql://'
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
        if event.get('date_time'):
            event['date_time'] = event['date_time'].isoformat()
        if event.get('end_time'):
            event['end_time'] = event['end_time'].isoformat()
        if event.get('created_at'):
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
        
        # Validate required fields
        required_fields = ['title', 'event_type', 'date_time']
        for field in required_fields:
            if field not in data:
                return jsonify({"success": False, "error": f"{field} is required"}), 400
        
        # Parse date_time
        try:
            date_time_str = data['date_time']
            # Handle various ISO formats
            if date_time_str.endswith('Z'):
                date_time_str = date_time_str[:-1] + '+00:00'
            elif not ('+' in date_time_str[-6:] or date_time_str.endswith('Z')):
                # No timezone info, assume local time
                date_time = datetime.fromisoformat(date_time_str)
            else:
                date_time = datetime.fromisoformat(date_time_str)
        except Exception as e:
            log_error(f"Date parsing error: {e}")
            return jsonify({"success": False, "error": "Invalid date_time format"}), 400
            
        # Parse end_time if provided
        end_time = None
        if data.get('end_time'):
            try:
                end_time_str = data['end_time']
                if end_time_str.endswith('Z'):
                    end_time_str = end_time_str[:-1] + '+00:00'
                elif not ('+' in end_time_str[-6:] or end_time_str.endswith('Z')):
                    end_time = datetime.fromisoformat(end_time_str)
                else:
                    end_time = datetime.fromisoformat(end_time_str)
            except Exception as e:
                log_error(f"End time parsing error: {e}")
                end_time = None
                
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
            
            # Schedule automated emails if status is published
            if data.get('status') == 'published':
                schedule_event_emails(event_id, date_time)
            
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

@app.route('/api/events/<int:event_id>', methods=['PUT'])
def update_event(event_id):
    """Update an existing event"""
    try:
        data = request.json or {}
        
        # Build dynamic update query
        update_fields = []
        params = []
        
        allowed_fields = [
            'title', 'event_type', 'game_title', 'date_time', 'end_time',
            'capacity', 'description', 'entry_fee', 'prize_pool', 'status',
            'image_url', 'requirements'
        ]
        
        for field in allowed_fields:
            if field in data:
                update_fields.append(f"{field} = %s")
                if field in ['date_time', 'end_time'] and data[field]:
                    try:
                        date_str = data[field]
                        if date_str.endswith('Z'):
                            date_str = date_str[:-1] + '+00:00'
                        params.append(datetime.fromisoformat(date_str))
                    except:
                        params.append(None)
                else:
                    params.append(data[field])
        
        if not update_fields:
            return jsonify({"success": False, "error": "No fields to update"}), 400
            
        params.append(event_id)
        query = f"""
            UPDATE events 
            SET {', '.join(update_fields)}
            WHERE id = %s
        """
        
        result = execute_query(query, params, fetch=False)
        
        if result and result > 0:
            log_activity(f"Updated event ID: {event_id}", "info")
            return jsonify({
                "success": True,
                "message": "Event updated successfully"
            })
        else:
            return jsonify({"success": False, "error": "Event not found"}), 404
            
    except Exception as e:
        log_error(f"Error updating event {event_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/events/<int:event_id>', methods=['DELETE'])
def delete_event(event_id):
    """Delete an event"""
    try:
        # Check if event exists and has registrations
        check_query = """
            SELECT COUNT(r.id) as registration_count
            FROM events e
            LEFT JOIN event_registrations r ON e.id = r.event_id
            WHERE e.id = %s
            GROUP BY e.id
        """
        
        result = execute_query_one(check_query, (event_id,))
        
        if not result:
            return jsonify({"success": False, "error": "Event not found"}), 404
            
        if result['registration_count'] > 0:
            if not request.args.get('force', '').lower() == 'true':
                return jsonify({
                    "success": False,
                    "error": f"Event has {result['registration_count']} registrations. Use force=true to delete anyway."
                }), 400
        
        query = "DELETE FROM events WHERE id = %s"
        result = execute_query(query, (event_id,), fetch=False)
        
        if result and result > 0:
            log_activity(f"Deleted event ID: {event_id}", "danger")
            return jsonify({
                "success": True,
                "message": "Event deleted successfully"
            })
        else:
            return jsonify({"success": False, "error": "Failed to delete event"}), 500
            
    except Exception as e:
        log_error(f"Error deleting event {event_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/events/<int:event_id>/register', methods=['POST'])
def register_for_event(event_id):
    """Register a subscriber for an event"""
    try:
        data = request.json or {}
        email = data.get('email', '').strip().lower()
        
        if not email:
            return jsonify({"success": False, "error": "Email is required"}), 400
            
        # Check if subscriber exists in the global subscribers_data
        # If this variable doesn't exist, you need to check the database
        try:
            # Try to check if subscriber exists in database instead
            subscriber_check = execute_query_one(
                "SELECT email FROM subscribers WHERE email = %s", 
                (email,)
            )
            if not subscriber_check:
                return jsonify({"success": False, "error": "Email not found in subscribers"}), 404
        except Exception as e:
            log_error(f"Error checking subscriber: {e}")
            # If subscriber check fails, continue anyway
            pass
            
        # Check event exists and has capacity
        event_query = """
            SELECT e.*, COUNT(r.id) as current_registrations
            FROM events e
            LEFT JOIN event_registrations r ON e.id = r.event_id
            WHERE e.id = %s
            GROUP BY e.id
        """
        
        event = execute_query_one(event_query, (event_id,))
        
        if not event:
            return jsonify({"success": False, "error": "Event not found"}), 404
            
        if event['capacity'] and event['capacity'] > 0:
            if event['current_registrations'] >= event['capacity']:
                return jsonify({"success": False, "error": "Event is full"}), 400
        
        # Generate confirmation code
        confirmation_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        
        # Register for event
        query = """
            INSERT INTO event_registrations (
                event_id, subscriber_email, player_name, team_name, notes, confirmation_code
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (event_id, subscriber_email) 
            DO UPDATE SET notes = EXCLUDED.notes
            RETURNING id
        """
        
        params = (
            event_id,
            email,
            data.get('player_name', email.split('@')[0]),
            data.get('team_name'),
            data.get('notes'),
            confirmation_code
        )
        
        result = execute_query_one(query, params)
        
        if result:
            log_activity(f"{email} registered for event ID: {event_id}", "success")
            
            # Send confirmation email if available
            try:
                send_registration_confirmation(email, event, confirmation_code)
            except Exception as e:
                log_error(f"Failed to send confirmation email: {e}")
            
            return jsonify({
                "success": True,
                "message": "Registration successful",
                "confirmation_code": confirmation_code
            })
        else:
            return jsonify({"success": False, "error": "Registration failed"}), 500
            
    except Exception as e:
        log_error(f"Error registering for event {event_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/events/<int:event_id>/unregister', methods=['POST'])
def unregister_from_event(event_id):
    """Unregister a subscriber from an event"""
    try:
        data = request.json or {}
        email = data.get('email', '').strip().lower()
        
        if not email:
            return jsonify({"success": False, "error": "Email is required"}), 400
            
        query = "DELETE FROM event_registrations WHERE event_id = %s AND subscriber_email = %s"
        result = execute_query(query, (event_id, email), fetch=False)
        
        if result and result > 0:
            log_activity(f"{email} unregistered from event ID: {event_id}", "info")
            
            return jsonify({
                "success": True,
                "message": "Unregistered successfully"
            })
        else:
            return jsonify({"success": False, "error": "Registration not found"}), 404
            
    except Exception as e:
        log_error(f"Error unregistering from event {event_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/events/<int:event_id>/attendees', methods=['GET'])
def get_event_attendees(event_id):
    """Get list of attendees for an event"""
    try:
        query = """
            SELECT 
                r.*,
                CASE 
                    WHEN r.subscriber_email IN (SELECT email FROM subscribers) 
                    THEN true 
                    ELSE false 
                END as is_subscriber
            FROM event_registrations r
            WHERE r.event_id = %s
            ORDER BY r.registered_at DESC
        """
        
        attendees = execute_query(query, (event_id,))
        
        if attendees is None:
            return jsonify({"success": False, "error": "Database error"}), 500
            
        # Convert datetime objects
        for attendee in attendees:
            if attendee.get('registered_at'):
                attendee['registered_at'] = attendee['registered_at'].isoformat()
            if attendee.get('checked_in_at'):
                attendee['checked_in_at'] = attendee['checked_in_at'].isoformat()
                
        return jsonify({
            "success": True,
            "attendees": attendees,
            "count": len(attendees)
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
        
        if not email:
            return jsonify({"success": False, "error": "Email is required"}), 400
            
        query = """
            UPDATE event_registrations 
            SET attended = true, checked_in_at = CURRENT_TIMESTAMP
            WHERE event_id = %s AND subscriber_email = %s
            RETURNING id
        """
        
        result = execute_query_one(query, (event_id, email))
        
        if result:
            log_activity(f"Checked in {email} for event ID: {event_id}", "success")
            return jsonify({
                "success": True,
                "message": "Check-in successful"
            })
        else:
            return jsonify({"success": False, "error": "Registration not found"}), 404
            
    except Exception as e:
        log_error(f"Error checking in attendee: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/events/calendar', methods=['GET'])
def get_calendar_events():
    """Get events in calendar format"""
    try:
        start_date = request.args.get('start', datetime.now().isoformat())
        end_date = request.args.get('end', (datetime.now() + timedelta(days=30)).isoformat())
        
        query = """
            SELECT 
                e.id,
                e.title,
                e.event_type,
                e.date_time as start,
                COALESCE(e.end_time, e.date_time + INTERVAL '2 hours') as end,
                CASE 
                    WHEN e.capacity > 0 THEN 
                        CONCAT(COALESCE(e.current_registrations, 0), '/', e.capacity, ' registered')
                    ELSE 
                        CONCAT(COALESCE(e.current_registrations, 0), ' registered')
                END as description,
                CASE e.event_type
                    WHEN 'tournament' THEN '#FF6B35'
                    WHEN 'game_night' THEN '#4ECDC4'
                    WHEN 'special' THEN '#FFD700'
                    WHEN 'birthday' THEN '#FF69B4'
                    ELSE '#95A5A6'
                END as color,
                e.status
            FROM events e
            WHERE e.date_time BETWEEN %s AND %s
                AND e.status != 'cancelled'
            ORDER BY e.date_time
        """
        
        events = execute_query(query, (start_date, end_date))
        
        if events is None:
            return jsonify({"success": False, "error": "Database error"}), 500
            
        # Format for FullCalendar
        calendar_events = []
        for event in events:
            calendar_events.append({
                'id': event['id'],
                'title': event['title'],
                'start': event['start'].isoformat() if event['start'] else None,
                'end': event['end'].isoformat() if event['end'] else None,
                'description': event['description'],
                'color': event['color'],
                'extendedProps': {
                    'event_type': event['event_type'],
                    'status': event['status']
                }
            })
            
        return jsonify({
            "success": True,
            "events": calendar_events
        })
        
    except Exception as e:
        log_error(f"Error getting calendar events: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# =============================
# Event Email Automation
# =============================

def schedule_event_emails(event_id, event_date):
    """Schedule automated emails for an event"""
    try:
        # Only schedule if we have email capability
        if not globals().get('api_instance'):
            log_activity(f"Email API not available, skipping email scheduling for event {event_id}", "warning")
            return
            
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
    try:
        query = """
            INSERT INTO event_emails (event_id, email_type, scheduled_for)
            VALUES (%s, %s, %s)
            ON CONFLICT DO NOTHING
        """
        execute_query(query, (event_id, email_type, scheduled_for), fetch=False)
    except Exception as e:
        log_error(f"Error scheduling email: {e}")

def send_registration_confirmation(email, event, confirmation_code):
    """Send registration confirmation email"""
    try:
        # Check if email API is available
        if not globals().get('api_instance'):
            log_activity(f"Email API not available, skipping confirmation email for {email}", "warning")
            return
            
        subject = f"Registration Confirmed: {event['title']}"
        html_content = f"""
        <h2>You're registered for {event['title']}!</h2>
        <p><strong>Date:</strong> {event['date_time']}</p>
        <p><strong>Confirmation Code:</strong> {confirmation_code}</p>
        <p>Show this code at check-in.</p>
        <p>See you at SideQuest Gaming Cafe!</p>
        """
        
        # Import and use email sending logic here
        # This will depend on your email service setup
        send_email = {
            "sender": {"name": globals().get('SENDER_NAME', 'SideQuest'), "email": globals().get('SENDER_EMAIL', 'noreply@sidequest.com')},
            "to": [{"email": email}],
            "subject": subject,
            "html_content": html_content
        }
        
        # Use your email API here
        globals().get('api_instance').send_transac_email(send_email)
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
                AVG(CASE WHEN e.capacity > 0 THEN (COALESCE(e.current_registrations, 0)::float / e.capacity * 100) END) as avg_capacity_filled
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
                SUM(COALESCE(e.entry_fee, 0) * COALESCE(e.current_registrations, 0)) as total_revenue,
                AVG(COALESCE(e.entry_fee, 0) * COALESCE(e.current_registrations, 0)) as avg_revenue_per_event
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
# Utility Functions
# =============================

# Initialize activity log if not exists
if 'activity_log' not in globals():
    activity_log = []

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

def log_error(error, error_type: str = "error") -> None:
    err = str(error)
    log_activity(f"Error: {err}", error_type)
    print(f"Error [{error_type}]: {err}")
    print(f"Traceback: {traceback.format_exc()}")

def log_error(error, error_type: str = "error") -> None:
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
# Database Schema Check/Creation
# =============================

def ensure_tables_exist():
    """Ensure all required tables exist"""
    try:
        # Check if events table exists and create if not
        create_events_table = """
        CREATE TABLE IF NOT EXISTS events (
            id SERIAL PRIMARY KEY,
            title VARCHAR(255) NOT NULL,
            event_type VARCHAR(50) NOT NULL,
            game_title VARCHAR(255),
            date_time TIMESTAMP WITH TIME ZONE NOT NULL,
            end_time TIMESTAMP WITH TIME ZONE,
            capacity INTEGER DEFAULT 0,
            description TEXT,
            entry_fee DECIMAL(10,2) DEFAULT 0,
            prize_pool VARCHAR(255),
            status VARCHAR(20) DEFAULT 'draft',
            image_url TEXT,
            requirements TEXT,
            current_registrations INTEGER DEFAULT 0,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
        """
        
        create_event_registrations_table = """
        CREATE TABLE IF NOT EXISTS event_registrations (
            id SERIAL PRIMARY KEY,
            event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
            subscriber_email VARCHAR(255) NOT NULL,
            player_name VARCHAR(255),
            team_name VARCHAR(255),
            notes TEXT,
            confirmation_code VARCHAR(20),
            attended BOOLEAN DEFAULT FALSE,
            registered_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            checked_in_at TIMESTAMP WITH TIME ZONE,
            UNIQUE(event_id, subscriber_email)
        );
        """
        
        create_event_emails_table = """
        CREATE TABLE IF NOT EXISTS event_emails (
            id SERIAL PRIMARY KEY,
            event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
            email_type VARCHAR(50) NOT NULL,
            scheduled_for TIMESTAMP WITH TIME ZONE NOT NULL,
            sent_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
        """
        
        create_subscribers_table = """
        CREATE TABLE IF NOT EXISTS subscribers (
            id SERIAL PRIMARY KEY,
            email VARCHAR(255) UNIQUE NOT NULL,
            name VARCHAR(255),
            date_joined TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            status VARCHAR(20) DEFAULT 'active',
            preferences JSONB DEFAULT '{}'::jsonb
        );
        """
        
        execute_query(create_events_table, fetch=False)
        execute_query(create_event_registrations_table, fetch=False)
        execute_query(create_event_emails_table, fetch=False)
        execute_query(create_subscribers_table, fetch=False)
        
        log_activity("Database tables verified/created", "success")
        
    except Exception as e:
        log_error(f"Error ensuring tables exist: {e}")

# Call this when the app starts
ensure_tables_exist()

# =============================
# Brevo client init (before routes)
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
        print(f"Error calculating stats: {e}")
        return {"total": 0, "today": 0, "week": 0, "sources": {}}

# =============================
# Middleware logging
# =============================

@app.before_request
def log_request_info():
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
        return jsonify({
            "status": "healthy",
            "subscribers_count": len(subscribers_data),
            "brevo_sync": AUTO_SYNC_TO_BREVO,
            "brevo_status": "connected" if brevo_connected else brevo_status,
            "brevo_email": brevo_email,
            "brevo_list_id": BREVO_LIST_ID,
            "activities": len(activity_log),
            "api_instances_initialized": (api_instance is not None and contacts_api is not None),
        })
    except Exception as e:
        error_msg = f"Health check error: {str(e)}"
        print(f"Health check error: {traceback.format_exc()}")
        return jsonify({
            "status": "error",
            "error": error_msg,
            "brevo_status": "error",
            "subscribers_count": len(subscribers_data) if subscribers_data else 0,
        }), 500


@app.route('/clear-data', methods=['POST'])
def clear_all_data():
    try:
        data = request.json or {}
        confirmation = data.get('confirmation', '')
        if confirmation != 'DELETE':
            return jsonify({"success": False, "error": "Invalid confirmation"}), 400
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
        print(f"Clear data error: {traceback.format_exc()}")
        log_activity(f"Failed to clear data: {error_msg}", "danger")
        return jsonify({"success": False, "error": error_msg}), 500


@app.route('/static/<path:filename>')
def serve_static(filename):
    try:
        return send_from_directory(app.static_folder, filename)
    except Exception:
        return jsonify({"success": False, "error": f"File not found: {filename}"}), 404


@app.route('/test', methods=['GET'])
def test_endpoint():
    try:
        return jsonify({
            "success": True,
            "message": "Backend is working",
            "timestamp": datetime.now().isoformat(),
            "subscribers_count": len(subscribers_data),
            "brevo_api_initialized": api_instance is not None,
            "contacts_api_initialized": contacts_api is not None,
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "timestamp": datetime.now().isoformat(),
        }), 500


@app.route('/admin')
def admin_dashboard():
    try:
        # Serve dashboard.html from same directory
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
    # Returns the same embedded HTML from your original code
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


@app.route('/subscribers', methods=['GET'])
def get_subscribers():
    try:
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
        return jsonify({"success": True, "activity": activity_log[:limit]})
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
        for email in emails:
            try:
                email = str(email).strip().lower()
                if not is_valid_email(email):
                    errors.append(f"Invalid email: {email}")
                    continue
                if email in subscribers_data:
                    errors.append(f"Already exists: {email}")
                    continue
                subscribers_data[email] = {
                    'date_added': datetime.now().isoformat(),
                    'source': source,
                    'status': 'active',
                }
                brevo_result = add_to_brevo_list(email)
                if not brevo_result.get("success", False):
                    errors.append(f"Brevo sync failed for {email}: {brevo_result.get('error', 'Unknown error')}")
                added += 1
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
        success_count = 0
        error_count = 0
        errors: list[str] = []
        for email in list(subscribers_data.keys()):
            try:
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


@app.route('/sync-status', methods=['GET'])
def sync_status():
    try:
        last_activity = activity_log[0] if activity_log else None
        return jsonify({
            "auto_sync_enabled": AUTO_SYNC_TO_BREVO,
            "brevo_list_id": BREVO_LIST_ID,
            "local_subscribers": len(subscribers_data),
            "last_activity": last_activity,
        })
    except Exception as e:
        error_msg = f"Error getting sync status: {str(e)}"
        print(f"Sync status error: {traceback.format_exc()}")
        return jsonify({"success": False, "error": error_msg}), 500

# =============================
# Error Handlers (after routes)
# =============================

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
# Main
# =============================
if __name__ == '__main__':
    try:
        print("🚀 SideQuest Backend starting...")
        print("=" * 50)
        print("🧪 Testing Brevo API connection...")
        brevo_connected, brevo_status, brevo_email = test_brevo_connection()
        if brevo_connected:
            print(f"✅ Brevo connection successful - {brevo_email}")
        else:
            print(f"❌ Brevo connection failed: {brevo_status}")
            print("⚠️  Email campaigns and sync features may not work")
        log_activity("SideQuest Backend started", "info")
        print(f"📧 Sender email: {SENDER_EMAIL}")
        print(f"🔄 Brevo Auto-Sync: {'ON' if AUTO_SYNC_TO_BREVO else 'OFF'}")
        print(f"📋 Brevo List ID: {BREVO_LIST_ID}")
        print(f"🌐 Server running on all interfaces")
        print(f"📱 Signup page: http://localhost:4000/signup")
        print(f"🔧 Admin dashboard: http://localhost:4000/admin")
        print(f"📊 API Health check: http://localhost:4000/health")
        print(f"🧪 Test endpoint: http://localhost:4000/test")
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
