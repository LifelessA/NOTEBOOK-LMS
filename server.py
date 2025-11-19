import sys
import os
import json
import base64
import io
import ast
import asyncio
import logging
import sqlite3
import hashlib
from html import escape
from contextlib import redirect_stdout

# --- Package Installation Check ---
try:
    import fastapi
    import uvicorn
    from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
    from fastapi import Request, Cookie, Response
    from starlette.websockets import WebSocket, WebSocketDisconnect
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import pandas as pd
    import jedi
except ImportError:
    print("Installing required packages: fastapi uvicorn python-multipart matplotlib pandas jedi")
    os.system(f'"{sys.executable}" -m pip install "fastapi[all]" uvicorn python-multipart matplotlib pandas jedi')
    import fastapi
    import uvicorn
    from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
    from fastapi import Request, Cookie, Response
    from starlette.websockets import WebSocket, WebSocketDisconnect
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import pandas as pd
    import jedi

app = fastapi.FastAPI()
logging.getLogger('websockets').setLevel(logging.ERROR)

# --- Global Settings ---
# This is what the teacher will control
global_settings = {
    "enable_autocomplete": True
}

# --- Database Setup ---
DB_NAME = 'lms.db'

def get_db_conn():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_conn()
    cursor = conn.cursor()
    
    # Users Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('admin', 'teacher', 'student'))
    );
    ''')
    
    # Add new columns to users table if they don't exist
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN name TEXT")
    except sqlite3.OperationalError:
        pass # Column already exists
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN semesters TEXT")
    except sqlite3.OperationalError:
        pass # Column already exists
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN subjects TEXT")
    except sqlite3.OperationalError:
        pass # Column already exists

    # Drop old tables for schema recreation (dev only)
    cursor.execute("DROP TABLE IF EXISTS assignments")
    cursor.execute("DROP TABLE IF EXISTS submissions")

    # Assignments Table (New Schema)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS assignments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        teacher_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        questions TEXT NOT NULL, -- JSON object: [{content: "...", marks: 10}, ...]
        semester TEXT NOT NULL,
        subject TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (teacher_id) REFERENCES users (id)
    );
    ''')
    
    # Submissions Table (New Schema)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        assignment_id INTEGER NOT NULL,
        student_id INTEGER NOT NULL,
        answers TEXT NOT NULL, -- JSON object: [{code: "..."}, ...]
        grades TEXT, -- JSON object: [{status: "correct/wrong", score: 10}, ...]
        submitted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (assignment_id) REFERENCES assignments (id),
        FOREIGN KEY (student_id) REFERENCES users (id)
    );
    ''')

    # Tickets Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        query_text TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'closed')),
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        resolved_at DATETIME,
        FOREIGN KEY (user_id) REFERENCES users (id)
    );
    ''')
    
    # Create a default admin user (if it doesn't exist)
    cursor.execute("SELECT * FROM users WHERE username = 'admin'")
    if not cursor.fetchone():
        hashed_password = hashlib.sha256('admin123'.encode()).hexdigest()
        cursor.execute("INSERT INTO users (username, password, role, name) VALUES (?, ?, ?, ?)",
                       ('admin', hashed_password, 'admin', 'Administrator'))
        print("Default admin user created with username 'admin' and password 'admin123'")
    
    conn.commit()
    conn.close()

# --- Hashing Utility ---
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# --- Notebook Server (Refactored for Sessions) ---
class NotebookServer:
    def __init__(self):
        # Each session_id gets its own execution context
        self.sessions = {}

    async def connect(self, websocket: WebSocket, session_id: str):
        await websocket.accept()
        # Create a new environment for each notebook session
        if session_id not in self.sessions:
            self.sessions[session_id] = {
                'locals': {'plt': plt, 'pd': pd},
                'websocket': websocket
            }
        print(f"Client connected: {session_id}")

    def disconnect(self, session_id: str):
        if session_id in self.sessions:
            del self.sessions[session_id]
            print(f"Client disconnected: {session_id}")

    async def handle_message(self, websocket: WebSocket, session_id: str, message: str):
        data = json.loads(message)
        msg_type = data.get('type')
        loop = asyncio.get_event_loop()

        if msg_type == 'run_code':
            output = await loop.run_in_executor(
                None, self.execute, session_id, data['code']
            )
            await websocket.send_json({
                'type': 'output',
                'cell_id': data['cell_id'],
                'output': output
            })
        elif msg_type == 'get_completions':
            # --- Teacher Control Logic ---
            if not global_settings["enable_autocomplete"]:
                completions = [] # Send empty list if disabled
            else:
                completions = self.get_completions(session_id, data['code'], data['line'], data['column'])
            
            await websocket.send_json({
                'type': 'completions',
                'request_id': data.get('request_id'),
                'completions': completions
            })
        elif msg_type == 'apply_design':
            notebook_session_id = data.get('session_id')
            target_ws = self.sessions.get(notebook_session_id, {}).get('websocket')
            if target_ws:
                await target_ws.send_json({
                    'type': 'design_applied',
                    'html': data['html']
                })

    def execute(self, session_id, code):
        # Get the correct 'locals' for this session
        session_env = self.sessions.get(session_id)
        if not session_env:
            return '<div class="error">Session expired. Please refresh.</div>'
        
        session_locals = session_env['locals']
        
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                tree = ast.parse(code)
                if tree.body and isinstance(tree.body[-1], ast.Expr):
                    if len(tree.body) > 1:
                        exec_code = compile(ast.Module(tree.body[:-1], type_ignores=[]), '<string>', 'exec')
                        exec(exec_code, session_locals)
                    eval_code = compile(ast.Expression(tree.body[-1].value), '<string>', 'eval')
                    result = eval(eval_code, session_locals)
                    if isinstance(result, pd.DataFrame):
                        buf.write(result.to_html())
                    elif result is not None:
                        buf.write(escape(str(result))) # Escape for XSS protection
                else:
                    exec(code, session_locals)
                fig = plt.gcf()
                if fig.axes:
                    img_buf = io.BytesIO()
                    fig.savefig(img_buf, format='png', bbox_inches='tight')
                    img_buf.seek(0)
                    plt.close(fig)
                    img_html = base64.b64encode(img_buf.read()).decode()
                    buf.write(f'<img src="data:image/png;base64,{img_html}"><br>')
            except Exception:
                import traceback
                buf.truncate(0)
                buf.seek(0)
                tb = traceback.format_exc()
                buf.write(f'<div class="error">{escape(tb)}</div>')
        return buf.getvalue()

    def get_completions(self, session_id, code, line, column):
        session_locals = self.sessions.get(session_id, {}).get('locals')
        if not session_locals:
            return []
        try:
            interpreter = jedi.Interpreter(code, [session_locals])
            completions = interpreter.complete(line=line + 1, column=column)
            return [c.name for c in completions]
        except Exception as e:
            print(f"Completion error: {e}")
            return []

server = NotebookServer()

# --- Authentication & API Endpoints ---

# Middleware to add no-cache headers
@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# Login Page
@app.get("/login", response_class=HTMLResponse)
async def get_login_page():
    with open("login.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

# Login Logic
@app.post("/login")
async def handle_login(request: Request):
    form = await request.json()
    username = form.get("username")
    password = form.get("password")
    
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
    user = cursor.fetchone()
    conn.close()
    
    if user and user["password"] == hash_password(password):
        response = JSONResponse({"status": "ok", "role": user["role"]})
        # Set HttpOnly cookies for security
        response.set_cookie(key="user_id", value=str(user["id"]), httponly=True, samesite="strict")
        response.set_cookie(key="user_role", value=user["role"], samesite="strict")
        return response
    
    return JSONResponse({"status": "error", "message": "Invalid username or password"}, status_code=401)

# Logout
@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login")
    response.delete_cookie("user_id")
    response.delete_cookie("user_role")
    return response

# --- Admin Panel ---
@app.get("/admin", response_class=HTMLResponse)
async def get_admin_panel(user_role: str = Cookie(None)):
    if user_role != 'admin':
        return RedirectResponse(url="/login")
    with open("admin_panel.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.post("/api/admin/create_user")
async def admin_create_user(request: Request, user_role: str = Cookie(None)):
    if user_role != 'admin':
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    
    form = await request.json()
    username = form.get("username")
    password = form.get("password")
    role = form.get("role")
    name = form.get("name")
    semesters = form.get("semesters", "")
    subjects = form.get("subjects", "")
    
    if not all([username, password, role, name]):
        return JSONResponse({"status": "error", "message": "Username, password, name, and role are required"}, status_code=400)
    
    conn = get_db_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (username, password, role, name, semesters, subjects) VALUES (?, ?, ?, ?, ?, ?)",
            (username, hash_password(password), role, name, semesters, subjects)
        )
        conn.commit()
        return JSONResponse({"status": "ok", "message": f"{role.capitalize()} '{username}' created."})
    except sqlite3.IntegrityError:
        return JSONResponse({"status": "error", "message": "Username already exists"}, status_code=400)
    finally:
        conn.close()

@app.get("/api/admin/users")
async def admin_get_users(user_role: str = Cookie(None)):
    if user_role != 'admin':
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    
    conn = get_db_conn()
    users = conn.execute("SELECT id, username, role, name, semesters, subjects FROM users").fetchall()
    conn.close()
    return JSONResponse([dict(user) for user in users])

@app.get("/api/admin/user/{user_id}")
async def admin_get_user(user_id: int, user_role: str = Cookie(None)):
    if user_role != 'admin':
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    
    conn = get_db_conn()
    user = conn.execute("SELECT id, username, role, name, semesters, subjects FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    
    if not user:
        return JSONResponse({"status": "error", "message": "User not found"}, status_code=404)
    return JSONResponse(dict(user))

@app.put("/api/admin/user/{user_id}")
async def admin_update_user(user_id: int, request: Request, user_role: str = Cookie(None)):
    if user_role != 'admin':
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
        
    form = await request.json()
    name = form.get("name")
    role = form.get("role")
    semesters = form.get("semesters", "")
    subjects = form.get("subjects", "")

    if not all([name, role]):
        return JSONResponse({"status": "error", "message": "Name and role are required"}, status_code=400)

    conn = get_db_conn()
    conn.execute(
        "UPDATE users SET name = ?, role = ?, semesters = ?, subjects = ? WHERE id = ?",
        (name, role, semesters, subjects, user_id)
    )
    conn.commit()
    conn.close()
    return JSONResponse({"status": "ok", "message": "User updated successfully."})


@app.delete("/api/admin/user/{user_id}")
async def admin_delete_user(user_id: int, admin_user_id: str = Cookie(alias="user_id"), user_role: str = Cookie(None)):
    if user_role != 'admin':
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    
    if int(admin_user_id) == user_id:
        return JSONResponse({"status": "error", "message": "Admin cannot delete themselves."}, status_code=400)

    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    
    if cursor.rowcount > 0:
        # Also delete related data for cleanliness
        cursor.execute("DELETE FROM assignments WHERE teacher_id = ?", (user_id,))
        cursor.execute("DELETE FROM submissions WHERE student_id = ?", (user_id,))
        conn.commit()
        return JSONResponse({"status": "ok", "message": "User deleted successfully."})
    else:
        conn.close()
        return JSONResponse({"status": "error", "message": "User not found."}, status_code=404)


# --- Ticket System ---

@app.post("/api/ticket/create")
async def create_ticket(request: Request, user_id: str = Cookie(None)):
    if not user_id:
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    
    data = await request.json()
    query_text = data.get("query_text")
    if not query_text:
        return JSONResponse({"status": "error", "message": "Ticket query cannot be empty."}, status_code=400)

    conn = get_db_conn()
    conn.execute("INSERT INTO tickets (user_id, query_text) VALUES (?, ?)", (int(user_id), query_text))
    conn.commit()
    conn.close()
    return JSONResponse({"status": "ok", "message": "Ticket created successfully."})

@app.get("/api/ticket/my_tickets")
async def get_my_tickets(user_id: str = Cookie(None)):
    if not user_id:
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    conn = get_db_conn()
    tickets = conn.execute("SELECT id, query_text, status, created_at, resolved_at FROM tickets WHERE user_id = ? ORDER BY created_at DESC", (int(user_id),)).fetchall()
    conn.close()
    return JSONResponse([dict(t) for t in tickets])

@app.get("/api/admin/tickets")
async def admin_get_tickets(user_role: str = Cookie(None)):
    if user_role != 'admin':
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    conn = get_db_conn()
    tickets = conn.execute("""
        SELECT t.id, t.query_text, t.status, t.created_at, u.username, u.name
        FROM tickets t
        JOIN users u ON t.user_id = u.id
        ORDER BY t.created_at DESC
    """).fetchall()
    conn.close()
    return JSONResponse([dict(t) for t in tickets])

@app.post("/api/admin/ticket/close/{ticket_id}")
async def admin_close_ticket(ticket_id: int, user_role: str = Cookie(None)):
    if user_role != 'admin':
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    conn = get_db_conn()
    conn.execute("UPDATE tickets SET status = 'closed', resolved_at = CURRENT_TIMESTAMP WHERE id = ?", (ticket_id,))
    conn.commit()
    conn.close()
    return JSONResponse({"status": "ok", "message": "Ticket closed."})


# --- Teacher Dashboard ---
@app.get("/teacher", response_class=HTMLResponse)
async def get_teacher_dashboard(user_role: str = Cookie(None)):
    if user_role != 'teacher':
        return RedirectResponse(url="/login")
    with open("teacher_dashboard.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.post("/api/teacher/assignment")
async def create_assignment(request: Request, user_id: str = Cookie(None), user_role: str = Cookie(None)):
    if user_role != 'teacher':
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    
    data = await request.json()
    title = data.get("title")
    questions = data.get("questions") # Expects a JSON string
    semester = data.get("semester")
    subject = data.get("subject")
    
    conn = get_db_conn()
    conn.execute("INSERT INTO assignments (teacher_id, title, questions, semester, subject) VALUES (?, ?, ?, ?, ?)",
                 (int(user_id), title, questions, semester, subject))
    conn.commit()
    conn.close()
    return JSONResponse({"status": "ok", "message": "Assignment posted"})

@app.get("/api/teacher/info")
async def get_teacher_info(user_id: str = Cookie(None), user_role: str = Cookie(None)):
    if user_role != 'teacher':
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    
    conn = get_db_conn()
    teacher = conn.execute("SELECT semesters, subjects FROM users WHERE id = ?", (int(user_id),)).fetchone()
    conn.close()
    
    if not teacher:
        return JSONResponse({"status": "error", "message": "Teacher not found"}, status_code=404)
        
    return JSONResponse({
        "semesters": [s.strip() for s in teacher['semesters'].split(',')] if teacher['semesters'] else [],
        "subjects": [s.strip() for s in teacher['subjects'].split(',')] if teacher['subjects'] else []
    })

@app.get("/api/teacher/assignments")
async def get_teacher_assignments(user_id: str = Cookie(None), user_role: str = Cookie(None)):
    if user_role != 'teacher':
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    
    conn = get_db_conn()
    assignments = conn.execute("SELECT id, title, created_at FROM assignments WHERE teacher_id = ?", (int(user_id),)).fetchall()
    conn.close()
    return JSONResponse([dict(a) for a in assignments])

@app.get("/api/teacher/submissions/{assignment_id}")
async def get_submissions_for_assignment(assignment_id: int, user_role: str = Cookie(None)):
    if user_role != 'teacher':
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
        
    conn = get_db_conn()
    submissions = conn.execute("""
        SELECT s.id, s.submitted_at, u.username 
        FROM submissions s
        JOIN users u ON s.student_id = u.id
        WHERE s.assignment_id = ?
    """, (assignment_id,)).fetchall()
    conn.close()
    return JSONResponse([dict(s) for s in submissions])

# This new endpoint provides all data needed for the review page
@app.get("/api/submission_details/{submission_id}")
async def get_submission_details(submission_id: int, user_id: str = Cookie(None), user_role: str = Cookie(None)):
    if not user_role:
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)

    conn = get_db_conn()
    # Security check: if student, they must own the submission. Teacher can see any.
    query = """
        SELECT s.id, s.student_id, s.answers, s.grades, a.questions, a.title
        FROM submissions s
        JOIN assignments a ON s.assignment_id = a.id
        WHERE s.id = ?
    """
    params = (submission_id,)
    
    submission = conn.execute(query, params).fetchone()

    if not submission:
        conn.close()
        return JSONResponse({"status": "error", "message": "Submission not found"}, status_code=404)

    # If student, verify ownership
    if user_role == 'student' and submission['student_id'] != int(user_id):
        conn.close()
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
        
    conn.close()
    return JSONResponse(dict(submission))


@app.post("/api/teacher/grade_submission")
async def grade_submission(request: Request, user_role: str = Cookie(None)):
    if user_role != 'teacher':
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    
    data = await request.json()
    submission_id = data.get('submission_id')
    question_index = data.get('question_index')
    status = data.get('status') # "correct" or "wrong"

    conn = get_db_conn()
    
    # Get existing grades and assignment questions
    submission = conn.execute("SELECT s.grades, a.questions FROM submissions s JOIN assignments a ON s.assignment_id = a.id WHERE s.id = ?", (submission_id,)).fetchone()
    if not submission:
        conn.close()
        return JSONResponse({"status": "error", "message": "Submission not found"}, status_code=404)

    grades = json.loads(submission['grades'] or '[]')
    questions = json.loads(submission['questions'])
    
    # Get the score for the graded question
    score = questions[question_index]['marks'] if status == 'correct' else 0
    
    # Update or add the grade for the specific question
    grade_found = False
    for grade in grades:
        if grade.get('question_index') == question_index:
            grade['status'] = status
            grade['score'] = score
            grade_found = True
            break
    
    if not grade_found:
        grades.append({'question_index': question_index, 'status': status, 'score': score})

    conn.execute("UPDATE submissions SET grades = ? WHERE id = ?", (json.dumps(grades), submission_id))
    conn.commit()
    conn.close()
    
    return JSONResponse({"status": "ok", "message": "Grade updated."})


@app.post("/api/teacher/settings")
async def update_settings(request: Request, user_role: str = Cookie(None)):
    if user_role != 'teacher':
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    
    data = await request.json()
    if 'enable_autocomplete' in data:
        global_settings['enable_autocomplete'] = bool(data['enable_autocomplete'])
    
    return JSONResponse({"status": "ok", "settings": global_settings})

@app.get("/api/teacher/settings")
async def get_settings(user_role: str = Cookie(None)):
    # Any logged-in user can get settings, as it affects the notebook
    if not user_role:
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    return JSONResponse(global_settings)


# --- Student Dashboard ---
@app.get("/student", response_class=HTMLResponse)
async def get_student_dashboard(user_role: str = Cookie(None)):
    if user_role != 'student':
        return RedirectResponse(url="/login")
    with open("student_dashboard.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/api/student/assignments")
async def get_student_assignments(user_id: str = Cookie(None), user_role: str = Cookie(None)):
    if user_role != 'student':
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    
    conn = get_db_conn()
    
    # Get student's semesters and subjects
    student = conn.execute("SELECT semesters, subjects FROM users WHERE id = ?", (int(user_id),)).fetchone()
    if not student:
        conn.close()
        return JSONResponse([])
        
    student_semesters = [s.strip() for s in student['semesters'].split(',')] if student['semesters'] else []
    student_subjects = [s.strip() for s in student['subjects'].split(',')] if student['subjects'] else []
    
    if not student_semesters or not student_subjects:
        conn.close()
        return JSONResponse([])

    semester_placeholders = ','.join('?' for _ in student_semesters)
    subject_placeholders = ','.join('?' for _ in student_subjects)

    # Get all assignments this student has NOT submitted and are for their semester and subject
    query = f"""
        SELECT a.id, a.title, u.username as teacher_name
        FROM assignments a
        JOIN users u ON a.teacher_id = u.id
        WHERE a.id NOT IN (
            SELECT s.assignment_id FROM submissions s WHERE s.student_id = ?
        ) AND a.semester IN ({semester_placeholders}) AND a.subject IN ({subject_placeholders})
    """
    
    params = [int(user_id)] + student_semesters + student_subjects
    assignments = conn.execute(query, params).fetchall()
    
    conn.close()
    return JSONResponse([dict(a) for a in assignments])

@app.get("/api/student/submissions")
async def get_student_submissions(user_id: str = Cookie(None), user_role: str = Cookie(None)):
    if user_role != 'student':
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    
    conn = get_db_conn()
    submissions = conn.execute("""
        SELECT s.id, s.submitted_at, a.title
        FROM submissions s
        JOIN assignments a ON s.assignment_id = a.id
        WHERE s.student_id = ?
    """, (int(user_id),)).fetchall()
    conn.close()
    return JSONResponse([dict(s) for s in submissions])


@app.get("/api/student/assignment/{assignment_id}")
async def get_assignment_content(assignment_id: int, user_id: str = Cookie(None), user_role: str = Cookie(None)):
    if user_role != 'student':
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    
    conn = get_db_conn()
    # Check if student already submitted
    existing = conn.execute("SELECT id FROM submissions WHERE assignment_id = ? AND student_id = ?", (assignment_id, int(user_id))).fetchone()
    if existing:
        conn.close()
        return JSONResponse({"status": "error", "message": "You have already submitted this assignment."}, status_code=403)
    
    assignment = conn.execute("SELECT questions FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
    conn.close()
    if assignment:
        return JSONResponse(json.loads(assignment["questions"]))
    return JSONResponse({"status": "error", "message": "Assignment not found"}, status_code=404)

@app.post("/api/student/submit/{assignment_id}")
async def submit_assignment(assignment_id: int, request: Request, user_id: str = Cookie(None), user_role: str = Cookie(None)):
    if user_role != 'student':
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=403)
    
    data = await request.json()
    answers = data.get("answers") # Expects a JSON object
    
    conn = get_db_conn()
    # Check they haven't submitted already
    existing = conn.execute("SELECT id FROM submissions WHERE assignment_id = ? AND student_id = ?", (assignment_id, int(user_id))).fetchone()
    if existing:
        conn.close()
        return JSONResponse({"status": "error", "message": "Already submitted"}, status_code=400)

    conn.execute("INSERT INTO submissions (assignment_id, student_id, answers) VALUES (?, ?, ?)",
                 (assignment_id, int(user_id), json.dumps(answers)))
    conn.commit()
    conn.close()
    return JSONResponse({"status": "ok", "message": "Assignment submitted"})


# --- Your Existing Routes (Now Protected) ---

# Root redirects to the correct dashboard
@app.get("/", response_class=HTMLResponse)
async def get_root(user_id: str = Cookie(None), user_role: str = Cookie(None)):
    if not user_id:
        return RedirectResponse(url="/login")
    # Redirect to the notebook page, which will handle its own logic
    with open("index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/design", response_class=HTMLResponse)
async def get_design(user_id: str = Cookie(None)):
    if not user_id:
        return RedirectResponse(url="/login")
        
    with open("design_animation.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await server.connect(websocket, session_id)
    try:
        while True:
            message = await websocket.receive_text()
            await server.handle_message(websocket, session_id, message)
    except WebSocketDisconnect:
        server.disconnect(session_id)
    
# /save endpoint (for local drafts by teachers)
@app.post("/save")
async def save_file(request: fastapi.Request, user_role: str = Cookie(None)):
    if user_role not in ['admin', 'teacher']:
        return JSONResponse({"status": "error", "message": "Only teachers can save local files."}, status_code=403)

    data = await request.json()
    filename = data.get("filename")
    content = data.get("content")
    
    if ".." in filename or os.path.isabs(filename):
         return JSONResponse({"status": "error", "message": "Invalid filename"}, status_code=400)
    
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    return {"status": "ok", "message": f"File '{filename}' saved."}

if __name__ == "__main__":
    print("Initializing database...")
    init_db()
    print("Starting server...")
    print("Open http://localhost:8000 in your browser.")
    uvicorn.run(app, host="127.0.0.1", port=8000)