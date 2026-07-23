# Student Portal - NEW DEPLOY (Fixed Render)

This is your NEW website - same NeonDB, new Render service, new GitHub repo.

## What was fixed for Render optimization

**Old problem:**
```
WEB_CONCURRENCY=1 + waitress + SSE /stream/admin (infinite) = queue depth 29
```

**New fix:**
- Server: `gunicorn -k gevent -w 1` (handles 1000+ concurrent SSE on 1 worker)
- No more waitress queue blocking
- Templates optimized: archived pages minimal, no student table
- Finished exams: count badge + BroadcastChannel sync (no SSE)

## Deploy Steps - New Website

### 1. Create new GitHub repo
1. Go to github.com -> New repository -> `student-portal-v2`
2. Don't init with README

### 2. Push this folder
```bash
cd NEW_PORTAL
git init
git add .
git commit -m "New portal - gevent + minimal archived + same NeonDB"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/student-portal-v2.git
git push -u origin main
```

### 3. Create new Render service
1. Render.com -> New -> Web Service -> Connect `student-portal-v2` repo
2. Settings:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn -k gevent -w 1 --bind 0.0.0.0:$PORT app:app --timeout 120`
   - Python Version: 3.11.11

3. Environment Variables (Render -> Environment):
   ```
   DATABASE_URL=postgresql://neondb_owner:npg_97DuTpZbOLJY@ep-cold-resonance-a1ldqo6i-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require
   SECRET_KEY=your_random_secret_123_change_this
   PYTHON_VERSION=3.11.11
   ```

4. Deploy -> New URL: https://student-portal-v2-xxxx.onrender.com

Same database = all students/exams stay. Old site stays live until you delete it.

### 4. Optimize NeonDB connection (already done in app.py)
```python
pool_size=5, max_overflow=10, pool_recycle=300, pool_pre_ping=True
```

## Files included
- templates/base.html -> fixed window.examChannel guard (no duplicate const)
- templates/archived_exams.html -> minimal + buttons preserved
- templates/security_logs_archived.html -> minimal no students
- templates/finished_exams.html -> count badge fixed + no SSE
- templates/view_exams.html -> seamless + modals

Enjoy new fast site!
