from wtforms import IntegerField, TextAreaField, StringField, PasswordField, FileField, SubmitField, SelectField
from flask import Flask, render_template, redirect, url_for, request, session, flash, send_file, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from wtforms.validators import DataRequired, Optional
from flask import Response, stream_with_context
from werkzeug.utils import secure_filename
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from flask import get_flashed_messages
from flask_caching import SimpleCache
from collections import defaultdict
from flask_migrate import Migrate
from urllib.parse import unquote
from flask_wtf import FlaskForm
from flask_caching import Cache
from time import perf_counter
from functools import wraps
from sqlalchemy import func
from sqlalchemy import text

import pandas as pd
import tempfile
import csv, io
import random
import time
import json
import os

# --- Flask Setup ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_secret_key_change_in_render')

# --- PHONE OPTIMIZED CACHE: short timeout for live data ---
cache = Cache(app, config={
    'CACHE_TYPE': 'SimpleCache',
    'CACHE_DEFAULT_TIMEOUT': 15  # 15 sec, not 300 - phone sees approval fast
})

# --- Ensure upload folder ---
basedir = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(basedir, 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'csv'}

# --- NeonDB - PHONE OPTIMIZED POOL ---
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 
    'postgresql://neondb_owner:npg_97DuTpZbOLJY@ep-cold-resonance-a1ldqo6i-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require'
)
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "connect_args": {"connect_timeout": 10},  # 10 not 15 - phone drops faster
    "pool_pre_ping": True,
    "pool_recycle": 300,
    "pool_size": 2,       # perfect for 0.1 CPU
    "max_overflow": 2     # was 3, lower = less Neon connections from phone spam
}
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# cache already defined as 15s for phone live

# --- Database ---
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# =========================
# NAME FORMATTING HELPER
# =========================
def format_display_name(last_name, first_name, middle_name=None):
    """Returns SURNAME, First name M.  Middle is optional."""
    if not last_name and not first_name:
        return ""
    last = (last_name or "").strip().upper()
    first = (first_name or "").strip()
    # Title case first name but keep as entered if already cased
    if first:
        # keep original casing but ensure first letter capital
        first = first.title() if first.isupper() or first.islower() else first
    if middle_name and middle_name.strip():
        mi = middle_name.strip()[0].upper() + "."
        return f"{last}, {first} {mi}" if last and first else f"{last or first} {mi}".strip()
    else:
        if last and first:
            return f"{last}, {first}"
        return last or first

def parse_fullname_to_parts(fullname):
    """Fallback for old data: try to split fullname into last, first, middle"""
    if not fullname:
        return "", "", ""
    parts = fullname.strip().split()
    if len(parts)==1:
        return parts[0], "", ""
    elif len(parts)==2:
        # Assume First Last? We want surname last word
        return parts[1], parts[0], ""
    else:
        # First Middle Last
        return parts[-1], parts[0], " ".join(parts[1:-1])


sse_events = defaultdict(list)  # LIVE auto-refresh - required, tiny RAM

# --- Models ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # Admin/Instructor/Student

    student_records = db.relationship(
        'Student',
        backref='user',
        lazy=True
    )

class Student(db.Model):
    __tablename__ = 'student'

    student_id = db.Column(db.String(100), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    # --- NEW SPLIT NAME FIELDS ---
    last_name = db.Column(db.String(100), nullable=False, default='')
    first_name = db.Column(db.String(100), nullable=False, default='')
    middle_name = db.Column(db.String(100), nullable=True)
    # Keep old column for backward migration (can be removed later)
    name = db.Column(db.String(150), nullable=True)

    @property
    def display_name(self):
        # Preferred display: SURNAME, First name M.
        if self.last_name or self.first_name:
            return format_display_name(self.last_name, self.first_name, self.middle_name)
        # fallback to old name
        if self.name:
            ln, fn, mn = parse_fullname_to_parts(self.name)
            return format_display_name(ln, fn, mn)
        return ""

    @property
    def middle_initial(self):
        return (self.middle_name[0].upper() + ".") if self.middle_name else ""

    def sync_legacy_name(self):
        self.name = self.display_name


    year = db.Column(db.String(20))
    section = db.Column(db.String(50))
    school_year = db.Column(db.String(20))
    semester = db.Column(db.String(20))

    subject = db.Column(db.String(100), primary_key=True)
    subject_name = db.Column(db.String(100))
    subject_code = db.Column(db.String(50))

    # Attendance
    midterm_attendance1 = db.Column(db.String(10), default='0')
    midterm_attendance2 = db.Column(db.String(10), default='0')
    midterm_attendance3 = db.Column(db.String(10), default='0')
    midterm_attendance4 = db.Column(db.String(10), default='0')
    final_attendance1 = db.Column(db.String(10), default='0')
    final_attendance2 = db.Column(db.String(10), default='0')
    final_attendance3 = db.Column(db.String(10), default='0')
    final_attendance4 = db.Column(db.String(10), default='0')

    # Midterm Quizzes
    midterm_quiz1 = db.Column(db.String(10), default='0')
    midterm_quiz2 = db.Column(db.String(10), default='0')
    midterm_quiz3 = db.Column(db.String(10), default='0')
    midterm_quiz4 = db.Column(db.String(10), default='0')
    midterm_e_quiz1 = db.Column(db.String(10), default='0')
    midterm_e_quiz2 = db.Column(db.String(10), default='0')
    midterm_e_quiz3 = db.Column(db.String(10), default='0')
    midterm_e_quiz4 = db.Column(db.String(10), default='0')
    midterm_l_quiz1 = db.Column(db.String(10), default='0')
    midterm_l_quiz2 = db.Column(db.String(10), default='0')
    midterm_l_quiz3 = db.Column(db.String(10), default='0')
    midterm_l_quiz4 = db.Column(db.String(10), default='0')

    # Final Quizzes
    final_quiz1 = db.Column(db.String(10), default='0')
    final_quiz2 = db.Column(db.String(10), default='0')
    final_quiz3 = db.Column(db.String(10), default='0')
    final_quiz4 = db.Column(db.String(10), default='0')
    final_e_quiz1 = db.Column(db.String(10), default='0')
    final_e_quiz2 = db.Column(db.String(10), default='0')
    final_e_quiz3 = db.Column(db.String(10), default='0')
    final_e_quiz4 = db.Column(db.String(10), default='0')
    final_l_quiz1 = db.Column(db.String(10), default='0')
    final_l_quiz2 = db.Column(db.String(10), default='0')
    final_l_quiz3 = db.Column(db.String(10), default='0')
    final_l_quiz4 = db.Column(db.String(10), default='0')

    # PIT
    midterm_pit1 = db.Column(db.String(10), default='0')
    midterm_pit2 = db.Column(db.String(10), default='0')
    midterm_pit3 = db.Column(db.String(10), default='0')
    midterm_pit4 = db.Column(db.String(10), default='0')
    final_pit1 = db.Column(db.String(10), default='0')
    final_pit2 = db.Column(db.String(10), default='0')
    final_pit3 = db.Column(db.String(10), default='0')
    final_pit4 = db.Column(db.String(10), default='0')

    # Exercises
    midterm_exercise1 = db.Column(db.String(10), default='0')
    midterm_exercise2 = db.Column(db.String(10), default='0')
    midterm_exercise3 = db.Column(db.String(10), default='0')
    midterm_exercise4 = db.Column(db.String(10), default='0')
    final_exercise1 = db.Column(db.String(10), default='0')
    final_exercise2 = db.Column(db.String(10), default='0')
    final_exercise3 = db.Column(db.String(10), default='0')
    final_exercise4 = db.Column(db.String(10), default='0')

    # Laboratory
    midterm_laboratory1 = db.Column(db.String(10), default='0')
    midterm_laboratory2 = db.Column(db.String(10), default='0')
    midterm_laboratory3 = db.Column(db.String(10), default='0')
    midterm_laboratory4 = db.Column(db.String(10), default='0')
    final_laboratory1 = db.Column(db.String(10), default='0')
    final_laboratory2 = db.Column(db.String(10), default='0')
    final_laboratory3 = db.Column(db.String(10), default='0')
    final_laboratory4 = db.Column(db.String(10), default='0')

    # Report
    midterm_report1 = db.Column(db.String(10), default='0')
    final_report1 = db.Column(db.String(10), default='0')

    # Exams
    midterm_exam = db.Column(db.String(10), default='0')
    final_exam = db.Column(db.String(10), default='0')
    midterm_laboratory_exam = db.Column(db.String(10), default='0')
    final_laboratory_exam = db.Column(db.String(10), default='0')

    # Grades and remarks (separated)
    midterm_grade = db.Column(db.String(10), default='0')
    final_grade = db.Column(db.String(10), default='0')
    midterm_remarks = db.Column(db.String(50))
    final_remarks = db.Column(db.String(50))

    __table_args__ = (
        db.UniqueConstraint('student_id', 'subject', name='uix_student_subject'),
    )

class Log(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user = db.Column(db.String(100))
    action = db.Column(db.String(255))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

# =========================
# ENROLLMENT SYSTEM - NEW (MULTI-SUBJECT RE-ENROLLMENT)
# =========================
class Enrollment(db.Model):
    __tablename__ = 'enrollment'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(100), nullable=False, index=True)  # NOT unique - allows re-enroll
    last_name = db.Column(db.String(100), nullable=False, default='')
    first_name = db.Column(db.String(100), nullable=False, default='')
    middle_name = db.Column(db.String(100), nullable=True)
    name = db.Column(db.String(150), nullable=True)

    @property
    def display_name(self):
        if self.last_name or self.first_name:
            return format_display_name(self.last_name, self.first_name, self.middle_name)
        if self.name:
            ln, fn, mn = parse_fullname_to_parts(self.name)
            return format_display_name(ln, fn, mn)
        return ""

    def sync_legacy_name(self):
        self.name = self.display_name

    course = db.Column(db.String(100))
    year = db.Column(db.String(20))
    section = db.Column(db.String(50))
    email = db.Column(db.String(150))
    contact = db.Column(db.String(50))
    username = db.Column(db.String(100))
    # NEW: subject tracking for re-enrollment
    subject = db.Column(db.String(100), nullable=True)
    subject_code = db.Column(db.String(50), nullable=True)
    subject_name = db.Column(db.String(150), nullable=True)
    status = db.Column(db.String(20), default='pending')
    is_deleted = db.Column(db.Boolean, default=False, nullable=False)
    deleted_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    approved_at = db.Column(db.DateTime, nullable=True)

# =========================
# EXAM SYSTEM MODELS
# =========================

class Subject(db.Model):
    __tablename__ = 'subjects'

    id = db.Column(db.Integer, primary_key=True)

    subject_code = db.Column(db.String(20), unique=True, nullable=False)
    subject_name = db.Column(db.String(100), nullable=False)

    exams = db.relationship(
        'Exam',
        backref='subject',
        lazy=True,
        cascade="all, delete"
    )

class Section(db.Model):
    __tablename__ = 'sections'
    id = db.Column(db.Integer, primary_key=True)
    section_code = db.Column(db.String(20), unique=True, nullable=False)  # e.g. 1A, 2A, 3A, 4A
    description = db.Column(db.String(150), nullable=True)  # e.g. First Year Section A
    school_year = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Exam(db.Model):
    __tablename__ = 'exam'

    id = db.Column(db.Integer, primary_key=True)

    subject_id = db.Column(
        db.Integer,
        db.ForeignKey('subjects.id'),
        nullable=False
    )

    title = db.Column(db.String(200), nullable=False)

    term = db.Column(db.String(50))
    exam_type = db.Column(db.String(50))
    section = db.Column(db.String(50))
    year = db.Column(db.String(20))
    school_year = db.Column(db.String(20))
    semester = db.Column(db.String(20))
    access_code = db.Column(db.String(100),nullable=True)

    description = db.Column(db.Text)
    duration_minutes = db.Column(db.Integer, default=30)
    is_active = db.Column(db.Boolean, default=True)
    is_archived = db.Column(db.Boolean, default=False)  # <-- NEW LINE 1: Is it in archive box?
    archived_at = db.Column(db.DateTime, nullable=True)  # <-- NEW LINE 2: When was it archived?

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # relationship
    questions = db.relationship('Question', backref='exam', cascade="all, delete-orphan")

class Question(db.Model):
    __tablename__ = 'questions'

    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey('exam.id'))

    question_type = db.Column(db.String(20), default="mcq")

    question_text = db.Column(db.Text)

    choice_a = db.Column(db.Text, nullable=True)
    choice_b = db.Column(db.Text, nullable=True)
    choice_c = db.Column(db.Text, nullable=True)
    choice_d = db.Column(db.Text, nullable=True)
    choices_json = db.Column(db.Text, nullable=True)

    correct_answer = db.Column(db.Text)
    points = db.Column(db.Integer, default=1)
    is_deleted = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    deleted_at = db.Column(db.DateTime, nullable=True)

    @property
    def choices(self):
        import json as _json
        if self.choices_json:
            try:
                return _json.loads(self.choices_json)
            except:
                pass
        d = {}
        if self.choice_a: d['A'] = self.choice_a
        if self.choice_b: d['B'] = self.choice_b
        if self.choice_c: d['C'] = self.choice_c
        if self.choice_d: d['D'] = self.choice_d
        return d

class ExamAttempt(db.Model):
    __tablename__ = 'exam_attempts'

    id = db.Column(db.Integer, primary_key=True)

    student_id = db.Column(
        db.String(100),
        db.ForeignKey('student.student_id'),
        nullable=False
    )

    exam_id = db.Column(
        db.Integer,
        db.ForeignKey('exam.id'),
        nullable=False
    )

    score = db.Column(db.Integer, default=0)

    # Time when the student actually starts the exam
    started_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    # Time when the exam should automatically end
    end_time = db.Column(db.DateTime)

    # Time when the student submits
    submitted_at = db.Column(db.DateTime)

    # ======================================================
    # Exam Security Summary
    # ======================================================

    security_score = db.Column(db.Integer, default=100)

    total_violations = db.Column(db.Integer, default=0)

    last_violation = db.Column(db.DateTime)

    last_violation_type = db.Column(db.String(50))

    is_submitted = db.Column(db.Boolean, default=False)

    answers = db.relationship(
        'StudentAnswer',
        backref='attempt',
        lazy=True,
        cascade="all, delete"
    )

class SecurityEvent(db.Model):
    __tablename__ = "security_events"

    # ======================================================
    # Identity
    # ======================================================

    id = db.Column(db.Integer, primary_key=True)

    attempt_id = db.Column(db.Integer, db.ForeignKey("exam_attempts.id"), nullable=False)

    # ======================================================
    # Security Event Information
    # ======================================================

    event_type = db.Column(db.String(50), nullable=False)

    description = db.Column(db.String(255))

    severity = db.Column(db.String(20), default="LOW")

    penalty = db.Column(db.Integer, default=0)

    source = db.Column(db.String(30))

    details = db.Column(db.String(255))

    occurred_at = db.Column(db.DateTime, default=datetime.utcnow)

    # ======================================================
    # Relationship
    # ======================================================

    attempt = db.relationship(
        "ExamAttempt",
        backref="security_events"
    )

class ExamAccess(db.Model):
    __tablename__ = "exam_access"

    __table_args__ = (
        db.UniqueConstraint(
            "exam_id",
            "student_id",
            name="uq_exam_student"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)

    exam_id = db.Column(
        db.Integer,
        db.ForeignKey("exam.id"),
        nullable=False
    )

    student_id = db.Column(
        db.String(100),
        db.ForeignKey("student.student_id"),
        nullable=False
    )

    entered_code = db.Column(db.String(100))

    status = db.Column(db.String(20), default="pending")

    is_reset = db.Column(db.Boolean, default=False)
    reset_at = db.Column(db.DateTime, nullable=True)

    student = db.relationship(
        "Student",
        lazy=True
    )

    exam = db.relationship(
        "Exam",
        backref="access_records"
    )

class CalendarEvent(db.Model):
    __tablename__ = "calendar_events"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    start_date = db.Column(db.DateTime, nullable=False)
    end_date = db.Column(db.DateTime, nullable=True)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class StudentAnswer(db.Model):
    __tablename__ = 'student_answers'

    __table_args__ = (
        db.Index(
            'idx_student_answer_attempt_question',
            'attempt_id',
            'question_id'
        ),
    )

    id = db.Column(db.Integer, primary_key=True)

    attempt_id = db.Column(
        db.Integer,
        db.ForeignKey('exam_attempts.id'),
        nullable=False
    )

    question_id = db.Column(
        db.Integer,
        db.ForeignKey('questions.id'),
        nullable=False
    )

    selected_answer = db.Column(db.String(1))

    is_correct = db.Column(db.Boolean)

# ======================================================
# RANDOMIZED QUESTION ORDER
# One record per question per exam attempt
# ======================================================

class AttemptQuestionOrder(db.Model):
    __tablename__ = "attempt_question_order"

    __table_args__ = (

        db.UniqueConstraint(
            "attempt_id",
            "display_order",
            name="uq_attempt_display"
        ),

        db.UniqueConstraint(
            "attempt_id",
            "question_id",
            name="uq_attempt_question"
        ),

    )

    id = db.Column(db.Integer, primary_key=True)

    attempt_id = db.Column(
        db.Integer,
        db.ForeignKey("exam_attempts.id"),
        nullable=False
    )

    question_id = db.Column(
        db.Integer,
        db.ForeignKey("questions.id"),
        nullable=False
    )

    display_order = db.Column(
        db.Integer,
        nullable=False
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

# ===================================================================
# ----- FlaskForms --------------------------------------------------
# ===================================================================
class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')

class CreateUserForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    role = SelectField('Role', choices=[('Instructor','Instructor'), ('Student','Student')], validators=[DataRequired()])
    submit = SubmitField('Create User')

class StudentForm(FlaskForm):
    student_id = StringField('Student ID', validators=[DataRequired()])
    last_name = StringField('Surname', validators=[DataRequired()])
    first_name = StringField('First Name', validators=[DataRequired()])
    middle_name = StringField('Middle Name', validators=[Optional()])
    name = StringField('Name (legacy)', validators=[Optional()])
    section = StringField('Section', validators=[Optional()])
    subject = StringField('Subject', validators=[Optional()])
    subject_name = StringField('Subject Name', validators=[Optional()])
    subject_code = StringField('Subject Code', validators=[Optional()])

    # Attendance
    midterm_attendance1 = StringField('Midterm Attendance 1', validators=[Optional()])
    midterm_attendance2 = StringField('Midterm Attendance 2', validators=[Optional()])
    midterm_attendance3 = StringField('Midterm Attendance 3', validators=[Optional()])
    midterm_attendance4 = StringField('Midterm Attendance 4', validators=[Optional()])
    final_attendance1 = StringField('Final Attendance 1', validators=[Optional()])
    final_attendance2 = StringField('Final Attendance 2', validators=[Optional()])
    final_attendance3 = StringField('Final Attendance 3', validators=[Optional()])
    final_attendance4 = StringField('Final Attendance 4', validators=[Optional()])
    
    # Midterm Quizzes
    midterm_quiz1 = StringField('Midterm Quiz 1', validators=[Optional()])
    midterm_quiz2 = StringField('Midterm Quiz 2', validators=[Optional()])
    midterm_quiz3 = StringField('Midterm Quiz 3', validators=[Optional()])
    midterm_quiz4 = StringField('Midterm Quiz 4', validators=[Optional()])
    midterm_e_quiz1 = StringField('Midterm Exercise Quiz 1', validators=[Optional()])
    midterm_e_quiz2 = StringField('Midterm Exercise Quiz 2', validators=[Optional()])
    midterm_e_quiz3 = StringField('Midterm Exercise Quiz 3', validators=[Optional()])
    midterm_e_quiz4 = StringField('Midterm Exercise Quiz 4', validators=[Optional()])
    midterm_l_quiz1 = StringField('Midterm Laboratory Quiz 1', validators=[Optional()])
    midterm_l_quiz2 = StringField('Midterm Laboratory Quiz 2', validators=[Optional()])
    midterm_l_quiz3 = StringField('Midterm Laboratory Quiz 3', validators=[Optional()])
    midterm_l_quiz4 = StringField('Midterm Laboratory Quiz 4', validators=[Optional()])

    # Final Quizzes
    final_quiz1 = StringField('Final Quiz 1', validators=[Optional()])
    final_quiz2 = StringField('Final Quiz 2', validators=[Optional()])
    final_quiz3 = StringField('Final Quiz 3', validators=[Optional()])
    final_quiz4 = StringField('Final Quiz 4', validators=[Optional()])
    final_e_quiz1 = StringField('Final Exercise Quiz 1', validators=[Optional()])
    final_e_quiz2 = StringField('Final Exercise Quiz 2', validators=[Optional()])
    final_e_quiz3 = StringField('Final Exercise Quiz 3', validators=[Optional()])
    final_e_quiz4 = StringField('Final Exercise Quiz 4', validators=[Optional()])
    final_l_quiz1 = StringField('Final Laboratory Quiz 1', validators=[Optional()])
    final_l_quiz2 = StringField('Final Laboratory Quiz 2', validators=[Optional()])
    final_l_quiz3 = StringField('Final Laboratory Quiz 3', validators=[Optional()])
    final_l_quiz4 = StringField('Final Laboratory Quiz 4', validators=[Optional()])

    # PIT
    midterm_pit1 = StringField('PIT 1', validators=[Optional()])
    midterm_pit2 = StringField('PIT 2', validators=[Optional()])
    midterm_pit3 = StringField('PIT 3', validators=[Optional()])
    midterm_pit4 = StringField('PIT 4', validators=[Optional()])
    final_pit1 = StringField('PIT 1', validators=[Optional()])
    final_pit2 = StringField('PIT 2', validators=[Optional()])
    final_pit3 = StringField('PIT 3', validators=[Optional()])
    final_pit4 = StringField('PIT 4', validators=[Optional()])

    # Exercises
    midterm_exercise1 = StringField('Exercise 1', validators=[Optional()])
    midterm_exercise2 = StringField('Exercise 2', validators=[Optional()])
    midterm_exercise3 = StringField('Exercise 3', validators=[Optional()])
    midterm_exercise4 = StringField('Exercise 4', validators=[Optional()])
    final_exercise1 = StringField('Exercise 1', validators=[Optional()])
    final_exercise2 = StringField('Exercise 2', validators=[Optional()])
    final_exercise3 = StringField('Exercise 3', validators=[Optional()])
    final_exercise4 = StringField('Exercise 4', validators=[Optional()])

    # Laboratories
    midterm_laboratory1 = StringField('Laboratory 1', validators=[Optional()])
    midterm_laboratory2 = StringField('Laboratory 2', validators=[Optional()])
    midterm_laboratory3 = StringField('Laboratory 3', validators=[Optional()])
    midterm_laboratory4 = StringField('Laboratory 4', validators=[Optional()])
    final_laboratory1 = StringField('Laboratory 1', validators=[Optional()])
    final_laboratory2 = StringField('Laboratory 2', validators=[Optional()])
    final_laboratory3 = StringField('Laboratory 3', validators=[Optional()])
    final_laboratory4 = StringField('Laboratory 4', validators=[Optional()])

    # Reports
    midterm_report1 = StringField('Midterm Report 1', validators=[Optional()])
    final_report1 = StringField('Final Report 1', validators=[Optional()])

    # Exams and grades
    midterm_exam = StringField('Midterm Exam', validators=[Optional()])
    final_exam = StringField('Final Exam', validators=[Optional()])
    midterm_laboratory_exam = StringField('Midterm Laboratory Exam', validators=[Optional()])
    final_laboratory_exam = StringField('Final Laboratory Exam', validators=[Optional()])
    midterm_grade = StringField('Midterm Grade', validators=[Optional()])
    final_grade = StringField('Final Grade', validators=[Optional()])

    # Midterm & Finals remarks
    midterm_remarks = StringField('Midterm Remarks', validators=[Optional()])
    final_remarks = StringField('Final Remarks', validators=[Optional()])

    submit = SubmitField('Save Changes')

class ChangePasswordForm(FlaskForm):
    current_password = PasswordField('Current Password', validators=[DataRequired()])
    new_password = PasswordField('New Password', validators=[DataRequired()])
    confirm_password = PasswordField('Confirm New Password', validators=[DataRequired()])
    submit = SubmitField('Change Password')

class UploadCSVForm(FlaskForm):
    file = FileField('CSV File', validators=[DataRequired()])
    submit = SubmitField('Upload')

# =========================
# EXAM SYSTEM FORMS
# =========================

class ExamForm(FlaskForm):

    subject_id = SelectField(
        'Subject',
        coerce=int,
        validators=[DataRequired()]
    )

    title = StringField(
        'Exam Title',
        validators=[DataRequired()]
    )

    description = TextAreaField(
        'Description',
        validators=[Optional()]
    )

    duration_minutes = IntegerField(
        'Duration (Minutes)',
        validators=[DataRequired()]
    )

    # NEW FIELDS 👇
    term = SelectField(
        'Term',
        choices=[
            ('prelim', 'Prelim'),
            ('midterm', 'Midterm'),
            ('final', 'Final')
        ],
        validators=[DataRequired()]
    )

    exam_type = SelectField(
        'Exam Type',
        choices=[
            ('quiz', 'Quiz'),
            ('major', 'Major Exam'),
            ('pit', 'PIT'),
            ('exercise', 'Exercise')
        ],
        validators=[DataRequired()]
    )

    submit = SubmitField('Create Exam')


class QuestionForm(FlaskForm):

    question_type = SelectField(
        'Question Type',
        choices=[
            ('mcq', 'Multiple Choice'),
            ('identification', 'Identification')
        ],
        validators=[DataRequired()]
    )

    question_text = TextAreaField(
        'Question',
        validators=[DataRequired()]
    )

    choice_a = StringField(
        'Choice A',
        validators=[Optional()]
    )

    choice_b = StringField(
        'Choice B',
        validators=[Optional()]
    )

    choice_c = StringField(
        'Choice C',
        validators=[Optional()]
    )

    choice_d = StringField(
        'Choice D',
        validators=[Optional()]
    )

    correct_answer = StringField(
        'Correct Answer',
        validators=[DataRequired()]
    )

    points = IntegerField(
        'Points',
        default=1,
        validators=[DataRequired()]
    )

    submit = SubmitField('Add Question')

# ==========================================================
# --- Utility and Helper Functions ---
# ==========================================================
def request_exam_response(is_ajax, success, message, category="info"):

    if is_ajax:

        status_code = 200 if success else 400

        return jsonify({
            "success": success,
            "message": message
        }), status_code

    flash(message, category)

    return redirect(url_for("dashboard_student"))

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def add_log(user, action):
    log_entry = Log(user=user, action=action)
    db.session.add(log_entry)
    db.session.commit()

def get_dashboard_url():
    role = session.get('role')
    if role == 'Admin':
        return url_for('dashboard_admin')
    elif role == 'Instructor':
        return url_for('dashboard_instructor')
    return url_for('login')

def patch_update(obj, data_dict):
    for key, value in data_dict.items():
        if hasattr(obj, key):

            if value is None:
                continue

            val = str(value).strip()

            # CLEAR keyword → set to blank (NULL in DB)
            if val.upper() == "CLEAR":
                setattr(obj, key, None)

            # normal update (includes 0)
            elif val != "":
                setattr(obj, key, val)

def clean_input(value):
    return value if value not in [None, ""] else None

def get_student(student_id):
    # fetch student from DB
    pass

def get_quiz_records(student_id):
    pass

def get_pit_records(student_id):
    pass

def get_laboratory_records(student_id):
    pass

def get_exercise_records(student_id):
    pass

def get_exam_records(student_id):
    pass

# ======================================
# ===== Get Exam Questions =============
# ======================================
def get_exam_questions(exam_id):
    """Only active questions - hidden from take_exam if soft deleted"""
    try:
        return (
            Question.query
            .filter(Question.exam_id==exam_id, Question.is_deleted==False, Question.is_active==True)
            .order_by(Question.id.asc())
            .all()
        )
    except:
        # fallback if columns not yet migrated
        return (
            Question.query
            .filter_by(exam_id=exam_id)
            .order_by(Question.id.asc())
            .all()
        )

# ==========================================================
# LOAD QUESTIONS USING THE STORED RANDOM ORDER
# ==========================================================

def get_attempt_questions(attempt_id):

    return (

        db.session.query(Question)

        .join(
            AttemptQuestionOrder,
            Question.id == AttemptQuestionOrder.question_id
        )

        .filter(
            AttemptQuestionOrder.attempt_id == attempt_id
        )

        .order_by(
            AttemptQuestionOrder.display_order.asc()
        )

        .all()

    )

# ======================================
# ===== Get Total Exam Points ==========
# ======================================
def get_total_points(exam_id):

    return sum(
        question.points
        for question in get_exam_questions(exam_id)
    )

# ======================================
# ===== Get Submitted Attempts =========
# ======================================
def get_submitted_attempts(exam_id):

    return (
        ExamAttempt.query
        .filter_by(
            exam_id=exam_id,
            is_submitted=True
        )
        .all()
    )

# ======================================
# === Get Latest Submitted Attempts ====
# ======================================
def get_latest_submitted_attempts(exam_id):

    attempts = (
        ExamAttempt.query
        .filter_by(
            exam_id=exam_id,
            is_submitted=True
        )
        .order_by(
            ExamAttempt.student_id,
            ExamAttempt.id.desc()
        )
        .all()
    )

    latest = {}

    for attempt in attempts:

        if attempt.student_id not in latest:
            latest[attempt.student_id] = attempt

    return list(latest.values())

# ======================================
# ===== Get Saved Answer ===============
# ======================================
def get_saved_answer(attempt_id, question_id):

    t = time.perf_counter()

    answer = StudentAnswer.query.filter_by(
        attempt_id=attempt_id,
        question_id=question_id
    ).first()

    print(
        "SQL get_saved_answer:",
        round(time.perf_counter() - t, 4),
        "sec"
    )

    return answer

# ======================================
# ===== Get First Unanswered Question ===
# ======================================

def get_first_unanswered_question(attempt_id):

    questions = get_attempt_questions(attempt_id)

    for question in questions:

        answer = StudentAnswer.query.filter_by(
            attempt_id=attempt_id,
            question_id=question.id
        ).first()

        if not answer:
            return question

    # Everything answered
    return questions[0] if questions else None

# ======================================
# ---- Build Exam Summary ----
# ======================================
def build_exam_summary(exam_id):

    accesses = (
        ExamAccess.query
        .filter_by(exam_id=exam_id)
        .filter(
            ExamAccess.status != "not_requested"
        )
        .all()
    )

    summary = {
        "requested": len(accesses),
        "pending": 0,
        "approved": 0,
        "completed": 0,
        "rejected": 0,
        "forced_submit": 0,
        "passed": 0,
        "failed": 0,
        "average": 0,
        "highest": 0
    }

    for access in accesses:

        if access.status in summary:
            summary[access.status] += 1

    total_points = get_total_points(exam_id)

    attempts = get_latest_submitted_attempts(exam_id)

    percentages = []

    for attempt in attempts:

        if total_points <= 0:
            continue

        percentage = calculate_percentage(
            attempt.score,
            total_points
        )

        percentages.append(percentage)

        if percentage >= 70:
            summary["passed"] += 1
        else:
            summary["failed"] += 1

    if percentages:

        summary["average"] = round(
            sum(percentages) / len(percentages),
            1
        )

        summary["highest"] = max(percentages)

    return summary

# ======================================
# ---- Update Exam Access ----
# ======================================

def update_exam_access(
    student_id,
    exam_id,
    status,
    extra_data=None
):

    access = ExamAccess.query.filter_by(
        student_id=student_id,
        exam_id=exam_id
    ).first()

    if not access:
        return None

    # -----------------------------
    # Update database
    # -----------------------------
    access.status = status
    db.session.commit()

    # -----------------------------
    # Build latest summary
    # -----------------------------
    summary = build_exam_summary(exam_id)

    # -----------------------------
    # Build SSE payload
    # -----------------------------
    payload = {
        "access_id": access.id,
        "student_id": access.student_id,
        "student_name": access.student.display_name,
        "section": access.student.section,
        "exam_id": access.exam_id,
        "status": status,
        "summary": summary
    }

    # -----------------------------
    # Merge extra data (optional)
    # -----------------------------
    if extra_data:
        payload.update(extra_data)

    # -----------------------------
    # Notify Admin Dashboard
    # -----------------------------
    send_admin_event(
        "live_update",
        payload
    )

    return access

# =========================================================
# ---- GET EXAM ACCESS ------------------------------------
# =========================================================

def get_exam_access(student_id, exam_id):

    return ExamAccess.query.filter_by(
        student_id=student_id,
        exam_id=exam_id
    ).first()

# ======================================
# ===== Get Latest Attempt ============
# ======================================

def get_latest_attempt(student_id, exam_id):

    return (
        ExamAttempt.query
        .filter_by(
            student_id=student_id,
            exam_id=exam_id
        )
        .order_by(ExamAttempt.id.desc())
        .first()
    )

# =========================================================
# ---- CENTRAL EXAM GRADING ENGINE ----
# =========================================================
def grade_attempt(attempt, forced=False):

    """
    Grades an exam attempt.

    Returns:
        access
        score
        total_points
    """

    student_id = attempt.student_id
    exam_id = attempt.exam_id

    # ============================
    # Load all answers
    # ============================
    answers = StudentAnswer.query.filter_by(
        attempt_id=attempt.id
    ).all()

    # ============================
    # Load all questions
    # ============================
    questions = {
        q.id: q
        for q in Question.query.filter_by(
            exam_id=exam_id
        ).all()
    }

    score = 0

    # ============================
    # Grade answers
    # ============================
    for ans in answers:

        question = questions.get(ans.question_id)

        if not question:
            continue

        student_answer = (
            ans.selected_answer or ""
        ).strip().lower()

        correct = (
            question.correct_answer or ""
        ).strip().lower()

        ans.is_correct = (
            student_answer == correct
        )

        if ans.is_correct:
            score += question.points

    # ============================
    # Total Points
    # ============================
    total_points = get_total_points(exam_id)

    # ============================
    # Finalize Attempt
    # ============================
    attempt.score = score
    attempt.is_submitted = True
    attempt.submitted_at = datetime.utcnow()

    return score, total_points

# ======================================
# ---- Send Admin SSE ----
# ======================================
def send_admin_event(event, data):

    if "admin" not in sse_events:
        sse_events["admin"] = []

    sse_events["admin"].append({
        "event": event,
        "data": data
    })

# ======================================
# ===== Notify Exam Status ============
# ======================================

def notify_exam_status(access, status, extra=None):

    data = {
        "access_id": access.id,
        "student_id": access.student_id,
        "student_name": access.student.display_name,
        "section": access.student.section,
        "exam_id": access.exam_id,
        "status": status,
        "summary": build_exam_summary(access.exam_id)
    }

    if extra:
        data.update(extra)

    send_admin_event(
        "live_update",
        data
    )

# ======================================
# ===== Notify Exam Started ============
# ======================================
def notify_exam_started(access):

    notify_exam_status(
        access,
        "taking"
    )

# ======================================
# ===== Notify Exam Completed ==========
# ======================================
def notify_exam_completed(
    access,
    attempt,
    score,
    total_points
):

    summary = build_exam_summary(
        access.exam_id
    )

    payload = build_live_update_payload(
        access,
        attempt,
        score,
        total_points,
        summary,
        status="completed"
    )

    send_admin_event(
        "live_update",
        payload
    )

# ======================================
# ===== Calculate Exam Progress ========
# ======================================

def calculate_exam_progress(attempt_id, total_questions):

    answers_count = StudentAnswer.query.filter_by(
        attempt_id=attempt_id
    ).count()

    if total_questions == 0:
        return 0

    return int(
        (answers_count / total_questions) * 100
    )

# ======================================
# ---- Build Live Update Payload -------
# ======================================
def build_live_update_payload(
    access,
    attempt,
    score,
    total_points,
    summary,
    status="completed"
):

    return {

        "access_id": access.id,

        "student_id": access.student_id,
        "student_name": access.student.display_name,
        "section": access.student.section,

        "exam_id": access.exam_id,

        "status": status,

        "score": score,

        "total_points": total_points,

        "submitted_at": (
            attempt.submitted_at + timedelta(hours=8)
        ).strftime("%Y-%m-%d %I:%M %p"),

        "summary": summary
    }

# ======================================
# ----- Clear Exam Session -------------
# ======================================
def clear_exam_session():

    session.pop("attempt_id", None)
    session.pop("question_order", None)
    session.pop("exam_end_time", None)

# ======================================
# ----- Calculate Percentage -----------
# ======================================
def calculate_percentage(score, total_points):

    if total_points <= 0:
        return 0

    return round(
        (score / total_points) * 100
    )

# ======================================
# ---- Login Required Decorator ----
# ======================================
def login_required(role=None):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                flash("Please log in first.", "danger")
                return redirect(url_for('login'))
            if role and session.get('role') not in (role if isinstance(role, list) else [role]):
                flash("Access denied.", "danger")
                return redirect(url_for('login'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# ==========================================================
# SECURITY EVENT DEFINITIONS
# ==========================================================

SECURITY_EVENTS = {

    "TAB_SWITCH": {
        "penalty": 1,
        "severity": "HIGH",
        "source": "Window",
        "description": "Student switched to another browser tab."
    },

    "FULLSCREEN_EXIT": {
        "penalty": 1,
        "severity": "CRITICAL",
        "source": "Window",
        "description": "Student exited fullscreen mode."
    },

    "RIGHT_CLICK": {
        "penalty": 1,
        "severity": "LOW",
        "source": "Mouse",
        "description": "Student attempted to open the context menu."
    },

    "COPY": {
        "penalty": 2,
        "severity": "MEDIUM",
        "source": "Clipboard",
        "description": "Student attempted to copy exam content."
    },

    "CUT": {
        "penalty": 2,
        "severity": "MEDIUM",
        "source": "Clipboard",
        "description": "Student attempted to cut exam content."
    },

    "PASTE": {
        "penalty": 2,
        "severity": "MEDIUM",
        "source": "Clipboard",
        "description": "Student attempted to paste content."
    },

    "F12": {
        "penalty": 30,
        "severity": "HIGH",
        "source": "Keyboard",
        "description": "Student attempted to open Developer Tools using F12."
    },

    "CTRL_U": {
        "penalty": 20,
        "severity": "HIGH",
        "source": "Keyboard",
        "description": "Student attempted to view the page source using Ctrl+U."
    },

    "CTRL_SHIFT_I": {
        "penalty": 30,
        "severity": "HIGH",
        "source": "Keyboard",
        "description": "Student attempted to open Developer Tools using Ctrl+Shift+I."
    },

    "TEXT_SELECTION": {
        "penalty": 2,
        "severity": "LOW",
        "source": "Mouse",
        "description": "Student attempted to select exam text."
    }

}

# ==========================================================
# --- ROUTES ---
# ==========================================================

# ==========================================================
# ENROLLMENT & REGISTER SYSTEM - NEW (SMOOTH COLORFUL)
# ==========================================================
@app.route('/api/check-id')
def check_id():
    sid = request.args.get('id','').strip()
    if not sid:
        return jsonify({"exists": False})
    exists_student = Student.query.filter_by(student_id=sid).first()
    exists_enroll = Enrollment.query.filter_by(student_id=sid).first()
    exists_user = User.query.filter_by(username=sid).first()
    has_user = exists_user is not None
    exists = exists_student is not None or has_user
    if exists_student:
        msg = f"ID {sid} found. Welcome back! You can add more subjects."
    elif exists_enroll:
        msg = f"ID {sid} already enrolled (pending). Can add more subjects."
    elif exists_user:
        msg = f"ID {sid} has account. Welcome back!"
    else:
        msg = "Available - new student"
    return jsonify({"exists": exists, "has_user": has_user, "message": msg, "allow_reenroll": True})

@app.route('/api/check-id-subjects')
def check_id_subjects():
    sid = request.args.get('id','').strip()
    if not sid:
        return jsonify({"found": False})
    students = Student.query.filter_by(student_id=sid).all()
    enrolls = Enrollment.query.filter_by(student_id=sid).all()
    if not students and not enrolls:
        return jsonify({"found": False})
    ref = students[0] if students else enrolls[0]
    enrolled_codes = list(set([s.subject_code for s in students if s.subject_code]))
    enrolled_names = [{"code": s.subject_code, "name": s.subject_name or s.subject, "subject": s.subject} for s in students if s.subject_code]
    pending_codes = list(set([e.subject_code for e in enrolls if e.subject_code and e.status=='pending']))
    return jsonify({
        "found": True,
        "last_name": ref.last_name or "",
        "first_name": ref.first_name or "",
        "middle_name": ref.middle_name or "",
        "course": getattr(ref, 'course', ''),
        "year": getattr(ref, 'year', ''),
        "section": getattr(ref, 'section', ''),
        "email": getattr(ref, 'email', ''),
        "contact": getattr(ref, 'contact', ''),
        "enrolled_codes": enrolled_codes,
        "enrolled_subjects": enrolled_names,
        "pending_codes": pending_codes
    })

@app.route('/api/subjects/search')
def search_subjects():
    q = request.args.get('q','').strip().lower()
    limit = int(request.args.get('limit', 30))
    query = Subject.query
    if q:
        query = query.filter(
            db.or_(
                func.lower(Subject.subject_code).like(f'%{q}%'),
                func.lower(Subject.subject_name).like(f'%{q}%')
            )
        )
    subjects = query.order_by(Subject.subject_code.asc()).limit(limit).all()
    result = [{"code": s.subject_code, "name": s.subject_name, "id": s.id} for s in subjects]
    return jsonify(result)

@app.route('/enrollment', methods=['GET','POST'])
def enrollment():
    form = None
    if request.method == 'POST':
        student_id = request.form.get('student_id','').strip()
        last_name = request.form.get('last_name','').strip()
        first_name = request.form.get('first_name','').strip()
        middle_name = request.form.get('middle_name','').strip()
        course = request.form.get('course','').strip()
        year = request.form.get('year','').strip()
        section = request.form.get('section','').strip()
        email = request.form.get('email','').strip()
        contact = request.form.get('contact','').strip()
        username = request.form.get('username','').strip() or student_id
        subjects_selected = request.form.getlist('subjects[]')
        if not subjects_selected:
            raw = request.form.get('subjects','').strip()
            if raw:
                subjects_selected = [s.strip() for s in raw.split(',') if s.strip()]

        if not student_id or not last_name or not first_name:
            flash("ID Number, Surname and First Name are required (ID is priority).", "danger")
            return render_template('enrollment.html', form=form)

        if not subjects_selected:
            flash("Please select at least one subject to enroll.", "warning")
            return render_template('enrollment.html', form=form)

        existing_students = Student.query.filter_by(student_id=student_id).all()
        is_reenroll = len(existing_students) > 0

        selected_codes = [c.strip().upper() for c in subjects_selected if c.strip()]
        selected_codes = list(dict.fromkeys(selected_codes))

        already_enrolled_codes = [s.subject_code.upper() for s in existing_students if s.subject_code]
        pending_enroll_codes = [e.subject_code.upper() for e in Enrollment.query.filter_by(student_id=student_id, status='pending').all() if e.subject_code]

        new_codes = []
        skipped_codes = []
        for code in selected_codes:
            if code in already_enrolled_codes or code in pending_enroll_codes:
                skipped_codes.append(code)
            else:
                new_codes.append(code)

        if not new_codes:
            flash(f"You are already enrolled in: {', '.join(skipped_codes)}. No new subjects.", "warning")
            return render_template('enrollment.html', form=form)

        try:
            disp = format_display_name(last_name, first_name, middle_name)
            created_count = 0
            for code in new_codes:
                subj_obj = Subject.query.filter(func.upper(Subject.subject_code)==code).first()
                subj_name = subj_obj.subject_name if subj_obj else code
                subj_display = subj_obj.subject_code if subj_obj else code

                enroll = Enrollment(
                    student_id=student_id,
                    last_name=last_name,
                    first_name=first_name,
                    middle_name=middle_name,
                    name=disp,
                    course=course,
                    year=year,
                    section=section,
                    email=email,
                    contact=contact,
                    username=username,
                    subject=subj_display,
                    subject_code=subj_display,
                    subject_name=subj_name,
                    status='pending'
                )
                db.session.add(enroll)

                existing_user = User.query.filter_by(username=student_id).first()
                should_auto_create_student = existing_user is not None or is_reenroll

                if should_auto_create_student:
                    if not Student.query.filter_by(student_id=student_id, subject_code=subj_display).first() and not Student.query.filter_by(student_id=student_id, subject=subj_display).first():
                        stud = Student(
                            student_id=student_id,
                            last_name=last_name,
                            first_name=first_name,
                            middle_name=middle_name,
                            name=disp,
                            year=year,
                            section=section,
                            subject=subj_display,
                            subject_code=subj_display,
                            subject_name=subj_name,
                            school_year=f"{datetime.now().year}-{datetime.now().year+1}"
                        )
                        db.session.add(stud)

                created_count += 1

            if is_reenroll:
                for s in existing_students:
                    s.last_name = last_name or s.last_name
                    s.first_name = first_name or s.first_name
                    s.middle_name = middle_name if middle_name else s.middle_name
                    s.year = year or s.year
                    s.section = section or s.section
                    s.name = disp

            db.session.commit()
            flash(f"Success! ID {student_id} enrolled in {created_count} subject(s): {', '.join(new_codes)}" + (f" | Skipped: {', '.join(skipped_codes)}" if skipped_codes else ""), "success")
            add_log(student_id, f"{'Re-enrollment' if is_reenroll else 'Enrollment'} - {disp} added: {', '.join(new_codes)}")
            return redirect(url_for('login') if not is_reenroll else url_for('enrollment'))
        except Exception as e:
            db.session.rollback()
            flash(f"Enrollment failed: {str(e)}", "danger")

    return render_template('enrollment.html', form=form)


@app.route('/register', methods=['GET','POST'])
def register():
    form = LoginForm()
    if request.method == 'POST':
        student_id = request.form.get('student_id','').strip()
        last_name = request.form.get('last_name','').strip()
        first_name = request.form.get('first_name','').strip()
        middle_name = request.form.get('middle_name','').strip()
        legacy_name = request.form.get('name','').strip()
        if not last_name and legacy_name:
            ln, fn, mn = parse_fullname_to_parts(legacy_name)
            last_name, first_name, middle_name = ln, fn, mn

        username = request.form.get('username','').strip() or student_id
        password = request.form.get('password','').strip()
        confirm = request.form.get('confirm_password','').strip()
        role = 'Student'

        if not student_id:
            flash("ID Number is required!", "danger")
            return render_template('register.html', form=form)
        if not last_name or not first_name or not username or not password:
            flash("Surname, First Name, Username and Password are required.", "danger")
            return render_template('register.html', form=form)
        if password != confirm:
            flash("Passwords do not match. Please check Confirm Password.", "danger")
            return render_template('register.html', form=form)
        if len(password) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return render_template('register.html', form=form)

        # Check enrollment must exist and be approved
        enroll = Enrollment.query.filter_by(student_id=student_id).first()
        if not enroll:
            flash(f"ID {student_id} not found in enrollment. Please enroll first.", "warning")
            return render_template('register.html', form=form)
        if enroll.status == 'pending':
            flash(f"ID {student_id} is still pending approval. Please wait for admin to approve.", "warning")
            return render_template('register.html', form=form)
        if enroll.status == 'rejected':
            flash(f"ID {student_id} enrollment was rejected. Contact admin.", "danger")
            return render_template('register.html', form=form)

        # Check if username already exists
        if User.query.filter_by(username=username).first():
            flash(f"Username/ID {username} already has an account. Please login.", "danger")
            return render_template('register.html', form=form)

        # Check if Student ID already has a User linked
        if User.query.filter_by(username=student_id).first() and username != student_id:
            # allow if username different? but still prevent duplicate student_id user
            pass

        disp = format_display_name(last_name, first_name, middle_name)

        try:
            # Create User
            user = User(username=username, password=generate_password_hash(password), role=role)
            db.session.add(user)
            
            # Ensure Student record exists (should have been created on approval, but fallback)
            stud = Student.query.filter_by(student_id=student_id).first()
            if not stud:
                # Use enrollment data for year/section if available
                stud = Student(
                    student_id=student_id,
                    last_name=enroll.last_name or last_name,
                    first_name=enroll.first_name or first_name,
                    middle_name=enroll.middle_name or middle_name,
                    name=disp,
                    year=enroll.year,
                    section=enroll.section,
                    subject='GENERAL',
                    school_year=f"{datetime.now().year}-{datetime.now().year+1}"
                )
                db.session.add(stud)

            db.session.commit()
            flash(f"Account created for ID {student_id}! Please login.", "success")
            add_log(username, f"Registered new account with ID {student_id} - {disp}")
            return redirect(url_for('login'))
        except Exception as e:
            db.session.rollback()
            flash(f"Registration failed: {e}", "danger")

    return render_template('register.html', form=form)

@app.route('/dashboard/admin/enrollments')
@login_required(role=['Admin','Instructor'])
def enrollment_admin():
    # Exclude deleted by default? No, show all and let tabs filter
    enrollments = Enrollment.query.order_by(Enrollment.created_at.desc()).all()
    return render_template('enrollment_admin.html', enrollments=enrollments)

@app.route('/api/enrollments/action', methods=['POST'])
@login_required(role=['Admin','Instructor'])
def api_enrollment_action():
    data = request.get_json() or request.form
    action = (data.get('action') or '').lower()
    eid = data.get('id')
    ids = data.get('ids') or []
    if eid:
        ids = [eid]
    if isinstance(ids, str):
        ids = [ids]
    try:
        ids = [int(i) for i in ids if str(i).strip()!='']
    except:
        return jsonify({"success": False, "error": "Invalid ids"}), 400
    if not ids:
        return jsonify({"success": False, "error": "No ids"}), 400
    enrollments = Enrollment.query.filter(Enrollment.id.in_(ids)).all()
    count=0
    results=[]
    for e in enrollments:
        prev_status = e.status
        if action == 'approve':
            e.status = 'approved'
            e.is_deleted = False
            e.deleted_at = None
            e.approved_at = datetime.utcnow()
            subj_code = (e.subject_code or e.subject or 'GENERAL').strip().upper() if (e.subject_code or e.subject) else 'GENERAL'
            subj_name = e.subject_name or subj_code
            existing = Student.query.filter_by(student_id=e.student_id, subject_code=subj_code).first() or Student.query.filter_by(student_id=e.student_id, subject=subj_code).first()
            if not existing:
                stud = Student(student_id=e.student_id, last_name=e.last_name, first_name=e.first_name, middle_name=e.middle_name, name=e.display_name, year=e.year, section=e.section, subject=subj_code, subject_code=subj_code, subject_name=subj_name, school_year=f"{datetime.now().year}-{datetime.now().year+1}")
                db.session.add(stud)
        elif action == 'reject':
            e.status = 'rejected'
            e.is_deleted = False
            e.deleted_at = None
        elif action == 'delete':
            e.is_deleted = True
            e.deleted_at = datetime.utcnow()
            # optionally also remove from Student table? Keep for restore logic - we will delete student record on delete
            subj_code = (e.subject_code or e.subject or '').strip().upper()
            if subj_code:
                st = Student.query.filter_by(student_id=e.student_id, subject_code=subj_code).first() or Student.query.filter_by(student_id=e.student_id, subject=subj_code).first()
                if st:
                    db.session.delete(st)
        elif action == 'restore':
            e.is_deleted = False
            e.deleted_at = None
            e.status = 'approved'
            e.approved_at = datetime.utcnow()
            subj_code = (e.subject_code or e.subject or 'GENERAL').strip().upper() if (e.subject_code or e.subject) else 'GENERAL'
            subj_name = e.subject_name or subj_code
            existing = Student.query.filter_by(student_id=e.student_id, subject_code=subj_code).first() or Student.query.filter_by(student_id=e.student_id, subject=subj_code).first()
            if not existing:
                stud = Student(student_id=e.student_id, last_name=e.last_name, first_name=e.first_name, middle_name=e.middle_name, name=e.display_name, year=e.year, section=e.section, subject=subj_code, subject_code=subj_code, subject_name=subj_name, school_year=f"{datetime.now().year}-{datetime.now().year+1}")
                db.session.add(stud)
        elif action == 'move_pending':
            e.status = 'pending'
            e.is_deleted = False
            e.deleted_at = None
            # also remove student record if exists so they don't show in Students page while pending
            subj_code = (e.subject_code or e.subject or '').strip().upper()
            if subj_code:
                st = Student.query.filter_by(student_id=e.student_id, subject_code=subj_code).first() or Student.query.filter_by(student_id=e.student_id, subject=subj_code).first()
                if st:
                    db.session.delete(st)
        elif action == 'hard_delete':
            subj_code = (e.subject_code or e.subject or '').strip().upper()
            if subj_code:
                st = Student.query.filter_by(student_id=e.student_id, subject_code=subj_code).first() or Student.query.filter_by(student_id=e.student_id, subject=subj_code).first()
                if st:
                    db.session.delete(st)
            db.session.delete(e)
        else:
            return jsonify({"success": False, "error": "Unknown action"}), 400
        count+=1
        results.append({"id": e.id, "student_id": e.student_id, "subject_code": e.subject_code or e.subject, "status": e.status, "is_deleted": e.is_deleted, "prev_status": prev_status})
    try:
        db.session.commit()
        return jsonify({"success": True, "count": count, "results": results, "action": action})
    except Exception as ex:
        db.session.rollback()
        return jsonify({"success": False, "error": str(ex)}), 500

@app.route('/api/enrollments/list')
@login_required(role=['Admin','Instructor'])
def api_enrollments_list():
    enrolls = Enrollment.query.order_by(Enrollment.created_at.desc()).all()
    def serialize(e):
        return {
            "id": e.id,
            "student_id": e.student_id,
            "last_name": e.last_name,
            "first_name": e.first_name,
            "middle_name": e.middle_name,
            "display_name": e.display_name,
            "course": e.course,
            "year": e.year,
            "section": e.section,
            "subject": e.subject,
            "subject_code": e.subject_code,
            "subject_name": e.subject_name,
            "status": e.status,
            "is_deleted": bool(e.is_deleted),
            "created_at": e.created_at.isoformat() if e.created_at else None,
            "created_at_str": e.created_at.strftime('%b %d, %Y') if e.created_at else ''
        }
    return jsonify([serialize(e) for e in enrolls])


@app.route('/dashboard/admin/enrollments/<int:id>/approve', methods=['POST'])
@login_required(role=['Admin','Instructor'])
def approve_enrollment(id):
    e = Enrollment.query.get_or_404(id)
    e.status = 'approved'
    e.approved_at = datetime.utcnow()
    subj_code = (e.subject_code or e.subject or 'GENERAL').strip().upper() if (e.subject_code or e.subject) else 'GENERAL'
    subj_name = e.subject_name or subj_code
    existing = Student.query.filter_by(student_id=e.student_id, subject_code=subj_code).first() or Student.query.filter_by(student_id=e.student_id, subject=subj_code).first()
    if not existing:
        stud = Student(
            student_id=e.student_id,
            last_name=e.last_name,
            first_name=e.first_name,
            middle_name=e.middle_name,
            name=e.display_name,
            year=e.year,
            section=e.section,
            subject=subj_code,
            subject_code=subj_code,
            subject_name=subj_name,
            school_year=f"{datetime.now().year}-{datetime.now().year+1}"
        )
        db.session.add(stud)
    try:
        db.session.commit()
        flash(f"Approved enrollment ID {e.student_id} - now visible in Students page, can register", "success")
    except Exception as ex:
        db.session.rollback()
        flash(f"Approve failed: {ex}", "danger")
    return redirect(url_for('enrollment_admin'))

@app.route('/dashboard/admin/enrollments/<int:id>/reject', methods=['POST'])
@login_required(role=['Admin','Instructor'])
def reject_enrollment(id):
    e = Enrollment.query.get_or_404(id)
    e.status = 'rejected'
    # If student was previously approved and has Student record, remove it so they don't appear in Students page
    existing_students = Student.query.filter_by(student_id=e.student_id).all()
    for s in existing_students:
        db.session.delete(s)
    # Also remove User account if exists (must re-enroll and re-register)
    u = User.query.filter_by(username=e.student_id).first()
    if u:
        # Only delete if student role, keep admin/instructor safe
        if u.role == 'Student':
            # Check if another enrollment uses different username? Keep simple: if username == student_id, delete
            if u.username == e.student_id or u.username == e.username:
                db.session.delete(u)
    try:
        db.session.commit()
        flash(f"Rejected enrollment ID {e.student_id} - removed from Students page", "warning")
    except Exception as ex:
        db.session.rollback()
        flash(f"Reject failed: {ex}", "danger")
    return redirect(url_for('enrollment_admin'))

@app.route('/dashboard/admin/enrollments/batch', methods=['POST'])
@app.route('/batch_approve_enrollments', methods=['POST'])
@login_required(role=['Admin','Instructor'])
def batch_approve_enrollments():
    ids = request.form.getlist('ids')
    action = request.form.get('action', 'approve')
    
    if not ids:
        flash("No enrollments selected.", "warning")
        return redirect(url_for('enrollment_admin'))
    
    enrollments = Enrollment.query.filter(Enrollment.id.in_(ids)).all()
    
    count = 0
    for e in enrollments:
        if action == 'approve':
            e.status = 'approved'
            e.approved_at = datetime.utcnow()
            subj_code = (e.subject_code or e.subject or 'GENERAL').strip().upper() if (e.subject_code or e.subject) else 'GENERAL'
            subj_name = e.subject_name or subj_code
            existing = Student.query.filter_by(student_id=e.student_id, subject_code=subj_code).first() or Student.query.filter_by(student_id=e.student_id, subject=subj_code).first()
            if not existing:
                stud = Student(student_id=e.student_id, last_name=e.last_name, first_name=e.first_name, middle_name=e.middle_name, name=e.display_name, year=e.year, section=e.section, subject=subj_code, subject_code=subj_code, subject_name=subj_name)
                db.session.add(stud)
            count += 1
        elif action == 'reject':
            e.status = 'rejected'
            count += 1
    
    try:
        db.session.commit()
        if action == 'approve':
            flash(f"✅ Batch approved {count} enrollment(s)!", "success")
        else:
            flash(f"🗑️ Batch rejected {count} enrollment(s)!", "warning")
    except Exception as ex:
        db.session.rollback()
        flash(f"Batch action failed: {ex}", "danger")
    
    return redirect(url_for('enrollment_admin'))

# ==========================================================
# Login
# ==========================================================
@app.route('/', methods=['GET', 'POST'])
def login():
    form = LoginForm()

    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()

        if user and check_password_hash(user.password, form.password.data):

            # SECURITY FIX: If Student, check enrollment approval
            if user.role == 'Student':
                enroll = Enrollment.query.filter_by(student_id=user.username).first()
                # Also check by username field in enrollment
                if not enroll:
                    enroll = Enrollment.query.filter_by(username=user.username).first()
                if enroll and enroll.status != 'approved':
                    if enroll.status == 'pending':
                        flash(f'Account {user.username} is pending approval. Wait for admin to approve enrollment.', 'warning')
                    else:
                        flash(f'Account {user.username} enrollment was rejected. Contact admin.', 'danger')
                    return render_template('login.html', form=form)

            session['user_id'] = user.id
            session['username'] = user.username
            session['role'] = user.role

            if user.role == 'Student':
                session['student_id'] = user.username

            flash(f'Logged in successfully as {user.username}', 'success')
            add_log(user.username, 'Logged in')

            if user.role == 'Admin':
                return redirect(url_for('dashboard_admin'))

            elif user.role == 'Instructor':
                return redirect(url_for('dashboard_instructor'))

            else:
                return redirect(url_for('dashboard_student'))

        else:
            flash('Invalid username or password', 'danger')

    return render_template('login.html', form=form)

# Logout
@app.route('/logout')
def logout():
    user = session.get('username')
    session.clear()
    if user:
        add_log(user, 'Logged out')
    return redirect(url_for('login'))

# Change Password
@app.route('/change_password', methods=['GET', 'POST'])
@login_required()
def change_password():
    user = User.query.get(session['user_id'])

    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        # Validate current password
        if not check_password_hash(user.password, current_password):
            flash("❌ Current password is incorrect.", "danger")
            return redirect(url_for('change_password'))

        # Ensure new password matches confirmation
        if new_password != confirm_password:
            flash("⚠️ New password do not match.", "warning")
            return redirect(url_for('change_password'))

        # Ensure new password isn't same as current
        if check_password_hash(user.password, new_password):
            flash("⚠️ New password must be different from the current one.", "warning")
            return redirect(url_for('change_password'))

        # Update password securely
        user.password = generate_password_hash(new_password)
        db.session.commit()

        # Log the event
        add_log(
            user.username,
            f"Changed their password (Role: {user.role})"
        )
        time.sleep(1)

        # Force logout for security
        session.clear()
        flash("✅ Nautro na imuhang password! Congrats!.", "success")
        return redirect(url_for('login'))

    return render_template('change_password.html')

# =========================
# --- Dashboards ---
# =========================

# ---- Admin Dashboard ----
@app.route('/dashboard/admin')
@login_required(role='Admin')
def dashboard_admin():
    # Fetch all students
    students = Student.query.all()
    
    # Fetch all instructors
    instructors = User.query.filter_by(role='Instructor').order_by(User.id.desc()).all()
    
    # Fetch all logs (newest first)
    logs = Log.query.order_by(Log.timestamp.desc()).limit(10).all()

    # FETCH SUBJECTS
    subjects = Subject.query.order_by(Subject.id.desc()).all()

    # FETCH EXAMS
    exams = Exam.query.all()

    # FETCH QUESTIONS
    all_questions = Question.query.all()
    
    # Calendar events for FullCalendar - with 12a cleaning
    try:
        calendar_events = CalendarEvent.query.order_by(CalendarEvent.start_date.asc()).all()
        calendar_list = []
        import re as _re
        import json as _json
        for ev in calendar_events:
            clean_title = ev.title or ""
            clean_title = _re.sub(r'^\s*\d{1,2}(?::\d{2})?\s*[ap]\s*m?\s*[-–—]*\s*', '', clean_title, flags=_re.I).strip()
            clean_title = _re.sub(r'^\s*\d{1,2}\s*[ap]\s*[-–—]*\s*', '', clean_title, flags=_re.I).strip()
            calendar_list.append({
                "id": str(ev.id),
                "title": clean_title,
                "start": ev.start_date.isoformat() if ev.start_date else "",
                "end": ev.end_date.isoformat() if ev.end_date else None,
                "description": ev.description or "",
                "allDay": True
            })
        calendar_events_json = _json.dumps(calendar_list)
    except Exception as e:
        print(f"Calendar load error: {e}")
        calendar_events = []
        calendar_events_json = "[]"
    
    # Render template
    return render_template(
        'dashboard_admin.html',
        students=students,
        instructors=instructors,
        logs=logs,
        subjects=subjects,
        exams=exams,
        calendar_events=calendar_events,
        calendar_events_json=calendar_events_json
    )

# ---- Instructor Dashboard ----
@app.route('/dashboard/instructor')
@login_required(role='Instructor')
def dashboard_instructor():
    # Fetch all students (or filter by instructor if needed)
    students = Student.query.all()

    # FETCH SUBJECTS
    subjects = Subject.query.order_by(Subject.subject_name.asc()).all()

    return render_template(
        'dashboard_instructor.html',
        students=students,
        subjects=subjects
    )

# ---- Student Dashboard ----
@app.route('/dashboard/student')
@login_required(role=['Student','Admin','Instructor'])
def dashboard_student():

    role = session.get('role')

    if role == 'Student':
        student_id = session.get('student_id')

        if not student_id:
            flash("Session expired. Please login again.", "danger")
            return redirect(url_for('login'))

    else:
        student_id = request.args.get('student_id')

    student = Student.query.filter_by(student_id=student_id).first()

    if not student:
        flash("Student not found.", "danger")
        return redirect(url_for('login'))

    student_name = student.name

    grades = Student.query.filter_by(student_id=student.student_id).all()

    # Get all subjects this student is enrolled in
    all_enrollments = Student.query.filter_by(student_id=student.student_id).all()
    enrolled_subject_codes_raw = [s.subject_code.strip() for s in all_enrollments if s.subject_code]
    enrolled_subject_codes = list(set(enrolled_subject_codes_raw))
    # For display
    enrolled_subjects = [{"code": s.subject_code, "name": s.subject_name or s.subject} for s in all_enrollments if s.subject_code]

    # Determine current semester/year from request or latest enrollment
    current_semester = request.args.get('semester') or (all_enrollments[0].semester if all_enrollments and all_enrollments[0].semester else "1st")
    current_school_year = all_enrollments[0].school_year if all_enrollments and all_enrollments[0].school_year else f"{datetime.now().year}-{datetime.now().year+1}"

    # FILTER: Only show exams for enrolled subjects - CASE-INSENSITIVE FIX
    if enrolled_subject_codes:
        active_exams = Exam.query.join(Subject).filter(
            func.upper(Subject.subject_code).in_([c.upper() for c in enrolled_subject_codes]),
            Exam.is_active==True
        ).all()
        print(f"[DASHBOARD] Student {student.student_id} enrolled in {enrolled_subject_codes} -> found {len(active_exams)} active exams")
    else:
        active_exams = []
        enrolled_subjects = []
        print(f"[DASHBOARD] No enrollment codes for {student.student_id}")

    # Debug: if no exams, list all active exams to see mismatch
    if not active_exams:
        all_active = Exam.query.filter_by(is_active=True).all()
        print(f"[DEBUG] No active exams for enrolled codes. All active exams in DB: {[(e.id, e.title, e.subject.subject_code if e.subject else 'no-subject') for e in all_active]}")


    # ALL EXAMS (for Examination Results)
    all_exams = Exam.query.all()

    allowed_exams = []

    for exam in active_exams:

        access = ExamAccess.query.filter_by(
            student_id=student.student_id,
            exam_id=exam.id
        ).first()

        if access:
            print("=" * 50)
            print("DASHBOARD STATUS CHECK")
            print("Exam:", exam.id)
            print("Status:", access.status)
            print("Reset:", access.is_reset)
            print("=" * 50)

        allowed_exams.append({
            "id": exam.id,
            "title": exam.title,
            "subject": exam.subject,
            "status": access.status if access else None,
            "description": exam.description,
            "duration": exam.duration_minutes,
            "exam_type": exam.exam_type,
            "term": exam.term,
            "question_count": len(exam.questions)
        })

    exam_results = []

    for exam in all_exams:

        access = ExamAccess.query.filter_by(
            student_id=student.student_id,
            exam_id=exam.id
        ).first()

        if access and access.status == "not_requested":
            continue

        attempt = (
            ExamAttempt.query
            .filter_by(
                student_id=student.student_id,
                exam_id=exam.id,
                is_submitted=True
            )
            .order_by(ExamAttempt.id.desc())
            .first()
        )

        if not attempt:
            continue

        total_points = sum(
            question.points
            for question in exam.questions
        )

        percentage = (
            round((attempt.score / total_points) * 100)
            if total_points > 0
            else 0
        )

        if percentage >= 85:
            result = "Excellent"
        elif percentage >= 70:
            result = "Passed"
        else:
            result = "Failed"

        exam_results.append({
            "title": exam.title,
            "subject": exam.subject,
            "score": attempt.score,
            "total_points": total_points,
            "percentage": percentage,
            "result": result,
            "submitted_at": (
                attempt.submitted_at + timedelta(hours=8)
                if attempt.submitted_at
                else None
            )
    })

    return render_template(
        "dashboard_student.html",
        student=student,
        student_name=student_name,
        role=role,
        allowed_exams=allowed_exams,
        grades=grades,
        exam_results=exam_results,
        enrolled_subjects=enrolled_subjects,
        current_semester=current_semester,
        current_school_year=current_school_year
    )

# =========================
# Exam routes
# =========================
# =========================================================
# 1. ➕ SUBJECT MANAGEMENT (ADMIN SETUP)
# =========================================================

@app.route('/add-subject', methods=['GET', 'POST'])
@login_required(role=['Admin', 'Instructor'])
def add_subject():

    if request.method == 'POST':

        code = request.form.get('subject_code').strip().upper()
        name = request.form.get('subject_name').strip()

        existing = Subject.query.filter_by(subject_code=code).first()

        if existing:
            return redirect(url_for(
                'view_subjects',
                result='exists'
            ))

        subject = Subject(
            subject_code=code,
            subject_name=name
        )

        try:
            db.session.add(subject)
            db.session.commit()

            return redirect(url_for(
                'view_subjects',
                result='success',
                open_create_exam=1,
                subject_id=subject.id
            ))

        except IntegrityError:

            db.session.rollback()

            return redirect(url_for(
                'view_subjects',
                result='exists'
            ))

    return redirect(url_for('view_subjects'))

@app.route('/subjects')
@login_required(role=['Admin', 'Instructor'])
def view_subjects():

    subjects = Subject.query.order_by(Subject.subject_name.asc()).all()
    all_students = Student.query.order_by(Student.name.asc()).all()

    return render_template(
        'view_subjects.html',
        subjects=subjects,
        all_students=all_students,
        current_year=f"{datetime.now().year}-{datetime.now().year+1}"
    )

# =========================
# SECTIONS MANAGEMENT - NEW (like subjects but for sections)
# =========================
@app.route('/sections')
@login_required(role=['Admin', 'Instructor'])
def view_sections():
    sections = Section.query.order_by(Section.section_code.asc()).all()
    # also get distinct sections from Student table to auto-populate counts
    student_sections = db.session.query(Student.section, func.count(Student.student_id)).group_by(Student.section).all() if hasattr(Student, 'section') else []
    # count per section_code
    counts = {sec: cnt for sec, cnt in student_sections if sec}
    # also get all students for stats
    all_students = Student.query.order_by(Student.last_name.asc()).all()
    return render_template('view_sections.html', sections=sections, counts=counts, all_students=all_students)

@app.route('/add-section', methods=['POST'])
@login_required(role=['Admin','Instructor'])
def add_section():
    code = request.form.get('section_code','').strip().upper()
    desc = request.form.get('description','').strip()
    sy = request.form.get('school_year','').strip() or f"{datetime.now().year}-{datetime.now().year+1}"
    if not code:
        flash("Section code required", "danger")
        return redirect(url_for('view_sections'))
    existing = Section.query.filter_by(section_code=code).first()
    if existing:
        flash(f"Section {code} already exists", "warning")
        return redirect(url_for('view_sections'))
    sec = Section(section_code=code, description=desc, school_year=sy)
    db.session.add(sec)
    db.session.commit()
    flash(f"Section {code} added", "success")
    return redirect(url_for('view_sections'))

@app.route('/edit-section/<int:section_id>', methods=['POST'])
@login_required(role=['Admin','Instructor'])
def edit_section(section_id):
    sec = Section.query.get_or_404(section_id)
    new_code = request.form.get('section_code','').strip().upper()
    desc = request.form.get('description','').strip()
    sy = request.form.get('school_year','').strip()
    if not new_code:
        flash("Section code required", "danger")
        return redirect(url_for('view_sections'))
    # check duplicate
    dup = Section.query.filter(Section.section_code==new_code, Section.id!=section_id).first()
    if dup:
        flash(f"Section code {new_code} already exists", "warning")
        return redirect(url_for('view_sections'))
    old_code = sec.section_code
    sec.section_code = new_code
    sec.description = desc
    sec.school_year = sy
    # update Student records that have old section
    try:
        Student.query.filter_by(section=old_code).update({"section": new_code})
    except Exception as e:
        print(f"update students section failed: {e}")
    db.session.commit()
    flash(f"Section updated to {new_code}", "success")
    return redirect(url_for('view_sections'))

@app.route('/delete-section/<int:section_id>', methods=['POST'])
@login_required(role=['Admin','Instructor'])
def delete_section(section_id):
    sec = Section.query.get_or_404(section_id)
    # don't delete if students still use it? allow but warn
    count = Student.query.filter_by(section=sec.section_code).count()
    if count>0:
        flash(f"Cannot delete {sec.section_code} - still has {count} students. Move them first.", "warning")
        return redirect(url_for('view_sections'))
    db.session.delete(sec)
    db.session.commit()
    flash(f"Section {sec.section_code} deleted", "success")
    return redirect(url_for('view_sections'))

@app.route('/sections/bulk_delete', methods=['POST'])
@login_required(role=['Admin','Instructor'])
def bulk_delete_sections():
    section_ids = request.form.getlist('section_ids')
    if not section_ids:
        flash("No sections selected", "warning")
        return redirect(url_for('view_sections'))
    deleted=0
    skipped=0
    for sid in section_ids:
        try:
            sec = Section.query.get(int(sid))
            if not sec:
                continue
            count = Student.query.filter_by(section=sec.section_code).count()
            if count>0:
                skipped+=1
                continue
            db.session.delete(sec)
            deleted+=1
        except Exception as e:
            print(f"bulk delete section error: {e}")
            skipped+=1
    db.session.commit()
    if deleted>0:
        flash(f"Deleted {deleted} section(s), {skipped} skipped (has students)", "success" if skipped==0 else "warning")
    else:
        flash(f"No sections deleted. {skipped} section(s) have students.", "warning")
    return redirect(url_for('view_sections'))

@app.route('/sections/<int:section_id>/bulk_remove', methods=['POST'])
@login_required(role=['Admin','Instructor'])
def bulk_remove_section_students(section_id):
    section = Section.query.get_or_404(section_id)
    student_ids = request.form.getlist('student_ids')
    if not student_ids:
        flash("No students selected", "warning")
        return redirect(url_for('view_section_students', section_id=section_id))
    removed=0
    for sid in student_ids:
        sid=sid.strip()
        if not sid: continue
        # remove all records with this section? set section to None
        try:
            updated = Student.query.filter_by(student_id=sid, section=section.section_code).update({"section": None})
            if updated:
                removed+=updated
        except Exception as e:
            print(f"bulk remove error: {e}")
    db.session.commit()
    flash(f"Removed {removed} student(s) from {section.section_code}", "success")
    return redirect(url_for('view_section_students', section_id=section_id))

@app.route('/view_section_students/<int:section_id>')
@app.route('/sections/<int:section_id>/students')
@login_required(role=['Admin','Instructor'])
def view_section_students(section_id):
    section = Section.query.get_or_404(section_id)
    students = Student.query.filter_by(section=section.section_code).order_by(Student.last_name.asc(), Student.first_name.asc()).all()
    # dedupe by student_id and sort alphabetically
    seen = {}
    for s in students:
        if s.student_id not in seen:
            seen[s.student_id] = s
    students = sorted(seen.values(), key=lambda x: (x.last_name or '', x.first_name or '', x.student_id))
    all_students = Student.query.order_by(Student.last_name.asc()).all()
    tmp = {}
    for s in all_students:
        if s.student_id not in tmp:
            tmp[s.student_id] = s
    all_students = list(tmp.values())
    all_sections = Section.query.order_by(Section.section_code.asc()).all()
    return render_template('view_section_students.html', section=section, students=students, all_students=all_students, all_sections=all_sections, current_year=f"{datetime.now().year}-{datetime.now().year+1}")

@app.route('/sections/<int:section_id>/batch_assign', methods=['POST'])
@login_required(role=['Admin','Instructor'])
def batch_assign_section_students(section_id):
    section = Section.query.get_or_404(section_id)
    student_ids = request.form.getlist('student_ids')
    if not student_ids:
        flash("No students selected", "warning")
        return redirect(url_for('view_section_students', section_id=section_id))
    added=0
    for sid in student_ids:
        sid=sid.strip()
        if not sid: continue
        st = Student.query.filter_by(student_id=sid).first()
        if not st:
            continue
        # update all records of this student to new section
        try:
            Student.query.filter_by(student_id=sid).update({"section": section.section_code})
            added+=1
        except Exception as e:
            print(e)
    db.session.commit()
    flash(f"Moved {added} students to {section.section_code}", "success")
    return redirect(url_for('view_section_students', section_id=section_id))

@app.route('/sections/<int:section_id>/remove_student/<student_id>', methods=['POST'])
@login_required(role=['Admin','Instructor'])
def remove_student_from_section(section_id, student_id):
    section = Section.query.get_or_404(section_id)
    st = Student.query.filter_by(student_id=student_id, section=section.section_code).first()
    if not st:
        flash("Student not found in this section", "warning")
        return redirect(url_for('view_section_students', section_id=section_id))
    # set section to None or empty instead of delete
    st.section = None
    db.session.commit()
    flash(f"Removed {st.display_name} from {section.section_code}", "success")
    return redirect(url_for('view_section_students', section_id=section_id)
)

@app.route('/sections/<int:section_id>/bulk_transfer', methods=['POST'])
@login_required(role=['Admin','Instructor'])
def bulk_transfer_section_students(section_id):
    section = Section.query.get_or_404(section_id)
    target_code = request.form.get('target_section_code','').strip().upper()
    student_ids = request.form.getlist('student_ids')
    if not target_code or not student_ids:
        flash("No target or students", "warning")
        return redirect(url_for('view_section_students', section_id=section_id))
    # Check target exists
    target = Section.query.filter_by(section_code=target_code).first()
    if not target:
        flash(f"Section {target_code} not found", "danger")
        return redirect(url_for('view_section_students', section_id=section_id))
    for sid in student_ids:
        Student.query.filter_by(student_id=sid.strip(), section=section.section_code).update({"section": target_code})
    db.session.commit()
    flash(f"Transferred {len(student_ids)} to {target_code}", "success")
    return redirect(url_for('view_section_students', section_id=section_id))

@app.route('/subjects/<int:subject_id>/students')
@app.route('/view_subject_students/<int:subject_id>')
@login_required(role=['Admin', 'Instructor'])
def view_subject_students(subject_id):
    subject = Subject.query.get_or_404(subject_id)
    students = []
    try:
        if hasattr(Student, 'subject_id'):
            students = Student.query.filter_by(subject_id=subject_id).order_by(Student.last_name.asc(), Student.first_name.asc()).all()
        elif hasattr(Student, 'subject'):
            students = Student.query.filter(Student.subject == subject.subject_code).order_by(Student.last_name.asc(), Student.first_name.asc(), Student.student_id.asc()).all()
        else:
            students = Student.query.limit(50).order_by(Student.last_name.asc()).all()
    except Exception as e:
        print(f"Error fetching students for subject {subject_id}: {e}")
        students = []

    # Dedupe enrolled by student_id and sort alphabetically by display name
    seen = {}
    for s in students:
        if s.student_id not in seen:
            seen[s.student_id] = s
    students = sorted(seen.values(), key=lambda x: (x.last_name or '', x.first_name or '', x.student_id))

    try:
        all_students_raw = Student.query.order_by(Student.last_name.asc(), Student.first_name.asc()).all()
        # dedupe for dropdown
        tmp = {}
        for s in all_students_raw:
            if s.student_id not in tmp:
                tmp[s.student_id] = s
        all_students = list(tmp.values())
    except Exception as e:
        print(f"Error fetching all_students: {e}")
        all_students = []

    return render_template(
        'view_subject_students.html',
        subject=subject,
        students=students,
        all_students=all_students,
        current_year="2026-2027"
    )
@app.route('/subjects/<int:subject_id>/remove_student/<student_id>', methods=['POST'])
@login_required(role=['Admin','Instructor'])
def remove_student_from_subject(subject_id, student_id):
    subject = Subject.query.get_or_404(subject_id)
    existing = Student.query.filter_by(student_id=student_id, subject=subject.subject_code).first()
    if not existing:
        flash(f"Student {student_id} not found in {subject.subject_code}", "warning")
        return redirect(url_for('view_subject_students', subject_id=subject_id))
    try:
        db.session.delete(existing)
        db.session.commit()
        flash(f"Removed {existing.display_name or student_id} from {subject.subject_code}", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Failed to remove: {e}", "danger")
    return redirect(url_for('view_subject_students', subject_id=subject_id))

@app.route('/subjects/<int:subject_id>/batch_assign', methods=['POST'])
@login_required(role=['Admin','Instructor'])
def batch_assign_students(subject_id):
    subject = Subject.query.get_or_404(subject_id)
    student_ids = request.form.getlist('student_ids')
    semester = request.form.get('semester') or getattr(subject, 'semester', None) or '1st'
    school_year = request.form.get('school_year') or getattr(subject, 'school_year', None) or f"{datetime.now().year}-{datetime.now().year+1}"
    if not student_ids:
        flash("No students selected", "warning")
        return redirect(url_for('view_subject_students', subject_id=subject_id))
    added=0; skipped=0
    for sid in student_ids:
        sid=sid.strip()
        if not sid: continue
        template_student = Student.query.filter_by(student_id=sid).first()
        if not template_student:
            continue
        existing = Student.query.filter_by(student_id=sid, subject=subject.subject_code).first()
        if existing:
            skipped+=1; continue
        try:
            new_row = Student(
                student_id=template_student.student_id,
                user_id=template_student.user_id,
                last_name=template_student.last_name,
                first_name=template_student.first_name,
                middle_name=template_student.middle_name,
                name=template_student.name or template_student.display_name,
                year=template_student.year,
                section=template_student.section,
                school_year=school_year,
                semester=semester,
                subject=subject.subject_code,
                subject_code=subject.subject_code,
                subject_name=subject.subject_name
            )
            db.session.add(new_row)
            added+=1
        except:
            skipped+=1
    db.session.commit()
    flash(f"Batch assign: {added} added, {skipped} skipped", "success")
    return redirect(url_for('view_subject_students', subject_id=subject_id))

@app.route('/subjects/<int:subject_id>/batch_remove', methods=['POST'])
@login_required(role=['Admin','Instructor'])
def batch_remove_students(subject_id):
    subject = Subject.query.get_or_404(subject_id)
    student_ids = request.form.getlist('student_ids')
    if not student_ids:
        flash("No students selected", "warning")
        return redirect(url_for('view_subject_students', subject_id=subject_id))
    removed=0
    for sid in student_ids:
        existing = Student.query.filter_by(student_id=sid, subject=subject.subject_code).first()
        if existing:
            db.session.delete(existing)
            removed+=1
    db.session.commit()
    flash(f"Removed {removed} student(s) from {subject.subject_code}", "success")
    return redirect(url_for('view_subject_students', subject_id=subject_id))

@app.route('/assign_student_to_subject', methods=['POST'])
@login_required(role=['Admin', 'Instructor'])
def assign_student_to_subject():
    subject_id = request.form.get('subject_id')
    student_id = request.form.get('student_id')
    semester = request.form.get('semester', '1st')
    school_year = request.form.get('school_year', f"{datetime.now().year}-{datetime.now().year+1}")

    if not subject_id or not student_id:
        flash("Subject and Student required", "danger")
        return redirect(url_for('view_subjects'))

    subject = Subject.query.get_or_404(subject_id)
    
    # Template student record for cloning
    template_student = Student.query.filter_by(student_id=student_id).first()
    if not template_student:
        flash(f"Student ID {student_id} not found", "danger")
        return redirect(url_for('view_subjects'))

    # Check if already enrolled in this subject
    existing = Student.query.filter_by(student_id=student_id, subject=subject.subject_code).first()
    if existing:
        existing.semester = semester
        existing.school_year = school_year
        existing.subject_code = subject.subject_code
        existing.subject_name = subject.subject_name
        if hasattr(existing, 'subject'):
            existing.subject = subject.subject_code
        db.session.commit()
        flash(f"✅ {template_student.name} is already in {subject.subject_code}, updated!", "info")
        return redirect(request.referrer or url_for('view_subjects'))

    try:
        # CREATE NEW ROW per subject (because PK is student_id + subject)
        new_enrollment = Student(
            student_id=template_student.student_id,
            user_id=template_student.user_id,
            name=template_student.name,
            year=template_student.year,
            section=template_student.section,
            school_year=school_year,
            semester=semester,
            subject=subject.subject_code,
            subject_code=subject.subject_code,
            subject_name=subject.subject_name
        )
        db.session.add(new_enrollment)
        db.session.commit()
        flash(f"✅ Assigned {new_enrollment.name} ({student_id}) to {subject.subject_code}!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Failed to assign: {e}", "danger")

    return redirect(request.referrer or url_for('view_subjects'))


@app.route('/admin/calendar/add', methods=['POST'])
@login_required(role=['Admin','Instructor'])
def add_calendar_event():
    title = request.form.get('title','').strip()
    date_str = request.form.get('start_date','')
    end_str = request.form.get('end_date','')
    desc = request.form.get('description','')
    if not title or not date_str:
        flash("Title and date required", "danger")
        return redirect(url_for('dashboard_admin'))
    try:
        from datetime import datetime as dt
        start = dt.fromisoformat(date_str) if 'T' in date_str else dt.strptime(date_str, "%Y-%m-%d")
        end = None
        if end_str:
            end = dt.fromisoformat(end_str) if 'T' in end_str else dt.strptime(end_str, "%Y-%m-%d")
        ev = CalendarEvent(title=title, start_date=start, end_date=end, description=desc)
        db.session.add(ev)
        db.session.commit()
        flash(f"✅ Added {title}!", "success")
    except Exception as e:
        flash(f"Error: {e}", "danger")
    return redirect(url_for('dashboard_admin'))

@app.route('/admin/calendar/edit/<int:event_id>', methods=['POST'])
@login_required(role=['Admin','Instructor'])
def edit_calendar_event(event_id):
    ev = CalendarEvent.query.get_or_404(event_id)
    ev.title = request.form.get('title', ev.title)
    date_str = request.form.get('start_date')
    end_str = request.form.get('end_date')
    ev.description = request.form.get('description', ev.description)
    try:
        from datetime import datetime as dt
        if date_str:
            ev.start_date = dt.fromisoformat(date_str) if 'T' in date_str else dt.strptime(date_str, "%Y-%m-%d")
        if end_str:
            ev.end_date = dt.fromisoformat(end_str) if 'T' in end_str else dt.strptime(end_str, "%Y-%m-%d")
        elif request.form.get('clear_end'):
            ev.end_date = None
        db.session.commit()
        flash(f"✅ Updated {ev.title}!", "success")
    except Exception as e:
        flash(f"Error: {e}", "danger")
    return redirect(url_for('dashboard_admin'))

@app.route('/admin/calendar/delete/<int:event_id>', methods=['POST'])
@login_required(role=['Admin','Instructor'])
def delete_calendar_event(event_id):
    ev = CalendarEvent.query.get_or_404(event_id)
    db.session.delete(ev)
    db.session.commit()
    flash(f"🗑️ Deleted {ev.title}", "info")
    return redirect(url_for('dashboard_admin'))

@app.route('/admin/calendar/delete-all', methods=['POST'])
@login_required(role=['Admin','Instructor'])
def delete_all_calendar():
    CalendarEvent.query.delete()
    db.session.commit()
    flash("🗑️ All calendar events deleted", "warning")
    return redirect(url_for('dashboard_admin'))

@app.route('/api/calendar/events')
def api_calendar_events():
    events = CalendarEvent.query.order_by(CalendarEvent.start_date.asc()).all()
    data = []
    for ev in events:
        # Clean title server-side - remove 12a artifacts
        clean_title = ev.title or ""
        import re as _re
        clean_title = _re.sub(r'^\s*\d{1,2}(?::\d{2})?\s*[ap]\s*m?\s*[-–—]*\s*', '', clean_title, flags=_re.I).strip()
        clean_title = _re.sub(r'^\s*\d{1,2}\s*[ap]\s*[-–—]*\s*', '', clean_title, flags=_re.I).strip()
        data.append({
            "id": ev.id,
            "title": clean_title,
            "start": ev.start_date.date().isoformat(),
            "end": ev.end_date.date().isoformat() if ev.end_date else None,
            "description": ev.description or "",
            "allDay": True
        })
    return jsonify(data)

@app.route('/admin/calendar/upload', methods=['POST'])
@app.route('/upload_calendar', methods=['POST'])
@login_required(role=['Admin','Instructor'])
def upload_calendar():
    """Handle PDF/Image upload and manual text - OCR parse dates & events - with 12a cleanup"""
    try:
        manual_text = request.form.get('manual_text','').strip()
        file = request.files.get('calendar_file')
        extracted_text = ""

        if file and file.filename:
            filename = secure_filename(file.filename)
            ext = filename.rsplit('.',1)[-1].lower() if '.' in filename else ''
            temp_path = os.path.join(app.config['UPLOAD_FOLDER'], f"cal_{int(time.time())}_{filename}")
            file.save(temp_path)
            try:
                if ext == 'pdf':
                    try:
                        import fitz
                        doc = fitz.open(temp_path)
                        for page in doc:
                            extracted_text += page.get_text() + "\n"
                    except ImportError:
                        try:
                            import PyPDF2
                            reader = PyPDF2.PdfReader(temp_path)
                            for page in reader.pages:
                                extracted_text += (page.extract_text() or "") + "\n"
                        except:
                            extracted_text = ""
                elif ext in ['png','jpg','jpeg','webp','bmp']:
                    try:
                        import pytesseract
                        from PIL import Image
                        img = Image.open(temp_path)
                        extracted_text = pytesseract.image_to_string(img)
                    except Exception as e:
                        print(f"OCR error: {e}")
                        extracted_text = ""
            finally:
                try:
                    os.remove(temp_path)
                except:
                    pass

        combined_text = (extracted_text + "\n" + manual_text).strip()

        if not combined_text:
            flash("No text found to parse.", "warning")
            return redirect(url_for('dashboard_admin'))

        import re
        from datetime import datetime as dt
        events_created = 0
        lines = [l.strip() for l in combined_text.splitlines() if l.strip()]

        date_patterns = [
            r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}',
            r'\d{1,2}/\d{1,2}/\d{2,4}',
            r'\d{4}-\d{1,2}-\d{1,2}',
            r'\d{1,2}-\d{1,2}-\d{2,4}'
        ]

        for line in lines:
            if len(line) < 5:
                continue
            found_date = None
            for pat in date_patterns:
                m = re.search(pat, line, re.I)
                if m:
                    found_date = m.group(0)
                    break
            if found_date:
                parsed = None
                for fmt in ["%B %d, %Y", "%b %d, %Y", "%b %d %Y", "%B %d %Y", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%m-%d-%Y"]:
                    try:
                        clean = found_date.replace(',', ' ').strip()
                        clean = re.sub(r'\s+', ' ', clean)
                        parsed = dt.strptime(found_date.strip(), fmt)
                        break
                    except:
                        try:
                            parsed = dt.strptime(clean, fmt)
                            break
                        except:
                            continue
                if not parsed:
                    try:
                        from dateutil import parser as dparser
                        parsed = dparser.parse(found_date, fuzzy=True)
                    except:
                        pass

                if parsed:
                    # CLEAN TITLE - remove date AND time artifacts like 12a, 12am, 12:00 am
                    title = re.sub('|'.join(date_patterns), '', line, flags=re.I)
                    title = re.sub(r'\b\d{1,2}(?::\d{2})?\s*[ap]m?\b', '', title, flags=re.I)  # 12a, 12am, 12:00am
                    title = re.sub(r'\b\d{1,2}\s*[ap]\b', '', title, flags=re.I)  # 12a
                    title = title.strip(' -:–—').strip()
                    if not title:
                        title = line[:80]
                    title = title[:190]
                    ev = CalendarEvent(title=title, start_date=parsed, description=line[:500])
                    db.session.add(ev)
                    events_created += 1

        if events_created == 0 and manual_text:
            clean_manual = re.sub(r'^\s*\d{1,2}(?::\d{2})?\s*[ap]\s*m?\s*[-–—]*\s*', '', manual_text, flags=re.I).strip()
            ev = CalendarEvent(title=clean_manual[:190] or "Calendar Event", start_date=datetime.utcnow(), description=manual_text[:500])
            db.session.add(ev)
            events_created = 1

        db.session.commit()

        if events_created > 0:
            flash(f"✅ Added {events_created} event(s)!", "success")
        else:
            flash("⚠️ No dates detected. Use format like 'Midterm Exam - Oct 15, 2024'", "warning")

    except Exception as e:
        import traceback
        traceback.print_exc()
        flash(f"❌ Upload failed: {str(e)}", "danger")

    return redirect(url_for('dashboard_admin'))


@app.route('/edit-subject/<int:subject_id>', methods=['GET', 'POST'])
@login_required(role=['Admin', 'Instructor'])
def edit_subject(subject_id):

    subject = Subject.query.get_or_404(subject_id)

    if request.method == 'POST':

        subject.subject_code = request.form['subject_code']
        subject.subject_name = request.form['subject_name']

        db.session.commit()

        flash('Subject updated successfully.', 'success')

        return redirect(url_for('view_subjects'))

    return render_template(
        'edit_subject.html',
        subject=subject
    )

@app.route('/delete-subject/<int:subject_id>', methods=['POST'])
@login_required(role=['Admin', 'Instructor'])
def delete_subject(subject_id):

    subject = Subject.query.get_or_404(subject_id)

    try:

        # ==========================================
        # DELETE SUBJECT
        # ==========================================
        db.session.delete(subject)

        db.session.commit()

        return redirect(url_for(
            'view_subjects',
            result='delete_success'
        ))

    except Exception as e:

        db.session.rollback()

        print(e)

        return redirect(url_for(
            'view_subjects',
            result='delete_failed'
        ))

# =========================================================
# 2. 🧠 EXAM MANAGEMENT (CREATE / VIEW / EDIT)
# =========================================================
@app.route('/create-exam', methods=['POST'])
@login_required(role=['Admin', 'Instructor'])
def create_exam():

    exam = Exam(
        subject_id=request.form.get('subject_id'),
        title=request.form.get('title'),

        # ✅ NEW
        term=request.form.get('term'),
        exam_type=request.form.get('exam_type'),
        section=request.form.get('section'),
        year=request.form.get('year'),
        school_year=request.form.get('school_year'),
        semester=request.form.get('semester'),

        access_code=request.form.get('access_code'),

        description=request.form.get('description'),
        duration_minutes=request.form.get('duration_minutes')
    )

    db.session.add(exam)
    db.session.commit()

    flash("Exam created successfully", "success")

    return redirect(url_for('add_question', exam_id=exam.id))

# =========================================================
# EDIT EXAM - You accidentally deleted this, let's put it back
# =========================================================
@app.route('/edit-exam/<int:exam_id>', methods=['GET', 'POST'])
@login_required(role=['Admin', 'Instructor'])
def edit_exam(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    subjects = Subject.query.order_by(Subject.subject_name.asc()).all()
    if request.method == 'POST':
        exam.title = request.form.get('title')
        exam.subject_id = request.form.get('subject_id')
        exam.term = request.form.get('term')
        exam.exam_type = request.form.get('exam_type')
        exam.section = request.form.get('section')
        exam.year = request.form.get('year')
        exam.school_year = request.form.get('school_year')
        exam.semester = request.form.get('semester')
        exam.access_code = request.form.get('access_code')
        exam.description = request.form.get('description')
        exam.duration_minutes = int(request.form.get('duration_minutes') or 30)
        db.session.commit()
        flash('Exam updated!', 'success')
        return redirect(url_for('view_exams'))
    return render_template('edit_exam.html', exam=exam, subjects=subjects)

# ========================
# 👁️👁️ VIEW EXAMS
# ========================
@app.route('/view-exams')
@login_required(role=['Admin', 'Instructor'])
@cache.cached(timeout=15)
def view_exams():
    subjects = Subject.query.order_by(Subject.subject_name.asc()).all()
    # Only Active AND Not Archived
    exams = Exam.query.filter_by(is_active=True, is_archived=False).order_by(Exam.created_at.desc()).all()
    access_records = ExamAccess.query.all()
    access_map = {(a.student_id, a.exam_id): a for a in ExamAccess.query.filter(ExamAccess.status != "not_requested").all()}
    exam_students = {}
    score_map = {}
    exam_summary = {}
    for exam in exams:
        students = db.session.query(Student).join(ExamAccess, Student.student_id == ExamAccess.student_id).filter(ExamAccess.exam_id == exam.id, Student.subject_code == exam.subject.subject_code, ExamAccess.status != "not_requested").distinct().all()
        exam_students[exam.id] = students
        summary = {"requested": len(students), "pending": 0, "approved": 0, "completed": 0, "rejected": 0, "forced_submit": 0, "passed": 0, "failed": 0, "average": 0, "highest": 0}
        percentages = []
        total_points = sum(q.points for q in exam.questions)
        for student in students:
            access = access_map.get((student.student_id, exam.id))
            if access:
                if access.status in summary: summary[access.status] += 1
            attempt = ExamAttempt.query.filter_by(student_id=student.student_id, exam_id=exam.id).order_by(ExamAttempt.id.desc()).first()
            score_map[(student.student_id, exam.id)] = {
                "attempt": attempt,
                "is_active_attempt": attempt is not None and not attempt.is_submitted,
                "score": attempt.score if attempt else None,
                "total": total_points,
                "percentage": round((attempt.score / total_points) * 100) if attempt and total_points > 0 else None,
                "submitted_at": attempt.submitted_at + timedelta(hours=8) if attempt and attempt.submitted_at else None
            }
            if score_map[(student.student_id, exam.id)]["percentage"] is not None:
                percentages.append(score_map[(student.student_id, exam.id)]["percentage"])
                if score_map[(student.student_id, exam.id)]["percentage"] >= 70: summary["passed"] += 1
                else: summary["failed"] += 1
        if percentages:
            summary["average"] = round(sum(percentages) / len(percentages), 1)
            summary["highest"] = max(percentages)
        exam_summary[exam.id] = summary
    return render_template('view_exams.html', exams=exams, exam_students=exam_students, access_map=access_map, score_map=score_map, exam_summary=exam_summary, subjects=subjects)

# =========================================================
# FINISHED EXAMS - Closed but Not Archived
# =========================================================
@app.route("/finished-exams")
@login_required(role=["Admin", "Instructor"])
@cache.cached(timeout=15)
def finished_exams():
    subjects = Subject.query.order_by(Subject.subject_name.asc()).all()
    # Only Closed AND Not Archived
    exams = Exam.query.filter_by(is_active=False, is_archived=False).order_by(Exam.created_at.desc()).all()
    access_map = {(a.student_id, a.exam_id): a for a in ExamAccess.query.filter(ExamAccess.status != "not_requested").all()}
    exam_students = {}
    score_map = {}
    exam_summary = {}
    for exam in exams:
        students = db.session.query(Student).join(ExamAccess, Student.student_id == ExamAccess.student_id).filter(ExamAccess.exam_id == exam.id, Student.subject_code == exam.subject.subject_code, ExamAccess.status != "not_requested").distinct().all()
        exam_students[exam.id] = students
        summary = {"requested": len(students), "pending": 0, "approved": 0, "completed": 0, "rejected": 0, "forced_submit": 0, "passed": 0, "failed": 0, "average": 0, "highest": 0}
        percentages = []
        total_points = sum(q.points for q in exam.questions)
        for student in students:
            access = access_map.get((student.student_id, exam.id))
            if access and access.status in summary:
                if access.status == "pending": summary["pending"] += 1
                elif access.status == "approved": summary["approved"] += 1
                elif access.status == "completed": summary["completed"] += 1
                elif access.status == "rejected": summary["rejected"] += 1
                elif access.status == "forced_submit": summary["forced_submit"] += 1
            attempt = ExamAttempt.query.filter_by(student_id=student.student_id, exam_id=exam.id).order_by(ExamAttempt.id.desc()).first()
            percentage = round((attempt.score / total_points) * 100) if attempt and total_points > 0 else None
            score_map[(student.student_id, exam.id)] = {"attempt": attempt, "is_active_attempt": attempt is not None and not attempt.is_submitted, "score": attempt.score if attempt else None, "total": total_points, "percentage": percentage, "submitted_at": attempt.submitted_at + timedelta(hours=8) if attempt and attempt.submitted_at else None}
            if percentage is not None:
                percentages.append(percentage)
                if percentage >= 70: summary["passed"] += 1
                else: summary["failed"] += 1
        if percentages:
            summary["average"] = round(sum(percentages) / len(percentages), 1)
            summary["highest"] = max(percentages)
        exam_summary[exam.id] = summary
    return render_template("finished_exams.html", exams=exams, exam_students=exam_students, access_map=access_map, score_map=score_map, exam_summary=exam_summary, subjects=subjects)

# =========================================================
# EXPORT EXAM SCORES - FIXED FOR VARCHAR student_id + reserved "user" table
# =========================================================
@app.route('/export-scores/<int:exam_id>')
@app.route('/export-exam-scores/<int:exam_id>')
@login_required(role=['Admin', 'Instructor'])
def export_exam_scores(exam_id):

    exam = Exam.query.get_or_404(exam_id)
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([f"Exam: {exam.title}"])
    writer.writerow([f"Subject: {exam.subject.subject_code if exam.subject else ''} - {exam.subject.subject_name if exam.subject else ''}"])
    writer.writerow([f"Term: {exam.term} | Type: {exam.exam_type} | Duration: {exam.duration_minutes} mins"])
    writer.writerow([])
    writer.writerow(['#', 'Student ID', 'Student Name', 'Section', 'Year', 'Score', 'Total Points', 'Percentage', 'Status', 'Submitted At'])

    total_points = 0
    try:
        total_points = sum(q.points for q in exam.questions) if exam.questions else 0
    except:
        pass
    if total_points == 0:
        total_points = 100

    # === CORRECT QUERY FOR YOUR SCHEMA ===
    # Student.student_id (STRING) = ExamAttempt.student_id (STRING)
    # ExamAttempt holds score and submitted_at
    # Student holds name, section, year

    try:
        # Use ORM for reliability - no raw SQL type issues
        attempts = (
            ExamAttempt.query
            .filter_by(exam_id=exam_id, is_submitted=True)
            .order_by(ExamAttempt.submitted_at.desc())
            .all()
        )

        count = 0
        for idx, attempt in enumerate(attempts, 1):
            count += 1
            # Get student record - there may be multiple student rows with same student_id but different subject
            # We want the one matching exam's subject_code if possible
            student_q = Student.query.filter_by(student_id=attempt.student_id)
            if exam.subject:
                student_match = student_q.filter_by(subject_code=exam.subject.subject_code).first()
                if not student_match:
                    student_match = student_q.first()
            else:
                student_match = student_q.first()

            if student_match:
                student_name = student_match.name
                section = student_match.section or ""
                year = student_match.year or ""
                student_id_str = student_match.student_id
            else:
                student_name = f"Student {attempt.student_id}"
                section = ""
                year = ""
                student_id_str = attempt.student_id

            score = attempt.score or 0
            perc = round((score / total_points * 100) if total_points else 0, 2)
            status = "Passed" if perc >= 75 else "Failed"
            submitted = attempt.submitted_at.strftime("%Y-%m-%d %I:%M %p") if attempt.submitted_at else ""

            writer.writerow([idx, student_id_str, student_name, section, year, score, total_points, f"{perc}%", status, submitted])

        if count == 0:
            # Try also including non-submitted but with score, or check ExamAccess for pending
            writer.writerow(['No submitted attempts yet. Checking all access records...'])
            accesses = ExamAccess.query.filter_by(exam_id=exam_id).all()
            if accesses:
                for idx, acc in enumerate(accesses, 1):
                    student = Student.query.filter_by(student_id=acc.student_id).first()
                    name = student.name if student else acc.student_id
                    sec = student.section if student else ""
                    yr = student.year if student else ""
                    # Try to find attempt even if not submitted
                    att = ExamAttempt.query.filter_by(student_id=acc.student_id, exam_id=exam_id).order_by(ExamAttempt.id.desc()).first()
                    score = att.score if att and att.score is not None else 0
                    perc = round((score / total_points * 100) if total_points else 0, 2)
                    submitted = att.submitted_at.strftime("%Y-%m-%d %I:%M %p") if att and att.submitted_at else acc.status
                    writer.writerow([idx, acc.student_id, name, sec, yr, score, total_points, f"{perc}%", acc.status, submitted])
            else:
                writer.writerow(['No students have taken this exam yet - No ExamAccess records'])

    except Exception as e:
        import traceback
        traceback.print_exc()
        writer.writerow([f"Error: {str(e)}"])
        try:
            db.session.rollback()
        except:
            pass

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={exam.title.replace(" ", "_")}_Scores.csv'}
    )

# ===========================
# ARCHIVED EXAMS - New Page
# ===========================
@app.route("/archived-exams")
@login_required(role=["Admin", "Instructor"])
def archived_exams():
    subjects = Subject.query.order_by(Subject.subject_name.asc()).all()
    exams = Exam.query.filter_by(is_archived=True).order_by(Exam.archived_at.desc()).all()
    exam_students = {}
    exam_summary = {}
    access_map = {}
    score_map = {}
    for exam in exams:
        exam_students[exam.id] = []
        exam_summary[exam.id] = {"requested":0,"pending":0,"approved":0,"completed":0,"rejected":0,"forced_submit":0,"passed":0,"failed":0,"average":0,"highest":0}
    return render_template("archived_exams.html", exams=exams, exam_students=exam_students, access_map=access_map, score_map=score_map, exam_summary=exam_summary, subjects=subjects)

# ===========================
# API of Exam Card
# ===========================
@app.route('/api/exam/<int:exam_id>/card')
@login_required(role=['Admin', 'Instructor'])
def api_exam_card(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    total_points = sum(q.points for q in exam.questions)
    return jsonify({
        "id": exam.id,
        "title": exam.title,
        "subject_code": exam.subject.subject_code if exam.subject else "",
        "subject_name": exam.subject.subject_name if exam.subject else "",
        "term": exam.term or "No Term",
        "exam_type": exam.exam_type or "No Type",
        "duration": exam.duration_minutes,
        "is_active": exam.is_active,
        "is_archived": exam.is_archived,
        "description": exam.description or "",
        "total_points": total_points,
        "question_count": len(exam.questions)
    })

# =========================================================
# START / END / ARCHIVE / RESTORE - Seamless (no refresh)
# =========================================================
@app.route('/start-exam/<int:exam_id>', methods=['POST'])
@login_required(role=['Instructor', 'Admin'])
def admin_start_exam(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    exam.is_active = True
    exam.is_archived = False
    db.session.commit()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"success": True, "exam_id": exam.id, "title": exam.title})
    flash("Exam started successfully!", "success")
    return redirect(url_for('view_exams'))

@app.route('/end-exam/<int:exam_id>', methods=['POST'])
@login_required(role=['Instructor', 'Admin'])
def end_exam(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    
    try:
        exam.is_active = False  # move inside try

        # FORCE SUBMIT all taking
        active_attempts = ExamAttempt.query.filter_by(exam_id=exam.id, is_submitted=False).all()
        for attempt in active_attempts:
            try:
                answers = StudentAnswer.query.filter_by(attempt_id=attempt.id).all()
                score = 0
                for ans in answers:
                    q = Question.query.get(ans.question_id)
                    if not q or not ans.selected_answer:
                        continue
                    correct = (q.correct_answer or '').strip()
                    selected = ans.selected_answer.strip()
                    if not correct or not selected:
                        continue
                    
                    if q.question_type == 'identification':
                        is_correct = selected.lower() == correct.lower()
                    else:
                        is_correct = selected.upper() == correct.upper()
                    
                    if is_correct:
                        score += q.points or 1
                        
                attempt.score = score
                attempt.is_submitted = True
                attempt.submitted_at = datetime.utcnow()
                
                acc = ExamAccess.query.filter_by(exam_id=exam.id, student_id=attempt.student_id).first()
                if acc:
                    acc.status = "completed"  # use completed, not submitted, to match your summary
                    acc.submitted_at = datetime.utcnow()
            except Exception as e:
                print(f"Force submit error for attempt {attempt.id}: {e}")
                continue  # don't break whole exam for one bad attempt

        db.session.commit()

        # notify AFTER commit success
        for access in ExamAccess.query.filter_by(exam_id=exam.id).all():
            if str(access.student_id) in sse_events:
                sse_events[access.student_id].append(
                    {"event": "exam_ended", "data": {"exam_id": exam.id}}
                )

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": True, "exam_id": exam.id, "title": exam.title})
        
        flash(f'Exam "{exam.title}" ended successfully!', "success")
        return redirect(url_for('view_exams'))

    except Exception as e:
        db.session.rollback()
        print(f"End exam error: {e}")
        import traceback; traceback.print_exc()
        
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "message": str(e)}), 500
            
        flash(f"Failed to end exam: {e}", "danger")
        return redirect(url_for('view_exams'))

@app.route('/archive-exam/<int:exam_id>', methods=['POST'])
@login_required(role=['Instructor', 'Admin'])
def archive_exam(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    exam.is_archived = True
    exam.is_active = False
    exam.archived_at = datetime.utcnow()
    db.session.commit()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"success": True, "exam_id": exam.id, "title": exam.title})
    flash(f'Exam "{exam.title}" archived.', 'success')
    return redirect(url_for('archived_exams'))

@app.route('/restore-exam/<int:exam_id>', methods=['POST'])
@login_required(role=['Instructor', 'Admin'])
def restore_exam(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    exam.is_archived = False
    exam.archived_at = None
    db.session.commit()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"success": True, "exam_id": exam.id, "title": exam.title})
    flash(f'Exam "{exam.title}" restored.', 'success')
    return redirect(url_for('finished_exams'))

# =========================================================
# 3. ✏️ QUESTION MANAGEMENT - Add Question
# =========================================================

@app.route('/add-question/<int:exam_id>', methods=['GET', 'POST'])
@login_required(role=['Admin', 'Instructor'])
def add_question(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    subjects = Subject.query.order_by(Subject.subject_name.asc()).all()
    import json

    if request.method == 'POST':
        question_types = request.form.getlist('question_type[]')
        questions_text = request.form.getlist('question_text[]')
        points_list = request.form.getlist('points[]')
        correct_answers = request.form.getlist('correct_answer[]')
        identification_answers = request.form.getlist('correct_identification[]')

        choice_letters = [chr(c) for c in range(ord('a'), ord('z')+1)]
        choices_by_letter = {}
        for letter in choice_letters:
            choices_by_letter[letter] = request.form.getlist(f'choice_{letter}[]')

        id_idx = 0
        mcq_idx = 0

        for i in range(len(questions_text)):
            if not questions_text[i].strip():
                continue
            q_type = question_types[i] if i < len(question_types) else 'mcq'
            try:
                points = int(points_list[i]) if i < len(points_list) and points_list[i] else 1
            except:
                points = 1

            if q_type == "identification":
                correct = identification_answers[id_idx] if id_idx < len(identification_answers) else ""
                id_idx += 1
                question = Question(
                    exam_id=exam.id,
                    question_type="identification",
                    question_text=questions_text[i],
                    correct_answer=correct,
                    points=points,
                    is_deleted=False,
                    is_active=True
                )
                db.session.add(question)
            else:
                all_choices = {}
                for letter in choice_letters:
                    lst = choices_by_letter.get(letter, [])
                    val = ""
                    if i < len(lst) and lst[i].strip():
                        val = lst[i]
                    elif mcq_idx < len(lst) and lst[mcq_idx].strip():
                        val = lst[mcq_idx]
                    if val:
                        all_choices[letter.upper()] = val

                choice_a = all_choices.get('A', '')
                choice_b = all_choices.get('B', '')
                choice_c = all_choices.get('C', '')
                choice_d = all_choices.get('D', '')

                correct = ""
                if mcq_idx < len(correct_answers) and correct_answers[mcq_idx].strip():
                    correct = correct_answers[mcq_idx]
                elif i < len(correct_answers):
                    correct = correct_answers[i]

                question = Question(
                    exam_id=exam.id,
                    question_type="mcq",
                    question_text=questions_text[i],
                    choice_a=choice_a,
                    choice_b=choice_b,
                    choice_c=choice_c,
                    choice_d=choice_d,
                    choices_json=json.dumps(all_choices) if all_choices else None,
                    correct_answer=correct,
                    points=points,
                    is_deleted=False,
                    is_active=True
                )
                db.session.add(question)
                mcq_idx += 1

        db.session.commit()
        flash("Questions added successfully!", "success")
        return redirect(url_for('add_question', exam_id=exam.id))

    # Get ALL questions including soft deleted for admin view
    try:
        all_questions = Question.query.filter_by(exam_id=exam_id).order_by(Question.id.asc()).all()
    except:
        all_questions = Question.query.filter_by(exam_id=exam_id).order_by(Question.id.asc()).all()

    # Robust counting - handle NULLs (old data before migration)
    active_questions = []
    hidden_questions = []
    for q in all_questions:
        is_del = getattr(q, 'is_deleted', False)
        is_act = getattr(q, 'is_active', True)
        # Treat None as False/True
        if is_del is None:
            is_del = False
        if is_act is None:
            is_act = True
        if is_del or not is_act:
            hidden_questions.append(q)
        else:
            active_questions.append(q)

    return render_template(
        'add_question.html',
        exam=exam,
        questions=all_questions,
        active_questions=active_questions,
        hidden_questions=hidden_questions,
        active_count=len(active_questions),
        hidden_count=len(hidden_questions),
        subjects=subjects
    )
# =========================
# Edit Exam Question Route
# =========================
@app.route('/edit-question/<int:question_id>', methods=['GET', 'POST'])
@login_required(role=['Admin', 'Instructor'])
def edit_question(question_id):
    import json
    question = Question.query.get_or_404(question_id)

    if request.method == "GET":
        choices = question.choices if hasattr(question, 'choices') else {}
        return jsonify({
            "id": question.id,
            "question_text": question.question_text,
            "choice_a": question.choice_a,
            "choice_b": question.choice_b,
            "choice_c": question.choice_c,
            "choice_d": question.choice_d,
            "choices": choices,
            "choices_json": question.choices_json if hasattr(question, 'choices_json') else None,
            "correct_answer": question.correct_answer,
            "points": question.points,
            "question_type": question.question_type
        })

    if request.method == 'POST' and request.is_json:
        data = request.get_json()
        new_type = data.get('question_type', question.question_type)
        question.question_text = data.get('question_text')
        question.question_type = new_type

        if new_type == 'identification':
            # TRANSFORM TO IDENTIFICATION - clear MCQ choices
            question.choices_json = None
            question.choice_a = None
            question.choice_b = None
            question.choice_c = None
            question.choice_d = None
        else:
            # TRANSFORM TO MCQ - save A-Z choices
            choices = data.get('choices', {})
            if choices:
                question.choices_json = json.dumps(choices)
                question.choice_a = choices.get('A', '') or choices.get('a', '')
                question.choice_b = choices.get('B', '') or choices.get('b', '')
                question.choice_c = choices.get('C', '') or choices.get('c', '')
                question.choice_d = choices.get('D', '') or choices.get('d', '')
                # Store extra choices beyond D in choices_json, already done
            else:
                # Fallback old fields
                question.choice_a = data.get('choice_a')
                question.choice_b = data.get('choice_b')
                question.choice_c = data.get('choice_c')
                question.choice_d = data.get('choice_d')

        question.correct_answer = data.get('correct_answer')
        try:
            question.points = int(data.get('points', 1))
        except:
            question.points = 1
        db.session.commit()
        return jsonify({"success": True, "transformed_to": new_type})

    if request.method == 'POST':
        question.question_text = request.form.get('question_text')
        question.choice_a = request.form.get('choice_a')
        question.choice_b = request.form.get('choice_b')
        question.choice_c = request.form.get('choice_c')
        question.choice_d = request.form.get('choice_d')
        question.correct_answer = request.form.get('correct_answer')
        try:
            question.points = int(request.form.get('points', 1))
        except:
            question.points = 1
        db.session.commit()
        flash("Question updated successfully!", "success")
        return redirect(url_for('add_question', exam_id=question.exam_id))

    return jsonify({"message": "Use AJAX to edit questions."})

# =========================
# Delete Exam Question Route
# =========================
@app.route('/delete-question/<int:question_id>', methods=['POST'])
@login_required(role=['Admin', 'Instructor'])
def delete_question(question_id):
    question = Question.query.get_or_404(question_id)
    data = request.get_json(silent=True) or {}
    mode = data.get('mode', 'soft')  # default soft!
    try:
        if mode == 'hard':
            # HARD DELETE only when explicitly Hard Delete
            StudentAnswer.query.filter_by(question_id=question.id).delete(synchronize_session=False)
            AttemptQuestionOrder.query.filter_by(question_id=question.id).delete(synchronize_session=False)
            db.session.flush()
            db.session.delete(question)
            db.session.commit()
            return jsonify({"success": True, "action": "hard_deleted"})
        else:
            # SOFT DELETE DEFAULT - safe, hides from take_exam.html
            question.is_deleted = True
            question.is_active = False
            question.deleted_at = datetime.utcnow()
            db.session.commit()
            return jsonify({"success": True, "action": "soft_deleted", "message": "Moved to Hidden/Deleted tab"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/restore-question/<int:question_id>', methods=['POST'])
@login_required(role=['Admin', 'Instructor'])
def restore_question(question_id):
    question = Question.query.get_or_404(question_id)
    if hasattr(question, 'is_deleted'):
        question.is_deleted = False
        question.is_active = True
        question.deleted_at = None
        db.session.commit()
        return jsonify({"success": True, "action": "restored"})
    return jsonify({"success": False, "message": "Soft delete not enabled"}), 400

@app.route('/hard-delete-question/<int:question_id>', methods=['POST'])
@login_required(role=['Admin', 'Instructor'])
def hard_delete_question(question_id):
    question = Question.query.get_or_404(question_id)
    StudentAnswer.query.filter_by(question_id=question.id).delete(synchronize_session=False)
    AttemptQuestionOrder.query.filter_by(question_id=question.id).delete(synchronize_session=False)
    db.session.delete(question)
    db.session.commit()
    return jsonify({"success": True, "action": "hard_deleted"})

@app.route('/get-question/<int:question_id>', methods=['GET'])
@login_required(role=['Admin', 'Instructor'])
def get_question_alias(question_id):
    # This fixes your console error GET /get-question/103 404
    # It just calls the real edit function
    return edit_question(question_id)

@app.route('/bulk-soft-delete', methods=['POST'])
@login_required(role=['Admin', 'Instructor'])
def bulk_soft_delete():
    data = request.get_json() or {}
    ids = data.get('ids', [])
    count = 0
    for qid in ids:
        q = Question.query.get(qid)
        if q:
            q.is_deleted = True
            q.is_active = False
            q.deleted_at = datetime.utcnow()
            count += 1
    db.session.commit()
    return jsonify({"success": True, "count": count})

@app.route('/bulk-restore', methods=['POST'])
@login_required(role=['Admin', 'Instructor'])
def bulk_restore():
    data = request.get_json() or {}
    ids = data.get('ids', [])
    count = 0
    for qid in ids:
        q = Question.query.get(qid)
        if q:
            q.is_deleted = False
            q.is_active = True
            q.deleted_at = None
            count += 1
    db.session.commit()
    return jsonify({"success": True, "count": count})

# =========================
# Import Exam Question Route
# =========================
@app.route('/import-questions/<int:exam_id>', methods=['POST'])
@login_required(role=['Admin', 'Instructor'])
def import_questions(exam_id):

    exam = Exam.query.get_or_404(exam_id)

    file = request.files.get('questions_file')

    if not file:
        flash("No file uploaded", "danger")
        return redirect(url_for('add_question', exam_id=exam.id))

    filename = secure_filename(file.filename)

    try:

        df = pd.read_csv(file) if filename.endswith('.csv') else pd.read_excel(file)

        required_columns = [
            'question',
            'choice_a',
            'choice_b',
            'choice_c',
            'choice_d',
            'correct_answer',
            'points'
        ]

        for col in required_columns:
            if col not in df.columns:
                flash(f"Missing column: {col}", "danger")
                return redirect(url_for('add_question', exam_id=exam.id))

        count = 0

        for _, row in df.iterrows():

            existing = Question.query.filter_by(
                exam_id=exam.id,
                question_text=row['question']
            ).first()

            if existing:
                continue

            question = Question(
                exam_id=exam.id,
                question_text=row['question'],
                choice_a=row['choice_a'],
                choice_b=row['choice_b'],
                choice_c=row['choice_c'],
                choice_d=row['choice_d'],
                correct_answer=str(row['correct_answer']).upper(),
                points=int(row['points']) if not pd.isna(row['points']) else 1
            )

            db.session.add(question)
            count += 1

        db.session.commit()

        flash(f"{count} questions imported successfully!", "success")

    except Exception as e:

        db.session.rollback()
        flash(f"Error importing questions: {str(e)}", "danger")

    return redirect(url_for('add_question', exam_id=exam.id))

# =========================
# Export Exam Question Route
# =========================
@app.route('/export-questions/<int:exam_id>')
@login_required(role=['Admin', 'Instructor'])
def export_questions(exam_id):

    exam = Exam.query.get_or_404(exam_id)

    questions = get_exam_questions(exam_id)

    data = [{
        'question': q.question_text,
        'choice_a': q.choice_a,
        'choice_b': q.choice_b,
        'choice_c': q.choice_c,
        'choice_d': q.choice_d,
        'correct_answer': q.correct_answer,
        'points': q.points
    } for q in questions]

    df = pd.DataFrame(data)

    output = io.BytesIO()

    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)

    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name=f"{exam.title}_questions.xlsx",
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@app.route('/export-question-template')
@login_required(role=['Admin', 'Instructor'])
def export_question_template():

    df = pd.DataFrame({

        "question": ["What is 2+2?"],

        "question_type": ["mcq"],

        "choice_a": ["1"],
        "choice_b": ["2"],
        "choice_c": ["3"],
        "choice_d": ["4"],

        "correct_answer": ["4"],

        "points": [1]

    })

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")

    df.to_excel(tmp.name, index=False)

    return send_file(
        tmp.name,
        as_attachment=True,
        download_name="question_template.xlsx"
    )


@app.route('/import-existing-questions/<int:exam_id>', methods=['POST'])
@login_required(role=['Instructor', 'Admin'])
def import_existing_questions(exam_id):
    import json
    selected_ids = []
    if request.is_json:
        data = request.get_json()
        selected_ids = data.get('question_ids', [])
    else:
        selected_ids = request.form.getlist('question_ids') or request.form.getlist('question_ids[]') or request.form.getlist('question_ids[]'.replace('[]',''))

    # also try form with subject flow legacy
    if not selected_ids:
        selected_ids = request.form.getlist('question_ids')

    if not selected_ids:
        if request.is_json:
            return jsonify({"success": False, "message": "No questions selected"})
        flash('No questions selected!', 'warning')
        return redirect(url_for('add_question', exam_id=exam_id))

    count = 0
    for qid in selected_ids:
        try:
            original = Question.query.get(int(qid))
        except:
            continue
        if original:
            new_q = Question(
                exam_id=exam_id,
                question_text=original.question_text,
                question_type=original.question_type,
                choice_a=original.choice_a,
                choice_b=original.choice_b,
                choice_c=original.choice_c,
                choice_d=original.choice_d,
                correct_answer=original.correct_answer,
                points=original.points
            )
            if hasattr(original, 'choices_json') and hasattr(new_q, 'choices_json'):
                new_q.choices_json = original.choices_json
            db.session.add(new_q)
            count += 1

    db.session.commit()

    if request.is_json:
        return jsonify({"success": True, "count": count})

    flash(f'{count} questions imported successfully!', 'success')
    return redirect(url_for('add_question', exam_id=exam_id))


@app.route('/api/questions-by-subject/<int:subject_id>')
@login_required(role=['Admin', 'Instructor'])
def get_questions_by_subject(subject_id):
    import json
    exclude_exam_id = request.args.get('exclude_exam', type=int)
    exams = Exam.query.filter_by(subject_id=subject_id).all()
    exam_ids = [e.id for e in exams]
    if not exam_ids:
        return jsonify({"questions": []})

    query = Question.query.filter(Question.exam_id.in_(exam_ids))
    if exclude_exam_id:
        query = query.filter(Question.exam_id != exclude_exam_id)

    questions = query.order_by(Question.id.desc()).limit(200).all()

    result = []
    for q in questions:
        choices = {}
        if hasattr(q, 'choices_json') and q.choices_json:
            try:
                choices = json.loads(q.choices_json)
            except:
                choices = {}
        if not choices:
            if q.choice_a: choices['A'] = q.choice_a
            if q.choice_b: choices['B'] = q.choice_b
            if q.choice_c: choices['C'] = q.choice_c
            if q.choice_d: choices['D'] = q.choice_d

        exam_title = ""
        for e in exams:
            if e.id == q.exam_id:
                exam_title = e.title
                break

        result.append({
            "id": q.id,
            "question_text": q.question_text,
            "question_type": q.question_type,
            "choices": choices,
            "correct_answer": q.correct_answer,
            "points": q.points,
            "exam_title": exam_title
        })

    return jsonify({"questions": result})

# =========================================================
# 4. 📚 STUDENT EXAM LIST
# =========================================================

@app.route('/student/exam')
@login_required(role=['Admin', 'Instructor', 'Student'])
def student_exams():

    exam = Exam.query.filter_by(is_active=True).all()
    return render_template('student_exams.html', exam=exam)


# =========================================================
# 5. 🚀 EXAM FLOW (START → TAKE → SAVE → SUBMIT)
# =========================================================
@app.route('/exam/<int:exam_id>/start')
@login_required(role=['Admin', 'Instructor', 'Student'])
def start_exam(exam_id):

    student_id = session.get('student_id')

    if not student_id:
        flash("Session expired. Please login again.", "danger")
        return redirect(url_for('login'))

    # 1. Check access
    access = get_exam_access(
        student_id,
        exam_id
    )

    if not access:
        flash("You are not allowed to take this exam.", "danger")
        return redirect(url_for("dashboard_student"))

    # =====================================
    # Remember if this launch is a RESET
    # =====================================
    was_reset = access.is_reset

    # ----------------------------------------
    # Normal students must be approved.
    # Reset students are allowed to continue.
    # ----------------------------------------

    if access.status != "approved" and not access.is_reset:
        flash("You are not allowed to take this exam.", "danger")
        return redirect(url_for("dashboard_student"))

    # 2. Check exam
    exam = Exam.query.get_or_404(exam_id)

    if not exam.is_active:
        flash("Exam is not active yet.", "warning")
        return redirect(url_for('dashboard_student'))

    # 3. Load first question (SINGLE SOURCE OF TRUTH)
    first_question = Question.query.filter_by(
        exam_id=exam.id
    ).order_by(Question.id.asc()).first()

    if not first_question:
        flash("This exam has no questions yet.", "danger")
        return redirect(url_for('dashboard_student'))

    # 4. Find existing attempt (IMPORTANT FIX)
    attempt = ExamAttempt.query.filter_by(
        student_id=student_id,
        exam_id=exam_id
    ).order_by(ExamAttempt.id.desc()).first()

    print("=" * 50)
    print("RESET DEBUG")
    print("Attempt Submitted:", attempt.is_submitted if attempt else None)
    print("Access Status:", access.status)
    print("Access Reset:", access.is_reset)
    print("=" * 50)

    # =====================================
    # Re-open existing attempt after reset
    # =====================================
    if attempt and was_reset:

        now = datetime.utcnow()

        attempt.is_submitted = False
        attempt.submitted_at = None

        attempt.started_at = now
        attempt.end_time = now + timedelta(
            minutes=exam.duration_minutes
        )

        db.session.commit()

    # 5. If already submitted → block
    if attempt and attempt.is_submitted and not access.is_reset:
        flash("You already completed this exam.", "warning")
        return redirect(url_for('dashboard_student'))

    # 6. Create attempt only if missing
    if not attempt:

        now = datetime.utcnow()

        attempt = ExamAttempt(
            student_id=student_id,
            exam_id=exam.id,
            started_at=now,
            end_time=now + timedelta(minutes=exam.duration_minutes),
            is_submitted=False

        )

        db.session.add(attempt)
        db.session.commit()

        access.is_reset = False
        access.reset_at = None

        db.session.commit()

        # ==========================================
        # CREATE RANDOM QUESTION ORDER
        # (Only once per exam attempt)
        # ==========================================

        questions = Question.query.filter_by(
            exam_id=exam.id
        ).all()

        random.shuffle(questions)

        for index, question in enumerate(questions):

            db.session.add(

                AttemptQuestionOrder(

                    attempt_id=attempt.id,

                    question_id=question.id,

                    display_order=index + 1

                )

            )

        db.session.commit()

    # 7. Store session state
    session['attempt_id'] = attempt.id

    # ==============================
    # Notify Admin: Student Started Exam
    # ==============================
    notify_exam_started(access)

    # ======================================
    # Determine where the student should resume
    # ======================================

    if was_reset:

        resume_question = get_first_unanswered_question(attempt.id)

    else:

        resume_question = first_question

    return redirect(url_for(
        "take_exam",
        exam_id=exam.id,
        question_id=resume_question.id
    )

)

#===================================
#===== REQUEST EXAM ROUTE =====
#===================================
@app.route("/request_exam", methods=["POST"])
@login_required(role=['Admin', 'Instructor', 'Student'])
def request_exam():

    print("===== REQUEST EXAM ROUTE HIT =====")

    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    # ==============================
    # GET FORM DATA
    # ==============================
    exam_id = request.form.get("exam_id")
    access_code = request.form.get("access_code")

    if not exam_id:

        if is_ajax:
            return jsonify({
                "success": False,
                "message": "Exam ID missing."
            }), 400

        flash("Exam ID missing.", "danger")
        return redirect(url_for("dashboard_student"))

    # ==============================
    # FIND EXAM
    # ==============================
    exam = Exam.query.get(int(exam_id))

    if not exam:

        return request_exam_response(
            is_ajax,
            False,
            "Exam not found.",
            "danger"
        )

    print("EXAM ID:", exam_id)
    print("ACCESS CODE:", access_code)
    print("SESSION USER ID:", session["user_id"])
    print("EXAM SUBJECT:", exam.subject.subject_code)

    # ==============================
    # Temporary FIND STUDENT
    # ==============================

    print("\n===== STUDENT RECORDS FOR THIS USER =====")

    students = Student.query.filter(
        Student.user_id == session["user_id"]
    ).all()

    for s in students:
        print(
            "Student ID:", s.student_id,
            "| Subject:", s.subject,
            "| Subject Code:", repr(s.subject_code)
        )

    print("========================================")

    # ==============================
    # FIND STUDENT
    # ==============================
    student = Student.query.filter(
        Student.user_id == session["user_id"],
        Student.subject_code == exam.subject.subject_code
    ).first()

    print("REQUEST STUDENT")
    if student is None:
        print("NO MATCHING STUDENT FOUND")
        print("SESSION USER ID:", session["user_id"])
        print("EXAM SUBJECT:", exam.subject.subject_code)

        print("------ STUDENT RECORDS ------")

        students = Student.query.filter(
            Student.user_id == session["user_id"]
        ).all()

        for s in students:
            print(
                s.student_id,
                "|",
                s.subject,
                "|",
                s.subject_code,
                "| user_id:",
                s.user_id
            )

        print("-----------------------------")

        flash("You are not enrolled in this subject.", "danger")
        return redirect(url_for("dashboard_student"))

    print("Student ID:", student.student_id)
    print("Subject Code:", student.subject_code)

    print("REQUEST EXAM")
    print("Exam ID:", exam.id)
    print("Exam Subject:", exam.subject.subject_code)

    print("FOUND STUDENT:", student)

    if not student:

        return request_exam_response(
            is_ajax,
            False,
            "Student record not found.",
            "danger"
        )

    # ==============================
    # VALIDATE SUBJECT
    # ==============================

    if student.subject_code.strip() != exam.subject.subject_code.strip():

        return request_exam_response(
            is_ajax,
            False,
            "This exam is not assigned to your subject.",
            "danger"
        )

    # ==============================
    # VALIDATE ACCESS CODE
    # ==============================
    if exam.access_code:

        if not access_code:
            flash("Access code is required.", "danger")
            return redirect(url_for("dashboard_student"))

        if access_code.strip() != exam.access_code.strip():

            return request_exam_response(
                is_ajax,
                False,
                "Invalid access code.",
                "danger"
            )

    # ==============================
    # CHECK EXISTING REQUEST
    # ==============================
    existing = ExamAccess.query.filter_by(
        student_id=student.student_id,
        exam_id=exam.id
    ).first()

    print("EXISTING:", existing)

    if existing:

        if existing.status == "approved":

            return request_exam_response(
                is_ajax,
                False,
                "You already have access to this exam.",
                "info"
            )

        if existing.status == "pending":

            return request_exam_response(
                is_ajax,
                False,
                "Your request is still pending approval.",
                "warning"
            )

        if existing and existing.status == "not_requested":

            existing.entered_code = access_code
            existing.status = "pending"
            #existing.is_reset = False
            existing.reset_at = None

            db.session.commit()

            if "admin" not in sse_events:
                sse_events["admin"] = []

            sse_events["admin"].append({
                "event": "new_request",
                "data": {
                    "access_id": existing.id,

                    "student_id": str(student.student_id).strip(),
                    "student_name": student.name,
                    "section": student.section,

                    "exam_id": exam.id,
                    "status": "pending",

                    "score": None,
                    "total_points": None,
                    "submitted_at": None
                }
            })

            return request_exam_response(
                is_ajax,
                True,
                "Exam request submitted successfully.",
                "success"
            )

    # ==============================
    # CREATE ACCESS RECORD
    # ==============================
    request_access = ExamAccess(
        student_id=student.student_id,
        exam_id=exam.id,
        entered_code=access_code,
        status="pending"
    )

    db.session.add(request_access)
    db.session.commit()

    print("ACCESS SAVED")

    # ==============================
    # SSE NOTIFICATION
    # ==============================
    if "admin" not in sse_events:
        sse_events["admin"] = []

    sse_events["admin"].append({
        "event": "new_request",
        "data": {
            "access_id": request_access.id,

            "student_id": str(student.student_id).strip(),
            "student_name": student.name,
            "section": student.section,

            "exam_id": exam.id,
            "status": "pending",

            "score": None,
            "total_points": None,
            "submitted_at": None
        }
    })

    print("===== ADMIN SSE QUEUE =====")
    print(sse_events["admin"])
    print("===========================")

    print("ADMIN QUEUE LENGTH:", len(sse_events["admin"]))

    print("SSE EVENT ADDED")

    return request_exam_response(
        is_ajax,
        True,
        "Exam request submitted successfully.",
        "success"
    )

#======================================
#===== CANCEL REQUEST EXAM ROUTE =====
#======================================
@app.route('/cancel-exam-request', methods=['POST'])
@login_required(role=['Student'])
def cancel_exam_request():

    data = request.get_json(silent=True) or {}
    exam_id = data.get("exam_id")
    if not exam_id:
        return jsonify({
            "success": False,
            "message": "Exam ID missing."
        }), 400
    student_id = session.get("student_id")

    access = ExamAccess.query.filter_by(
        exam_id=exam_id,
        student_id=student_id,
        status="pending"
    ).first()

    if not access:
        return jsonify({
            "success": False,
            "message": "No pending request found."
        })

    access_id = access.id
    student_id = access.student_id
    exam_id = access.exam_id

    db.session.delete(access)
    db.session.commit()

    if "admin" not in sse_events:
        sse_events["admin"] = []

    sse_events["admin"].append({
        "event": "live_update",
        "data": {
            "access_id": access_id,
            "student_id": student_id,
            "exam_id": exam_id,
            "status": "not_requested"
        }
    })

    return jsonify({
        "success": True,
        "message": "Exam request cancelled."
    })

#======================================
#===== TAKE EXAM ROUTE =====
#======================================
@app.route('/exam/<int:exam_id>/take/<int:question_id>')
@login_required(role=['Admin', 'Instructor', 'Student'])
def take_exam(exam_id, question_id):

    page_timer = time.perf_counter()
    route_start = time.perf_counter()

    student_id = session.get('student_id')
    attempt_id = session.get('attempt_id')

    if not student_id or not attempt_id:
        flash("Session expired. Please restart the exam.", "danger")
        return redirect(url_for('dashboard_student'))

    attempt = ExamAttempt.query.filter_by(
        id=attempt_id,
        student_id=student_id,
        exam_id=exam_id,
        is_submitted=False,
    ).first()

    if not attempt:
        flash("Invalid or completed exam attempt.", "danger")
        return redirect(url_for('dashboard_student'))

    # ============================
    # Calculate remaining time
    # ============================
    if attempt.end_time:
        remaining_seconds = int(
            (attempt.end_time - datetime.utcnow()).total_seconds()
        )
    else:
        remaining_seconds = 0

    # Don't allow negative values
    if remaining_seconds < 0:
        remaining_seconds = 0

    # Time is up
    if remaining_seconds == 0:
        return redirect(url_for(
            "submit_exam",
            exam_id=exam_id
        ))

    t = time.perf_counter()

    exam = Exam.query.get_or_404(exam_id)

    print(
        "EXAM LOAD:",
        round(time.perf_counter() - t, 4),
        "sec"
    )

    # ==========================================
    # LOAD RANDOMIZED QUESTION ORDER
    # ==========================================

    t = time.perf_counter()

    questions = get_attempt_questions(attempt.id)

    print(
        "QUESTION LOAD:",
        round(time.perf_counter() - t, 4),
        "sec"
    )

    if not questions:

        flash("No questions found.", "danger")

        return redirect(url_for("dashboard_student"))

    # 1. Find index ONCE
    current_index = next(
        (i for i, q in enumerate(questions) if q.id == question_id),
        0
    )

    # 2. Current question
    current_question = questions[current_index]

    # 3. Navigation
    prev_question = questions[current_index - 1] if current_index > 0 else None
    next_question = questions[current_index + 1] if current_index + 1 < len(questions) else None

    # Get student's previously selected answer (if any)
    t = time.perf_counter()

    saved_answer = get_saved_answer(
        attempt_id,
        current_question.id
    )

    print(
        "ANSWER LOAD:",
        round(time.perf_counter() - t, 4),
        "sec"
    )

    # 4. REAL progress (DB-based)
    total_questions = len(questions)

    t = time.perf_counter()

    answer_progress = calculate_exam_progress(
        attempt_id,
        total_questions
    )

    print(
        "PROGRESS LOAD:",
        round(time.perf_counter() - t, 4),
        "sec"
    )

    print(
        f"TAKE_EXAM BUILD TIME: "
        f"{time.perf_counter() - page_timer:.4f} sec"
    )

    t = time.perf_counter()

    response = render_template(
        "take_exam.html",
        exam=exam,
        student_id=student_id,
        question=current_question,
        current_index=current_index + 1,
        total=total_questions,
        progress=answer_progress,
        next_question=next_question,
        prev_question=prev_question,
        remaining_seconds=remaining_seconds,
        saved_answer=saved_answer
    )

    print(
        "RENDER:",
        round(time.perf_counter() - t, 4),
        "sec"
    )

    return response

@app.route('/save-answer', methods=['POST'])
@login_required(role=['Admin', 'Instructor', 'Student'])
def save_answer():

    start = perf_counter()

    data = request.get_json()
    attempt_id = session.get('attempt_id')

    if not attempt_id:
        return jsonify({"error": "No active attempt"}), 400

    answer = StudentAnswer.query.filter_by(
        attempt_id=attempt_id,
        question_id=data['question_id']
    ).first()

    if not answer:
        answer = StudentAnswer(
            attempt_id=attempt_id,
            question_id=data['question_id'],
            selected_answer=data['answer']
        )
        db.session.add(answer)
    else:
        answer.selected_answer = data['answer']

    db.session.commit()

    print(
        f"SAVE ANSWER TIME: {perf_counter() - start:.4f} sec"
    )

    return jsonify({"status": "saved"})

@app.route('/exam/<int:exam_id>/answer', methods=['POST'])
@login_required(role=['Admin', 'Instructor', 'Student'])
def submit_answer(exam_id):

    student_id = session.get('student_id')
    attempt_id = session.get('attempt_id')

    if not student_id or not attempt_id:
        flash("Session expired. Please restart exam.", "danger")
        return redirect(url_for('dashboard_student'))

    # 1. Get attempt (must be active)
    attempt = ExamAttempt.query.filter_by(
        id=attempt_id,
        student_id=student_id,
        exam_id=exam_id,
        is_submitted=False
    ).first()

    if not attempt:
        flash("Invalid exam attempt.", "danger")
        return redirect(url_for('dashboard_student'))

    # 2. Get form data
    question_id = request.form.get('question_id')
    selected_answer = request.form.get('answer')

    if not question_id:
        flash("Invalid question.", "danger")
        return redirect(url_for('dashboard_student'))

    question = Question.query.filter_by(
        id=question_id,
        exam_id=exam_id
    ).first()

    if not question:
        flash("Question not found.", "danger")
        return redirect(url_for('start_exam', exam_id=exam_id))

    # 3. Normalize answer
    selected_answer = (selected_answer or "").strip()

    # For identification questions, use text input instead
    if question.question_type == "identification":
        selected_answer = request.form.get('answer_text', "").strip()

    # 4. Check existing answer (IMPORTANT FIX)
    existing = StudentAnswer.query.filter_by(
        attempt_id=attempt.id,
        question_id=question.id
    ).first()

    if existing:
        existing.selected_answer = selected_answer
    else:
        new_answer = StudentAnswer(
            attempt_id=attempt.id,
            question_id=question.id,
            selected_answer=selected_answer
        )
        db.session.add(new_answer)

    db.session.commit()

    # 5. Load next question
    questions = get_exam_questions(exam_id)

    question_ids = [q.id for q in questions]

    try:
        index = question_ids.index(int(question_id))
    except:
        flash("Navigation error.", "danger")
        return redirect(url_for('dashboard_student'))

    # 6. NEXT QUESTION LOGIC
    if index + 1 < len(question_ids):
        next_id = question_ids[index + 1]
        return redirect(url_for('take_exam', exam_id=exam_id, question_id=next_id))

    # 7. LAST QUESTION → GO TO SUBMIT PAGE
    flash("You reached the last question. Submit your exam.", "info")
    return redirect(url_for('review_exam', exam_id=exam_id))

@app.route('/exam/<int:exam_id>/submit')
@login_required(role=['Admin', 'Instructor', 'Student'])
def submit_exam(exam_id):

    student_id = session.get('student_id')

    if not student_id:
        flash("Session expired.", "danger")
        return redirect(url_for('login'))

    # 1. Get attempt safely
    attempt = ExamAttempt.query.filter_by(
        student_id=student_id,
        exam_id=exam_id,
        is_submitted=False
    ).first()

    if not attempt:
        flash("No active attempt or already submitted.", "danger")
        return redirect(url_for('dashboard_student'))

    # 2. Prevent double submission immediately
    if attempt.is_submitted:
        flash("Exam already submitted.", "warning")

        print("===== SENDING LIVE UPDATE =====")
        print(sse_events.get("admin"))
        print("===============================")

        return redirect(url_for('dashboard_student'))

    # ============================
    # Grade Exam
    # ============================
    score, total_points = grade_attempt(attempt)

    # 7. Mark exam access as completed
    access = update_exam_access(
        student_id,
        exam_id,
        "completed",
        {
            "score": score,
            "total_points": total_points,
            "submitted_at": (
                attempt.submitted_at + timedelta(hours=8)
            ).strftime("%Y-%m-%d %I:%M %p")
        }
    )

    # ==============================
    # Notify Admin Dashboard
    # ==============================
    if access:

        # ==============================
        # BUILD LIVE SUMMARY
        # ==============================

        summary = build_exam_summary(exam_id)

#        notify_exam_completed(
#            access,
#            attempt,
#            score,
#            total_points
#        )

    # 8. Clean session safely
    clear_exam_session()

    flash(f"Exam submitted successfully! Score: {score}", "success")

    print("===== SENDING LIVE UPDATE =====")
    print(sse_events.get("admin"))
    print("===============================")

    return redirect(url_for('dashboard_student'))

# ==========================================================
# REVIEW MODULE
# ==========================================================

@app.route('/review-answers/<int:exam_id>')
@login_required(role=['Admin', 'Instructor', 'Student'])
def review_answers(exam_id):

    student_id = session.get("student_id")
    attempt_id = session.get("attempt_id")

    if not student_id or not attempt_id:
        return jsonify([])

    # ==========================================================
    # Load all questions in this exam
    # ==========================================================

    questions = get_attempt_questions(attempt_id)

    # ==========================================================
    # Load student's saved answers
    # ==========================================================

    answers = StudentAnswer.query.filter_by(
        attempt_id=attempt_id
    ).all()

    # ==========================================================
    # Build review data
    # ==========================================================

    review_data = []

    answer_lookup = {
        answer.question_id: answer
        for answer in answers
    }

    # ==========================================================
    # Build one review item per question
    # ==========================================================

    for index, question in enumerate(questions, start=1):

        saved_answer = answer_lookup.get(question.id)

        review_data.append({

            "question_number": index,

            "question_id": question.id,

            "answered": saved_answer is not None,

            "selected_answer":
                saved_answer.selected_answer if saved_answer else "",

            "url": url_for(
                "take_exam",
                exam_id=exam_id,
                question_id=question.id
            )

        })

    # ==========================================================
    # Return review data as JSON
    # ==========================================================

    return jsonify(review_data)

# =========================================================
# LIVE SECURITY LOGS - FIXED to use ORIGINAL URL
# =========================================================
@app.route('/log-security-event', methods=['POST'])
@login_required(role=['Admin', 'Instructor', 'Student'])
def log_security_event():
    attempt = ExamAttempt.query.get(session.get('attempt_id'))
    if not attempt:
        return jsonify({"success": False, "error": "no_attempt"}), 404

    # FIX: handle both fetch (application/json) and sendBeacon (blob) - Beacon sometimes arrives as raw bytes
    data = None
    try:
        data = request.get_json(force=False, silent=True)
    except:
        data = None
    if not data:
        try:
            # Try raw body as json
            import json as _json
            raw = request.get_data(as_text=True) or ""
            if raw:
                data = _json.loads(raw)
        except:
            data = None
    if not data:
        data = request.form.to_dict() if request.form else {}

    event = (data.get("event") if data else None)
    if not event:
        event = request.args.get("event")
    if not event:
        return jsonify({"success": False, "error": "missing_event"}), 400

    # Normalize: upper + strip
    event = str(event).strip().upper()

    config = SECURITY_EVENTS.get(event)
    if not config:
        # Log unknown for debugging instead of 400, so you see it in DB
        print(f"Unknown security event received: {event}")
        return jsonify({"success": False, "error": f"unknown_event:{event}"}), 400

    security_event = SecurityEvent(
        attempt_id=attempt.id,
        event_type=event,
        description=config["description"],
        severity=config["severity"],
        penalty=config["penalty"],
        source=config["source"]
    )
    db.session.add(security_event)
    attempt.security_score -= config["penalty"]
    attempt.total_violations += 1
    attempt.last_violation = datetime.utcnow()
    attempt.last_violation_type = event
    if attempt.security_score < 0:
        attempt.security_score = 0
    db.session.commit()

    all_events = SecurityEvent.query.filter_by(attempt_id=attempt.id).all()
    highest_type = event
    highest_penalty = config["penalty"]
    for ev in all_events:
        if ev.penalty > highest_penalty:
            highest_penalty = ev.penalty
            highest_type = ev.event_type

    student = Student.query.filter_by(student_id=attempt.student_id).first()
    exam = Exam.query.get(attempt.exam_id)

    if "admin" not in sse_events:
        sse_events["admin"] = []
    # FIX: include exam_id correctly so it goes to correct exam card
    sse_events["admin"].append({
        "event": "security_violation",
        "data": {
            "attempt_id": attempt.id,
            "exam_id": attempt.exam_id,
            "event_id": security_event.id,
            "student_id": attempt.student_id,
            "student_name": student.name if student else attempt.student_id,
            "section": student.section if student else "",
            "exam_title": exam.title if exam else f"Exam {attempt.exam_id}",
            "event_type": event,
            "penalty": config["penalty"],
            "severity": config["severity"],
            "security_score": attempt.security_score,
            "total_violations": attempt.total_violations,
            "highest_penalty_type": highest_type,
            "occurred_at": security_event.occurred_at.strftime("%I:%M:%S %p")
        }
    })

    warning = None
    force_submit = False
    if attempt.security_score <= 0:
        warning = "forced"
        force_submit = True
    elif attempt.security_score <= 40:
        warning = "critical"
    elif attempt.security_score <= 70:
        warning = "warning"

    return jsonify({
        "success": True,
        "security_score": attempt.security_score,
        "warning": warning,
        "force_submit": force_submit
    })

# =========================================================
# SECURITY LOGS - CARD LAYOUT BY EXAM + EXPORT + ARCHIVED
# =========================================================
# LIVE API for security logs - auto updates without F5
@app.route('/api/security_logs_live')
@login_required(role=['Admin', 'Instructor'])
def api_security_logs_live():
    active_attempts = ExamAttempt.query.filter_by(is_submitted=False).all()
    result = []
    for attempt in active_attempts:
        student = Student.query.filter_by(student_id=attempt.student_id).first()
        events = SecurityEvent.query.filter_by(attempt_id=attempt.id).all()
        counts = {'TAB_SWITCH':0,'RIGHT_CLICK':0,'TEXT_SELECTION':0,'COPY':0,'PASTE':0,'CUT':0,'F12':0,'CTRL_U':0,'CTRL_SHIFT_I':0,'FULLSCREEN_EXIT':0}
        for ev in events:
            if ev.event_type in counts:
                counts[ev.event_type] += 1
        highest_type = None
        highest_pen = -1
        for ev in events:
            if ev.penalty > highest_pen:
                highest_pen = ev.penalty
                highest_type = ev.event_type
        result.append({
            'exam_id': attempt.exam_id,
            'attempt_id': attempt.id,
            'student_id': attempt.student_id,
            'student_name': student.name if student else attempt.student_id,
            'section': student.section if student else "",
            'security_score': attempt.security_score,
            'total_violations': attempt.total_violations,
            'last_violation_type': attempt.last_violation_type,
            'highest_penalty_type': highest_type,
            'occurred_at': attempt.last_violation.strftime("%I:%M:%S %p") if attempt.last_violation else "Just now",
            'counts': counts
        })
    return jsonify(result)

@app.route('/security-logs')
@login_required(role=['Admin', 'Instructor'])
def security_logs():
    # Only ACTIVE exams that are being taken - FIXED: compute from actual events to avoid mismatch
    active_attempts = ExamAttempt.query.filter_by(is_submitted=False).all()
    from collections import defaultdict
    grouped = defaultdict(list)
    for attempt in active_attempts:
        grouped[attempt.exam_id].append(attempt)

    exams_data = []
    total_monitored = len(active_attempts)
    total_events = 0

    for exam_id, attempts in grouped.items():
        exam = Exam.query.get(exam_id)
        if not exam or exam.is_archived:
            continue
        students = []
        cheating_count = 0
        for attempt in attempts:
            student = Student.query.filter_by(student_id=attempt.student_id).first()
            events = SecurityEvent.query.filter_by(attempt_id=attempt.id).order_by(SecurityEvent.occurred_at.desc()).all()
            # FIX: case-insensitive counts, handle any variation
            counts = {
                'TAB_SWITCH': 0, 'RIGHT_CLICK': 0, 'TEXT_SELECTION': 0,
                'COPY': 0, 'PASTE': 0, 'CUT': 0,
                'F12': 0, 'CTRL_U': 0, 'CTRL_SHIFT_I': 0, 'FULLSCREEN_EXIT': 0
            }
            total_penalty = 0
            for ev in events:
                et = (ev.event_type or '').strip().upper()
                if et in counts:
                    counts[et] += 1
                else:
                    # try to map close names
                    for k in counts:
                        if k in et or et in k:
                            counts[k] += 1
                            break
                total_penalty += ev.penalty or 0

            # FIX: compute score and total from actual events, not stale attempt fields
            actual_score = max(0, 100 - total_penalty)
            actual_total = len(events)
            last_type = events[0].event_type if events else (attempt.last_violation_type or 'None')
            last_time = events[0].occurred_at.strftime("%I:%M:%S %p") if events else (attempt.last_violation.strftime("%I:%M:%S %p") if attempt.last_violation else "Just now")

            highest_type = None
            highest_pen = -1
            for ev in events:
                if (ev.penalty or 0) > highest_pen:
                    highest_pen = ev.penalty or 0
                    highest_type = ev.event_type

            if actual_score < 100:
                cheating_count += 1
            total_events += actual_total

            students.append({
                'attempt_id': attempt.id,
                'student_id': attempt.student_id,
                'student_name': student.name if student else attempt.student_id,
                'section': student.section if student else "",
                'security_score': actual_score,
                'total_violations': actual_total,
                'last_violation_type': last_type,
                'highest_penalty_type': highest_type,
                'last_occurred': last_time,
                'counts': counts,
                'raw_events': events
            })
        exams_data.append({
            'exam': exam,
            'students': students,
            'cheating_count': cheating_count
        })

    return render_template('security_logs.html',
        exams_data=exams_data,
        total_monitored=total_monitored,
        total_events=total_events
    )

@app.route('/api/security-summary')
@login_required(role=['Admin', 'Instructor'])
@cache.cached(timeout=10)
def api_security_summary():
    # light summary only, no raw_events (saves Neon + phone data)
    recent = SecurityEvent.query.order_by(SecurityEvent.occurred_at.desc()).limit(20).all()
    data = []
    for ev in recent:
        attempt = ExamAttempt.query.get(ev.attempt_id)
        if not attempt: continue
        student = Student.query.filter_by(student_id=attempt.student_id).first()
        exam = Exam.query.get(attempt.exam_id)
        data.append({
            'attempt_id': attempt.id,
            'exam_id': attempt.exam_id,
            'event_id': ev.id,
            'student_id': attempt.student_id,
            'student_name': student.name if student else attempt.student_id,
            'section': student.section if student else '',
            'exam_title': exam.title if exam else '',
            'event_type': ev.event_type,
            'penalty': ev.penalty,
            'security_score': attempt.security_score,
            'total_violations': attempt.total_violations,
            'highest_penalty_type': attempt.last_violation_type,
            'occurred_at': ev.occurred_at.strftime("%I:%M:%S %p") if ev.occurred_at else ''
        })
    return jsonify(data)

@app.route('/security-logs-archived')
@login_required(role=['Admin', 'Instructor'])
def archived_security_logs():
    exams = Exam.query.filter_by(is_archived=True).order_by(Exam.archived_at.desc()).all()
    exam_groups = []
    for exam in exams:
        attempts = ExamAttempt.query.filter_by(exam_id=exam.id).all()
        if not attempts:
            continue
        students_data = []
        for attempt in attempts:
            if attempt.total_violations == 0:
                continue
            student = Student.query.filter_by(student_id=attempt.student_id).first()
            events = SecurityEvent.query.filter_by(attempt_id=attempt.id).order_by(SecurityEvent.occurred_at.desc()).all()
            highest_type = None
            highest_pen = -1
            for ev in events:
                if ev.penalty > highest_pen:
                    highest_pen = ev.penalty
                    highest_type = ev.event_type
            counts = {
                'TAB_SWITCH': 0, 'RIGHT_CLICK': 0, 'TEXT_SELECTION': 0,
                'COPY': 0, 'PASTE': 0, 'CUT': 0,
                'F12': 0, 'CTRL_U': 0, 'CTRL_SHIFT_I': 0, 'FULLSCREEN_EXIT': 0
            }
            for ev in events:
                if ev.event_type in counts:
                    counts[ev.event_type] += 1
            students_data.append({
                'attempt_id': attempt.id,
                'student_id': attempt.student_id,
                'student_name': student.name if student else attempt.student_id,
                'section': student.section if student else "",
                'security_score': attempt.security_score,
                'total_violations': attempt.total_violations,
                'last_violation_type': attempt.last_violation_type,
                'last_occurred': attempt.last_violation.strftime("%I:%M %p") if attempt.last_violation else "",
                'highest_penalty_type': highest_type,
                'counts': counts
            })
        if students_data:
            exam_groups.append({'exam': exam, 'students': students_data})
    return render_template('security_logs_archived.html', exam_groups=exam_groups)

@app.route('/export-security-logs/<int:exam_id>')
@app.route('/export-security-logs-archived/<int:exam_id>')
@login_required(role=['Admin', 'Instructor'])
def export_security_logs(exam_id):

    exam = Exam.query.get_or_404(exam_id)
    attempts = ExamAttempt.query.filter_by(exam_id=exam_id).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([f"Security Logs - Exam: {exam.title}"])
    writer.writerow([f"Subject: {exam.subject.subject_code if exam.subject else ''} - {exam.subject.subject_name if exam.subject else ''}"])
    writer.writerow([f"Exported: {datetime.utcnow().strftime('%Y-%m-%d %I:%M %p')}"])
    writer.writerow([])
    writer.writerow(['#', 'Student ID', 'Student Name', 'Section', 'Security Score', 'Total Violations', 'Last Violation Type', 'Highest Penalty Type', 'TAB', 'R-Click', 'Select', 'Copy', 'Paste', 'Cut', 'F12', 'Ctrl+U', 'Ctrl+Shift+I', 'FullScr', 'Last Occurred'])
    for idx, attempt in enumerate(attempts, 1):
        student = Student.query.filter_by(student_id=attempt.student_id).first()
        events = SecurityEvent.query.filter_by(attempt_id=attempt.id).all()
        counts = {k:0 for k in ['TAB_SWITCH','RIGHT_CLICK','TEXT_SELECTION','COPY','PASTE','CUT','F12','CTRL_U','CTRL_SHIFT_I','FULLSCREEN_EXIT']}
        for ev in events:
            if ev.event_type in counts:
                counts[ev.event_type] += 1
        highest = max(events, key=lambda x: x.penalty).event_type if events else "None"
        writer.writerow([
            idx, attempt.student_id, student.name if student else attempt.student_id,
            student.section if student else "", attempt.security_score,
            attempt.total_violations, attempt.last_violation_type or "None", highest,
            counts['TAB_SWITCH'], counts['RIGHT_CLICK'], counts['TEXT_SELECTION'], counts['COPY'], counts['PASTE'],
            counts['CUT'], counts['F12'], counts['CTRL_U'], counts['CTRL_SHIFT_I'], counts['FULLSCREEN_EXIT'],
            attempt.last_violation.strftime("%Y-%m-%d %I:%M %p") if attempt.last_violation else ""
        ])
    output.seek(0)
    return Response(output.getvalue(), mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={exam.title.replace(" ","_")}_Security_Logs.csv'})

# =========================================================
# 6. 👨‍🏫 ADMIN EXAM CONTROL
# =========================================================

@app.route('/approve-exam-student/<int:exam_id>/<student_id>')
@login_required(role=['Admin', 'Instructor'])
def approve_exam_student(exam_id, student_id):

    student = Student.query.filter_by(student_id=student_id).first()

    if not student:
        flash("Student not found.", "danger")
        return redirect(url_for('view_exams'))

    access = ExamAccess.query.filter_by(
        student_id=student_id,
        exam_id=exam_id
    ).first()

    if access:
        access.status = "approved"
    else:
        db.session.add(ExamAccess(student_id=student_id, exam_id=exam_id, status='approved'))

    db.session.commit()

    flash("Student approved for exam.", "success")
    return redirect(url_for('view_exams'))

# ==================================
# Approve Student Exam Request ROUTE
# ==================================
@app.route('/approve-request/<int:access_id>')
@login_required(role=['Admin', 'Instructor'])
def approve_request(access_id):

    access = ExamAccess.query.get_or_404(access_id)
    access.status = "approved"
    db.session.commit()

    print("🔥 SENDING LIVE UPDATE TO ADMIN")
    # 🔥 SEND UPDATE TO ADMIN STREAM
    sse_events["admin"].append({
        "event": "live_update",
        "data": {
            "access_id": access.id,
            "status": "approved",
            "exam_id": access.exam_id,
            "student_id": access.student_id
        }
    })

    # 🔥 SEND UPDATE TO SPECIFIC STUDENT STREAM
    sse_events[str(access.student_id)].append({
        "event": "approved",
        "data": {
            "exam_id": access.exam_id,
            "status": "approved"
        }
    })

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({
            "success": True,
            "student_id": access.student_id,
            "exam_id": access.exam_id,
            "status": "approved"
        })

    return redirect(url_for("view_exams"))

# ==================================
# Reject / Cancel Request - Admin cancels pending request (NOT retake)
# ==================================
@app.route('/reject-request/<int:access_id>', methods=['GET', 'POST'])
@login_required(role=['Admin', 'Instructor'])
def reject_request(access_id):
    access = ExamAccess.query.get_or_404(access_id)
    access.status = "rejected"
    access.is_reset = False
    access.reset_at = None
    db.session.commit()

    sse_events["admin"].append({
        "event": "live_update",
        "data": {
            "access_id": access.id,
            "status": "rejected",
            "exam_id": access.exam_id,
            "student_id": access.student_id
        }
    })

    sse_events[str(access.student_id)].append({
        "event": "rejected",
        "data": {
            "exam_id": access.exam_id,
            "status": "rejected"
        }
    })

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({
            "success": True,
            "student_id": access.student_id,
            "exam_id": access.exam_id,
            "status": "rejected",
            "message": "Request cancelled."
        })

    return redirect(url_for("view_exams"))

@app.route('/cancel-request-admin', methods=['POST'])
@login_required(role=['Admin', 'Instructor'])
def cancel_request_admin():
    data = request.get_json(silent=True) or {}
    exam_id = data.get("exam_id")
    student_id = data.get("student_id")
    if not exam_id or not student_id:
        return jsonify({"success": False, "message": "Missing data"}), 400

    access = ExamAccess.query.filter_by(exam_id=exam_id, student_id=student_id).first()
    if not access:
        return jsonify({"success": False, "message": "Access not found"}), 404

    if access.status == "pending":
        db.session.delete(access)
        db.session.commit()
        new_status = "not_requested"
    else:
        access.status = "rejected"
        access.is_reset = False
        access.reset_at = None
        db.session.commit()
        new_status = "rejected"

    sse_events["admin"].append({
        "event": "live_update",
        "data": {
            "access_id": access.id if access else 0,
            "status": new_status,
            "exam_id": int(exam_id),
            "student_id": student_id
        }
    })

    sse_events[str(student_id)].append({
        "event": new_status,
        "data": {
            "exam_id": int(exam_id),
            "status": new_status
        }
    })

    return jsonify({
        "success": True,
        "status": new_status,
        "message": "Request cancelled successfully."
    })

# ==================================
# Admin Deletes Exam
# ==================================
@app.route('/delete-exam/<int:exam_id>', methods=['POST'])
@login_required(role=['Admin', 'Instructor'])
def delete_exam(exam_id):

    print("===== DELETE EXAM ROUTE HIT =====")
    print("EXAM ID:", exam_id)

    try:

        # =========================
        # FIND EXAM
        # =========================
        exam = Exam.query.get_or_404(exam_id)

        print("FOUND EXAM:", exam.title)

        # =========================
        # DELETE STUDENT ANSWERS
        # =========================
        attempts = ExamAttempt.query.filter_by(
            exam_id=exam.id
        ).all()

        print("ATTEMPTS FOUND:", len(attempts))

        for attempt in attempts:

            StudentAnswer.query.filter_by(
                attempt_id=attempt.id
            ).delete()

        print("STUDENT ANSWERS DELETED")

        # =========================
        # DELETE EXAM ATTEMPTS
        # =========================
        ExamAttempt.query.filter_by(
            exam_id=exam.id
        ).delete()

        print("EXAM ATTEMPTS DELETED")

        # =========================
        # DELETE EXAM ACCESS
        # =========================
        ExamAccess.query.filter_by(
            exam_id=exam.id
        ).delete()

        print("EXAM ACCESS RECORDS DELETED")

        # =========================
        # DELETE QUESTIONS
        # =========================
        Question.query.filter_by(
            exam_id=exam.id
        ).delete()

        print("QUESTIONS DELETED")

        # =========================
        # DELETE EXAM
        # =========================
        db.session.delete(exam)

        print("EXAM MARKED FOR DELETION")

        # =========================
        # COMMIT
        # =========================
        db.session.commit()

        print("DELETE COMMITTED SUCCESSFULLY")

        flash(
            f'Exam "{exam.title}" deleted successfully.',
            'success'
        )

    except Exception as e:

        db.session.rollback()

        print("DELETE ERROR:", str(e))

        flash(
            f"Delete failed: {str(e)}",
            "danger"
        )

    return redirect(url_for('view_exams'))

@app.route('/student-exam-status')
@login_required(role=['Student'])
def student_exam_status():
    # FIXED: Returns ALL active exams for enrolled subjects, not just those with ExamAccess
    student_id = session.get('student_id')
    if not student_id:
        return jsonify([])

    student = Student.query.filter_by(student_id=student_id).first()
    if not student:
        return jsonify([])

    # Get enrolled subject codes - strip and normalize
    all_enrollments = Student.query.filter_by(student_id=student_id).all()
    enrolled_codes = []
    for s in all_enrollments:
        if s.subject_code:
            enrolled_codes.append(s.subject_code.strip().upper())
            enrolled_codes.append(s.subject_code.strip())  # keep original case too

    # Deduplicate
    enrolled_codes = list(set([c for c in enrolled_codes if c]))
    print(f"[REFRESH] Student {student_id} enrolled codes: {enrolled_codes}")

    if enrolled_codes:
        # Use case-insensitive comparison by uppercasing Subject codes in query
        # Fetch active exams for enrolled subjects
        active_exams = Exam.query.join(Subject).filter(
            Exam.is_active==True,
            func.upper(Subject.subject_code).in_([c.upper() for c in enrolled_codes])
        ).all()
    else:
        active_exams = []
        print(f"[REFRESH] No enrolled codes for {student_id} - returning empty")

    result = []
    for exam in active_exams:
        access = ExamAccess.query.filter_by(
            student_id=student_id,
            exam_id=exam.id
        ).first()

        status = access.status if access else "not_requested"

        result.append({
            "exam_id": exam.id,
            "status": status,
            "title": exam.title,
            "subject": exam.subject.subject_code if exam.subject else "N/A",
            "description": exam.description or "",
            "duration": exam.duration_minutes or 0,
            "exam_type": exam.exam_type or "",
            "term": exam.term or "",
            "question_count": len(exam.questions) if exam.questions else 0,
            "start_url": url_for("start_exam", exam_id=exam.id)
        })

    print(f"[REFRESH] Returning {len(result)} exams for {student_id}")
    return jsonify(result)

@app.route('/student-dashboard-data')
@login_required(role=['Student'])
def student_dashboard_data():

    student_id = session.get("student_id")

    accesses = ExamAccess.query.filter_by(
        student_id=student_id
    ).all()

    result = []

    for access in accesses:

        exam = Exam.query.get(access.exam_id)

        if not exam:
            continue

        result.append({

            "exam_id": exam.id,
            "title": exam.title,
            "description": exam.description,
            "duration": exam.duration_minutes,
            "question_count": len(exam.questions),
            "exam_type": exam.exam_type,
            "term": exam.term,
            "subject": exam.subject.subject_code if exam.subject else "",
            "status": access.status,
            "start_url": url_for(
                "start_exam",
                exam_id=exam.id
            )

        })

    return jsonify(result)

# =========================
# RESET EXAM OUTE
# =========================
@app.route("/reset-exam", methods=["POST"])
@login_required(role=["Admin"])
def reset_exam():
    data = request.get_json()
    exam_id = data.get("exam_id")
    student_id = data.get("student_id")

    access = ExamAccess.query.filter_by(exam_id=exam_id, student_id=student_id).first()
    if not access:
        return jsonify({"success": False, "message": "Exam access not found."})

    access.status = "approved"
    access.is_reset = True
    access.reset_at = datetime.utcnow()

    attempt = get_latest_attempt(student_id, exam_id)
    if attempt:
        try:
            # DELETE logs so security_logs.html clears automatically
            SecurityEvent.query.filter_by(attempt_id=attempt.id).delete(synchronize_session=False)
            SecurityLog.query.filter_by(attempt_id=attempt.id).delete(synchronize_session=False)
            StudentAnswer.query.filter_by(attempt_id=attempt.id).delete(synchronize_session=False)
        except:
            pass
        attempt.security_score = 100
        attempt.total_violations = 0
        attempt.last_violation = None
        attempt.last_violation_type = None
    db.session.commit()

    access = ExamAccess.query.filter_by(
        exam_id=exam_id,
        student_id=student_id
    ).first()

    print("=" * 50)
    print("AFTER RESET")
    print("Status:", access.status)
    print("is_reset:", access.is_reset)
    print("reset_at:", access.reset_at)
    print("=" * 50)

    return jsonify({
        "success": True,
        "message": "Exam has been reset. Student may immediately retake the exam."
    })

# =========================
# FORCE SUBMIT ROUTE
# =========================

@app.route("/force-submit", methods=["POST"])
@login_required(role=["Admin"])
def force_submit():

    data = request.get_json()

    exam_id = data.get("exam_id")
    student_id = data.get("student_id")

    print("=" * 60)
    print("FORCE SUBMIT REQUEST RECEIVED")
    print("Exam ID:", exam_id)
    print("Student ID:", student_id)
    print("=" * 60)

    # ------------------------------------
    # Find latest attempt
    # ------------------------------------

    attempt = get_latest_attempt(
        student_id,
        exam_id
    )

    if not attempt or attempt.is_submitted:

        return jsonify({
            "success": False,
            "message": "No active exam attempt found."
        })

    # ------------------------------------
    # Grade current answers
    # ------------------------------------

    score, total_points = grade_attempt(attempt)

    db.session.commit()

    # ------------------------------------
    # Update Exam Access
    # ------------------------------------

    update_exam_access(
        student_id,
        exam_id,
        "forced_submit",
        {
            "score": score,
            "total_points": total_points,
            "submitted_at": (
                attempt.submitted_at + timedelta(hours=8)
            ).strftime("%Y-%m-%d %I:%M %p")
        }
    )

    # ------------------------------------
    # Notify Admin Dashboard
    # ------------------------------------

    if "admin" not in sse_events:
        sse_events["admin"] = []

    sse_events["admin"].append({

        "event": "live_update",

        "data": {

            "student_id": student_id,
            "exam_id": exam_id,

            "status": "forced_submit",

            "score": score,
            "total_points": total_points,

            "submitted_at": (
                attempt.submitted_at + timedelta(hours=8)
            ).strftime("%Y-%m-%d %I:%M %p"),

            "summary": build_exam_summary(exam_id)

        }

    })

    access = get_exam_access(
        student_id,
        exam_id
    )

    print("=" * 60)
    print("AFTER FORCE SUBMIT")
    print("Student:", student_id)
    print("Exam:", exam_id)
    print("Access Status:", access.status)
    print("Reset Flag:", access.is_reset)
    print("=" * 60)

    # ------------------------------------
    # Notify Student (SSE)
    # ------------------------------------

    if str(student_id) not in sse_events:
        sse_events[str(student_id)] = []

    sse_events[str(student_id)].append({

        "event": "force_submit",

        "data": {

            "exam_id": exam_id,

            "message": "Your examination has been force submitted by your instructor."

        }

    })

    print("CURRENT SSE DICTIONARY")
    print(sse_events)
    print("QUEUE FOR STUDENT")
    print(sse_events.get(str(student_id)))
    print("Queue after append:")
    print(sse_events)
    print("🔥 FORCE SUBMIT EVENT SENT TO STUDENT:", student_id)

    return jsonify({
        "success": True,
        "message": "Student has been force submitted."
    })

@app.route("/exam/check-force-submit")
@login_required(role=["Student"])
def check_force_submit():

    attempt_id = session.get("attempt_id")

    print("CHECK:", attempt_id)

    if not attempt_id:
        print("No attempt")
        return jsonify({"force_submit": False})

    attempt = ExamAttempt.query.get(attempt_id)

    if not attempt:
        return jsonify({"force_submit": False})

    access = ExamAccess.query.filter_by(
        exam_id=attempt.exam_id,
        student_id=attempt.student_id
    ).first()

    print("ACCESS:", access)

    if access:
        print("STATUS:", access.status)

    if not access:
        return jsonify({"force_submit": False})

    return jsonify({
        "force_submit": access.status == "forced_submit"
    })

# =========================================================
# ===== REVIEW STUDENT ATTEMPT ============================
# =========================================================
@app.route('/review-attempt/<int:exam_id>/<student_id>')
@login_required(role=['Admin', 'Instructor'])
def review_attempt(exam_id, student_id):

    return f"""
    <h2>Review Attempt</h2>

    <p>Exam ID: {exam_id}</p>

    <p>Student ID: {student_id}</p>
    """

# =========================================================
# ===== REVIEW ATTEMPT DATA ===============================
# =========================================================
@app.route('/review-attempt-data/<int:exam_id>/<student_id>')
@login_required(role=['Admin', 'Instructor'])
def review_attempt_data(exam_id, student_id):

    student = Student.query.filter_by(
        student_id=student_id
    ).first_or_404()

    exam = Exam.query.get_or_404(exam_id)

    attempt = (
        ExamAttempt.query
        .filter_by(
            student_id=student_id,
            exam_id=exam_id
        )
        .order_by(
            ExamAttempt.id.desc()
        )
        .first()
    )

    answers = []

    if attempt:

        answers = (
            StudentAnswer.query
            .filter_by(
                attempt_id=attempt.id
            )
            .all()
        )

        questions = (
            Question.query
            .filter_by(exam_id=exam_id)
            .order_by(Question.id.asc())
            .all()
        )

        answer_map = {
            answer.question_id: answer
            for answer in answers
        }

        review_questions = []

        for index, question in enumerate(questions, start=1):

            answer = answer_map.get(question.id)

            student_answer = ""

            if answer:
                student_answer = answer.selected_answer or ""

            review_questions.append({

                "number": index,

                "question_id": question.id,

                "question": question.question_text,

                "student_answer": student_answer,

                "correct_answer": question.correct_answer,

                "points": question.points,

                "answered": bool(student_answer),

                "is_correct": (
                    answer.is_correct
                    if answer
                    else False
                )

            })

    total_questions = len(review_questions)

    answered_questions = sum(
        1
        for q in review_questions
        if q["answered"]
    )

    correct_answers = sum(
        1
        for q in review_questions
        if q["is_correct"]
    )

    wrong_answers = sum(
        1
        for q in review_questions
        if q["answered"] and not q["is_correct"]
    )

    unanswered_questions = (
        total_questions - answered_questions
    )

    progress = (
        round(
            (answered_questions / total_questions) * 100
        )
        if total_questions
        else 0
    )

    return jsonify({

        "student_name": student.name,

        "student_id": student.student_id,

        "section": student.section,

        "subject": exam.subject.subject_code
                   if exam.subject else "",

        "exam_title": exam.title,

        "exam_type": exam.exam_type,

        "answers_count": len(answers),

        "questions": review_questions,

        "progress": {

            "percent": progress,

            "answered": answered_questions,

            "correct": correct_answers,

            "wrong": wrong_answers,

            "unanswered": unanswered_questions,

            "total": total_questions

        }

    })

# =========================================================
# ===== FULL REVIEW PAGE ==================================
# =========================================================

@app.route('/review-report/<int:exam_id>/<student_id>')
@login_required(role=['Admin', 'Instructor'])
def review_report(exam_id, student_id):

    student = Student.query.filter_by(
        student_id=student_id
    ).first_or_404()

    exam = Exam.query.get_or_404(exam_id)

    attempt = (
        ExamAttempt.query
        .filter_by(
            student_id=student_id,
            exam_id=exam_id
        )
        .order_by(
            ExamAttempt.id.desc()
        )
        .first()
    )

    questions = (
        Question.query
        .filter_by(exam_id=exam_id)
        .all()
    )

    answers = []

    if attempt:

        answers = (
            StudentAnswer.query
            .filter_by(
                attempt_id=attempt.id
            )
            .all()
        )

    answer_map = {
        answer.question_id: answer
        for answer in answers
    }

    question_review = []

    for index, question in enumerate(questions, start=1):

        answer = answer_map.get(question.id)

        student_answer = ""

        if answer:
            student_answer = answer.selected_answer or ""

        question_review.append({

            "number": index,

            "question": question.question_text,

            "student_answer": student_answer,

            "correct_answer": question.correct_answer,

            "answered": bool(student_answer),

            "is_correct": (
                answer.is_correct
                if answer
                else False
            ),

            "points": question.points,

            "earned_points": (
                question.points
                if answer and answer.is_correct
                else 0
            )

        })

    total_questions = len(questions)

    answered = len(answer_map)

    correct = sum(
        1
        for answer in answers
        if answer.is_correct
    )

    wrong = answered - correct

    unanswered = total_questions - answered

    progress = (
        round(answered / total_questions * 100)
        if total_questions
        else 0
    )

    return render_template(
        "review_report.html",

        student=student,

        exam=exam,

        attempt=attempt,

        progress=progress,

        answered=answered,

        correct=correct,

        wrong=wrong,

        unanswered=unanswered,

        question_review=question_review

    )

# =========================
# SSE ROUTE
# =========================
@app.route('/stream/<user_id>')
@login_required(role=['Admin', 'Instructor', 'Student'])
def stream(user_id):

    def event_stream():

        while True:

            if user_id in sse_events and sse_events[user_id]:

                event = sse_events[user_id].pop(0)

                print(f"➡ Sending to {user_id}: {event}")
                print("QUEUE LENGTH AFTER POP:", len(sse_events[user_id]))

                yield (
                    f"event: {event['event']}\n"
                    f"data: {json.dumps(event['data'])}\n\n"
                )

                print("===== SENDING RAW SSE =====")
                print(
                    f"event: {event['event']}\n"
                    f"data: {json.dumps(event['data'])}\n"
                )

            else:
                # heartbeat
                yield "data: ping\n\n"

            time.sleep(1)

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no"
        }
    )

# =========================================================
# 7. 📊 STATUS API
# =========================================================

@app.route("/exam-status")
@login_required(role=['Admin', 'Instructor'])
def exam_status():
    return jsonify({
        "active": Exam.query.filter_by(is_active=True).count(),
        "ongoing": ExamAttempt.query.filter_by(is_submitted=False).count()
    })

# =========================
# End of Exam Routes
# =========================

# ---- Student Grades ----
@app.route('/student/grades/<student_id>')
@login_required(role=['Admin', 'Instructor', 'Student'])
def student_grades(student_id):
    """
    Fetch all records for a given student I number (student_id).
    Display grades as stored in the database (no computation).
    """
    # Ensure student_id is a string and stripped of extra spaces
    student_id = str(student_id).strip()

    # Fetch all student records that match this student_id (I number)
    students = Student.query.filter_by(student_id=student_id).all()

    if not students:
        flash(f"No records found for student ID {student_id}", "warning")
        # Redirect based on user role
        role = session.get('role')
        if role == 'Student':
            return redirect(url_for('dashboard_student'))
        elif role == 'Instructor':
            return redirect(url_for('dashboard_instructor'))
        else:
            return redirect(url_for('dashboard_admin'))

    # Render template with all subjects for this student
    # No computation; display stored quiz, exam, and overall grades
    return render_template(
        'student_grades.html',
        students=students,
        student_name=students[0].name  # Use first record's name
    )

# ---- Subject Performance ----
@app.route('/student/performance/<student_id>/<subject>')
@login_required(role=['Student','Admin','Instructor'])
def subject_performance(student_id, subject):

    subject = unquote(subject)  # decode URL

    student_records = Student.query.filter_by(
        student_id=student_id,
        subject=subject
    ).first_or_404()

    record = student_records

    performance = {
        "subject": record.subject,

        # MIDTERM QUIZZES
        "midterm_quiz1": record.midterm_quiz1,
        "midterm_quiz2": record.midterm_quiz2,
        "midterm_quiz3": record.midterm_quiz3,
        "midterm_quiz4": record.midterm_quiz4,

        # EXERCISE QUIZZES
        "midterm_e_quiz1": record.midterm_e_quiz1,
        "midterm_e_quiz2": record.midterm_e_quiz2,
        "midterm_e_quiz3": record.midterm_e_quiz3,
        "midterm_e_quiz4": record.midterm_e_quiz4,

        # LAB QUIZZES
        "midterm_l_quiz1": record.midterm_l_quiz1,
        "midterm_l_quiz2": record.midterm_l_quiz2,
        "midterm_l_quiz3": record.midterm_l_quiz3,
        "midterm_l_quiz4": record.midterm_l_quiz4,

        # PIT
        "pit": [
            record.midterm_pit1, record.midterm_pit2,
            record.midterm_pit3, record.midterm_pit4,
            record.final_pit1, record.final_pit2,
            record.final_pit3, record.final_pit4
        ],

        # LABORATORY
        "laboratory": [
            record.midterm_laboratory1, record.midterm_laboratory2,
            record.midterm_laboratory3, record.midterm_laboratory4,
            record.final_laboratory1, record.final_laboratory2,
            record.final_laboratory3, record.final_laboratory4
        ],

        # EXERCISES
        "exercises": [
            record.midterm_exercise1, record.midterm_exercise2,
            record.midterm_exercise3, record.midterm_exercise4,
            record.final_exercise1, record.final_exercise2,
            record.final_exercise3, record.final_exercise4
        ],

        # EXAMS
        "exams": {
            "midterm": record.midterm_exam,
            "final": record.final_exam
        },

        # GRADES
        "grades": {
            "midterm": record.midterm_grade,
            "final": record.final_grade
        },

        # REMARKS
        "remarks": {
            "midterm": record.midterm_remarks,
            "final": record.final_remarks
        }
    }

    return render_template(
        "subject_performance.html",
        student=record,
        performance=performance
    )

# View Student (Admin)
@app.route('/admin/view_student/<student_id>')
@login_required(role=['Admin','Instructor'])
def admin_view_student(student_id):
    return redirect(url_for('dashboard_student', student_id=student_id))

# View Logs (Admin)
@app.route('/view_logs')
@login_required(role='Admin')
def view_logs():
    logs = Log.query.order_by(Log.timestamp.desc()).all()
    logs_with_student_name = []
    for log in logs:
        student = Student.query.filter_by(student_id=log.user).first()
        student_name = student.name if student else None
        logs_with_student_name.append({
            'id': log.id,
            'student_name': student_name,
            'user': log.user,
            'action': log.action,
            'timestamp': log.timestamp
        })
    return render_template('logs.html', logs=logs_with_student_name)

# Bulk Delete Logs (Admin)
@app.route('/logs/bulk_delete', methods=['POST'])
@login_required(role='Admin')
def bulk_delete_logs():
    log_ids = request.form.getlist('log_ids')
    if log_ids:
        deleted_logs = []
        for lid in log_ids:
            log = Log.query.get(int(lid))
            if log:
                deleted_logs.append(f"{log.user} - {log.action}")
                db.session.delete(log)
        db.session.commit()
        add_log(session['username'], f'Bulk deleted logs: {", ".join(deleted_logs)}')
        flash(f'{len(log_ids)} log(s) deleted successfully!', 'success')
    else:
        flash('No logs selected for deletion.', 'warning')
    return redirect(url_for('view_logs'))

# Create User (Admin)
@app.route('/dashboard/admin/create_user', methods=['GET','POST'])
@login_required(role='Admin')
def create_user():
    form = CreateUserForm()
    if form.validate_on_submit():
        if User.query.filter_by(username=form.username.data).first():
            flash('Username already exists!', 'danger')
        else:
            hashed_password = generate_password_hash(form.password.data)
            db.session.add(User(username=form.username.data, password=hashed_password, role=form.role.data))
            db.session.commit()
            add_log(session['username'], f'Created {form.role.data} account: {form.username.data}')
            flash(f'{form.role.data} account created successfully!', 'success')
            return redirect(url_for('dashboard_admin'))
    return render_template('create_user.html', form=form)

# View Instructors (Admin)
@app.route('/dashboard/admin/instructors')
@login_required(role='Admin')
def view_instructors():
    instructors = User.query.filter_by(role='Instructor').all()
    return render_template('instructors.html', instructors=instructors)

# Add/Edit/Delete Instructors
@app.route('/dashboard/admin/instructors/add', methods=['GET','POST'])
@login_required(role='Admin')
def add_instructor():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if User.query.filter_by(username=username).first():
            flash('Username already exists!', 'danger')
            return redirect(url_for('add_instructor'))
        hashed_password = generate_password_hash(password)
        db.session.add(User(username=username, password=hashed_password, role='Instructor'))
        db.session.commit()
        add_log(session['username'], f'Added Instructor: {username}')
        flash('Instructor added successfully!', 'success')
        return redirect(url_for('view_instructors'))
    return render_template('add_instructor.html')

@app.route('/dashboard/admin/instructors/edit/<int:id>', methods=['GET','POST'])
@login_required(role='Admin')
def edit_instructor(id):
    instructor = User.query.get_or_404(id)
    if request.method == 'POST':
        old_username = instructor.username
        instructor.username = request.form['username']
        if request.form['password']:
            instructor.password = generate_password_hash(request.form['password'])
        db.session.commit()
        add_log(session['username'], f'Edited Instructor: {old_username} -> {instructor.username}')
        flash('Instructor updated successfully!', 'success')
        return redirect(url_for('view_instructors'))
    return render_template('edit_instructor.html', instructor=instructor)

@app.route('/dashboard/admin/instructors/delete/<int:id>', methods=['POST'])
@login_required(role='Admin')
def delete_instructor(id):
    instructor = User.query.get_or_404(id)
    db.session.delete(instructor)
    db.session.commit()
    add_log(session['username'], f'Deleted Instructor: {instructor.username}')
    flash('Instructor deleted successfully!', 'success')
    return redirect(url_for('view_instructors'))

@app.route('/dashboard/admin/instructors/bulk_delete', methods=['POST'])
@login_required(role='Admin')
def bulk_delete_instructors():
    instructor_ids = request.form.getlist('instructor_ids')
    if instructor_ids:
        deleted_usernames = []
        for iid in instructor_ids:
            instructor = User.query.get(int(iid))
            if instructor and instructor.role == 'Instructor':
                deleted_usernames.append(instructor.username)
                db.session.delete(instructor)
        db.session.commit()
        add_log(session['username'], f'Bulk deleted Instructors: {", ".join(deleted_usernames)}')
        flash(f'{len(instructor_ids)} instructor(s) deleted successfully!', 'success')
    else:
        flash('No instructors selected for deletion.', 'warning')
    return redirect(url_for('view_instructors'))

@app.route('/attendance/<student_id>/<subject>', methods=['GET', 'POST'])
@login_required(role=['Admin','Instructor','Student'])
def attendance_page(student_id, subject):


    subject = unquote(subject)

    student = Student.query.filter_by(
        student_id=student_id,
        subject=subject
    ).first_or_404()

    # 🔥 SAVE DATA (POST)
    if request.method == 'POST':

        # 🚫 BLOCK STUDENTS FROM EDITING
        if session.get('role') not in ['Admin', 'Instructor']:
            flash("Not allowed to edit.", "danger")
            return redirect(request.url)

        # UPDATE ATTENDANCE
        student.midterm_attendance1 = request.form.get('midterm_attendance1')
        student.midterm_attendance2 = request.form.get('midterm_attendance2')
        student.midterm_attendance3 = request.form.get('midterm_attendance3')
        student.midterm_attendance4 = request.form.get('midterm_attendance4')

        student.final_attendance1 = request.form.get('final_attendance1')
        student.final_attendance2 = request.form.get('final_attendance2')
        student.final_attendance3 = request.form.get('final_attendance3')
        student.final_attendance4 = request.form.get('final_attendance4')

        db.session.commit()

        flash("Attendance updated successfully!", "success")

        return redirect(url_for(
            'attendance_page',
            student_id=student.student_id,
            subject=student.subject,
            saved=1
        ))

    # 🔵 GET REQUEST
    saved = request.args.get('saved')

    return render_template(
        "attendance.html",
        student=student,
        saved=saved
    )

@app.route('/quizzes/<student_id>/<subject>', methods=['GET', 'POST'])
@login_required(role=['Student','Admin','Instructor'])
def quizzes_page(student_id, subject):

    subject = unquote(subject)

    student = Student.query.filter_by(
        student_id=student_id,
        subject=subject
    ).first_or_404()

    # 🔥 SAVE CHANGES
    if request.method == 'POST':

        # 🚫 only Admin / Instructor can edit
        if session.get('role') not in ['Admin', 'Instructor']:
            flash("Not allowed to edit quizzes.", "danger")
            return redirect(request.url)

        # MIDTERM QUIZZES
        student.midterm_quiz1 = request.form.get('midterm_quiz1')
        student.midterm_quiz2 = request.form.get('midterm_quiz2')
        student.midterm_quiz3 = request.form.get('midterm_quiz3')
        student.midterm_quiz4 = request.form.get('midterm_quiz4')

        student.midterm_e_quiz1 = request.form.get('midterm_e_quiz1')
        student.midterm_e_quiz2 = request.form.get('midterm_e_quiz2')
        student.midterm_e_quiz3 = request.form.get('midterm_e_quiz3')
        student.midterm_e_quiz4 = request.form.get('midterm_e_quiz4')

        student.midterm_l_quiz1 = request.form.get('midterm_l_quiz1')
        student.midterm_l_quiz2 = request.form.get('midterm_l_quiz2')
        student.midterm_l_quiz3 = request.form.get('midterm_l_quiz3')
        student.midterm_l_quiz4 = request.form.get('midterm_l_quiz4')

        # FINAL QUIZZES
        student.final_quiz1 = request.form.get('final_quiz1')
        student.final_quiz2 = request.form.get('final_quiz2')
        student.final_quiz3 = request.form.get('final_quiz3')
        student.final_quiz4 = request.form.get('final_quiz4')

        student.final_e_quiz1 = request.form.get('final_e_quiz1')
        student.final_e_quiz2 = request.form.get('final_e_quiz2')
        student.final_e_quiz3 = request.form.get('final_e_quiz3')
        student.final_e_quiz4 = request.form.get('final_e_quiz4')

        student.final_l_quiz1 = request.form.get('final_l_quiz1')
        student.final_l_quiz2 = request.form.get('final_l_quiz2')
        student.final_l_quiz3 = request.form.get('final_l_quiz3')
        student.final_l_quiz4 = request.form.get('final_l_quiz4')

        db.session.commit()

        flash("Quizzes updated successfully!", "success")

        return redirect(url_for(
            'quizzes_page',
            student_id=student.student_id,
            subject=student.subject,
            saved=1
        ))

    saved = request.args.get('saved')

    return render_template(
        "quizzes.html",
        student=student,
        saved=saved
    )

@app.route('/pit/<student_id>/<subject>')
def pit_page(student_id, subject):

    subject = unquote(subject)

    student = Student.query.filter_by(
        student_id=student_id,
        subject=subject
    ).first_or_404()

    pit = {
        "midterm": [
            student.midterm_pit1,
            student.midterm_pit2,
            student.midterm_pit3,
            student.midterm_pit4
        ],
        "final": [
            student.final_pit1,
            student.final_pit2,
            student.final_pit3,
            student.final_pit4
        ]
    }

    return render_template(
        "pit.html",
        student=student,
        pit=pit
    )

@app.route('/report/<student_id>/<subject>', methods=['GET', 'POST'])
@login_required(role=['Admin','Instructor','Student'])
def report_page(student_id, subject):

    subject = unquote(subject)

    student = Student.query.filter_by(
        student_id=student_id,
        subject=subject
    ).first_or_404()

    if request.method == 'POST':

        if session.get('role') not in ['Admin', 'Instructor']:
            flash("Not allowed to edit.", "danger")
            return redirect(request.url)

        student.midterm_report1 = request.form.get('midterm_report1')
        student.final_report1 = request.form.get('final_report1')

        db.session.commit()

        return redirect(url_for(
            'report_page',
            student_id=student.student_id,
            subject=student.subject,
            saved=1
        ))

    saved = request.args.get('saved')

    return render_template(
        "report.html",
        student=student,
        saved=saved
    )

@app.route('/laboratory/<student_id>/<subject>')
def laboratory_page(student_id, subject):

    subject = unquote(subject)

    student = Student.query.filter_by(
        student_id=student_id,
        subject=subject
    ).first_or_404()

    labs = {
        "midterm": [
            student.midterm_laboratory1,
            student.midterm_laboratory2,
            student.midterm_laboratory3,
            student.midterm_laboratory4
        ],
        "final": [
            student.final_laboratory1,
            student.final_laboratory2,
            student.final_laboratory3,
            student.final_laboratory4
        ]
    }

    return render_template(
        "laboratory.html",
        student=student,
        labs=labs
    )

@app.route('/exercises/<student_id>/<subject>')
def exercises_page(student_id, subject):

    subject = unquote(subject)

    student = Student.query.filter_by(
        student_id=student_id,
        subject=subject
    ).first_or_404()

    exercises = {
        "midterm": [
            student.midterm_exercise1,
            student.midterm_exercise2,
            student.midterm_exercise3,
            student.midterm_exercise4
        ],
        "final": [
            student.final_exercise1,
            student.final_exercise2,
            student.final_exercise3,
            student.final_exercise4
        ]
    }

    return render_template(
        "exercises.html",
        student=student,
        exercises=exercises
    )

@app.route('/exams/<student_id>/<subject>', methods=['GET', 'POST'])
@login_required(role=['Admin', 'Instructor', 'Student'])
def exams_page(student_id, subject):

    subject = unquote(subject)

    student = Student.query.filter_by(
        student_id=student_id,
        subject=subject
    ).first_or_404()

    if request.method == 'POST':

        if session.get('role') not in ['Admin', 'Instructor']:
            flash("Not allowed to edit exams.", "danger")
            return redirect(request.url)

        student.midterm_exam = request.form.get('midterm_exam')
        student.midterm_laboratory_exam = request.form.get('midterm_laboratory_exam')
        student.midterm_grade = request.form.get('midterm_grade')
        student.midterm_remarks = request.form.get('midterm_remarks')

        student.final_exam = request.form.get('final_exam')
        student.final_laboratory_exam = request.form.get('final_laboratory_exam')
        student.final_grade = request.form.get('final_grade')
        student.final_remarks = request.form.get('final_remarks')

        db.session.commit()

        flash("Exams updated successfully!", "success")

        return redirect(url_for(
            'exams_page',
            student_id=student.student_id,
            subject=student.subject,
            saved=1
        ))

    saved = request.args.get('saved')

    # 🔥 ALWAYS SEND THIS
    exams = {
        "midterm_exam": student.midterm_exam,
        "midterm_laboratory_exam": student.midterm_laboratory_exam,
        "midterm_grade": student.midterm_grade,
        "midterm_remarks": student.midterm_remarks,

        "final_exam": student.final_exam,
        "final_laboratory_exam": student.final_laboratory_exam,
        "final_grade": student.final_grade,
        "final_remarks": student.final_remarks
    }

    return render_template(
        "exams.html",
        student=student,
        exams=exams,
        saved=saved
    )

# ====================
# --- Student View ---
# ====================
@app.route('/dashboard/admin/students')
@app.route('/dashboard/instructor/students')
@login_required(role=['Admin','Instructor'])
def view_students():
    # Only show students whose enrollment is approved (fixes bug where pending/rejected appear)
    approved_ids = [e.student_id for e in Enrollment.query.filter_by(status='approved').all()]
    if approved_ids:
        students = Student.query.filter(Student.student_id.in_(approved_ids)).all()
    else:
        # Fallback: if no enrollment system used (legacy data), show all
        # But prefer approved only
        students = Student.query.filter(Student.student_id.notin_(
            db.session.query(Enrollment.student_id).filter(Enrollment.status.in_(['pending','rejected']))
        )).all() if Enrollment.query.first() else Student.query.all()
    return render_template('students.html', students=students)

# ====================
# --- Student Add ---
# ====================
@app.route('/add_student', methods=['GET', 'POST'])
@login_required(role=['Admin','Instructor'])
def add_student():
    form = StudentForm()

    def get_val(field):
        """Safely get .data as string or None"""
        if not field or not field.data:
            return None
        v = str(field.data).strip()
        return v if v != "" else None

    if form.validate_on_submit():
        try:
            student_id = get_val(form.student_id)
            last_name = get_val(form.last_name)
            first_name = get_val(form.first_name)
            middle_name = get_val(form.middle_name)
            subject = get_val(form.subject)

            if not subject:
                flash("Subject is required (part of primary key).", "danger")
                return render_template('add_student.html', form=form)

            if Student.query.filter_by(student_id=student_id, subject=subject).first():
                flash(f"Student ID {student_id} with subject {subject} already exists!", "danger")
                return render_template('add_student.html', form=form)

            student = Student(
                student_id=student_id,
                last_name=last_name or "",
                first_name=first_name or "",
                middle_name=middle_name,
                subject=subject,
                section=get_val(form.section),
                subject_name=get_val(form.subject_name) or get_val(form.subject),
                subject_code=get_val(form.subject_code),

                # Grades / Remarks - FIXED: use .data not field object
                midterm_grade=get_val(form.midterm_grade),
                final_grade=get_val(form.final_grade),
                midterm_remarks=get_val(form.midterm_remarks),
                final_remarks=get_val(form.final_remarks),
            )
            # Sync legacy name column
            student.sync_legacy_name()

            db.session.add(student)
            db.session.commit()
            flash(f"Student {student.display_name} added successfully!", "success")
            return redirect(url_for('view_students'))

        except IntegrityError as e:
            db.session.rollback()
            flash(f"Database error: Duplicate Student ID + Subject? {str(e)}", "danger")
        except Exception as e:
            db.session.rollback()
            flash(f"Error adding student: {str(e)}", "danger")
    else:
        if request.method == 'POST':
            for field, errors in form.errors.items():
                for error in errors:
                    flash(f"{field}: {error}", "danger")

    return render_template('add_student.html', form=form)

# ====================
# --- Student Edit ---
# ====================
@app.route('/dashboard/admin/students/edit/<student_id>/<subject>', methods=['GET', 'POST'])
@app.route('/dashboard/instructor/students/edit/<student_id>/<subject>', methods=['GET', 'POST'])
@login_required(role=['Admin', 'Instructor'])
def edit_student(student_id, subject):
    student = Student.query.filter_by(student_id=student_id, subject=subject).first_or_404()
    form = StudentForm(obj=student)

    if form.validate_on_submit():
        old_student_id = student.student_id
        new_student_id = (form.student_id.data or "").strip()
        new_subject = (form.subject.data or "").strip() or student.subject

        # --- NEW: split name handling ---
        last_name = request.form.get('last_name','').strip() or getattr(student, 'last_name', '')
        first_name = request.form.get('first_name','').strip() or getattr(student, 'first_name', '')
        middle_name = request.form.get('middle_name','').strip() or getattr(student, 'middle_name', '')

        if last_name or first_name:
            student.last_name = last_name
            student.first_name = first_name
            student.middle_name = middle_name
            # Build display: SURNAME, First M.
            disp = last_name.upper() + ', ' + first_name
            if middle_name:
                disp += ' ' + middle_name[0].upper() + '.'
            student.name = disp
        else:
            # fallback to old form.name if exists
            student.name = (form.name.data or "").strip() if hasattr(form, 'name') else student.name

        student.section = (request.form.get('section','') or getattr(form, 'section', None) and form.section.data or "").strip()
        student.subject = new_subject

        # If student_id changed
        if old_student_id != new_student_id:
            existing_user = User.query.filter_by(username=new_student_id).first()
            if existing_user and new_student_id != old_student_id:
                flash(f"A user with ID {new_student_id} already exists!", "danger")
                return render_template('edit_student.html', form=form, student=student)
            user = User.query.filter_by(username=old_student_id, role='Student').first()
            if user:
                user.username = new_student_id
            student.student_id = new_student_id
            db.session.flush()

        db.session.commit()
        add_log(session.get('username'), f'Edited Student: {old_student_id} → {student.student_id} ({new_subject}) | {student.name}')
        flash('Student record updated successfully!', 'success')

        # --- CHANGED: go back to students.html not dashboard_student ---
        return redirect(url_for('view_students'))

    return render_template('edit_student.html', form=form, student=student)

# --- Student Reset Password ---
@app.route('/admin/reset_password/<student_id>', methods=['POST'])
@login_required(role='Admin')
def reset_password(student_id):
    user = User.query.filter_by(username=student_id, role='Student').first()

    if not user:
        flash("User not found.", "danger")
        return redirect(request.referrer)

    # Reset to default = student_id
    user.password = generate_password_hash(student_id)
    db.session.commit()

    add_log(session['username'], f"Reset password for student {student_id}")
    flash("Password reset to default (Student ID).", "success")
    return redirect(request.referrer)

# --- Student Change Password ---
@app.route('/admin/change_student_password/<student_id>', methods=['POST'])
@login_required(role='Admin')
def change_student_password(student_id):
    new_password = request.form.get('new_password')

    if not new_password:
        flash("Password cannot be empty.", "danger")
        return redirect(request.referrer)

    user = User.query.filter_by(username=student_id, role='Student').first()

    if not user:
        flash("User not found.", "danger")
        return redirect(request.referrer)

    user.password = generate_password_hash(new_password)
    db.session.commit()

    add_log(session['username'], f"Changed password for student {student_id}")
    flash("Password updated successfully.", "success")
    return redirect(request.referrer)

# --- Student delete ---
@app.route('/dashboard/admin/students/delete/<student_id>/<subject>', methods=['POST'])
@app.route('/dashboard/instructor/students/delete/<student_id>/<subject>', methods=['POST'])
@login_required(role=['Admin','Instructor'])
def delete_student(student_id, subject):
    student = Student.query.filter_by(student_id=student_id, subject=subject).first_or_404()
    
    # Delete corresponding User if exists
    user = User.query.filter_by(username=student.student_id, role='Student').first()
    if user:
        db.session.delete(user)

    db.session.delete(student)
    db.session.commit()

    add_log(session['username'], f'Deleted Student: {student.student_id} ({student.subject})')
    flash('Student deleted successfully!', 'success')
    return redirect(url_for('view_students'))

# --- Student bulk delete ---
@app.route('/dashboard/admin/students/bulk_delete', methods=['POST'])
@app.route('/dashboard/instructor/students/bulk_delete', methods=['POST'])
@login_required(role=['Admin','Instructor'])
def bulk_delete_students():
    student_ids = request.form.getlist('student_ids')
    if student_ids:
        deleted_students = []

        for sid in student_ids:
            student = Student.query.get(int(sid))
            if student:
                deleted_students.append(f'{student.student_id} ({student.subject})')

                # Delete corresponding User
                user = User.query.filter_by(username=student.student_id, role='Student').first()
                if user:
                    db.session.delete(user)

                db.session.delete(student)

        db.session.commit()
        add_log(session['username'], f'Bulk deleted Students: {", ".join(deleted_students)}')
        flash(f'{len(student_ids)} student(s) deleted successfully!', 'success')
    else:
        flash('No students selected for deletion.', 'warning')
    return redirect(url_for('view_students'))

# --- CSV upload ---
def parse_fullname(fullname):
    """Parse 'DEL SOCORRO, Charles T' -> last, first, middle"""
    if not fullname:
        return '', '', ''
    fullname = fullname.strip().strip('"')
    if ',' in fullname:
        parts = [p.strip() for p in fullname.split(',', 1)]
        last = parts[0]
        rest = parts[1] if len(parts) > 1 else ''
        rest_parts = rest.split()
        if not rest_parts:
            return last, '', ''
        first = rest_parts[0]
        middle = ' '.join(rest_parts[1:]) if len(rest_parts) > 1 else ''
        return last, first, middle
    else:
        toks = fullname.split()
        if len(toks) == 1:
            return toks[0], '', ''
        elif len(toks) == 2:
            return toks[1], toks[0], ''
        else:
            return toks[-1], toks[0], ' '.join(toks[1:-1])

@app.route('/dashboard/admin/students/upload', methods=['GET', 'POST'])
@app.route('/dashboard/instructor/students/upload', methods=['GET', 'POST'])
@login_required(role=['Admin', 'Instructor'])
def upload_students():
    form = UploadCSVForm()
    summary = None
    errors = []

    def safe_str(value):
        return str(value).strip() if value is not None else ""

    username = session.get('username', 'UnknownUser')

    if form.validate_on_submit():
        file = form.file.data
        if not file or file.filename == '':
            msg = "No file selected."
            errors.append(msg)
            add_log(username, f"CSV upload failed: {msg}")
            return render_template('upload_students.html', form=form, summary=summary, errors=errors)

        filename = secure_filename(file.filename)
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        added_students = []
        updated_students = []
        skipped_students = []

        try:
            with open(filepath, newline='', encoding='utf-8-sig') as csvfile:
                reader = csv.DictReader(csvfile)
                # Allow both old and new formats
                if not reader.fieldnames:
                    msg = "CSV has no header"
                    errors.append(msg)
                    return render_template('upload_students.html', form=form, summary=summary, errors=errors)

                # Normalize fieldnames lower
                lower_fields = [f.lower() for f in reader.fieldnames]
                # Required: student_id and subject, and at least one of name OR last_name+first_name
                has_name = 'name' in lower_fields or 'fullname' in lower_fields
                has_split = 'last_name' in lower_fields and 'first_name' in lower_fields
                if 'student_id' not in lower_fields or 'subject' not in lower_fields or not (has_name or has_split):
                    msg = f"CSV missing required columns. Found: {reader.fieldnames}. Need student_id, subject and (name OR last_name+first_name)"
                    errors.append(msg)
                    return render_template('upload_students.html', form=form, summary=summary, errors=errors)

                for row_num, row in enumerate(reader, start=2):
                    try:
                        # Case-insensitive get
                        def get_col(*keys):
                            for k in keys:
                                if k in row:
                                    return row.get(k)
                                # try case-insensitive
                                for rk in row.keys():
                                    if rk.lower() == k.lower():
                                        return row.get(rk)
                            return ''

                        student_id = safe_str(get_col('student_id')).strip()
                        section = safe_str(get_col('section')).strip()
                        subject = safe_str(get_col('subject')).strip().title()

                        # Try split first
                        last_name = safe_str(get_col('last_name')).strip()
                        first_name = safe_str(get_col('first_name')).strip()
                        middle_name = safe_str(get_col('middle_name', 'mi')).strip()

                        # If split not provided, parse from name/fullname
                        name_raw = safe_str(get_col('name', 'fullname')).strip()
                        if (not last_name or not first_name) and name_raw:
                            ln, fn, mn = parse_fullname(name_raw)
                            last_name = last_name or ln
                            first_name = first_name or fn
                            middle_name = middle_name or mn

                        # Build display name: SURNAME, First M.
                        if last_name or first_name:
                            disp = last_name.upper() + ', ' + first_name
                            if middle_name:
                                disp += ' ' + middle_name[0].upper() + '.'
                            name = disp
                        else:
                            name = name_raw

                        # Subject lookup
                        subject_record = Subject.query.filter_by(subject_name=subject).first()
                        subject_code = ""
                        subject_name = subject
                        if subject_record:
                            subject_code = subject_record.subject_code
                            subject_name = subject_record.subject_name

                        if not student_id or not subject or not name:
                            msg = f"Row {row_num}: missing required field(s) student_id/subject/name."
                            errors.append(msg)
                            continue

                        if subject.lower() == name.lower() or subject.lower() in name.lower():
                            skipped_students.append(f"{student_id} - {name} ({subject})")
                            continue

                        field_updates = {
                            'section': section,
                            'year': get_col('year'),
                            'school_year': get_col('school_year'),
                            'semester': get_col('semester'),
                            'subject_code': subject_code,
                            'subject_name': subject_name,
                            # Attendance, quizzes etc - keep original mapping
                            'midterm_attendance1': row.get('midterm_attendance1'),
                            'midterm_attendance2': row.get('midterm_attendance2'),
                            'midterm_attendance3': row.get('midterm_attendance3'),
                            'midterm_attendance4': row.get('midterm_attendance4'),
                            'final_attendance1': row.get('final_attendance1'),
                            'final_attendance2': row.get('final_attendance2'),
                            'final_attendance3': row.get('final_attendance3'),
                            'final_attendance4': row.get('final_attendance4'),
                            'midterm_quiz1': row.get('midterm_quiz1'),
                            'midterm_quiz2': row.get('midterm_quiz2'),
                            'midterm_quiz3': row.get('midterm_quiz3'),
                            'midterm_quiz4': row.get('midterm_quiz4'),
                            'midterm_e_quiz1': row.get('midterm_e_quiz1'),
                            'midterm_e_quiz2': row.get('midterm_e_quiz2'),
                            'midterm_e_quiz3': row.get('midterm_e_quiz3'),
                            'midterm_e_quiz4': row.get('midterm_e_quiz4'),
                            'midterm_l_quiz1': row.get('midterm_l_quiz1'),
                            'midterm_l_quiz2': row.get('midterm_l_quiz2'),
                            'midterm_l_quiz3': row.get('midterm_l_quiz3'),
                            'midterm_l_quiz4': row.get('midterm_l_quiz4'),
                            'final_quiz1': row.get('final_quiz1'),
                            'final_quiz2': row.get('final_quiz2'),
                            'final_quiz3': row.get('final_quiz3'),
                            'final_quiz4': row.get('final_quiz4'),
                            'final_e_quiz1': row.get('final_e_quiz1'),
                            'final_e_quiz2': row.get('final_e_quiz2'),
                            'final_e_quiz3': row.get('final_e_quiz3'),
                            'final_e_quiz4': row.get('final_e_quiz4'),
                            'final_l_quiz1': row.get('final_l_quiz1'),
                            'final_l_quiz2': row.get('final_l_quiz2'),
                            'final_l_quiz3': row.get('final_l_quiz3'),
                            'final_l_quiz4': row.get('final_l_quiz4'),
                            'midterm_pit1': row.get('midterm_pit1'),
                            'midterm_pit2': row.get('midterm_pit2'),
                            'midterm_pit3': row.get('midterm_pit3'),
                            'midterm_pit4': row.get('midterm_pit4'),
                            'final_pit1': row.get('final_pit1'),
                            'final_pit2': row.get('final_pit2'),
                            'final_pit3': row.get('final_pit3'),
                            'final_pit4': row.get('final_pit4'),
                            'midterm_report1': row.get('midterm_report1'),
                            'final_report1': row.get('final_report1'),
                            'midterm_exercise1': row.get('midterm_exercise1'),
                            'midterm_exercise2': row.get('midterm_exercise2'),
                            'midterm_exercise3': row.get('midterm_exercise3'),
                            'midterm_exercise4': row.get('midterm_exercise4'),
                            'final_exercise1': row.get('final_exercise1'),
                            'final_exercise2': row.get('final_exercise2'),
                            'final_exercise3': row.get('final_exercise3'),
                            'final_exercise4': row.get('final_exercise4'),
                            'midterm_laboratory1': row.get('midterm_laboratory1'),
                            'midterm_laboratory2': row.get('midterm_laboratory2'),
                            'midterm_laboratory3': row.get('midterm_laboratory3'),
                            'midterm_laboratory4': row.get('midterm_laboratory4'),
                            'final_laboratory1': row.get('final_laboratory1'),
                            'final_laboratory2': row.get('final_laboratory2'),
                            'final_laboratory3': row.get('final_laboratory3'),
                            'final_laboratory4': row.get('final_laboratory4'),
                            'midterm_exam': row.get('midterm_exam'),
                            'final_exam': row.get('final_exam'),
                            'midterm_laboratory_exam': row.get('midterm_laboratory_exam'),
                            'final_laboratory_exam': row.get('final_laboratory_exam'),
                            'midterm_grade': row.get('midterm_grade'),
                            'final_grade': row.get('final_grade'),
                            'midterm_remarks': row.get('midterm_remarks'),
                            'final_remarks': row.get('final_remarks'),
                        }

                        # If your Student model has last_name, first_name, middle_name columns, include them
                        if hasattr(Student, 'last_name'):
                            field_updates['last_name'] = last_name
                            field_updates['first_name'] = first_name
                            field_updates['middle_name'] = middle_name

                        student = Student.query.filter(
                            db.func.lower(Student.student_id) == student_id.lower(),
                            db.func.lower(Student.subject) == subject.lower()
                        ).first()
                        if student:
                            patch_update(student, field_updates)
                            if name:
                                student.name = name
                            updated_students.append(f"{student_id} - {name} ({subject})")
                        else:
                            create_kwargs = {
                                'student_id': student_id.strip(),
                                'name': name.strip(),
                                'section': section.strip(),
                                'subject': subject.strip().title(),
                                'subject_code': subject_code,
                                'subject_name': subject_name,
                                'year': safe_str(get_col('year')).strip(),
                                'school_year': safe_str(get_col('school_year')).strip(),
                                'semester': safe_str(get_col('semester')).strip(),
                            }
                            for k, v in field_updates.items():
                                if hasattr(Student, k):
                                    create_kwargs[k] = v
                            new_student = Student(**create_kwargs)
                            db.session.add(new_student)
                            added_students.append(f"{student_id} - {name} ({subject})")

                        existing_user = User.query.filter_by(username=student_id).first()
                        if not existing_user:
                            hashed_password = generate_password_hash(student_id)
                            new_user = User(username=student_id, password=hashed_password, role='Student')
                            db.session.add(new_user)

                    except Exception as row_error:
                        msg = f"Row {row_num}: {type(row_error).__name__} - {row_error}"
                        errors.append(msg)

            try:
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                errors.append(f"Database commit failed: {e}")

        except Exception as file_error:
            errors.append(f"Error reading file: {file_error}")

        if errors:
            return render_template('upload_students.html', form=form, summary=None, errors=errors)

        flash(f'CSV uploaded: {len(added_students)} added, {len(updated_students)} updated', 'success')
        summary = {'added': added_students, 'updated': updated_students, 'skipped': skipped_students}
        return render_template('upload_students.html', form=form, summary=summary, errors=None)

    return render_template('upload_students.html', form=form, summary=summary, errors=None)


    return render_template('upload_students.html', form=form, summary=summary, errors=None)

# --- CSV download ---
@app.route('/dashboard/admin/download_csv')
@login_required(role=['Admin', 'Instructor'])
def download_csv():

    # Get filters from export page
    year = request.args.get('year')
    section = request.args.get('section')
    subject = request.args.get('subject')
    semester = request.args.get('semester')
    school_year = request.args.get('school_year')

    # Base query
    query = Student.query

    # Apply filters only if they exist
    if year:
        query = query.filter(Student.year == year)

    if section:
        query = query.filter(Student.section == section)

    if subject:
        query = query.filter(Student.subject == subject)

    if semester:
        query = query.filter(Student.semester == semester)

    if school_year:
        query = query.filter(Student.school_year == school_year)

    students = query.all()

    def clean(value):
        return '' if value is None else str(value).strip()

    def generate():
        headers = [column.name for column in Student.__table__.columns]
        yield ",".join(headers) + "\n"

        for s in students:
            row = []
            for column in headers:
                value = getattr(s, column)
                value = clean(value).replace('"', '""')
                row.append(f'"{value}"')
            yield ",".join(row) + "\n"

    return Response(
        generate(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=students_filtered.csv"
        }
    )

# --- Export Data ---
@app.route('/dashboard/admin/export')
@login_required(role=['Admin', 'Instructor'])
def export_csv():

    preview = request.args.get('preview')

    year = (request.args.get('year') or '').strip()
    section = (request.args.get('section') or '').strip()
    subject = (request.args.get('subject') or '').strip()
    semester = (request.args.get('semester') or '').strip()
    school_year = (request.args.get('school_year') or '').strip()
    activity_type = (request.args.get('activity_type') or '').strip()
    term = (request.args.get('term') or '').strip().lower()

    query = Student.query

    # --- Filters (case-insensitive safe) ---
    if year:
        query = query.filter(func.lower(Student.year) == year.lower())

    if section:
        query = query.filter(func.lower(Student.section) == section.lower())

    if subject:
        query = query.filter(func.lower(Student.subject) == subject.lower())

    if semester:
        query = query.filter(func.lower(Student.semester) == semester.lower())

    if school_year:
        query = query.filter(func.lower(Student.school_year) == school_year.lower())

    students = query.limit(50).all()

    # --- Base headers ---
    base_headers = [
        "student_id",
        "name",
        "year",
        "section",
        "school_year",
        "semester",
        "subject"
    ]

    # --- Activity mapping ---
    activity_columns = {

        "attendance": [
            "midterm_attendance1","midterm_attendance2",
            "midterm_attendance3","midterm_attendance4",
            "final_attendance1","final_attendance2",
            "final_attendance3","final_attendance4"
        ],

        "quiz": [
            "midterm_quiz1","midterm_quiz2","midterm_quiz3","midterm_quiz4",
            "final_quiz1","final_quiz2","final_quiz3","final_quiz4",

            "midterm_e_quiz1","midterm_e_quiz2",
            "midterm_e_quiz3","midterm_e_quiz4",

            "final_e_quiz1","final_e_quiz2",
            "final_e_quiz3","final_e_quiz4",

            "midterm_l_quiz1","midterm_l_quiz2",
            "midterm_l_quiz3","midterm_l_quiz4",

            "final_l_quiz1","final_l_quiz2",
            "final_l_quiz3","final_l_quiz4"
        ],

        "exam": [
            "midterm_exam",
            "final_exam",
            "midterm_laboratory_exam",
            "final_laboratory_exam"
        ],

        "pit": [
            "midterm_pit1","midterm_pit2",
            "midterm_pit3","midterm_pit4",

            "final_pit1","final_pit2",
            "final_pit3","final_pit4"
        ],

        "exercise": [
            "midterm_exercise1","midterm_exercise2",
            "midterm_exercise3","midterm_exercise4",

            "final_exercise1","final_exercise2",
            "final_exercise3","final_exercise4"
        ],

        "laboratory": [
            "midterm_laboratory1","midterm_laboratory2",
            "midterm_laboratory3","midterm_laboratory4",

            "final_laboratory1","final_laboratory2",
            "final_laboratory3","final_laboratory4"
        ],

        "report": [
            "midterm_report1",
            "final_report1"
        ],

        "grades": [
            "midterm_grade",
            "final_grade",
            "midterm_remarks",
            "final_remarks"
        ]
    }

    # --- Build final headers ---
    headers = base_headers.copy()

    if activity_type in activity_columns:

        selected_columns = activity_columns[activity_type]

        # --- FILTER BY TERM ---
        if term == "midterm":
            selected_columns = [
                col for col in selected_columns
                if col.startswith("midterm")
            ]

        elif term == "final":
            selected_columns = [
                col for col in selected_columns
                if col.startswith("final")
            ]

        headers += selected_columns

    # --- PREVIEW MODE ---
    if preview:

        data = []

        for s in students:

            row = {}

            for h in headers:
                value = getattr(s, h, "")
                row[h] = "" if value is None else str(value).strip()

            data.append(row)

        return render_template(
            "export_preview.html",
            data=data,
            headers=headers,
            filters=request.args
        )

    # --- SAFE VALUE ---
    def safe_value(v):
        if v is None:
            return ""
        return str(v).strip()

    # --- CSV GENERATOR ---
    def generate():

        yield '\ufeff'

        yield ",".join(headers) + "\n"

        for s in students:

            row = []

            for col in headers:

                value = safe_value(getattr(s, col, ""))

                value = value.replace('"', '""')

                row.append(f'"{value}"')

            yield ",".join(row) + "\n"

    filename = f"{activity_type or 'students'}_{term or 'all'}_export.csv"

    return Response(
        generate(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )

# --- Export Page ---
@app.route('/dashboard/instructor/export_page')
@app.route('/dashboard/admin/export_page')
@login_required(role=['Admin', 'Instructor'])
def export_page():
    return render_template('export.html')

def ensure_enrollment_columns():
    """Auto-migrate enrollment for multi-subject re-enrollment"""
    try:
        from sqlalchemy import text as _text
        with db.engine.connect() as conn:
            conn.execute(_text("ALTER TABLE enrollment ADD COLUMN IF NOT EXISTS subject VARCHAR(100);"))
            conn.execute(_text("ALTER TABLE enrollment ADD COLUMN IF NOT EXISTS subject_code VARCHAR(50);"))
            conn.execute(_text("ALTER TABLE enrollment ADD COLUMN IF NOT EXISTS subject_name VARCHAR(150);"))
            conn.execute(_text("ALTER TABLE enrollment ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE;"))
            conn.execute(_text("ALTER TABLE enrollment ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP;"))
            conn.execute(_text("CREATE INDEX IF NOT EXISTS idx_enrollment_is_deleted ON enrollment(is_deleted);"))
            conn.execute(_text("UPDATE enrollment SET is_deleted = FALSE WHERE is_deleted IS NULL;"))
            conn.execute(_text("ALTER TABLE enrollment DROP CONSTRAINT IF EXISTS enrollment_student_id_key;"))
            conn.execute(_text("CREATE INDEX IF NOT EXISTS idx_enrollment_student_id ON enrollment(student_id);"))
            conn.execute(_text("CREATE INDEX IF NOT EXISTS idx_enrollment_subject_code ON enrollment(subject_code);"))
            # Also allow student table to have multiple subjects per ID (composite PK already handles it, but ensure columns)
            conn.execute(_text("ALTER TABLE student ADD COLUMN IF NOT EXISTS subject_name VARCHAR(100);"))
            conn.execute(_text("ALTER TABLE student ADD COLUMN IF NOT EXISTS subject_code VARCHAR(50);"))
            conn.commit()
        print("✅ Enrollment auto-migration OK")
    except Exception as e:
        print(f"⚠️ Enrollment auto-migration skipped: {e}")

def ensure_question_columns():
    """Auto-migrate NeonDB - IF NOT EXISTS, safe for 0.1 CPU Render + gunicorn - no manual cd command"""
    try:
        from sqlalchemy import text as _text
        with db.engine.connect() as conn:
            conn.execute(_text("ALTER TABLE questions ADD COLUMN IF NOT EXISTS choices_json TEXT;"))
            conn.execute(_text("ALTER TABLE questions ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE;"))
            conn.execute(_text("ALTER TABLE questions ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP;"))
            conn.execute(_text("ALTER TABLE questions ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;"))
            conn.execute(_text("CREATE INDEX IF NOT EXISTS idx_questions_is_deleted ON questions(is_deleted);"))
            conn.execute(_text("CREATE INDEX IF NOT EXISTS idx_questions_is_active ON questions(is_active);"))
            conn.commit()
            conn.execute(_text("UPDATE questions SET is_deleted = FALSE WHERE is_deleted IS NULL;"))
            conn.execute(_text("UPDATE questions SET is_active = TRUE WHERE is_active IS NULL;"))
            conn.commit()
        print("✅ Auto-migration OK - no manual command needed")
    except Exception as e:
        print(f"⚠️ Auto-migration skipped: {e}")

# Run on import - for gunicorn on Render (no waitress)
try:
    with app.app_context():
        ensure_enrollment_columns()
        ensure_question_columns()
except Exception as _e:
    print(f"Auto-migrate on import skipped: {_e}")

# For local Flask terminal: python app.py
# For Render: gunicorn app:app
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        ensure_enrollment_columns()
        ensure_question_columns()
        if not User.query.filter_by(username='admin').first():
            db.session.add(User(username='admin', password=generate_password_hash('admin123'), role='Admin'))
        if not User.query.filter_by(username='student').first():
            db.session.add(User(username='student', password=generate_password_hash('stud123'), role='Student'))
        db.session.commit()

    ENV = os.environ.get('FLASK_ENV', 'development')
    port = int(os.environ.get('PORT', 5000))
    app.run(host="0.0.0.0", port=port, debug=(ENV == 'development'), threaded=True)
