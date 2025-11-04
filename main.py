#!/usr/bin/env python3
"""
secure_mcp_server.py

Secure MCP Server for Leave Management + HR + Company Management
with API Key Authentication
"""

import os
import re
import secrets
import urllib.parse
from typing import List, Optional, Dict, Any
from difflib import SequenceMatcher
from datetime import datetime, date, timedelta

# Third-party imports
import mysql.connector
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import PlainTextResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware import Middleware

# Optional Levenshtein import
try:
    import Levenshtein
except ImportError:
    Levenshtein = None

# -------------------------------
# Security Configuration
# -------------------------------
class SecurityConfig:
    # API Keys from environment (comma-separated)
    API_KEYS = os.environ.get("MCP_API_KEYS", "").split(",")
    # Filter out empty strings
    API_KEYS = [key.strip() for key in API_KEYS if key.strip()]
    
    # Generate a default key if none provided (for development)
    if not API_KEYS:
        DEFAULT_KEY = "dev-key-" + secrets.token_hex(16)
        API_KEYS = [DEFAULT_KEY]
        print(f"⚠️  DEVELOPMENT MODE: Using auto-generated API key: {DEFAULT_KEY}")
        print("⚠️  Set MCP_API_KEYS environment variable in production!")

    REQUIRED_HEADER = "X-API-Key"
    
    # Rate limiting (basic implementation)
    RATE_LIMIT_REQUESTS = 100  # requests per minute
    rate_limit_store = {}

# -------------------------------
# API Key Middleware
# -------------------------------
class APIKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip auth for health checks and root
        if request.url.path in ["/health", "/", "/docs", "/openapi.json"]:
            return await call_next(request)
        
        # Check for API key in header
        api_key = request.headers.get(SecurityConfig.REQUIRED_HEADER)
        
        if not api_key:
            return JSONResponse(
                {"error": f"API key required in {SecurityConfig.REQUIRED_HEADER} header"}, 
                status=401
            )
        
        if api_key not in SecurityConfig.API_KEYS:
            return JSONResponse(
                {"error": "Invalid API key"}, 
                status=401
            )
        
        # Basic rate limiting
        client_ip = request.client.host if request.client else "unknown"
        current_minute = datetime.now().strftime("%Y-%m-%d-%H-%M")
        rate_key = f"{client_ip}:{current_minute}"
        
        SecurityConfig.rate_limit_store[rate_key] = SecurityConfig.rate_limit_store.get(rate_key, 0) + 1
        
        if SecurityConfig.rate_limit_store[rate_key] > SecurityConfig.RATE_LIMIT_REQUESTS:
            return JSONResponse(
                {"error": "Rate limit exceeded"}, 
                status=429
            )
        
        # Clean old rate limit entries (basic cleanup)
        old_keys = [k for k in SecurityConfig.rate_limit_store.keys() 
                   if not k.endswith(current_minute)]
        for k in old_keys:
            SecurityConfig.rate_limit_store.pop(k, None)
        
        return await call_next(request)

# -------------------------------
# MCP Server with Middleware
# -------------------------------
mcp = FastMCP(
    "SecureLeaveManagerPlus", 
    middleware=[
        Middleware(APIKeyMiddleware)
    ]
)

# -------------------------------
# Database Connection
# -------------------------------
def get_connection():
    """Establish database connection with environment variables"""
    return mysql.connector.connect(
        host=os.environ.get("DB_HOST", "103.174.10.72"),
        user=os.environ.get("DB_USER", "tt_crm_mcp"),
        password=os.environ.get("DB_PASSWORD", "F*PAtqhu@sg2w58n"),
        database=os.environ.get("DB_NAME", "tt_crm_mcp"),
        port=int(os.environ.get("DB_PORT", "3306")),
        autocommit=True,
        connection_timeout=30,
        pool_size=5
    )

# -------------------------------
# AI-Powered Name Matching
# -------------------------------
class NameMatcher:
    @staticmethod
    def normalize_name(name: str) -> str:
        name = (name or "").lower().strip()
        name = re.sub(r'[^\w\s]', '', name)
        name = re.sub(r'\s+', ' ', name)
        return name

    @staticmethod
    def similarity_score(name1: str, name2: str) -> float:
        name1_norm = NameMatcher.normalize_name(name1)
        name2_norm = NameMatcher.normalize_name(name2)

        if Levenshtein:
            try:
                dist = Levenshtein.distance(name1_norm, name2_norm)
                max_len = max(len(name1_norm), len(name2_norm), 1)
                levenshtein_sim = 1 - (dist / max_len)
            except Exception:
                levenshtein_sim = SequenceMatcher(None, name1_norm, name2_norm).ratio()
        else:
            levenshtein_sim = SequenceMatcher(None, name1_norm, name2_norm).ratio()

        sequence_sim = SequenceMatcher(None, name1_norm, name2_norm).ratio()
        combined_score = (levenshtein_sim * 0.6) + (sequence_sim * 0.4)
        return combined_score

    @staticmethod
    def extract_name_parts(full_name: str) -> Dict[str, str]:
        parts = (full_name or "").split()
        if len(parts) == 0:
            return {'first': '', 'last': ''}
        if len(parts) == 1:
            return {'first': parts[0], 'last': ''}
        elif len(parts) == 2:
            return {'first': parts[0], 'last': parts[1]}
        else:
            return {'first': parts[0], 'last': parts[-1]}

    @staticmethod
    def fuzzy_match_employee(search_name: str, employees: List[Dict[str, Any]], threshold: float = 0.6) -> List[Dict[str, Any]]:
        matches = []
        search_parts = NameMatcher.extract_name_parts(search_name)

        for emp in employees:
            scores = []
            emp_full_name = f"{emp.get('developer_name','')}".strip()
            scores.append(NameMatcher.similarity_score(search_name, emp_full_name))

            if ' ' in emp_full_name:
                first_name = emp_full_name.split()[0]
                last_name = ' '.join(emp_full_name.split()[1:])
                scores.append(NameMatcher.similarity_score(search_name, f"{first_name} {last_name}"))
                scores.append(NameMatcher.similarity_score(search_name, f"{last_name} {first_name}"))

            if search_parts['last']:
                first_score = NameMatcher.similarity_score(search_parts['first'], emp_full_name.split()[0] if emp_full_name else '')
                last_score = NameMatcher.similarity_score(search_parts['last'], ' '.join(emp_full_name.split()[1:]) if ' ' in emp_full_name else '')
                if first_score > 0 or last_score > 0:
                    scores.append((first_score + last_score) / 2)

            best_score = max(scores) if scores else 0
            if best_score >= threshold:
                matches.append({'employee': emp, 'score': best_score, 'match_type': 'fuzzy'})

        matches.sort(key=lambda x: x['score'], reverse=True)
        return matches

# -------------------------------
# Employee Search & Resolution
# -------------------------------
def fetch_employees_ai(search_term: str = None, emp_id: int = None) -> List[Dict[str, Any]]:
    """Enhanced employee search with fuzzy matching"""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        if emp_id:
            cursor.execute("""
                SELECT d.id, d.developer_name, d.designation, d.email_id, d.mobile, 
                       d.status, d.doj, d.emp_number, d.blood_group,
                       u.username, d.opening_leave_balance, d.is_pf_enabled, d.pf_join_date,
                       d.personal_emaill, d.emergency_contact_name, d.emergency_contact_no,
                       d.confirmation_date, d.releiving_date
                FROM developer d
                LEFT JOIN user u ON d.user_id = u.user_id
                WHERE d.id = %s
            """, (emp_id,))
        elif search_term:
            cursor.execute("""
                SELECT d.id, d.developer_name, d.designation, d.email_id, d.mobile, 
                       d.status, d.doj, d.emp_number, d.blood_group,
                       u.username, d.opening_leave_balance, d.is_pf_enabled, d.pf_join_date,
                       d.personal_emaill, d.emergency_contact_name, d.emergency_contact_no,
                       d.confirmation_date, d.releiving_date
                FROM developer d
                LEFT JOIN user u ON d.user_id = u.user_id
                WHERE d.developer_name LIKE %s OR d.email_id LIKE %s 
                   OR d.mobile LIKE %s OR d.emp_number LIKE %s
                ORDER BY d.developer_name
            """, (f"%{search_term}%", f"%{search_term}%", f"%{search_term}%", f"%{search_term}%"))
        else:
            return []

        rows = cursor.fetchall()

        # Fuzzy search fallback
        if search_term and not rows:
            cursor.execute("""
                SELECT d.id, d.developer_name, d.designation, d.email_id, d.mobile, 
                       d.status, d.doj, d.emp_number, d.blood_group,
                       u.username, d.opening_leave_balance, d.is_pf_enabled, d.pf_join_date,
                       d.personal_emaill, d.emergency_contact_name, d.emergency_contact_no,
                       d.confirmation_date, d.releiving_date
                FROM developer d
                LEFT JOIN user u ON d.user_id = u.user_id
                WHERE d.status = 1
            """)
            all_employees = cursor.fetchall()
            fuzzy_matches = NameMatcher.fuzzy_match_employee(search_term, all_employees)
            rows = [match['employee'] for match in fuzzy_matches[:5]]

        return rows

    except Exception as e:
        print(f"Database error in fetch_employees_ai: {e}")
        return []
    finally:
        cursor.close()
        conn.close()

def format_employee_options(employees: List[Dict[str, Any]]) -> str:
    """Format employee list for user selection"""
    options = []
    for i, emp in enumerate(employees, 1):
        option = f"{i}. 👤 {emp.get('developer_name','Unknown')}"
        if emp.get('designation'):
            option += f" | 💼 {emp.get('designation')}"
        if emp.get('email_id'):
            option += f" | 📧 {emp.get('email_id')}"
        if emp.get('emp_number'):
            option += f" | 🆔 {emp.get('emp_number')}"
        if emp.get('mobile'):
            option += f" | 📞 {emp.get('mobile')}"
        status = "Active" if emp.get('status') == 1 else "Inactive"
        option += f" | 🔰 {status}"
        options.append(option)
    return "\n".join(options)

def resolve_employee_ai(search_name: str, additional_context: str = None) -> Dict[str, Any]:
    """Resolve employee name with AI-powered matching"""
    employees = fetch_employees_ai(search_term=search_name)

    if not employees:
        return {'status': 'not_found', 'message': f"No employees found matching '{search_name}'"}

    if len(employees) == 1:
        return {'status': 'resolved', 'employee': employees[0]}

    if additional_context:
        context_lower = (additional_context or '').lower()
        filtered_employees = []
        for emp in employees:
            designation = (emp.get('designation') or '').lower()
            email = (emp.get('email_id') or '').lower()
            emp_number = (emp.get('emp_number') or '').lower()
            
            if (context_lower in designation or 
                context_lower in email or 
                context_lower in emp_number or
                context_lower in emp.get('developer_name', '').lower()):
                filtered_employees.append(emp)
        
        if len(filtered_employees) == 1:
            return {'status': 'resolved', 'employee': filtered_employees[0]}

    return {
        'status': 'ambiguous',
        'employees': employees,
        'message': f"Found {len(employees)} employees. Please specify:"
    }

# -------------------------------
# Core Leave Management Functions
# -------------------------------
def get_leave_balance_for_employee(developer_id: int) -> Dict[str, Any]:
    """Calculate leave balance for an employee"""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT opening_leave_balance, doj, status 
            FROM developer 
            WHERE id = %s
        """, (developer_id,))
        developer_info = cursor.fetchone()
        
        if not developer_info:
            return {"error": "Employee not found"}
        
        cursor.execute("""
            SELECT leave_type, COUNT(*) as count
            FROM leave_requests 
            WHERE developer_id = %s AND status = 'Approved'
            GROUP BY leave_type
        """, (developer_id,))
        leave_counts = cursor.fetchall()
        
        used_leaves = 0.0
        for leave in leave_counts:
            lt = (leave.get('leave_type') or '').upper()
            cnt = float(leave.get('count') or 0)
            if lt == 'FULL DAY':
                used_leaves += cnt
            elif lt in ['HALF DAY', 'COMPENSATION HALF DAY']:
                used_leaves += cnt * 0.5
            elif lt in ['2 HRS', 'COMPENSATION 2 HRS']:
                used_leaves += cnt * 0.25
            else:
                used_leaves += cnt

        opening_balance = float(developer_info.get('opening_leave_balance') or 0)
        current_balance = opening_balance - used_leaves
        
        return {
            "opening_balance": opening_balance,
            "used_leaves": used_leaves,
            "current_balance": current_balance,
            "leave_details": leave_counts
        }
        
    except Exception as e:
        return {"error": f"Error calculating leave balance: {str(e)}"}
    finally:
        cursor.close()
        conn.close()

def get_employee_work_report(developer_id: int, days: int = 30) -> List[Dict[str, Any]]:
    """Get recent work reports for an employee"""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT wr.task, wr.description, wr.date, wr.total_time, 
                   p.title as project_name, c.client_name
            FROM work_report wr
            LEFT JOIN project p ON wr.project_id = p.id
            LEFT JOIN client c ON wr.client_id = c.id
            WHERE wr.developer_id = %s 
            AND wr.date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
            ORDER BY wr.date DESC
            LIMIT 100
        """, (developer_id, days))
        
        return cursor.fetchall()
        
    except Exception as e:
        print(f"Error fetching work report: {e}")
        return []
    finally:
        cursor.close()
        conn.close()

def get_employee_leave_requests(developer_id: int, limit: int = 100) -> List[Dict[str, Any]]:
    """Get leave requests for an employee"""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT request_id, leave_type, date_of_leave, status, 
                   dev_comments, admin_comments, created_at
            FROM leave_requests 
            WHERE developer_id = %s 
            ORDER BY date_of_leave DESC
            LIMIT %s
        """, (developer_id, limit))
        
        return cursor.fetchall()
        
    except Exception as e:
        print(f"Error fetching leave requests: {e}")
        return []
    finally:
        cursor.close()
        conn.close()

# -------------------------------
# MCP Tools - Leave Management
# -------------------------------
@mcp.tool()
def get_employee_details(name: str, additional_context: Optional[str] = None) -> str:
    """Get comprehensive details for an employee including personal info, leave balance, and recent activity"""
    resolution = resolve_employee_ai(name, additional_context)
    
    if resolution['status'] == 'not_found':
        return f"❌ No employee found matching '{name}'."
    
    if resolution['status'] == 'ambiguous':
        options_text = format_employee_options(resolution['employees'])
        return f"🔍 {resolution['message']}\n\n{options_text}\n\n💡 Tip: Specify by designation, email, employee number, or say the number (e.g., '1')"

    emp = resolution['employee']
    
    # Get additional information
    leave_balance = get_leave_balance_for_employee(emp['id'])
    work_reports = get_employee_work_report(emp['id'], days=7)
    leave_requests = get_employee_leave_requests(emp['id'], limit=10)
    
    response = f"✅ **Employee Details**\n\n"
    response += f"👤 **{emp['developer_name']}**\n"
    response += f"🆔 Employee ID: {emp['id']} | Employee #: {emp.get('emp_number', 'N/A')}\n"
    response += f"💼 Designation: {emp.get('designation', 'N/A')}\n"
    response += f"📧 Email: {emp.get('email_id', 'N/A')}\n"
    response += f"📞 Mobile: {emp.get('mobile', 'N/A')}\n"
    response += f"🩸 Blood Group: {emp.get('blood_group', 'N/A')}\n"
    response += f"📅 Date of Joining: {emp.get('doj', 'N/A')}\n"
    response += f"🔰 Status: {'Active' if emp.get('status') == 1 else 'Inactive'}\n\n"
    
    # Leave Balance
    if 'error' not in leave_balance:
        response += f"📊 **Leave Balance:** {leave_balance['current_balance']:.1f} days\n"
        response += f"   - Opening Balance: {leave_balance['opening_balance']}\n"
        response += f"   - Leaves Used: {leave_balance['used_leaves']:.1f} days\n\n"
    else:
        response += f"📊 Leave Balance: Data not available\n\n"
    
    # Recent Work Reports
    if work_reports:
        response += f"📋 **Recent Work (Last 7 days):**\n"
        for report in work_reports[:3]:
            hours = (report['total_time'] or 0) / 3600 if report.get('total_time') else 0.0
            response += f"   - {report['date']}: {report['task'][:60]}... ({hours:.1f}h)\n"
        response += "\n"
    
    # Recent Leave Requests
    if leave_requests:
        response += f"🏖️  **Recent Leave Requests:**\n"
        for leave in leave_requests[:3]:
            status_icon = "✅" if leave['status'] == 'Approved' else "⏳" if leave['status'] in ['Requested', 'Pending'] else "❌"
            response += f"   - {leave['date_of_leave']}: {leave['leave_type']} {status_icon}\n"
    
    return response

@mcp.tool()
def get_leave_balance(name: str, additional_context: Optional[str] = None) -> str:
    """Get detailed leave balance information for an employee"""
    resolution = resolve_employee_ai(name, additional_context)
    
    if resolution['status'] == 'not_found':
        return f"❌ No employee found matching '{name}'."
    
    if resolution['status'] == 'ambiguous':
        options_text = format_employee_options(resolution['employees'])
        return f"🔍 {resolution['message']}\n\n{options_text}"

    emp = resolution['employee']
    leave_balance = get_leave_balance_for_employee(emp['id'])
    
    if 'error' in leave_balance:
        return f"❌ Error retrieving leave balance for {emp['developer_name']}: {leave_balance['error']}"
    
    response = f"📊 **Leave Balance for {emp['developer_name']}**\n\n"
    response += f"💼 Designation: {emp.get('designation', 'N/A')}\n"
    response += f"📧 Email: {emp.get('email_id', 'N/A')}\n\n"
    
    response += f"💰 **Current Balance:** {leave_balance['current_balance']:.1f} days\n"
    response += f"📥 Opening Balance: {leave_balance['opening_balance']} days\n"
    response += f"📤 Leaves Used: {leave_balance['used_leaves']:.1f} days\n\n"
    
    if leave_balance['leave_details']:
        response += f"📋 **Breakdown of Used Leaves:**\n"
        for leave in leave_balance['leave_details']:
            lt = (leave.get('leave_type') or '').upper()
            days_equiv = 1.0 if lt == 'FULL DAY' else 0.5 if lt in ['HALF DAY','COMPENSATION HALF DAY'] else 0.25 if lt in ['2 HRS','COMPENSATION 2 HRS'] else 1.0
            total_days = float(leave.get('count') or 0) * days_equiv
            response += f"   - {leave['leave_type']}: {leave['count']} times ({total_days:.1f} days)\n"
    
    return response

@mcp.tool()
def get_work_report(name: str, days: int = 7, additional_context: Optional[str] = None) -> str:
    """Get work report for an employee for specified number of days"""
    resolution = resolve_employee_ai(name, additional_context)
    
    if resolution['status'] == 'not_found':
        return f"❌ No employee found matching '{name}'."
    
    if resolution['status'] == 'ambiguous':
        options_text = format_employee_options(resolution['employees'])
        return f"🔍 {resolution['message']}\n\n{options_text}"

    emp = resolution['employee']
    work_reports = get_employee_work_report(emp['id'], days)
    
    response = f"📋 **Work Report for {emp['developer_name']}**\n"
    response += f"💼 Designation: {emp.get('designation', 'N/A')}\n"
    response += f"📅 Period: Last {days} days\n\n"
    
    if not work_reports:
        response += "No work reports found for the specified period."
        return response
    
    total_hours = 0.0
    for report in work_reports:
        hours = (report['total_time'] or 0) / 3600 if report.get('total_time') else 0.0
        total_hours += hours
        
        response += f"**{report['date']}** - {report.get('project_name', 'No Project')}\n"
        response += f"Client: {report.get('client_name', 'N/A')}\n"
        response += f"Task: {report['task'][:120]}{'...' if len(report.get('task','')) > 120 else ''}\n"
        if report.get('description'):
            response += f"Details: {report['description'][:120]}{'...' if len(report.get('description','')) > 120 else ''}\n"
        response += f"Hours: {hours:.1f}h\n"
        response += "---\n"
    
    response += f"\n**Total Hours ({days} days): {total_hours:.1f}h**\n"
    if days > 0:
        response += f"Average per day: {total_hours/days:.1f}h"
    
    return response

@mcp.tool()
def get_leave_history(name: str, additional_context: Optional[str] = None) -> str:
    """Get leave history for an employee"""
    resolution = resolve_employee_ai(name, additional_context)
    
    if resolution['status'] == 'not_found':
        return f"❌ No employee found matching '{name}'."
    
    if resolution['status'] == 'ambiguous':
        options_text = format_employee_options(resolution['employees'])
        return f"🔍 {resolution['message']}\n\n{options_text}"

    emp = resolution['employee']
    leave_requests = get_employee_leave_requests(emp['id'], limit=100)
    
    response = f"🏖️  **Leave History for {emp['developer_name']}**\n"
    response += f"💼 Designation: {emp.get('designation', 'N/A')}\n\n"
    
    if not leave_requests:
        response += "No leave requests found."
        return response
    
    approved_count = sum(1 for lr in leave_requests if lr['status'] == 'Approved')
    pending_count = sum(1 for lr in leave_requests if lr['status'] in ['Requested', 'Pending'])
    declined_count = sum(1 for lr in leave_requests if lr['status'] == 'Declined')
    
    response += f"📊 Summary: {approved_count} Approved, {pending_count} Pending, {declined_count} Declined\n\n"
    
    for leave in leave_requests[:20]:
        status_icon = "✅" if leave['status'] == 'Approved' else "⏳" if leave['status'] in ['Requested', 'Pending'] else "❌"
        response += f"**{leave['date_of_leave']}** - {leave['leave_type']} {status_icon}\n"
        if leave.get('dev_comments'):
            response += f"Reason: {leave['dev_comments']}\n"
        if leave.get('admin_comments') and leave['status'] != 'Pending':
            response += f"Admin Note: {leave['admin_comments']}\n"
        response += "---\n"
    
    return response

@mcp.tool()
def search_employees(search_query: str) -> str:
    """Search for employees by name, designation, email, or employee number"""
    employees = fetch_employees_ai(search_term=search_query)
    
    if not employees:
        return f"❌ No employees found matching '{search_query}'"
    
    response = f"🔍 **Search Results for '{search_query}':**\n\n"
    
    for i, emp in enumerate(employees, 1):
        response += f"{i}. **{emp['developer_name']}**\n"
        response += f"   💼 {emp.get('designation', 'N/A')}\n"
        response += f"   📧 {emp.get('email_id', 'N/A')}\n"
        response += f"   📞 {emp.get('mobile', 'N/A')}\n"
        response += f"   🆔 {emp.get('emp_number', 'N/A')}\n"
        response += f"   🔰 {'Active' if emp.get('status') == 1 else 'Inactive'}\n"
        
        # Get quick leave balance
        try:
            leave_balance = get_leave_balance_for_employee(emp['id'])
            if 'error' not in leave_balance:
                response += f"   📊 Leave Balance: {leave_balance['current_balance']:.1f} days\n"
        except Exception:
            pass
        
        response += "\n"
    
    return response

# -------------------------------
# HR Management Tools
# -------------------------------
@mcp.tool()
def get_employee_profile(name: str, additional_context: Optional[str] = None) -> str:
    """Return extended HR profile (documents, PF status, confirmation, etc.)"""
    resolution = resolve_employee_ai(name, additional_context)
    if resolution['status'] != 'resolved':
        if resolution['status'] == 'ambiguous':
            return f"🔍 Ambiguous: \n\n{format_employee_options(resolution['employees'])}"
        return f"❌ No employee found matching '{name}'."

    emp = resolution['employee']
    response = f"📇 **HR Profile: {emp['developer_name']}**\n"
    response += f"🆔 ID: {emp['id']}  |  Emp#: {emp.get('emp_number','N/A')}\n"
    response += f"💼 Designation: {emp.get('designation','N/A')}\n"
    response += f"📅 DOJ: {emp.get('doj','N/A')}  |  Confirmation Date: {emp.get('confirmation_date','N/A')}\n"
    response += f"🏥 PF Enabled: {'Yes' if emp.get('is_pf_enabled') in [1,'1',True] else 'No'}\n"
    response += f"📧 Work Email: {emp.get('email_id','N/A')}  |  Personal Email: {emp.get('personal_emaill','N/A')}\n"
    response += f"📞 Mobile: {emp.get('mobile','N/A')}  |  Emergency Contact: {emp.get('emergency_contact_name','N/A')} ({emp.get('emergency_contact_no','N/A')})\n\n"

    # Documents information
    doc_keys = ['pan_front','pan_back','aadhar_front','aadhar_back','degree_front','degree_back']
    docs_present = [k for k in doc_keys if emp.get(k)]
    if docs_present:
        response += f"🗂️ Documents available: {', '.join(docs_present)}\n"
    else:
        response += "🗂️ No HR document images found in database.\n"

    # Additional info
    if 'opening_leave_balance' in emp:
        try:
            response += f"📊 Opening Leave Balance: {float(emp.get('opening_leave_balance') or 0):.1f} days\n"
        except Exception:
            pass
    if emp.get('pf_join_date'):
        response += f"📌 PF Join Date: {emp.get('pf_join_date')}\n"
    if emp.get('releiving_date'):
        response += f"🔚 Releiving Date: {emp.get('releiving_date')}\n"

    return response

@mcp.tool()
def get_attendance_summary(name: str, days: int = 30, additional_context: Optional[str] = None) -> str:
    """Summarize attendance using work reports and approved leaves"""
    resolution = resolve_employee_ai(name, additional_context)
    if resolution['status'] != 'resolved':
        if resolution['status'] == 'ambiguous':
            return f"🔍 Ambiguous: \n\n{format_employee_options(resolution['employees'])}"
        return f"❌ No employee found matching '{name}'"
    
    emp = resolution['employee']
    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Work report days
        cursor.execute("""
            SELECT DISTINCT date FROM work_report
            WHERE developer_id = %s AND date >= %s AND date <= %s
        """, (emp['id'], start_date, end_date))
        work_days = {r['date'] for r in cursor.fetchall() if r.get('date')}
        
        # Approved leaves
        cursor.execute("""
            SELECT date_of_leave, leave_type FROM leave_requests
            WHERE developer_id = %s AND status = 'Approved' AND date_of_leave >= %s AND date_of_leave <= %s
        """, (emp['id'], start_date, end_date))
        leaves = cursor.fetchall()
        leave_days = [l['date_of_leave'] for l in leaves if l.get('date_of_leave')]

        total_days = (end_date - start_date).days + 1
        present_days = len(work_days)
        approved_leave_days = len(set(leave_days))
        absent_or_missing = total_days - (present_days + approved_leave_days)

        response = f"📅 **Attendance Summary for {emp['developer_name']}**\n"
        response += f"Period: {start_date} to {end_date} ({total_days} days)\n"
        response += f"✅ Present (work logged): {present_days} days\n"
        response += f"🏖️ Approved Leaves: {approved_leave_days} days\n"
        response += f"❌ Absent/Missing logs: {absent_or_missing} days\n"
        
        return response
    except Exception as e:
        return f"❌ Error generating attendance summary: {e}"
    finally:
        cursor.close()
        conn.close()

# -------------------------------
# Company Management Tools
# -------------------------------
@mcp.tool()
def get_client_list(active_only: bool = True) -> str:
    """List clients with contact details"""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        if active_only:
            cursor.execute("SELECT id, client_name, company_name, contact_person, email_id, phone, status FROM client WHERE status = 1 ORDER BY client_name")
        else:
            cursor.execute("SELECT id, client_name, company_name, contact_person, email_id, phone, status FROM client ORDER BY client_name")
        rows = cursor.fetchall()
        if not rows:
            return "ℹ️ No clients found."

        response = "👥 **Clients**\n\n"
        for r in rows[:50]:
            response += f"• {r.get('client_name')} — {r.get('company_name')}\n"
            response += f"   Contact: {r.get('contact_person') or 'N/A'} — {r.get('email_id') or 'N/A'} — {r.get('phone') or 'N/A'}\n"
            response += f"   Status: {'Active' if r.get('status') == 1 else 'Inactive'}\n\n"
        return response
    except Exception as e:
        return f"❌ Error fetching clients: {e}"
    finally:
        cursor.close()
        conn.close()

@mcp.tool()
def get_projects_overview(active_only: bool = True) -> str:
    """Show active (or all) projects with client info"""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        if active_only:
            cursor.execute("""
                SELECT p.id, p.title, p.status, c.client_name, c.email_id
                FROM project p
                LEFT JOIN client c ON p.client_id = c.id
                WHERE p.status = 1
                ORDER BY p.date DESC
            """)
        else:
            cursor.execute("""
                SELECT p.id, p.title, p.status, c.client_name, c.email_id
                FROM project p
                LEFT JOIN client c ON p.client_id = c.id
                ORDER BY p.date DESC
            """)
        projects = cursor.fetchall()
        if not projects:
            return "❌ No projects found."

        response = "🏗️ **Projects Overview**\n\n"
        for proj in projects[:50]:
            response += f"📌 {proj.get('title')} (ID: {proj.get('id')})\n"
            response += f"   Client: {proj.get('client_name') or 'N/A'} — {proj.get('email_id') or 'N/A'}\n"
            response += f"   Status: {'Active' if proj.get('status') == 1 else 'Inactive'}\n\n"
        return response
    except Exception as e:
        return f"❌ Error fetching projects: {e}"
    finally:
        cursor.close()
        conn.close()

@mcp.tool()
def get_holidays(upcoming_days: int = 90) -> str:
    """List upcoming company holidays"""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        today = date.today()
        end = today + timedelta(days=upcoming_days)
        cursor.execute("""
            SELECT occasion, holiday_date
            FROM holidays
            WHERE holiday_date >= %s AND holiday_date <= %s
            ORDER BY holiday_date ASC
        """, (today, end))
        rows = cursor.fetchall()
        if not rows:
            return f"ℹ️ No holidays in the next {upcoming_days} days."

        response = f"🎉 **Upcoming Holidays (next {upcoming_days} days)**\n"
        for r in rows:
            response += f"• {r.get('holiday_date')} — {r.get('occasion')}\n"
        return response
    except Exception as e:
        return f"❌ Error fetching holidays: {e}"
    finally:
        cursor.close()
        conn.close()

# -------------------------------
# Security Management Tools
# -------------------------------
@mcp.tool()
def generate_api_key(description: str) -> str:
    """
    Generate a new API key for client applications.
    Note: In production, restrict this to admin users.
    """
    new_key = "ttmcp-" + secrets.token_hex(32)
    SecurityConfig.API_KEYS.append(new_key)
    
    # Log the generation (in production, persist to database)
    print(f"🔑 NEW API KEY GENERATED: {new_key}")
    print(f"   Description: {description}")
    print(f"   Total active keys: {len(SecurityConfig.API_KEYS)}")
    
    return f"✅ New API key generated:\n\n**{new_key}**\n\nDescription: {description}\n\n⚠️ Save this key securely - it cannot be retrieved later!"

@mcp.tool()
def list_active_api_keys() -> str:
    """
    List currently active API keys (masked for security).
    Note: This should be restricted to admin users.
    """
    masked_keys = []
    for key in SecurityConfig.API_KEYS:
        if len(key) > 8:
            masked = key[:6] + "..." + key[-4:]
        else:
            masked = "***"
        masked_keys.append(masked)
    
    return f"🔑 Active API Keys ({len(masked_keys)}):\n" + "\n".join([f"{i+1}. {key}" for i, key in enumerate(masked_keys)])

@mcp.tool()
def revoke_api_key(key_to_revoke: str) -> str:
    """
    Revoke an API key immediately.
    Note: This should be restricted to admin users.
    """
    if key_to_revoke in SecurityConfig.API_KEYS:
        SecurityConfig.API_KEYS.remove(key_to_revoke)
        return f"✅ API key revoked successfully. Remaining active keys: {len(SecurityConfig.API_KEYS)}"
    else:
        return "❌ API key not found in active keys list."

# -------------------------------
# MCP Endpoints
# -------------------------------
@mcp.custom_route("/mcp", methods=["POST"])
async def mcp_endpoint(request: Request):
    """MCP protocol endpoint (protected by API key)"""
    return JSONResponse({
        "status": "Secure MCP server is running", 
        "security": "API key authentication enabled",
        "version": "2.0.0",
        "tools_available": 12
    })

@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> PlainTextResponse:
    """Public health check endpoint"""
    return PlainTextResponse("OK")

@mcp.custom_route("/", methods=["GET"])
async def root(request: Request) -> JSONResponse:
    """Public root endpoint with security info"""
    return JSONResponse({
        "message": "Secure Leave Manager + HR + Company Management MCP Server",
        "status": "running",
        "version": "2.0.0",
        "security": "API key authentication required",
        "header": SecurityConfig.REQUIRED_HEADER,
        "documentation": "Use /health for status, /mcp for MCP protocol"
    })

@mcp.custom_route("/auth-test", methods=["GET"])
async def auth_test(request: Request) -> JSONResponse:
    """Test endpoint to verify API key is working"""
    # This will only be reached if API key is valid (due to middleware)
    api_key = request.headers.get(SecurityConfig.REQUIRED_HEADER, "unknown")
    return JSONResponse({
        "status": "authenticated",
        "message": "API key is valid",
        "key_prefix": api_key[:8] + "..." if len(api_key) > 8 else "***",
        "timestamp": datetime.now().isoformat()
    })

# -------------------------------
# Server Startup
# -------------------------------
if __name__ == "__main__":
    # Environment check
    required_env_vars = ["DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME"]
    missing_vars = [var for var in required_env_vars if not os.environ.get(var)]
    
    if missing_vars:
        print(f"⚠️  Warning: Missing environment variables: {', '.join(missing_vars)}")
        print("   Using default values which may not work in production")

    if Levenshtein is None:
        print("⚠️  Warning: python-levenshtein not installed. Fuzzy matching quality may be lower.")
        print("   Install with: pip install python-levenshtein")

    # Server configuration
    transport = os.environ.get("MCP_TRANSPORT", "streamable-http")
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))

    print(f"\n🔒 Starting SECURE MCP Server")
    print(f"🌐 Address: {host}:{port}")
    print(f"📡 Transport: {transport}")
    print(f"🔑 Security: API Key Authentication Enabled")
    print(f"🔑 Active API Keys: {len(SecurityConfig.API_KEYS)}")
    print(f"📋 Required Header: {SecurityConfig.REQUIRED_HEADER}")
    print(f"🛠️  Available Tools: 12")
    print(f"🚀 Server ready!\n")

    # Start the server
    mcp.run(transport=transport, host=host, port=port)