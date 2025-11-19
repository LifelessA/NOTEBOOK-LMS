# NOTEBOOK-LMS

Here is a **clean, professional, attractive, GitHub-style `README.md`** for your project **NOTEBOOK-LMS**
(optimized for open-source presentation ✨)

---

# 📘 **NOTEBOOK-LMS**

A modern, notebook-based Learning Management System that combines **Python execution**, **assignment workflows**, **teacher controls**, and a **visual design tool** — all inside a single, interactive web platform.

---

## 🚀 **Overview**

NOTEBOOK-LMS is a full-stack, browser-based LMS designed for coding-oriented courses.
It allows **teachers**, **students**, and **admins** to interact seamlessly through:

* 🧠 Python Notebook (with execution, plots, DataFrames, etc.)
* 📝 Assignment creation, submission & grading
* 🧑‍🏫 Teacher Dashboard with controls (autocomplete, subjects, semesters)
* 🧑‍🎓 Student Dashboard for solving & reviewing assignments
* 🛠 Admin Panel (user management + ticket management)
* 🎨 Built-in Design Tool for creating diagrams (flowcharts, shapes, arrows)
* 🗄 SQLite-based backend (fast, portable)
* 🔒 Cookie-based session authentication

---

## 🏗 **Project Structure**

```
📁 NOTEBOOK-LMS
├── server.py                # FastAPI backend, DB, notebook engine
├── lms.db                   # SQLite database
├── index.html               # Notebook interface
├── login.html               # Login page
├── admin_panel.html         # Admin dashboard
├── teacher_dashboard.html   # Teacher control panel
├── student_dashboard.html   # Student dashboard
└── design_animation.html    # Advanced design/canvas tool
```

---

## 🔥 **Key Features**

### 🎓 **Notebook System**

* Real-time Python execution
* Pandas, Matplotlib supported
* Autocomplete (teacher-controlled)
* Markdown + Code cells
* Run-all, undo/redo, autosave
* PDF export

---

### 🧑‍🏫 **Teacher Features**

✔ Create assignments with code questions
✔ View student submissions
✔ Grade submissions (correct/wrong + scoring)
✔ Toggle autocomplete for students
✔ Manage semesters & subjects
✔ Submit ticket requests

---

### 🧑‍🎓 **Student Features**

✔ View new assignments
✔ Solve inside notebook interface
✔ Submit answers
✔ Review graded submissions
✔ Raise support tickets

---

### 🛠 **Admin Features**

✔ Create users (teachers, students)
✔ Assign semesters & subjects
✔ View all users
✔ Edit/delete users
✔ Manage support tickets

---

### 🎨 **Design Tool**

A powerful whiteboard with:

* Shapes (rectangle, circle, diamond, parallelogram…)
* Arrows, callouts, flowchart components
* Text tool
* Stroke/fill settings
* Array modifier
* Undo/redo
* Export/apply to notebook

Perfect for diagrams in assignments or answers.

---

## 🗄 **Database Schema (SQLite)**

### **Users Table**

* id
* username
* password
* role (admin/teacher/student)
* name
* semesters
* subjects

### **Assignments Table**

* teacher_id
* title
* questions (JSON)
* semester
* subject

### **Submissions Table**

* assignment_id
* student_id
* answers (JSON)
* grades (JSON)

### **Tickets Table**

* user_id
* query_text
* status (open/closed)

---

## ⚙️ **How to Run the Project**

### **1. Install Requirements**

```bash
pip install fastapi uvicorn python-multipart matplotlib pandas jedi
```

### **2. Start Server**

```bash
uvicorn server:app --reload
```

### **3. Open Browser**

```
http://localhost:8000/login
```

---

## 🔐 **Default Admin Login**

```
Username: admin  
Password: admin123
```

---

## 🤝 **Contributing**

Pull requests are welcome!
You can add new notebook features, expand teacher tools, or enhance the design UI.

---

## 📜 **License**

This project is open-source.


