import os
import threading
import atexit
import smtplib
import html
from datetime import datetime, date, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Flask & Auth imports
from flask import Flask, jsonify, request, render_template, session, redirect, url_for
from flask_cors import CORS
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from authlib.integrations.flask_client import OAuth

# Internal modules
import agent
import pandas as pd
from db import (
    init_db, get_all_cases, get_case_by_id, create_case, update_case, 
    delete_case, get_cases_by_status, get_upcoming_hearings, get_supabase_client
)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

load_dotenv()

app = Flask(__name__)
CORS(app)

# --- CONFIGURATION ---
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'super-secret-key-change-me')
app.config['SESSION_COOKIE_NAME'] = 'google-login-session'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

# Email Config
EMAIL_SENDER = os.getenv('EMAIL_SENDER')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')
EMAIL_RECIPIENT = os.getenv('EMAIL_RECIPIENT')
SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', 587))

# --- OAUTH SETUP ---
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.getenv('GOOGLE_CLIENT_ID'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

# Allow HTTP for OAuth (Only needed if you are NOT using HTTPS)
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

# Semaphore for RAM protection
research_semaphore = threading.Semaphore(4) 
scheduler = BackgroundScheduler()
scheduler_started = False

# --- AUTH DECORATOR ---
from functools import wraps

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = dict(session).get('user', None)
        if not user:
            # If it's an API call, return 401
            if request.path.startswith('/api/'):
                return jsonify({"error": "Unauthorized"}), 401
            # If it's a page load, redirect to login
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function

# --- AUTH ROUTES ---

@app.route('/login')
def login_page():
    """Renders a simple login page."""
    user = dict(session).get('user', None)
    if user:
        return redirect('/')
    return render_template('login.html')

@app.route('/auth/google')
def google_login():
    """Redirects to Google for Auth."""
    # ✅ FORCE HTTPS: Manually build the URL to avoid http/https mismatch errors
    redirect_uri = url_for('auth_callback', _external=True)
    
    # If your server is behind Nginx (SSL), Flask might think it's HTTP.
    # This forces it to be HTTPS to match what you put in Google Console.
    if redirect_uri.startswith('http://'):
        redirect_uri = redirect_uri.replace('http://', 'https://', 1)

    return google.authorize_redirect(redirect_uri)

@app.route('/auth/callback')
def auth_callback():
    """Handles the callback from Google."""
    try:
        token = google.authorize_access_token()
        user_info = token['userinfo']
        email = user_info.get('email', '')

        # ✅ SECURITY CHECK: Domain Restriction
        if '@nbmediaproduction' not in email:
            session.clear()
            return f"""
            <div style="text-align:center; padding:50px; font-family:sans-serif;">
                <h1 style="color:red;">Unauthorized</h1>
                <p>Sorry, only users from <b>@nbmediaproduction</b> are allowed.</p>
                <p>You tried to login with: {email}</p>
                <a href="/logout">Try Again</a>
            </div>
            """, 403

        # Log user in
        session['user'] = user_info
        session.permanent = True
        return redirect('/')
    
    except Exception as e:
        return f"Auth Error: {e}", 400

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

@app.route('/api/user')
@login_required
def get_user_info():
    """Returns current logged-in user info."""
    return jsonify(session.get('user'))


# --- EXISTING APP ROUTES (With Protection) ---

@app.route('/')
@login_required  # <--- PROTECTED
def index():
    return render_template('index.html', user=session.get('user'))

def send_email_alert(subject: str, body: str) -> bool:
    """
    Send an email alert using SMTP (Gmail).
    """
    if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECIPIENT]):
        print("⚠️ Email configuration incomplete. Skipping email alert.")
        return False
    
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_SENDER
        msg['To'] = EMAIL_RECIPIENT
        msg['Subject'] = subject
        
        msg.attach(MIMEText(body, 'html'))
        
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.send_message(msg)
        
        print(f"✅ Email sent: {subject}")
        return True
    
    except Exception as e:
        print(f"❌ Failed to send email: {e}")
        return False


def send_case_status_alert(case: dict, new_status: str, verdict: dict):
    """
    Send an email alert when a case status changes to Closed or Verdict Reached.
    """
    # Escaping for security
    safe_case_name = html.escape(case.get('case_name', 'Unknown'))
    safe_status = html.escape(new_status)
    safe_next_hearing = html.escape(str(verdict.get('next_hearing_date', 'N/A')))
    safe_victim = html.escape(str(verdict.get('victim_name', 'N/A')))
    safe_suspect = html.escape(str(verdict.get('suspect_name', 'N/A')))
    safe_notes = html.escape(str(verdict.get('notes', 'N/A')))

    subject = f"🚨 Case Alert: {safe_case_name} - {safe_status}"
    
    body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; padding: 20px;">
        <h2 style="color: #d32f2f;">Case Status Update</h2>
        <hr>
        <h3>{safe_case_name}</h3>
        <table style="border-collapse: collapse; width: 100%;">
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd;"><strong>New Status</strong></td>
                <td style="padding: 8px; border: 1px solid #ddd;">{safe_status}</td>
            </tr>
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd;"><strong>Next Hearing Date</strong></td>
                <td style="padding: 8px; border: 1px solid #ddd;">{safe_next_hearing}</td>
            </tr>
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd;"><strong>Victim</strong></td>
                <td style="padding: 8px; border: 1px solid #ddd;">{safe_victim}</td>
            </tr>
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd;"><strong>Suspect</strong></td>
                <td style="padding: 8px; border: 1px solid #ddd;">{safe_suspect}</td>
            </tr>
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd;"><strong>Notes</strong></td>
                <td style="padding: 8px; border: 1px solid #ddd;">{safe_notes}</td>
            </tr>
        </table>
        <br>
        <p style="color: #666;">This is an automated alert from the Legal Case Tracking System.</p>
    </body>
    </html>
    """
    
    send_email_alert(subject, body)

def send_daily_summary_email(summary_report):
    """
    Sends a single email checking all updates from the daily run.
    """
    if not summary_report:
        return

    count = len(summary_report)
    subject = f"Daily Summary: {count} Cases Updated"
    
    rows_html = ""
    for item in summary_report:
        # Escape item fields
        safe_case_name = html.escape(item.get('case_name', 'Unknown'))
        safe_status = html.escape(item.get('status', 'Unknown'))
        safe_next_hearing = html.escape(str(item.get('next_hearing', 'N/A')))
        
        # item['changes'] is a list of strings, likely generated by us. 
        # But safely escaping them is good practice.
        safe_changes = [html.escape(c) for c in item.get('changes', [])]
        changes_str = "<br>".join([f"• {c}" for c in safe_changes])
        
        status_color = "#2563eb"
        if item['status'] == 'Closed': status_color = "#dc2626"
        if item['status'] == 'Verdict Reached': status_color = "#059669"
        
        rows_html += f"""
        <tr style="border-bottom: 1px solid #eee;">
            <td style="padding: 12px; font-weight: bold;">{safe_case_name}</td>
            <td style="padding: 12px; color: {status_color}; text-transform: uppercase; font-size: 12px; font-weight: bold;">{safe_status}</td>
            <td style="padding: 12px;">{changes_str}</td>
            <td style="padding: 12px; color: #555;">{safe_next_hearing}</td>
        </tr>
        """

    body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; padding: 20px; background-color: #f9fafb;">
        <div style="max-width: 800px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; border: 1px solid #ddd;">
            <h2 style="color: #1a202c; border-bottom: 2px solid #3182ce; padding-bottom: 10px;">Daily Research Summary</h2>
            <p>The automated agent researched your active cases. Here are the updates:</p>
            
            <table style="width: 100%; border-collapse: collapse; margin-top: 20px;">
                <thead>
                    <tr style="background-color: #f7fafc; text-align: left;">
                        <th style="padding: 12px; border-bottom: 2px solid #edf2f7;">Case Name</th>
                        <th style="padding: 12px; border-bottom: 2px solid #edf2f7;">Status</th>
                        <th style="padding: 12px; border-bottom: 2px solid #edf2f7;">Changes</th>
                        <th style="padding: 12px; border-bottom: 2px solid #edf2f7;">Next Hearing</th>
                    </tr>
                </thead>
                <tbody>
                    {rows_html}
                </tbody>
            </table>
            
            <p style="margin-top: 30px; font-size: 12px; color: #718096; text-align: center;">Generated by Legal Case Tracker AI</p>
        </div>
    </body>
    </html>
    """
    
    send_email_alert(subject, body)

def process_case_update(case: dict, return_alert_only=False) -> dict:
    """Run the research agent for a case and update the database."""
    case_id = case['id']
    case_name = case['case_name']
    docket_url = case.get('docket_url')
    old_status = case.get('status', 'Open')
    old_next_date = case.get('next_hearing_date')

    print(f"🔄 Processing case {case_id}: {case_name}")
    
    try:
        result = agent.process_case(case_name, docket_url=docket_url, case_id=case_id)
        verdict = result.get('verdict', {})
        
        changes = []
        
        new_status = verdict.get('case_status', 'Unknown')
        if new_status not in ['Unknown', 'Pending'] and new_status != old_status:
            changes.append(f"Status: {old_status} -> {new_status}")
            
        new_next_date = verdict.get('next_hearing_date')
        if new_next_date and new_next_date != 'Unknown' and new_next_date != old_next_date:
            changes.append(f"Next Hearing: {new_next_date}")
            
        def clean_val(v): return None if not v or str(v).lower() == 'unknown' else v
        
        update_data = {
            'last_checked_date': datetime.now(timezone.utc).isoformat(),
            'notes': verdict.get('notes', ''),
            'confidence': verdict.get('confidence', 'high')
        }
        
        if new_next_date and new_next_date != 'Unknown':
            update_data['next_hearing_date'] = new_next_date
            
        if clean_val(verdict.get('victim_name')):
            update_data['victim_name'] = verdict.get('victim_name')
            
        if clean_val(verdict.get('suspect_name')):
            update_data['suspect_name'] = verdict.get('suspect_name')

        if new_status not in ['Unknown', 'Pending']:
            update_data['status'] = new_status

        if clean_val(verdict.get('last_hearing_date')):
            update_data['last_hearing_date'] = verdict.get('last_hearing_date')

        update_case(case_id, update_data)
        print(f"✅ Case {case_id} updated successfully")

        alert_data = None
        if changes or new_status in ['Closed', 'Verdict Reached']:
             alert_data = {
                 'case_name': case_name,
                 'status': update_data.get('status', old_status),
                 'changes': changes,
                 'next_hearing': update_data.get('next_hearing_date', 'N/A'),
                 'notes': update_data.get('notes', '')
             }

        if return_alert_only:
            return alert_data
        else:
            if alert_data and (new_status in ['Closed', 'Verdict Reached'] and old_status != new_status):
                send_case_status_alert(case, new_status, verdict)
            return result
    
    except Exception as e:
        print(f"❌ Error processing case {case_id}: {e}")
        return {'error': str(e)}

def scheduled_case_check():
    """
    Background job that runs every 24 hours to check and update cases.
    BATCHES emails into one daily summary.
    """
    print("\n" + "=" * 60)
    print("🕐 Running scheduled case check...")
    print(f"   Time: {datetime.now().isoformat()}")
    print("=" * 60 + "\n")
    
    summary_report = []

    try:
        open_cases = get_cases_by_status('Open')
        pending_cases = get_cases_by_status('Pending')
        all_cases = open_cases + pending_cases
        
        if not all_cases:
            print("No open or pending cases to check.")
            return
        
        today = date.today()
        cases_processed = 0
        cases_skipped = 0
        
        for case in all_cases:
            case_name = case.get('case_name', 'Unknown')
            next_hearing = case.get('next_hearing_date')
            status = case.get('status', 'Open')
            
            if status == 'Closed':
                cases_skipped += 1
                continue
            
            should_run = False
            if next_hearing is None:
                should_run = True
            else:
                try:
                    if isinstance(next_hearing, str):
                        try:
                            hearing_date = datetime.strptime(next_hearing, "%Y-%m-%d").date()
                        except ValueError:
                            import dateutil.parser
                            hearing_date = dateutil.parser.parse(next_hearing).date()
                    else:
                        hearing_date = next_hearing
                    
                    days_until_hearing = (hearing_date - today).days
                    
                    if days_until_hearing < 0 or days_until_hearing <= 7:
                        should_run = True
                    elif 7 < days_until_hearing <= 30:
                        # Check "Middle Distance" cases if not checked in the last 3 days
                        last_checked_str = case.get('last_checked_date')
                        if last_checked_str:
                            try:
                                last_checked = datetime.fromisoformat(last_checked_str).date()
                                if (today - last_checked).days >= 3:
                                    should_run = True
                            except:
                                should_run = True
                        else:
                            should_run = True
                    elif days_until_hearing > 30:
                        should_run = False
                except Exception:
                    should_run = True
            
            if should_run:
                print(f"🔍 Running agent for: {case_name}")
                alert_data = process_case_update(case, return_alert_only=True)
                
                if alert_data:
                    summary_report.append(alert_data)
                    
                cases_processed += 1
            else:
                cases_skipped += 1
        
        if summary_report:
            print(f"📧 Sending Daily Summary for {len(summary_report)} cases...")
            send_daily_summary_email(summary_report)
        else:
            print("💤 No significant updates found. No email sent.")

        print(f"\n📊 Scheduled check complete: {cases_processed} processed, {cases_skipped} skipped")
    
    except Exception as e:
        print(f"❌ Scheduled job error: {e}")

def start_scheduler():
    """Start the background scheduler."""
    global scheduler_started
    
    if scheduler_started:
        return
    
    scheduler.add_job(
        func=scheduled_case_check,
        trigger=IntervalTrigger(hours=24),
        id='daily_case_check',
        name='Daily Case Status Check',
        replace_existing=True
    )
    
    scheduler.start()
    scheduler_started = True
    print("✅ Background scheduler started (running every 24 hours)")
    
    atexit.register(lambda: scheduler.shutdown())

@app.route('/api/health')
def health_check():
    """API health check endpoint."""
    return jsonify({
        "status": "healthy",
        "scheduler_running": scheduler.running if scheduler_started else False
    })


@app.route('/api/cases', methods=['GET'])
@login_required 
def list_cases():
    """
    List all cases with optional status filtering.
    """
    status = request.args.get('status')
    
    try:
        if status:
            cases = get_cases_by_status(status)
        else:
            cases = get_all_cases()
        
        return jsonify({
            "success": True,
            "data": cases,
            "count": len(cases)
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/api/cases/<int:case_id>', methods=['GET'])
@login_required
def get_case(case_id):
    """Get a specific case by ID."""
    try:
        case = get_case_by_id(case_id)
        
        if case:
            return jsonify({
                "success": True,
                "data": case
            })
        else:
            return jsonify({
                "success": False,
                "error": "Case not found"
            }), 404
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

def clean_date_input(date_str):
    """Converts 'Unknown', empty strings, or None to Python None (SQL NULL)."""
    if not date_str or str(date_str).strip().lower() in ['unknown', 'none', 'n/a', '']:
        return None
    return date_str

@app.route('/api/add_case', methods=['POST'])
@app.route('/api/cases', methods=['POST'])
@login_required 
def add_case():
    """
    Create a new case with 'Pending' status.
    """
    try:
        data = request.get_json()
        
        if not data or 'case_name' not in data:
            return jsonify({
                "success": False,
                "error": "case_name is required"
            }), 400
        
        case_data = {
            "case_name": data['case_name'],
            "docket_url": data.get('docket_url'),
            "victim_name": data.get('victim_name'),
            "suspect_name": data.get('suspect_name'),
            "next_hearing_date": clean_date_input(data.get('next_hearing_date')),
            "status": "Pending",  # Always start as Pending
            "notes": data.get('notes', 'Case added, awaiting research.'),
            "last_checked_date": datetime.utcnow().isoformat()
        }
        
        new_case = create_case(case_data)
        
        return jsonify({
            "success": True,
            "data": new_case,
            "message": "Case created with 'Pending' status. Use /api/trigger_update to research."
        }), 201
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/api/cases/<int:case_id>', methods=['PUT'])
@login_required
def modify_case(case_id):
    """Update an existing case."""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                "success": False,
                "error": "No data provided"
            }), 400
        
        update_data = {}
        allowed_fields = ['case_name', 'docket_url', 'victim_name', 'suspect_name', 
                         'next_hearing_date', 'status', 'notes', 'last_checked_date']
        
        for field in allowed_fields:
            if field in data:
                if field == 'next_hearing_date':
                    update_data[field] = clean_date_input(data[field])
                else:
                    update_data[field] = data[field]
        
        updated_case = update_case(case_id, update_data)
        
        if updated_case:
            return jsonify({
                "success": True,
                "data": updated_case,
                "message": "Case updated successfully"
            })
        else:
            return jsonify({
                "success": False,
                "error": "Case not found"
            }), 404
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/api/cases/<int:case_id>', methods=['DELETE'])
@login_required
def remove_case(case_id):
    """Delete a case."""
    try:
        delete_case(case_id)
        return jsonify({
            "success": True,
            "message": "Case deleted successfully"
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


from agent import PROGRESS

@app.route('/api/progress/<int:case_id>', methods=['GET'])
@login_required
def get_progress(case_id):
    """
    Get the real-time progress of a case research.
    checks Memory first (fastest), then DB (persistence).
    """
    progress = PROGRESS.get(case_id)
    if progress:
        return jsonify(progress)
    
    try:
        case = get_case_by_id(case_id)
        if case and case.get('processing_status') == 'processing':
            return jsonify({
                 "status": case.get('processing_status'),
                 "percent": case.get('progress_percent', 0),
                 "message": case.get('progress_message', 'Resuming...')
            })
        elif case and case.get('processing_status') == 'complete':
             return jsonify({
                 "status": "complete",
                 "percent": 100,
                 "message": "Complete"
            })
            
    except Exception as e:
        print(f"⚠️ DB Progress fetch failed: {e}")

    return jsonify({"status": "idle", "percent": 0, "message": "Waiting..."})


def run_case_background_update(case_id):
    """
    The actual logic to run the agent, update DB, and send emails.
    """
    with research_semaphore:
        # Create a new app context since we are in a thread
        with app.app_context():
            try:
                # ✅ FIX: Thread-Safety (Shared Brain Risk)
                # Create a fresh client connection for this thread
                from db import SUPABASE_URL, SUPABASE_KEY
                from supabase import create_client
                thread_safe_supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
                
                print(f"🔄 Starting background update for Case ID: {case_id}")
                
                thread_safe_supabase.table("cases").update({
                    "processing_status": "processing",
                    "progress_percent": 0,
                    "progress_message": "Starting research..."
                }).eq("id", case_id).execute()

                result = thread_safe_supabase.table("cases").select("*").eq("id", case_id).execute()

                if not result.data:
                    print(f"❌ Case {case_id} not found in background job.")
                    return
                
                case = result.data[0]
                old_case = case.copy()
                is_first_run = case.get('last_hearing_date') is None and case.get('next_hearing_date') is None

                case_name = case['case_name']
                docket_url = case.get('docket_url')

                updated_info = agent.process_case(case_name, docket_url=docket_url, case_id=case_id)

                changes_detected = []
                
                if is_first_run:
                    changes_detected.append("🚀 Initial Research Complete (First Run)")

                new_next_date = updated_info.get('verdict', {}).get('next_hearing_date')
                old_next_date = old_case.get('next_hearing_date')
                
                if new_next_date != old_next_date:
                    changes_detected.append(f"📅 Next Hearing: {new_next_date}")

                new_status = updated_info.get('verdict', {}).get('case_status', 'Open')
                if new_status != old_case.get('status'):
                    changes_detected.append(f"⚖️ Status Update: {new_status}")

                verdict = updated_info.get('verdict', {})

                def clean_date_for_db(date_str):
                    """Converts 'Unknown', empty strings, or None to Python None (SQL NULL)."""
                    if not date_str or str(date_str).lower() == 'unknown':
                        return None
                    return date_str

                data_to_save = {
                    "status": new_status,
                    "next_hearing_date": clean_date_for_db(verdict.get('next_hearing_date')),
                    "last_hearing_date": clean_date_for_db(verdict.get('last_hearing_date')),
                    "victim_name": verdict.get('victim_name'),
                    "suspect_name": verdict.get('suspect_name'),
                    "notes": verdict.get('notes'), 
                    "confidence": verdict.get('confidence', 'high'),
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }
                
                thread_safe_supabase.table("cases").update(data_to_save).eq("id", case_id).execute()

                if changes_detected:
                    print(f"📧 Sending email for: {case_name}")
                    
                    email_subject = f"⚖️ Update: {case_name}"
                    if is_first_run:
                        email_subject = f"🆕 New Case Analyzed: {case_name}"

                    updates_html = "".join([f"<li style='margin-bottom: 5px;'>{c}</li>" for c in changes_detected])
                    
                    next_date_display = data_to_save['next_hearing_date'] if data_to_save['next_hearing_date'] else '<span style="color:#999; font-style:italic;">None</span>'
                    last_date_display = data_to_save['last_hearing_date'] if data_to_save['last_hearing_date'] else '<span style="color:#999; font-style:italic;">Unknown</span>'
                    
                    status_color = "#2563eb" # Blue for Open
                    if data_to_save['status'] == 'Closed': status_color = "#dc2626" # Red
                    if data_to_save['status'] == 'Verdict Reached': status_color = "#059669" # Green

                    email_body = f"""
                    <html>
                    <body style="font-family: 'Segoe UI', Arial, sans-serif; color: #333; line-height: 1.6; background-color: #f9fafb; padding: 20px;">
                        
                        <div style="max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 8px; border: 1px solid #e5e7eb; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
                            
                            <div style="background-color: #0f172a; padding: 20px; text-align: center;">
                                <h2 style="color: #ffffff; margin: 0; font-size: 20px;">Legal Case Update</h2>
                            </div>

                            <div style="padding: 30px;">
                                
                                <h1 style="color: #1e293b; font-size: 24px; margin-top: 0; margin-bottom: 5px;">{case_name}</h1>
                                <p style="color: #64748b; font-size: 14px; margin-top: 0;">Automated Report</p>

                                <table style="width: 100%; border-collapse: collapse; margin: 25px 0;">
                                    <tr>
                                        <td style="padding: 10px 0; border-bottom: 1px solid #f1f5f9; color: #64748b; width: 40%;"><strong>Status</strong></td>
                                        <td style="padding: 10px 0; border-bottom: 1px solid #f1f5f9; font-weight: bold; color: {status_color};">
                                            {data_to_save['status']}
                                        </td>
                                    </tr>
                                    <tr>
                                        <td style="padding: 10px 0; border-bottom: 1px solid #f1f5f9; color: #64748b;"><strong>Next Hearing</strong></td>
                                        <td style="padding: 10px 0; border-bottom: 1px solid #f1f5f9; font-weight: bold;">
                                            {next_date_display}
                                        </td>
                                    </tr>
                                    <tr>
                                        <td style="padding: 10px 0; border-bottom: 1px solid #f1f5f9; color: #64748b;"><strong>Last Hearing</strong></td>
                                        <td style="padding: 10px 0; border-bottom: 1px solid #f1f5f9;">
                                            {last_date_display}
                                        </td>
                                    </tr>
                                </table>

                                <div style="background-color: #f8fafc; padding: 15px; border-radius: 6px; border-left: 4px solid {status_color}; margin-bottom: 25px;">
                                    <strong style="color: #334155; display: block; margin-bottom: 10px;">Recent Changes:</strong>
                                    <ul style="margin: 0; padding-left: 20px; color: #475569;">
                                        {updates_html}
                                    </ul>
                                </div>

                                <div>
                                    <strong style="color: #334155;">AI Analysis:</strong>
                                    <p style="background-color: #fff; border: 1px solid #e2e8f0; padding: 15px; border-radius: 6px; color: #475569; margin-top: 8px;">
                                        {data_to_save['notes']}
                                    </p>
                                </div>

                            </div>
                            
                            <div style="background-color: #f1f5f9; padding: 15px; text-align: center; font-size: 12px; color: #94a3b8;">
                                Generated by Legal Intelligence Dashboard • {datetime.now().strftime("%Y-%m-%d %H:%M")}
                            </div>
                        </div>
                    </body>
                    </html>
                    """
                    
                    try:
                        send_email_alert(email_subject, email_body)
                    except Exception as e:
                        print(f"⚠️ Email failed: {e}")
                else:
                    print("💤 No changes found. No email sent.")

            except Exception as e:
                print(f"❌ Background Process Error: {e}")


@app.route('/api/trigger_update', methods=['POST'])
@app.route('/api/trigger_update/<int:case_id>', methods=['POST'])
@login_required 
def trigger_update(case_id=None):
    """
    Triggers AI Research (Async).
    """
    try:
        supabase = get_supabase_client()

        if case_id is None:
             data = request.get_json()
             if not data or 'case_id' not in data:
                 return jsonify({"error": "case_id is required"}), 400
             case_id = data['case_id']

        # Get current status to prevent redundant runs
        result = supabase.table("cases").select("*").eq("id", case_id).execute()
        if not result.data:
            return jsonify({"error": "Case not found"}), 404
        
        case = result.data[0]

        status = case.get('processing_status')
        if status in ['processing', 'queued']:
             # ✅ FIX: Check if it's a "Zombie" (stuck for > 1 hour)
             last_checked = case.get('last_checked_date')
             is_stuck = False
             if last_checked:
                 try:
                     last_time = datetime.fromisoformat(last_checked.replace('Z', '+00:00'))
                     if datetime.now(timezone.utc) - last_time > timedelta(hours=1):
                         is_stuck = True
                 except:
                     pass # Date parse error, ignore

             if not is_stuck:
                 return jsonify({
                     "success": False, 
                     "error": f"Case is already {status}! Please wait."
                 }), 429
             else:
                 print(f"⚠️ Detected ZOMBIE case {case_id}. Forcing unlock.")
        
        # Mark as queued IMMEDIATELY
        supabase.table("cases").update({
            "processing_status": "queued",
            "progress_percent": 0,
            "progress_message": "Waiting in queue..."
        }).eq("id", case_id).execute()

        # Start background thread
        import threading
        thread = threading.Thread(target=run_case_background_update, args=(case_id,))
        thread.start()

        return jsonify({"success": True, "message": "Research started in background"}), 202

    except Exception as e:
        print(f"❌ Error: {e}")
        return jsonify({"error": str(e)}), 500
    

@app.route('/api/trigger_all', methods=['POST'])
@login_required # <--- PROTECTED because it triggers heavy load
def trigger_all():
    """
    Manually trigger the scheduled case check for all eligible cases.
    """
    try:
        scheduled_case_check()
        return jsonify({
            "success": True,
            "message": "Scheduled case check completed"
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/api/scheduler/status', methods=['GET'])
@login_required # <--- PROTECTED
def scheduler_status():
    """Get the scheduler status and next run time."""
    try:
        jobs = []
        if scheduler_started:
            for job in scheduler.get_jobs():
                jobs.append({
                    "id": job.id,
                    "name": job.name,
                    "next_run": str(job.next_run_time) if job.next_run_time else None
                })
        
        return jsonify({
            "success": True,
            "scheduler_running": scheduler.running if scheduler_started else False,
            "jobs": jobs
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
        
@app.route('/api/scheduler/run-now', methods=['POST'])
@login_required
def run_scheduler_now():
    """Manually trigger the scheduled job immediately."""
    try:
        scheduled_case_check()
        return jsonify({
            "success": True,
            "message": "Scheduled job executed"
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/api/schedule_custom_check', methods=['POST'])
@login_required
def schedule_custom_check():
    """
    Schedules a one-time 'God Mode' check for specific cases at a specific time.
    """
    try:
        data = request.get_json()
        case_ids = data.get('case_ids', [])
        run_time_str = data.get('run_time') # Expects ISO format "2026-01-21T09:00"
        
        if not case_ids or not run_time_str:
            return jsonify({"error": "Missing case_ids or run_time"}), 400

        # Parse the time string into a python datetime object
        run_date = datetime.fromisoformat(run_time_str)

        # Schedule a job for EACH case selected
        scheduled_count = 0
        for cid in case_ids:
            # We reuse the existing 'trigger_update' function because it ALREADY has email logic!
            job_id = f"manual_{cid}_{int(datetime.now().timestamp())}"
            
            scheduler.add_job(
                func=trigger_update, # The function to run
                args=[cid],          # The argument (case_id)
                trigger='date',      # Run once at a specific date
                run_date=run_date,
                id=job_id,
                name=f"One-time check for Case {cid}"
            )
            scheduled_count += 1
            print(f"⏰ Scheduled Check for Case {cid} at {run_date}")

        return jsonify({
            "success": True, 
            "message": f"Successfully scheduled {scheduled_count} cases for {run_time_str}"
        }), 200

    except Exception as e:
        print(f"❌ Scheduling Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/cases/upcoming-hearings', methods=['GET'])
@login_required
def upcoming_hearings():
    """
    Get cases with upcoming hearings.
    """
    try:
        days = request.args.get('days', 7, type=int)
        cases = get_upcoming_hearings(days)
        
        return jsonify({
            "success": True,
            "data": cases,
            "count": len(cases),
            "days_ahead": days
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/api/init-db', methods=['POST'])
@login_required # <--- RESTRICT THIS ONE CAREFULLY
def initialize_database():
    """
    Initialize the database (create tables if needed).
    This endpoint should be called once during initial setup.
    """
    try:
        success = init_db()
        
        if success:
            return jsonify({
                "success": True,
                "message": "Database initialized successfully"
            })
        else:
            return jsonify({
                "success": False,
                "message": "Database initialization requires manual intervention. Check server logs."
            }), 500
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "success": False,
        "error": "Endpoint not found"
    }), 404


@app.errorhandler(500)
def server_error(error):
    return jsonify({
        "success": False,
        "error": "Internal server error"
    }), 500


@app.route('/api/import_cases', methods=['POST'])
@login_required
def import_cases():
    """Import cases from an Excel or CSV file."""
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    try:
        # Read the file into a DataFrame
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file)
        elif file.filename.endswith(('.xls', '.xlsx')):
            df = pd.read_excel(file)
        else:
            return jsonify({"error": "Invalid file type. Please upload .csv or .xlsx"}), 400

        # --- SMART COLUMN MAPPING ("Spelling Bee" Fix) ---
        # 1. Normalize all existing columns to lower case stripped
        df.columns = [str(c).lower().strip().replace(' ', '').replace('_', '') for c in df.columns]

        # 2. Define acceptable variations
        column_map = {
            'case_name': ['casename', 'case', 'name', 'title', 'subject'],
            'victim_name': ['victimname', 'victim', 'plaintiff'],
            'suspect_name': ['suspectname', 'suspect', 'defendant', 'accused'],
            'docket_url': ['docketurl', 'docket', 'url', 'link']
        }

        # 3. Rename columns based on map
        new_columns = {}
        found_target_cols = set() # Track what we found

        for target_col, variations in column_map.items():
            for col in df.columns:
                # If we haven't found this target yet AND this column matches
                if target_col not in found_target_cols and col in variations:
                    new_columns[col] = target_col
                    found_target_cols.add(target_col) 
                    break # Stop looking for this target column!
        
        df.rename(columns=new_columns, inplace=True)
        
        # Check if 'case_name' exists after remapping
        if 'case_name' not in df.columns:
            return jsonify({"error": f"Could not find a 'Case Name' column. Found: {list(df.columns)}"}), 400

        # Loop through rows and add to Supabase
        imported_count = 0
        for _, row in df.iterrows():
            case_data = {
                "case_name": row['case_name'],
                "victim_name": row.get('victim_name', None),
                "suspect_name": row.get('suspect_name', None),
                "status": "Open",  # Default status
                "notes": f"Imported from {file.filename}",
                "last_checked_date": datetime.utcnow().isoformat()
            }
            # Insert into DB using our helper
            create_case(case_data)
            imported_count += 1

        return jsonify({"message": f"Successfully imported {imported_count} cases!"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==================== Main Entry Point ====================

if __name__ == '__main__':
    # Initialize database on startup
    print("Initializing database...")
    init_db()
    
    # Start the background scheduler
    start_scheduler()
    
    # Run the Flask app
    debug_mode = os.getenv('FLASK_DEBUG', 'True').lower() == 'true'
    port = int(os.getenv('PORT', 3004))
    
    print(f"\n🚀 Starting Legal Case Tracking API on port {port}")
    app.run(debug=debug_mode, host='0.0.0.0', port=port, use_reloader=False)
