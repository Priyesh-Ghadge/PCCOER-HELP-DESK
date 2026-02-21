import os
import io
import logging
from datetime import datetime
from typing import Optional

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    send_file,
)
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    login_required,
    logout_user,
    current_user,
)

# Firebase
import firebase_admin
from firebase_admin import credentials, db

# PDF generation
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from PIL import Image

# ---------------- CONFIG (EDIT THESE) ----------------
SERVICE_ACCOUNT_PATH = r"Service account path"
DATABASE_URL = "Database URL"
TEMPLATE_IMAGE_PATH = os.path.join(os.getcwd(), "bonafide_template.png")
ADMIN_USERNAME = "Admin Username"
ADMIN_PASSWORD = "Admin Password"
FLASK_PORT = 5000
# ----------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "change_this_secret_for_prod")

# Flask-Login setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# Firebase refs (populated in init_firebase)
applications_ref = None
student_ref = None


class AdminUser(UserMixin):
    def __init__(self, id: str = "admin"):
        self.id = id

    def get_id(self):
        return self.id


@login_manager.user_loader
def load_user(user_id: str) -> Optional[AdminUser]:
    if user_id == ADMIN_USERNAME:
        return AdminUser(id=user_id)
    return None


def init_firebase():
    global applications_ref, student_ref
    if not os.path.exists(SERVICE_ACCOUNT_PATH):
        logger.error("Service account file not found: %s", SERVICE_ACCOUNT_PATH)
        raise FileNotFoundError(f"Service account not found at {SERVICE_ACCOUNT_PATH}")

    try:
        cred = credentials.Certificate(SERVICE_ACCOUNT_PATH)
        firebase_admin.initialize_app(cred, {"databaseURL": DATABASE_URL})
        logger.info("Initialized Firebase with DB URL: %s", DATABASE_URL)
    except ValueError:
        # already initialized, that's fine
        logger.warning("Firebase app already initialized in this process.")
    except Exception as e:
        logger.exception("Failed to init firebase_admin: %s", e)
        raise

    applications_ref = db.reference("bonafide_applications")
    student_ref = db.reference("Students")


# ----------------------- Routes -----------------------
@app.route("/", methods=["GET"])
def index():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            user = AdminUser(id=username)
            login_user(user)
            flash("Logged in successfully.", "success")
            return redirect(url_for("dashboard"))
        else:
            flash("Invalid username or password.", "danger")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out.", "info")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    try:
        all_apps = applications_ref.get() or {}
    except Exception as e:
        logger.exception("Error reading applications: %s", e)
        all_apps = {}
        flash("Could not load applications from database.", "warning")
    return render_template("dashboard.html", applications=all_apps)

@app.route("/application/<app_id>/process", methods=["POST"])
@login_required
def process_application(app_id: str):
    """
    Update status field for the application (from the admin UI).
    This is the endpoint the form in application.html posts to.
    """
    # Validate input
    new_status = request.form.get("status", "").strip()
    if new_status not in ("Pending", "Approved", "Rejected"):
        flash("Invalid status.", "error")
        return redirect(url_for("view_application", app_id=app_id))

    try:
        # Update DB
        applications_ref.child(app_id).update({
            "status": new_status,
            "processed_at": datetime.utcnow().isoformat()
        })
        flash(f"Application {app_id} status updated to {new_status}.", "success")
    except Exception as e:
        logger.exception("Failed to update application %s: %s", app_id, e)
        flash("Failed to update application in database.", "danger")

    return redirect(url_for("view_application", app_id=app_id))



@app.route("/application/<app_id>")
@login_required
def view_application(app_id: str):
    try:
        app_data = applications_ref.child(app_id).get()
    except Exception as e:
        logger.exception("Error reading application %s: %s", app_id, e)
        app_data = None
    if not app_data:
        flash("Application not found.", "danger")
        return redirect(url_for("dashboard"))
    return render_template("application.html", app_id=app_id, app=app_data)


# ------------------ PDF generation -------------------
import io
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from datetime import datetime

def generate_bonafide_pdf(application_data):
    """
    Generates bonafide certificate PDF using PNG template
    with perfectly aligned text.
    """

    buffer = io.BytesIO()

    # --- Load template image ---
    template_path = "static/bonafide_template.png"
    bg = ImageReader(template_path)
    img_width, img_height = bg.getSize()

    # --- Create canvas EXACTLY same size as image ---
    pdf = canvas.Canvas(buffer, pagesize=(img_width, img_height))

    # --- Draw background ---
    pdf.drawImage(
        template_path,
        0,
        0,
        width=img_width,
        height=img_height
    )

    # ================= TEXT SETTINGS =================
    pdf.setFillColorRGB(0, 0, 0)

    # ---------- NAME (CENTERED) ----------
    pdf.setFont("Times-Bold", 22)
    pdf.drawCentredString(
        img_width / 2,
        img_height - 470,
        application_data["name"]
    )

    # ---------- YEAR & BRANCH ----------
    pdf.setFont("Times-Roman", 14)
    pdf.drawString(
        500,
        img_height - 545,
        "First Year"
    )

    pdf.drawString(
        650,
        img_height - 545,
        "Engineering"
    )

    # ---------- PRN ----------
    pdf.drawString(
        520,
        img_height - 595,
        application_data["prn"]
    )

    # ---------- BATCH ----------
    pdf.drawString(
        345,
        img_height - 660,
        application_data["batch"]
    )

    # ---------- PURPOSE ----------
    pdf.drawString(
        480,
        img_height - 720,
        "Official Use"
    )

    # ---------- DATE ----------
    today = datetime.now().strftime("%d / %m / %Y")
    pdf.drawString(
        320,
        img_height - 770,
        today
    )

    # ---------- PLACE ----------
    pdf.drawString(
        150,
        img_height - 840,
        "Pune"
    )

    # =================================================

    pdf.showPage()
    pdf.save()
    buffer.seek(0)

    return buffer




@app.route("/application/<app_id>/print")
@login_required
def print_bonafide(app_id):
    try:
        app_data = applications_ref.child(app_id).get()

        if not app_data:
            return "Application not found", 404

        if app_data.get("status") != "Approved":
            return "Application not approved", 403

        import io
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.utils import ImageReader
        from datetime import datetime

        buffer = io.BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4

        # ---- Background Image ----
        bg_path = "static/bonafide_template.png"
        pdf.drawImage(
            ImageReader(bg_path),
            0, 0,
            width=width,
            height=height
        )

        # ---- SAFE DATA FETCH ----
        name = app_data.get("name", "")
        prn = app_data.get("prn", "")
        batch = app_data.get("batch", "")
        branch = app_data.get("branch", "Engineering")
        year = app_data.get("year", "First Year")
        purpose = app_data.get("purpose", "Bonafide Certificate")

        # ---- TEXT ----
        pdf.setFont("Times-Roman", 14)
        pdf.drawString(180, 515, name)
        pdf.drawString(230, 485, year)
        pdf.drawString(310, 485, branch)
        pdf.drawString(260, 455, prn)
        pdf.drawString(360, 455, batch)
        pdf.drawString(120, 420, purpose)

        pdf.setFont("Times-Roman", 12)
        pdf.drawString(
            120, 390,
            datetime.now().strftime("%d / %m / %Y")
        )

        pdf.showPage()
        pdf.save()
        buffer.seek(0)

        return send_file(
            buffer,
            as_attachment=True,
            download_name=f"{prn}_bonafide.pdf",
            mimetype="application/pdf"
        )

    except Exception as e:
        print("🔥 PDF ERROR:", e)
        return "PDF generation failed. Check server logs.", 500



# ---------------------- start app -----------------------
def ensure_templates():
    """Create default Bootstrap templates if missing (only if you didn't already create them)."""
    TPLS = {
        "login.html": """<!doctype html>
<html lang="en"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Admin Login</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet"></head>
<body class="bg-light"><div class="container py-5"><div class="row justify-content-center">
<div class="col-md-6 col-lg-5"><div class="card shadow-sm"><div class="card-body p-4">
<h4 class="card-title mb-3">Admin Login</h4>
{% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, msg in messages %}
<div class="alert alert-{{ category }} alert-dismissible fade show" role="alert">{{ msg }} <button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>
{% endfor %}{% endif %}{% endwith %}
<form method="post" novalidate><div class="mb-3"><label class="form-label">Username</label><input name="username" class="form-control" required autofocus></div>
<div class="mb-3"><label class="form-label">Password</label><input name="password" type="password" class="form-control" required></div>
<div class="d-grid"><button class="btn btn-primary">Login</button></div></form>
<small class="text-muted mt-3 d-block">This is a simple admin interface — secure properly for production.</small>
</div></div></div></div></div><script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script></body></html>""",
        "dashboard.html": """<!doctype html><html lang="en"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/><title>Dashboard</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
<style>.table-responsive { max-height: 70vh; overflow: auto; }</style></head><body>
<nav class="navbar navbar-expand-lg navbar-dark bg-primary"><div class="container-fluid"><a class="navbar-brand" href="{{ url_for('dashboard') }}">DTIL Admin</a>
<div class="collapse navbar-collapse"><ul class="navbar-nav ms-auto"><li class="nav-item"><a class="nav-link" href="{{ url_for('logout') }}">Logout</a></li></ul></div></div></nav>
<div class="container my-4"><div class="d-flex justify-content-between align-items-center mb-3"><h3>Bonafide Applications</h3>
<div><input id="searchBox" class="form-control" placeholder="Search PRN / Name / Status" style="min-width:300px;"></div></div>
{% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, msg in messages %}
<div class="alert alert-{{ category }} alert-dismissible fade show" role="alert">{{ msg }} <button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>{% endfor %}{% endif %}{% endwith %}
<div class="table-responsive"><table class="table table-striped table-hover align-middle"><thead class="table-light position-sticky top-0"><tr><th>Firebase ID</th><th>PRN</th><th>Name</th><th>Phone</th><th>Batch</th><th>Status</th><th>Submitted</th><th>Actions</th></tr></thead>
<tbody id="appTableBody">{% if applications %}{% for app_id, app in applications.items() %}<tr><td class="text-monospace">{{ app_id }}</td><td>{{ app.get('prn','') }}</td><td>{{ app.get('name','') }}</td><td>{{ app.get('phone','') }}</td><td>{{ app.get('batch','') }}</td><td>{% if app.get('status') == 'Approved' %}<span class="badge bg-success">Approved</span>{% elif app.get('status') == 'Rejected' %}<span class="badge bg-danger">Rejected</span>{% else %}<span class="badge bg-secondary">Pending</span>{% endif %}</td><td>{{ app.get('submitted_at','') }}</td>
<td><a class="btn btn-sm btn-outline-primary" href="{{ url_for('view_application', app_id=app_id) }}">View</a></td></tr>{% endfor %}{% else %}<tr><td colspan="8" class="text-center">No applications found.</td></tr>{% endif %}</tbody></table></div></div>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script><script>const searchBox=document.getElementById('searchBox');const tbody=document.getElementById('appTableBody');searchBox.addEventListener('input',function(){const q=this.value.trim().toLowerCase();if(!q){Array.from(tbody.rows).forEach(r=>r.style.display='');return;}Array.from(tbody.rows).forEach(function(row){const text=row.innerText.toLowerCase();row.style.display=text.includes(q)?'':'none';});});</script></body></html>""",
        "application.html": """<!doctype html><html lang="en"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/><title>Application {{ app_id }}</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet"></head><body>
<nav class="navbar navbar-expand-lg navbar-dark bg-primary"><div class="container-fluid"><a class="navbar-brand" href="{{ url_for('dashboard') }}">DTIL Admin</a><div class="collapse navbar-collapse"><ul class="navbar-nav ms-auto"><li class="nav-item"><a class="nav-link" href="{{ url_for('logout') }}">Logout</a></li></ul></div></div></nav>
<div class="container my-4"><div class="card"><div class="card-body"><h4>Application <small class="text-monospace">{{ app_id }}</small></h4>
<dl class="row"><dt class="col-sm-3">PRN</dt><dd class="col-sm-9">{{ app.get('prn') }}</dd><dt class="col-sm-3">Name</dt><dd class="col-sm-9">{{ app.get('name') }}</dd><dt class="col-sm-3">Phone</dt><dd class="col-sm-9">{{ app.get('phone') }}</dd>
<dt class="col-sm-3">Batch</dt><dd class="col-sm-9">{{ app.get('batch') }}</dd><dt class="col-sm-3">Year</dt><dd class="col-sm-9">{{ app.get('year') }}</dd><dt class="col-sm-3">Branch</dt><dd class="col-sm-9">{{ app.get('branch') }}</dd>
<dt class="col-sm-3">Purpose</dt><dd class="col-sm-9">{{ app.get('purpose') }}</dd><dt class="col-sm-3">Submitted at</dt><dd class="col-sm-9">{{ app.get('submitted_at') }}</dd><dt class="col-sm-3">Status</dt><dd class="col-sm-9">{{ app.get('status') }}</dd></dl>
<form method="post" action="{{ url_for('process_application', app_id=app_id) }}" class="row g-2 mb-3"><div class="col-auto"><select name="status" class="form-select"><option value="Pending" {% if app.get('status')=='Pending' %}selected{% endif %}>Pending</option><option value="Approved" {% if app.get('status')=='Approved' %}selected{% endif %}>Approve</option><option value="Rejected" {% if app.get('status')=='Rejected' %}selected{% endif %}>Reject</option></select></div>
<div class="col-auto"><button class="btn btn-primary">Update Status</button></div><div class="col-auto"><a class="btn btn-outline-secondary" href="{{ url_for('dashboard') }}">Back</a></div></form>
<a class="btn btn-success" href="{{ url_for('generate_bonafide', app_id=app_id) }}">Generate & Download Bonafide PDF</a>
</div></div></div><script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script></body></html>"""
    }
    tpl_dir = os.path.join(os.getcwd(), "templates")
    os.makedirs(tpl_dir, exist_ok=True)
    for name, content in TPLS.items():
        path = os.path.join(tpl_dir, name)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info("Wrote default template: %s", path)


def start_app():
    ensure_templates()
    init_firebase()
    logger.info("Starting Flask app on port %d", FLASK_PORT)
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=False)


if __name__ == "__main__":
    start_app()
