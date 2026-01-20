from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
import pandas as pd
from werkzeug.utils import secure_filename
from db import (
    init_db, get_all_cases, get_case_by_id, create_case, update_case, 
    delete_case, get_cases_by_status, get_upcoming_hearings, get_supabase_client
)
import agent
from dotenv import load_dotenv
from datetime import datetime, date, timedelta, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import atexit

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
CORS(app)  # Enable CORS for JavaScript frontend
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')

# Email configuration
EMAIL_SENDER = os.getenv('EMAIL_SENDER')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')  # Gmail App Password
EMAIL_RECIPIENT = os.getenv('EMAIL_RECIPIENT')
SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', 587))

# Initialize scheduler
scheduler = BackgroundScheduler()
scheduler_started = False


# ==================== Email Utility ====================

def send_email_alert(subject: str, body: str) -> bool:
    """
    Send an email alert using SMTP (Gmail).
    
    Args:
        subject: Email subject line.
        body: Email body content.
    
    Returns:
        True if email sent successfully, False otherwise.
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
    
    Args:
        case: The case dictionary.
        new_status: The new status.
        verdict: The research verdict from the agent.
    """
    subject = f"🚨 Case Alert: {case['case_name']} - {new_status}"
    
    body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; padding: 20px;">
        <h2 style="color: #d32f2f;">Case Status Update</h2>
        <hr>
        <h3>{case['case_name']}</h3>
        <table style="border-collapse: collapse; width: 100%;">
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd;"><strong>New Status</strong></td>
                <td style="padding: 8px; border: 1px solid #ddd;">{new_status}</td>
            </tr>
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd;"><strong>Next Hearing Date</strong></td>
                <td style="padding: 8px; border: 1px solid #ddd;">{verdict.get('next_hearing_date', 'N/A')}</td>
            </tr>
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd;"><strong>Victim</strong></td>
                <td style="padding: 8px; border: 1px solid #ddd;">{verdict.get('victim_name', 'N/A')}</td>
            </tr>
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd;"><strong>Suspect</strong></td>
                <td style="padding: 8px; border: 1px solid #ddd;">{verdict.get('suspect_name', 'N/A')}</td>
            </tr>
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd;"><strong>Notes</strong></td>
                <td style="padding: 8px; border: 1px solid #ddd;">{verdict.get('notes', 'N/A')}</td>
            </tr>
        </table>
        <br>
        <p style="color: #666;">This is an automated alert from the Legal Case Tracking System.</p>
    </body>
    </html>
    """
    
    send_email_alert(subject, body)


# ==================== Background Scheduler Logic ====================

def process_case_update(case: dict) -> dict:
    """
    Run the research agent for a case and update the database.
    
    Args:
        case: The case dictionary from Supabase.
    
    Returns:
        The research result.
    """
    case_id = case['id']
    case_name = case['case_name']
    old_status = case.get('status', 'Open')
    
    print(f"🔄 Processing case {case_id}: {case_name}")
    
    try:
        # Run the research agent
        result = research_case(case_name)
        verdict = result.get('verdict', {})
        
        # Prepare update data
        update_data = {
            'last_checked_date': datetime.utcnow().isoformat(),
            'notes': verdict.get('notes', '')
        }
        
        # Update next hearing date if found
        next_hearing = verdict.get('next_hearing_date')
        if next_hearing and next_hearing != 'Unknown':
            update_data['next_hearing_date'] = next_hearing
        
        # Update victim/suspect names if found
        if verdict.get('victim_name') and verdict.get('victim_name') != 'Unknown':
            update_data['victim_name'] = verdict.get('victim_name')
        
        if verdict.get('suspect_name') and verdict.get('suspect_name') != 'Unknown':
            update_data['suspect_name'] = verdict.get('suspect_name')
        
        # Update status if determined
        new_status = verdict.get('case_status')
        if new_status and new_status not in ['Unknown', 'Pending']:
            update_data['status'] = new_status
            
            # Send email alert if status changed to Closed or Verdict Reached
            if new_status in ['Closed', 'Verdict Reached'] and old_status != new_status:
                send_case_status_alert(case, new_status, verdict)
        
        # Update the case in Supabase
        update_case(case_id, update_data)
        
        print(f"✅ Case {case_id} updated successfully")
        return result
    
    except Exception as e:
        print(f"❌ Error processing case {case_id}: {e}")
        return {'error': str(e)}


def scheduled_case_check():
    """
    Background job that runs every 24 hours to check and update cases.
    
    Logic:
    - Query all 'Open' cases
    - If next_hearing_date > 30 days away: SKIP
    - If next_hearing_date < 7 days away: RUN agent
    - If next_hearing_date is None/Unknown: RUN agent
    - If status is 'Closed': SKIP
    """
    print("\n" + "=" * 60)
    print("🕐 Running scheduled case check...")
    print(f"   Time: {datetime.now().isoformat()}")
    print("=" * 60 + "\n")
    
    try:
        # Get all open cases
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
            
            # Skip closed cases (shouldn't be in the list, but just in case)
            if status == 'Closed':
                print(f"⏭️ Skipping closed case: {case_name}")
                cases_skipped += 1
                continue
            
            # Determine if we should run the agent
            should_run = False
            reason = ""
            
            if next_hearing is None:
                should_run = True
                reason = "No hearing date set"
            else:
                try:
                    # Parse the hearing date
                    if isinstance(next_hearing, str):
                        hearing_date = datetime.strptime(next_hearing, "%Y-%m-%d").date()
                    else:
                        hearing_date = next_hearing
                    
                    days_until_hearing = (hearing_date - today).days
                    
                    if days_until_hearing < 0:
                        # Hearing date is in the past
                        should_run = True
                        reason = f"Hearing date passed ({next_hearing})"
                    elif days_until_hearing <= 7:
                        # Within 7 days
                        should_run = True
                        reason = f"Hearing in {days_until_hearing} days"
                    elif days_until_hearing > 30:
                        # More than 30 days away
                        should_run = False
                        reason = f"Hearing > 30 days away ({days_until_hearing} days)"
                    else:
                        # Between 7 and 30 days
                        should_run = False
                        reason = f"Hearing in {days_until_hearing} days (between 7-30)"
                
                except (ValueError, TypeError) as e:
                    should_run = True
                    reason = f"Could not parse hearing date: {next_hearing}"
            
            if should_run:
                print(f"🔍 Running agent for: {case_name} ({reason})")
                process_case_update(case)
                cases_processed += 1
            else:
                print(f"⏭️ Skipping: {case_name} ({reason})")
                cases_skipped += 1
        
        print(f"\n📊 Scheduled check complete: {cases_processed} processed, {cases_skipped} skipped")
    
    except Exception as e:
        print(f"❌ Scheduled job error: {e}")


def start_scheduler():
    """Start the background scheduler."""
    global scheduler_started
    
    if scheduler_started:
        return
    
    # Add the job to run every 24 hours
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
    
    # Shutdown scheduler when the app stops
    atexit.register(lambda: scheduler.shutdown())


# ==================== Health Check Endpoints ====================

@app.route('/')
def index():
    """Serve the Frontend."""
    return render_template('index.html')


@app.route('/api/health')
def health_check():
    """API health check endpoint."""
    return jsonify({
        "status": "healthy",
        "scheduler_running": scheduler.running if scheduler_started else False
    })


# ==================== Case CRUD Endpoints ====================

@app.route('/api/cases', methods=['GET'])
def list_cases():
    """
    List all cases with optional status filtering.
    
    Query params:
        status: Filter by case status ('Open', 'Closed', 'Verdict Reached', 'Pending')
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


# ==================== Helpers ====================

def clean_date_input(date_str):
    """Converts 'Unknown', empty strings, or None to Python None (SQL NULL)."""
    if not date_str or str(date_str).strip().lower() in ['unknown', 'none', 'n/a', '']:
        return None
    return date_str

@app.route('/api/add_case', methods=['POST'])
@app.route('/api/cases', methods=['POST'])
def add_case():
    """
    Create a new case with 'Pending' status.
    
    Request body:
        case_name: Required
        victim_name: Optional
        suspect_name: Optional
        next_hearing_date: Optional (YYYY-MM-DD format)
        notes: Optional
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
def modify_case(case_id):
    """Update an existing case."""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                "success": False,
                "error": "No data provided"
            }), 400
        
        # Only include fields that are provided
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


# ==================== Agent Trigger Endpoints ====================

@app.route('/api/trigger_update', methods=['POST'])
@app.route('/api/trigger_update/<int:case_id>', methods=['POST'])
def trigger_update(case_id=None):
    """
    Triggers AI Research. 
    Sends email if:
    1. Data changed (Dates, Status).
    2. It is the FIRST run (New case/Imported case).
    """
    try:
        # DB Connection
        supabase = get_supabase_client()

        # Handle case_id from JSON if not in URL
        if case_id is None:
             data = request.get_json()
             if not data or 'case_id' not in data:
                 return jsonify({"error": "case_id is required"}), 400
             case_id = data['case_id']

        # 1. Get the current (OLD) data
        current_data = supabase.table("cases").select("*").eq("id", case_id).execute().data
        if not current_data:
            return jsonify({"error": "Case not found"}), 404
        
        old_case = current_data[0]
        case_name = old_case['case_name']
        docket_url = old_case.get('docket_url')

        # --- FIRST RUN DETECTION ---
        # We check if the notes say "Imported" OR if the dates are empty (None)
        is_first_run = False
        if old_case.get('next_hearing_date') is None and old_case.get('last_hearing_date') is None:
            is_first_run = True
        if "Imported" in (old_case.get('notes') or ""):
            is_first_run = True

        # 2. Run the AI Agent (Searcher)
        print(f"🔄 Processing case {case_id}: {case_name}")
        # Using agent.process_case (aliased to research_case)
        updated_info = agent.process_case(case_name, docket_url=docket_url)

        # 3. Detect Changes
        changes_detected = []
        
        # A. Check if it's the first run
        if is_first_run:
            changes_detected.append("🚀 Initial Research Complete (First Run)")

        # B. Check Date Changes (ignore if both are None/Unknown)
        new_next_date = updated_info.get('verdict', {}).get('next_hearing_date')
        old_next_date = old_case.get('next_hearing_date')
        
        if new_next_date != old_next_date:
            # Don't double count if it's first run, but good to list specific dates found
            changes_detected.append(f"📅 Next Hearing: {new_next_date}")

        # C. Check Status Change
        new_status = updated_info.get('verdict', {}).get('case_status', 'Open')
        if new_status != old_case.get('status'):
            changes_detected.append(f"⚖️ Status Update: {new_status}")

        # 4. Save to Database
        verdict = updated_info.get('verdict', {})

        # --- HELPER: Clean Dates for Database ---
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
        
        supabase.table("cases").update(data_to_save).eq("id", case_id).execute()

        # 5. SEND EMAIL LOGIC
        if changes_detected:
            print(f"📧 Sending email for: {case_name}")
            
            email_subject = f"⚖️ Update: {case_name}"
            if is_first_run:
                email_subject = f"🆕 New Case Analyzed: {case_name}"

            # Professional HTML Email Template
            updates_html = "".join([f"<li style='margin-bottom: 5px;'>{c}</li>" for c in changes_detected])
            
            # Format dates nicely (handle None)
            next_date_display = data_to_save['next_hearing_date'] if data_to_save['next_hearing_date'] else '<span style="color:#999; font-style:italic;">None</span>'
            last_date_display = data_to_save['last_hearing_date'] if data_to_save['last_hearing_date'] else '<span style="color:#999; font-style:italic;">Unknown</span>'
            
            # Status Color Logic
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
            
            # Send the mail
            try:
                 send_email_alert(email_subject, email_body)
            except Exception as e:
                 print(f"⚠️ Email failed: {e}")
            
            return jsonify({"success": True, "message": "Updated & Email Sent!"}), 200
        else:
            print("💤 No changes found. No email sent.")
            return jsonify({"success": True, "message": "Checked, no updates."}), 200

    except Exception as e:
        print(f"❌ Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/trigger_all', methods=['POST'])
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


# ==================== Scheduler Control Endpoints ====================

@app.route('/api/scheduler/status', methods=['GET'])
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


# ==================== Utility Endpoints ====================

@app.route('/api/cases/upcoming-hearings', methods=['GET'])
def upcoming_hearings():
    """
    Get cases with upcoming hearings.
    
    Query params:
        days: Number of days to look ahead (default: 7)
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


# ==================== Error Handlers ====================

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

        # Standardize column names (lower case, strip spaces)
        df.columns = [c.lower().strip().replace(' ', '_') for c in df.columns]

        # Check if 'case_name' exists
        if 'case_name' not in df.columns:
            return jsonify({"error": "File must have a 'Case Name' column"}), 400

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
    port = int(os.getenv('PORT', 5000))
    
    print(f"\n🚀 Starting Legal Case Tracking API on port {port}")
    app.run(debug=debug_mode, host='0.0.0.0', port=port, use_reloader=False)
